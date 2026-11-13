"""
device_crawler.py
-----------------
Web crawler that scrapes handheld device / microcontroller / PCB specs
from known sources (Waveshare, Adafruit, SparkFun, Arduino, RPi Foundation).
Returns a normalised DeviceSpec dict ready for the dimension parser.

Search strategy (in priority order):
  1. Token-fuzzy match against WAVESHARE_LCD_HATS (handles typos, partial names,
     numbers-only queries like "1.3", "240x240", "2inch", etc.)
  2. Alias table lookup (handles common shorthand like "ws1.3", "hat13", etc.)
  3. Local JSON cache
  4. Live web crawl with HTML scraping + regex extraction
  5. Zero-dimension fallback with vendor + model name preserved
"""

import re
import time
import json
import math
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin

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
# ---------------------------------------------------------------------------
GPIO_HEADER_VARIANTS = {
    "standard_11mm": {
        "label":        "Standard 11mm (RPi HAT default)",
        "above_mm":     8.5,
        "below_mm":     3.0,
        "mated_gap_mm": 11.5,
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
        "mated_gap_mm": 1.0,
        "note":         "HAT soldered flat to host board — minimal height, non-removable",
    },
    "gpio_ribbon": {
        "label":        "GPIO ribbon cable + 11mm standoffs",
        "above_mm":     2.5,
        "below_mm":     0.0,
        "mated_gap_mm": 11.5,
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
    "mounting_holes": re.compile(r"mounting\s*holes?[:\s]*([\d.]+)\s*mm", re.IGNORECASE),
    "weight":         re.compile(r"weight[:\s]*([\d.]+)\s*g", re.IGNORECASE),
    "voltage":        re.compile(r"(?:operating|input|supply)\s*voltage[:\s]*([\d.]+)\s*[-–to]?\s*([\d.]+)?\s*[Vv]",
                                 re.IGNORECASE),
    "interface":      re.compile(r"(?:interface|communication)[:\s]*([A-Za-z0-9,/ ]+)", re.IGNORECASE),
    "header_height":  re.compile(r"(?:gpio\s*header|pin\s*header|header)[:\s]*.*?(\d+(?:\.\d+)?)\s*mm",
                                 re.IGNORECASE),
    "standoff_height":re.compile(r"(?:standoff|spacer)[:\s]*.*?(\d+(?:\.\d+)?)\s*mm", re.IGNORECASE),
    "component_height":re.compile(r"(?:component\s*height|tallest\s*component|max\s*height)[:\s]*([\d.]+)\s*mm",
                                  re.IGNORECASE),
    "header_variant": re.compile(r"(low[\s-]?profile|tall|stacking|standard)\s*(?:gpio\s*)?(?:pin\s*)?header",
                                 re.IGNORECASE),
}

# ---------------------------------------------------------------------------
# Waveshare LCD HAT hardcoded specs
# ---------------------------------------------------------------------------
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
        "pixel_pitch_mm": 0.0975,
        "ppi": 260.6,
        "gpio_header_pitch_mm": 2.54,
        "gpio_rows": 2,
        "gpio_cols": 20,
        "gpio_header_variant":    "standard_11mm",
        "gpio_header_above_mm":   8.5,
        "gpio_header_below_mm":   3.0,
        "gpio_header_mated_gap_mm": 11.5,
        "tallest_component_mm":   8.0,
        "tallest_component_label":"5-way joystick nub",
        "installed_components": [
            {"key": "button_tact", "h_mm": 5.0, "label": "KEY1", "edge": "right", "pos_mm": 8.5},
            {"key": "button_tact", "h_mm": 5.0, "label": "KEY2", "edge": "right", "pos_mm": 15.0},
            {"key": "button_tact", "h_mm": 5.0, "label": "KEY3", "edge": "right", "pos_mm": 21.5},
        ],
        "mounting_hole_diameter_mm": 2.9,
        "mounting_hole_positions_mm": [(3.5,3.5),(61.5,3.5),(3.5,26.6),(61.5,26.6)],
        "joystick_center_mm": (40.0, 15.0),
        "joystick_diameter_mm": 12.0,
        "button_positions_mm": [(55.0,8.5,"KEY1"),(55.0,15.0,"KEY2"),(55.0,21.5,"KEY3")],
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
        "mounting_hole_positions_mm": [(3.5,3.5),(61.5,3.5),(3.5,26.6),(61.5,26.6)],
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
        "mounting_hole_positions_mm": [(3.5,3.5),(61.5,3.5),(3.5,26.7),(61.5,26.7)],
        "compatible_boards": ["Raspberry Pi Zero", "RPi 3", "RPi 4", "RPi 5"],
    },
}

