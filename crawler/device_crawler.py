"""
device_crawler.py
-----------------
Web crawler that scrapes handheld device / microcontroller / PCB specs
from known sources (Waveshare, Adafruit, SparkFun, Arduino, RPi Foundation).
Returns a normalised DeviceSpec dict ready for the dimension parser.
"""

import re
import time
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known product page patterns per vendor
# ---------------------------------------------------------------------------
VENDOR_PATTERNS = {
    "waveshare": {
        "base_url": "https://www.waveshare.com",
        "search_url": "https://www.waveshare.com/catalogsearch/result/?q={query}",
        "dimension_selectors": [
            "table.specifications td",
            "div.product-attribute-specs-table td",
            "table.data-table td",
        ],
    },
    "adafruit": {
        "base_url": "https://www.adafruit.com",
        "search_url": "https://www.adafruit.com/search?q={query}",
        "dimension_selectors": [
            "table.product-specs td",
            "div#product-description li",
            "div.productDescription li",
        ],
    },
    "sparkfun": {
        "base_url": "https://www.sparkfun.com",
        "search_url": "https://www.sparkfun.com/search/results?term={query}",
        "dimension_selectors": [
            "table.table-striped td",
            "div.description li",
        ],
    },
    "arduino": {
        "base_url": "https://store.arduino.cc",
        "search_url": "https://store.arduino.cc/search?q={query}",
        "dimension_selectors": [
            "table.specs td",
            "ul.specs li",
        ],
    },
    "raspberrypi": {
        "base_url": "https://www.raspberrypi.com",
        "search_url": "https://www.raspberrypi.com/search/?q={query}",
        "dimension_selectors": [
            "table td",
            "ul.specs li",
        ],
    },
}

# ---------------------------------------------------------------------------
# GPIO header / installed component catalogue
# All heights are from PCB surface (not pin tip).
# above_mm = height of plastic housing above top PCB surface
# below_mm = pin length protruding below bottom PCB surface (soldering clearance)
# ---------------------------------------------------------------------------
GPIO_HEADER_VARIANTS = {
    "standard_11mm": {
        "label":        "Standard 11mm (RPi HAT default)",
        "above_mm":     8.5,    # plastic housing above PCB
        "below_mm":     3.0,    # pins below PCB
        "mated_gap_mm": 11.5,   # board-to-board gap when HAT is mated
        "note":         "Standard 2×20 female header shipped with most HATs",
    },
    "tall_15mm": {
        "label":        "Tall 15mm stacking header",
        "above_mm":     12.0,
        "below_mm":     3.0,
        "mated_gap_mm": 15.0,
        "note":         "Used when extra clearance needed between host board and HAT",
    },
    "low_profile_5mm": {
        "label":        "Low-profile 5mm header",
        "above_mm":     2.5,
        "below_mm":     3.0,
        "mated_gap_mm": 5.5,
        "note":         "Compact build; reduces total shell depth significantly",
    },
    "low_profile_8mm": {
        "label":        "Low-profile 8mm header",
        "above_mm":     5.5,
        "below_mm":     3.0,
        "mated_gap_mm": 8.5,
        "note":         "Common aftermarket compact header",
    },
    "stacking_20mm": {
        "label":        "Stacking 20mm header",
        "above_mm":     17.0,
        "below_mm":     3.0,
        "mated_gap_mm": 20.0,
        "note":         "Used when stacking multiple HATs; not typical for handheld",
    },
    "soldered_direct": {
        "label":        "Soldered direct (no header)",
        "above_mm":     0.0,
        "below_mm":     0.0,
        "mated_gap_mm": 1.0,    # only solder joint gap
        "note":         "HAT soldered flat to host board — minimal height, non-removable",
    },
    "gpio_ribbon": {
        "label":        "GPIO ribbon cable + 11mm standoffs",
        "above_mm":     2.5,    # only IDC connector height on HAT side
        "below_mm":     0.0,
        "mated_gap_mm": 11.5,   # standoff-controlled gap
        "note":         "Host board connected via flat ribbon; HAT mounted on standoffs",
    },
    "pogo_pin": {
        "label":        "Pogo-pin connector",
        "above_mm":     3.0,
        "below_mm":     0.5,
        "mated_gap_mm": 4.0,
        "note":         "Spring-loaded pins; very low profile; requires alignment jig in shell",
    },
}