# ---------------------------------------------------------------------------
# Search alias table — maps common shorthand / typos → canonical HAT key
# Any number of aliases can point to the same key.
# ---------------------------------------------------------------------------
_HAT_ALIASES: Dict[str, str] = {
    # 1.3-inch
    "1.3":          "1.3inch_lcd_hat",
    "1.3inch":      "1.3inch_lcd_hat",
    "1.3 inch":     "1.3inch_lcd_hat",
    "1.3\"":        "1.3inch_lcd_hat",
    "ws1.3":        "1.3inch_lcd_hat",
    "hat13":        "1.3inch_lcd_hat",
    "lcd13":        "1.3inch_lcd_hat",
    "st7789":       "1.3inch_lcd_hat",
    "240x240":      "1.3inch_lcd_hat",
    "240 240":      "1.3inch_lcd_hat",
    # 2-inch
    "2inch":        "2inch_lcd_hat",
    "2.0":          "2inch_lcd_hat",
    "2.0inch":      "2inch_lcd_hat",
    "2 inch":       "2inch_lcd_hat",
    "2\"":          "2inch_lcd_hat",
    "ws2":          "2inch_lcd_hat",
    "240x320":      "2inch_lcd_hat",
    "240 320":      "2inch_lcd_hat",
}

# Tokens that appear in every model name and add no search signal
_STOP_WORDS = {"waveshare", "lcd", "hat", "inch", "display", "screen", "rpi", "raspberry", "pi", "board"}


def _tokenise(text: str) -> List[str]:
    """Lowercase, strip punctuation, split on whitespace and separators."""
    text = text.lower()
    text = re.sub(r"[_\-×xX*,]", " ", text)
    text = re.sub(r"[^a-z0-9. ]", "", text)
    return [t for t in text.split() if t]


def _search_score(query_tokens: List[str], candidate_key: str, candidate_data: dict) -> int:
    """
    Score a candidate HAT against query tokens.
    Higher = better match.
    """
    candidate_tokens = _tokenise(candidate_key) + _tokenise(candidate_data["model"])
    score = 0
    for qt in query_tokens:
        for ct in candidate_tokens:
            if qt == ct:
                score += 10         # exact token match
            elif qt in ct or ct in qt:
                score += 5          # substring match
            elif qt.rstrip("s") == ct.rstrip("s"):
                score += 3          # plural/singular
            # numeric proximity: "1.3" vs "1.3inch" — both contain "1.3"
            if re.search(r'\d', qt) and qt in ct:
                score += 8
    return score


def _fuzzy_lookup(query: str) -> Optional[str]:
    """
    Return the best-matching WAVESHARE_LCD_HATS key for a query string,
    or None if the best score is too low to be confident.
    """
    q_clean = query.strip().lower()

    # 1. Direct alias table lookup (fastest path)
    if q_clean in _HAT_ALIASES:
        return _HAT_ALIASES[q_clean]
    # Partial alias match
    for alias, key in _HAT_ALIASES.items():
        if alias in q_clean or q_clean in alias:
            return key

    # 2. Direct key match (after normalisation)
    q_slug = re.sub(r"[^a-z0-9.]", "_", q_clean)
    for key in WAVESHARE_LCD_HATS:
        if key == q_slug or key in q_slug or q_slug in key:
            return key

    # 3. Model name substring match
    for key, data in WAVESHARE_LCD_HATS.items():
        if q_clean in data["model"].lower() or data["model"].lower() in q_clean:
            return key

    # 4. Token scoring
    q_tokens = [t for t in _tokenise(q_clean) if t not in _STOP_WORDS]
    if not q_tokens:
        # All stop words — fall back to first entry
        return list(WAVESHARE_LCD_HATS.keys())[0]

    scores = {
        key: _search_score(q_tokens, key, data)
        for key, data in WAVESHARE_LCD_HATS.items()
    }
    best_key = max(scores, key=lambda k: scores[k])
    best_score = scores[best_key]

    # Require a minimum confidence score of 3 to avoid spurious matches
    return best_key if best_score >= 3 else None


# ---------------------------------------------------------------------------
# DeviceSpec dataclass
# ---------------------------------------------------------------------------

@dataclass
class DeviceSpec:
    """Normalised device specification extracted from crawling."""
    model: str = ""
    vendor: str = ""
    source_url: str = ""

    board_width_mm: float = 0.0
    board_height_mm: float = 0.0
    board_thickness_mm: float = 1.6

    display_diagonal_inch: float = 0.0
    display_width_mm: float = 0.0
    display_height_mm: float = 0.0
    resolution_w: int = 0
    resolution_h: int = 0
    pixel_pitch_mm: float = 0.0
    ppi: float = 0.0

    mounting_hole_diameter_mm: float = 2.9
    mounting_hole_positions_mm: list = field(default_factory=list)

    voltage_v: float = 3.3
    interface: str = ""
    controller_ic: str = ""

    gpio_header_pitch_mm: float = 2.54
    gpio_rows: int = 0
    gpio_cols: int = 0

    gpio_header_variant: str = "standard_11mm"
    gpio_header_above_mm: float = 8.5
    gpio_header_below_mm: float = 3.0
    gpio_header_mated_gap_mm: float = 11.5

    tallest_component_mm: float = 0.0
    tallest_component_label: str = ""

    installed_components: list = field(default_factory=list)

    button_positions_mm: list = field(default_factory=list)
    joystick_center_mm: tuple = field(default_factory=tuple)
    joystick_diameter_mm: float = 0.0

    compatible_boards: list = field(default_factory=list)

    raw_specs: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# DeviceCrawler
# ---------------------------------------------------------------------------