# Through-hole / SMD components that add clearance above PCB surface.
# These are measured as height above the TOP surface of the PCB.
# Used to compute the minimum internal cavity depth needed.
COMMON_COMPONENT_HEIGHTS = {
    "usb_a":          {"h_mm": 14.5, "label": "USB-A connector (vertical)"},
    "usb_micro":      {"h_mm":  3.5, "label": "Micro USB connector"},
    "usb_c":          {"h_mm":  3.2, "label": "USB-C connector"},
    "usb_mini":       {"h_mm":  3.5, "label": "Mini USB connector"},
    "hdmi_full":      {"h_mm": 14.5, "label": "Full HDMI connector"},
    "hdmi_micro":     {"h_mm":  4.5, "label": "Micro HDMI connector"},
    "hdmi_mini":      {"h_mm":  5.0, "label": "Mini HDMI connector"},
    "sd_card":        {"h_mm":  2.5, "label": "MicroSD card slot"},
    "audio_jack_3.5": {"h_mm":  5.5, "label": "3.5mm audio jack"},
    "electrolytic_cap_5mm": {"h_mm": 7.0,  "label": "5mm dia electrolytic cap"},
    "electrolytic_cap_8mm": {"h_mm": 12.0, "label": "8mm dia electrolytic cap"},
    "crystal_hc49":   {"h_mm":  5.0, "label": "HC-49 crystal"},
    "dip_ic_8pin":    {"h_mm":  7.0, "label": "DIP-8 IC socket"},
    "button_tact":    {"h_mm":  5.0, "label": "Tactile button (5mm)"},
    "button_tact_low":{"h_mm":  2.5, "label": "Tactile button low-profile"},
    "lcd_ribbon_conn":{"h_mm":  3.0, "label": "LCD ribbon ZIF connector"},
    "rpi_camera_conn":{"h_mm":  2.5, "label": "RPi camera FFC connector"},
    "barrel_jack":    {"h_mm":  11.0,"label": "DC barrel jack"},
    "ethernet":       {"h_mm": 14.5, "label": "RJ45 Ethernet"},
}

# ---------------------------------------------------------------------------
# Dimension / spec extraction regex patterns
# ---------------------------------------------------------------------------
DIM_PATTERNS = {
    "board_size":     re.compile(r"(?:board\s*size|pcb\s*size|dimensions?)[:\s]*"
                                 r"([\d.]+)\s*[×xX*]\s*([\d.]+)(?:\s*[×xX*]\s*([\d.]+))?\s*mm",
                                 re.IGNORECASE),
    "display_size":   re.compile(r"(?:display|screen|lcd)\s*size[:\s]*([\d.]+)\s*(?:inch|\")",
                                 re.IGNORECASE),
    "display_res":    re.compile(r"(?:resolution|res)[:\s]*([\d]+)\s*[×xX]\s*([\d]+)",
                                 re.IGNORECASE),
    "display_mm":     re.compile(r"(?:active\s*area|viewing\s*area)[:\s]*([\d.]+)\s*[×xX]\s*([\d.]+)\s*mm",
                                 re.IGNORECASE),
    "mounting_holes": re.compile(r"mounting\s*holes?[:\s]*([\d.]+)\s*mm",
                                 re.IGNORECASE),
    "weight":         re.compile(r"weight[:\s]*([\d.]+)\s*g",
                                 re.IGNORECASE),
    "voltage":        re.compile(r"(?:operating|input|supply)\s*voltage[:\s]*([\d.]+)\s*[-–to]?\s*([\d.]+)?\s*[Vv]",
                                 re.IGNORECASE),
    "interface":      re.compile(r"(?:interface|communication)[:\s]*([A-Za-z0-9,/ ]+)",
                                 re.IGNORECASE),
    # Header / component height patterns
    "header_height":  re.compile(
        r"(?:gpio\s*header|pin\s*header|header)[:\s]*.*?(\d+(?:\.\d+)?)\s*mm",
        re.IGNORECASE),
    "standoff_height":re.compile(
        r"(?:standoff|spacer)[:\s]*.*?(\d+(?:\.\d+)?)\s*mm",
        re.IGNORECASE),
    "component_height":re.compile(
        r"(?:component\s*height|tallest\s*component|max\s*height)[:\s]*([\d.]+)\s*mm",
        re.IGNORECASE),
    "header_variant": re.compile(
        r"(low[\s-]?profile|tall|stacking|standard)\s*(?:gpio\s*)?(?:pin\s*)?header",
        re.IGNORECASE),
}