class DeviceCrawler:

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    REQUEST_DELAY = 1.5

    def __init__(self, cache_dir: str = "data/devices"):
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_device(self, query: str, vendor: str = "waveshare") -> DeviceSpec:
        """
        Main entry point. Resolution order:
          1. Waveshare HAT fuzzy lookup (handles typos, partial names, numbers)
          2. Local JSON cache
          3. Live web crawl
          4. Graceful zero-spec fallback (never crashes geometry engine)
        """
        # ── 1. Waveshare DB fuzzy lookup ──────────────────────────────
        if vendor in ("waveshare", ""):
            matched_key = _fuzzy_lookup(query)
            if matched_key:
                data = WAVESHARE_LCD_HATS[matched_key]
                logger.info(f"[DB hit] '{query}' → {matched_key} ({data['model']})")
                spec = DeviceSpec(**{
                    k: v for k, v in data.items()
                    if k in DeviceSpec.__dataclass_fields__
                })
                spec.vendor = "waveshare"
                spec.raw_specs = {
                    "source": "hardcoded_db",
                    "matched_key": matched_key,
                    "query": query,
                }
                return spec

        # ── 2. Try JSON cache ─────────────────────────────────────────
        cached = self._load_cache(query, vendor)
        if cached:
            return cached

        # ── 3. Live crawl ─────────────────────────────────────────────
        try:
            spec = self._crawl(query, vendor)
            if spec.board_width_mm > 0:          # only cache successful scrapes
                self._save_cache(query, vendor, spec)
            return spec
        except Exception as e:
            logger.warning(f"[Crawl] failed for '{query}': {e}")

        # ── 4. Graceful fallback — return a minimal valid spec ────────
        logger.warning(f"[Fallback] returning empty spec for '{query}'")
        return DeviceSpec(
            model=query or "Unknown Device",
            vendor=vendor,
            board_width_mm=65.0,
            board_height_mm=30.0,
            board_thickness_mm=1.6,
            gpio_header_variant="standard_11mm",
            gpio_header_above_mm=8.5,
            gpio_header_below_mm=3.0,
            gpio_header_mated_gap_mm=11.5,
            raw_specs={"source": "fallback", "query": query},
        )

    def list_known_devices(self) -> list:
        return [v["model"] for v in WAVESHARE_LCD_HATS.values()]

    def search_known_devices(self, query: str) -> List[Tuple[str, str, int]]:
        """
        Return (key, model_name, score) tuples for all known HATs, sorted by score descending.
        Useful for the UI to show ranked suggestions.
        """
        q_tokens = [t for t in _tokenise(query) if t not in _STOP_WORDS] or _tokenise(query)
        results = []
        for key, data in WAVESHARE_LCD_HATS.items():
            score = _search_score(q_tokens, key, data)
            results.append((key, data["model"], score))
        return sorted(results, key=lambda x: x[2], reverse=True)

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
        parts = []
        for sel in selectors:
            for el in soup.select(sel):
                parts.append(el.get_text(separator=" ", strip=True))
        parts.append(soup.get_text(separator="\n", strip=True))
        return "\n".join(parts)

    def _parse_dimensions(self, text: str) -> DeviceSpec:
        spec = DeviceSpec()

        m = DIM_PATTERNS["board_size"].search(text)
        if m:
            spec.board_width_mm  = float(m.group(1))
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
            spec.display_width_mm  = float(m.group(1))
            spec.display_height_mm = float(m.group(2))

        if spec.display_width_mm and spec.resolution_w:
            spec.pixel_pitch_mm = round(spec.display_width_mm / spec.resolution_w, 6)
        if spec.display_diagonal_inch and spec.resolution_w and spec.resolution_h:
            diag_px = math.sqrt(spec.resolution_w**2 + spec.resolution_h**2)
            spec.ppi = round(diag_px / spec.display_diagonal_inch, 2)

        m = DIM_PATTERNS["voltage"].search(text)
        if m:
            spec.voltage_v = float(m.group(1))

        m = DIM_PATTERNS["interface"].search(text)
        if m:
            spec.interface = m.group(1).strip()

        m = DIM_PATTERNS["header_variant"].search(text)
        if m:
            keyword = m.group(1).lower().replace("-", "").replace(" ", "")
            if "lowprofile" in keyword or "low" in keyword:
                variant_key = "low_profile_8mm"
            elif "tall" in keyword or "stacking" in keyword:
                variant_key = "tall_15mm"
            else:
                variant_key = "standard_11mm"
            hv = GPIO_HEADER_VARIANTS[variant_key]
            spec.gpio_header_variant      = variant_key
            spec.gpio_header_above_mm     = hv["above_mm"]
            spec.gpio_header_below_mm     = hv["below_mm"]
            spec.gpio_header_mated_gap_mm = hv["mated_gap_mm"]

        m = DIM_PATTERNS["header_height"].search(text)
        if m:
            h = float(m.group(1))
            best_key = min(GPIO_HEADER_VARIANTS,
                           key=lambda k: abs(GPIO_HEADER_VARIANTS[k]["mated_gap_mm"] - h))
            hv = GPIO_HEADER_VARIANTS[best_key]
            spec.gpio_header_variant      = best_key
            spec.gpio_header_above_mm     = hv["above_mm"]
            spec.gpio_header_below_mm     = hv["below_mm"]
            spec.gpio_header_mated_gap_mm = hv["mated_gap_mm"]

        m = DIM_PATTERNS["component_height"].search(text)
        if m:
            spec.tallest_component_mm    = float(m.group(1))
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