# Waveshare LCD HAT specific known dimensions (hardcoded fallback + math basis)
# gpio_header_* fields reflect the standard 11mm female header shipped with each HAT.
# Override gpio_header_variant in the UI or spec JSON to use a different header.
WAVESHARE_LCD_HATS = {
    "1.3inch_lcd_hat": {
        "model": "Waveshare 1.3inch LCD HAT",
        "board_width_mm": 65.0,
        "board_height_mm": 30.1,
        "board_thickness_mm": 1.6,
        "display_diagonal_inch": 1.3,
        "display_width_mm": 23.4,
        "display_height_mm": 23.4,
        "resolution_w": 240,
        "resolution_h": 240,
        "pixel_pitch_mm": 0.0975,       # 23.4mm / 240px
        "ppi": 260.6,
        "gpio_header_pitch_mm": 2.54,
        "gpio_rows": 2,
        "gpio_cols": 20,                # 40-pin 2×20
        # Installed header — standard 11mm female header (HAT side)
        "gpio_header_variant":    "standard_11mm",
        "gpio_header_above_mm":   8.5,   # housing protrudes 8.5mm above HAT PCB
        "gpio_header_below_mm":   3.0,   # pins protrude 3mm below HAT PCB
        "gpio_header_mated_gap_mm": 11.5, # RPi PCB → HAT PCB gap when mated
        # Tallest other component on the HAT (joystick nub above board surface)
        "tallest_component_mm":   8.0,
        "tallest_component_label":"5-way joystick nub",
        # Components needing edge clearance
        "installed_components": [
            {"key": "button_tact", "h_mm": 5.0, "label": "KEY1", "edge": "right", "pos_mm": 8.5},
            {"key": "button_tact", "h_mm": 5.0, "label": "KEY2", "edge": "right", "pos_mm": 15.0},
            {"key": "button_tact", "h_mm": 5.0, "label": "KEY3", "edge": "right", "pos_mm": 21.5},
        ],
        "mounting_hole_diameter_mm": 2.9,
        "mounting_hole_positions_mm": [  # (x, y) from bottom-left corner
            (3.5, 3.5),
            (61.5, 3.5),
            (3.5, 26.6),
            (61.5, 26.6),
        ],
        "joystick_center_mm": (40.0, 15.0),
        "joystick_diameter_mm": 12.0,
        "button_positions_mm": [        # (x, y, label)
            (55.0, 8.5,  "KEY1"),
            (55.0, 15.0, "KEY2"),
            (55.0, 21.5, "KEY3"),
        ],
        "interface": "SPI",
        "controller_ic": "ST7789",
        "voltage_v": 3.3,
        "compatible_boards": ["Raspberry Pi Zero", "RPi 3", "RPi 4"],
    },
    "2inch_lcd_hat": {
        "model": "Waveshare 2inch LCD HAT",
        "board_width_mm": 65.0,
        "board_height_mm": 30.1,
        "board_thickness_mm": 1.6,
        "display_diagonal_inch": 2.0,
        "display_width_mm": 30.6,
        "display_height_mm": 40.8,
        "resolution_w": 240,
        "resolution_h": 320,
        "pixel_pitch_mm": 0.1275,
        "ppi": 199.3,
        "gpio_header_pitch_mm": 2.54,
        "gpio_rows": 2,
        "gpio_cols": 20,
        "gpio_header_variant":    "standard_11mm",
        "gpio_header_above_mm":   8.5,
        "gpio_header_below_mm":   3.0,
        "gpio_header_mated_gap_mm": 11.5,
        "tallest_component_mm":   5.0,
        "tallest_component_label":"tactile button",
        "installed_components": [],
        "mounting_hole_diameter_mm": 2.9,
        "mounting_hole_positions_mm": [
            (3.5, 3.5),
            (61.5, 3.5),
            (3.5, 26.6),
            (61.5, 26.6),
        ],
        "interface": "SPI",
        "controller_ic": "ST7789",
        "voltage_v": 3.3,
        "compatible_boards": ["Raspberry Pi Zero", "RPi 3", "RPi 4"],
    },
    "240x240_1.3_hat": {
        "model": "Waveshare 240x240 1.3inch HAT",
        "board_width_mm": 65.0,
        "board_height_mm": 30.2,
        "board_thickness_mm": 1.6,
        "display_diagonal_inch": 1.3,
        "display_width_mm": 23.4,
        "display_height_mm": 23.4,
        "resolution_w": 240,
        "resolution_h": 240,
        "pixel_pitch_mm": round(23.4 / 240, 6),
        "ppi": round((240 / 23.4) * 25.4, 2),
        "interface": "SPI",
        "controller_ic": "ST7789",
        "voltage_v": 3.3,
        "gpio_header_pitch_mm": 2.54,
        "gpio_header_variant":    "standard_11mm",
        "gpio_header_above_mm":   8.5,
        "gpio_header_below_mm":   3.0,
        "gpio_header_mated_gap_mm": 11.5,
        "tallest_component_mm":   8.0,
        "tallest_component_label":"5-way joystick nub",
        "installed_components": [],
        "mounting_hole_diameter_mm": 2.9,
        "mounting_hole_positions_mm": [
            (3.5, 3.5), (61.5, 3.5), (3.5, 26.7), (61.5, 26.7),
        ],
        "compatible_boards": ["Raspberry Pi Zero", "RPi 3", "RPi 4", "RPi 5"],
    },
}


@dataclass
class DeviceSpec:
    """Normalised device specification extracted from crawling."""
    model: str = ""
    vendor: str = ""
    source_url: str = ""

    # PCB / board physical
    board_width_mm: float = 0.0
    board_height_mm: float = 0.0
    board_thickness_mm: float = 1.6

    # Display (if applicable)
    display_diagonal_inch: float = 0.0
    display_width_mm: float = 0.0
    display_height_mm: float = 0.0
    resolution_w: int = 0
    resolution_h: int = 0
    pixel_pitch_mm: float = 0.0
    ppi: float = 0.0

    # Mounting
    mounting_hole_diameter_mm: float = 2.9
    mounting_hole_positions_mm: list = field(default_factory=list)

    # Electrical
    voltage_v: float = 3.3
    interface: str = ""
    controller_ic: str = ""

    # GPIO
    gpio_header_pitch_mm: float = 2.54
    gpio_rows: int = 0
    gpio_cols: int = 0

    # Installed header — the physical header variant actually fitted to the board.
    # This drives shell depth calculation in dimension_math.py.
    gpio_header_variant: str = "standard_11mm"   # key into GPIO_HEADER_VARIANTS
    gpio_header_above_mm: float = 8.5            # housing height above PCB top surface
    gpio_header_below_mm: float = 3.0            # pin length below PCB bottom surface
    gpio_header_mated_gap_mm: float = 11.5       # board-to-board gap when HAT mated

    # Tallest component above PCB top surface (excluding GPIO header).
    # Used to ensure shell cavity is deep enough.
    tallest_component_mm: float = 0.0
    tallest_component_label: str = ""

    # List of installed components that need clearance / edge cutouts.
    # Each entry: {"key": str, "h_mm": float, "label": str, "edge": str, "pos_mm": float}
    installed_components: list = field(default_factory=list)

    # UI elements
    button_positions_mm: list = field(default_factory=list)
    joystick_center_mm: tuple = field(default_factory=tuple)
    joystick_diameter_mm: float = 0.0

    # Compatibility
    compatible_boards: list = field(default_factory=list)

    # Raw scraped text for audit
    raw_specs: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class DeviceCrawler:
    """
    Crawls vendor pages or uses local known-device database to retrieve
    full DeviceSpec objects for 3D shell generation.
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    REQUEST_DELAY = 1.5  # seconds between requests (polite crawling)

    def __init__(self, cache_dir: str = "data/devices"):
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_device(self, query: str, vendor: str = "waveshare") -> DeviceSpec:
        """
        Main entry. Tries local known-device DB first, then live crawl.
        Returns a fully populated DeviceSpec.
        """
        # 1. Check Waveshare HAT hardcoded DB
        slug = query.lower().replace(" ", "_")
        if vendor == "waveshare":
            for key, data in WAVESHARE_LCD_HATS.items():
                if key in slug or slug in key or query.lower() in data["model"].lower():
                    logger.info(f"[DB hit] {data['model']}")
                    spec = DeviceSpec(**{k: v for k, v in data.items()
                                        if k in DeviceSpec.__dataclass_fields__})
                    spec.vendor = "waveshare"
                    return spec

        # 2. Try cached JSON
        cached = self._load_cache(query, vendor)
        if cached:
            return cached

        # 3. Live crawl
        spec = self._crawl(query, vendor)
        self._save_cache(query, vendor, spec)
        return spec

    def list_known_devices(self) -> list:
        """Returns list of all hardcoded Waveshare HAT models."""
        return [v["model"] for v in WAVESHARE_LCD_HATS.values()]

    # ------------------------------------------------------------------
    # Internal crawling
    # ------------------------------------------------------------------

    def _crawl(self, query: str, vendor: str) -> DeviceSpec:
        cfg = VENDOR_PATTERNS.get(vendor, VENDOR_PATTERNS["waveshare"])
        search_url = cfg["search_url"].format(query=requests.utils.quote(query))

        logger.info(f"[Crawl] {search_url}")
        try:
            resp = self.session.get(search_url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Search failed: {e}")
            return DeviceSpec(model=query, vendor=vendor)

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find first product link
        product_url = self._find_product_link(soup, cfg["base_url"], query)
        if not product_url:
            logger.warning("No product page found.")
            return DeviceSpec(model=query, vendor=vendor)

        time.sleep(self.REQUEST_DELAY)
        try:
            presp = self.session.get(product_url, timeout=15)
            presp.raise_for_status()
        except Exception as e:
            logger.warning(f"Product page fetch failed: {e}")
            return DeviceSpec(model=query, vendor=vendor)

        psoup = BeautifulSoup(presp.text, "html.parser")
        raw_text = self._extract_spec_text(psoup, cfg["dimension_selectors"])
        spec = self._parse_dimensions(raw_text)
        spec.model = query
        spec.vendor = vendor
        spec.source_url = product_url
        return spec

    def _find_product_link(self, soup: BeautifulSoup, base_url: str, query: str) -> Optional[str]:
        """Heuristically find the most relevant product link on a search results page."""
        query_words = query.lower().split()
        best_link = None
        best_score = 0

        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            score = sum(1 for w in query_words if w in text or w in href.lower())
            if score > best_score:
                best_score = score
                best_link = href

        if best_link:
            if best_link.startswith("http"):
                return best_link
            return urljoin(base_url, best_link)
        return None

    def _extract_spec_text(self, soup: BeautifulSoup, selectors: list) -> str:
        """Collect all spec text from known selector patterns."""
        parts = []
        for sel in selectors:
            tag, *cls = sel.split(".")
            for el in soup.select(sel):
                parts.append(el.get_text(separator=" ", strip=True))
        # Also grab full page text as fallback
        parts.append(soup.get_text(separator="\n", strip=True))
        return "\n".join(parts)

    def _parse_dimensions(self, text: str) -> DeviceSpec:
        """Apply all regex patterns to raw text and build a DeviceSpec."""
        spec = DeviceSpec()

        m = DIM_PATTERNS["board_size"].search(text)
        if m:
            spec.board_width_mm = float(m.group(1))
            spec.board_height_mm = float(m.group(2))
            if m.group(3):
                spec.board_thickness_mm = float(m.group(3))

        m = DIM_PATTERNS["display_size"].search(text)
        if m:
            spec.display_diagonal_inch = float(m.group(1))

        m = DIM_PATTERNS["display_res"].search(text)
        if m:
            spec.resolution_w = int(m.group(1))
            spec.resolution_h = int(m.group(2))

        m = DIM_PATTERNS["display_mm"].search(text)
        if m:
            spec.display_width_mm = float(m.group(1))
            spec.display_height_mm = float(m.group(2))

        # Compute pixel pitch and PPI when we have resolution + mm size
        if spec.display_width_mm and spec.resolution_w:
            spec.pixel_pitch_mm = round(spec.display_width_mm / spec.resolution_w, 6)
        if spec.display_diagonal_inch and spec.resolution_w and spec.resolution_h:
            import math
            diag_px = math.sqrt(spec.resolution_w**2 + spec.resolution_h**2)
            spec.ppi = round(diag_px / spec.display_diagonal_inch, 2)

        m = DIM_PATTERNS["voltage"].search(text)
        if m:
            spec.voltage_v = float(m.group(1))

        m = DIM_PATTERNS["interface"].search(text)
        if m:
            spec.interface = m.group(1).strip()

        # --- Installed header detection ---
        # 1. Try to identify header variant from descriptive text
        m = DIM_PATTERNS["header_variant"].search(text)
        if m:
            keyword = m.group(1).lower().replace("-", "").replace(" ", "")
            if "lowprofile" in keyword or "low" in keyword:
                # Pick 8mm as default low-profile guess
                variant_key = "low_profile_8mm"
            elif "tall" in keyword or "stacking" in keyword:
                variant_key = "tall_15mm"
            else:
                variant_key = "standard_11mm"
            hv = GPIO_HEADER_VARIANTS[variant_key]
            spec.gpio_header_variant    = variant_key
            spec.gpio_header_above_mm   = hv["above_mm"]
            spec.gpio_header_below_mm   = hv["below_mm"]
            spec.gpio_header_mated_gap_mm = hv["mated_gap_mm"]

        # 2. Try to extract explicit header height in mm
        m = DIM_PATTERNS["header_height"].search(text)
        if m:
            h = float(m.group(1))
            # Find the closest matching variant by mated_gap_mm
            best_key = min(
                GPIO_HEADER_VARIANTS,
                key=lambda k: abs(GPIO_HEADER_VARIANTS[k]["mated_gap_mm"] - h)
            )
            hv = GPIO_HEADER_VARIANTS[best_key]
            spec.gpio_header_variant      = best_key
            spec.gpio_header_above_mm     = hv["above_mm"]
            spec.gpio_header_below_mm     = hv["below_mm"]
            spec.gpio_header_mated_gap_mm = hv["mated_gap_mm"]

        # 3. Explicit tallest-component height
        m = DIM_PATTERNS["component_height"].search(text)
        if m:
            spec.tallest_component_mm   = float(m.group(1))
            spec.tallest_component_label = "scraped from spec page"

        spec.raw_specs = {
            "scraped_text_length": len(text),
            "header_variant_detected": spec.gpio_header_variant,
            "header_mated_gap_mm": spec.gpio_header_mated_gap_mm,
        }
        return spec

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_path(self, query: str, vendor: str) -> str:
        import os
        slug = re.sub(r"[^\w]", "_", query.lower())
        os.makedirs(self.cache_dir, exist_ok=True)
        return f"{self.cache_dir}/{vendor}_{slug}.json"

    def _load_cache(self, query: str, vendor: str) -> Optional[DeviceSpec]:
        import os
        path = self._cache_path(query, vendor)
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            logger.info(f"[Cache] loaded {path}")
            return DeviceSpec(**{k: v for k, v in data.items()
                                 if k in DeviceSpec.__dataclass_fields__})
        return None

    def _save_cache(self, query: str, vendor: str, spec: DeviceSpec):
        path = self._cache_path(query, vendor)
        with open(path, "w") as f:
            json.dump(spec.to_dict(), f, indent=2)
        logger.info(f"[Cache] saved {path}")
