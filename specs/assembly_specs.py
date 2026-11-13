"""
assembly_specs.py
-----------------
Generates a full human-readable assembly specification document
from a HandheldBuild + ShellGeometry.

Output formats:
  - JSON  (.json)
  - Markdown (.md)
  - Plain text BOM (.txt)
"""

import json
import math
import os
from dataclasses import asdict
from typing import List


# ---------------------------------------------------------------------------
# Filament / material catalogue
# ---------------------------------------------------------------------------

MATERIALS = {
    "PLA": {
        "density_g_cm3": 1.24,
        "cost_per_kg_usd": 22.0,
        "print_temp_c": 210,
        "bed_temp_c": 60,
        "notes": "Easy to print. Good surface finish. Not flexible.",
    },
    "PETG": {
        "density_g_cm3": 1.27,
        "cost_per_kg_usd": 24.0,
        "print_temp_c": 235,
        "bed_temp_c": 80,
        "notes": "Tougher than PLA. Slight flex. Good layer adhesion.",
    },
    "ABS": {
        "density_g_cm3": 1.05,
        "cost_per_kg_usd": 20.0,
        "print_temp_c": 240,
        "bed_temp_c": 110,
        "notes": "High strength. Warping risk — use enclosure.",
    },
    "TPU": {
        "density_g_cm3": 1.21,
        "cost_per_kg_usd": 30.0,
        "print_temp_c": 225,
        "bed_temp_c": 50,
        "notes": "Flexible. Good for grip panels and button caps.",
    },
}

# Default hardware BOM per shell
HARDWARE_BOM = {
    "M2.5_screw_6mm": {
        "description": "M2.5 × 6mm Button Head Cap Screw",
        "purpose":     "PCB to standoff fastening",
        "per_boss":    1,
    },
    "M2.5_heatset": {
        "description": "M2.5 × 3.5mm Brass Heat-Set Insert",
        "purpose":     "Threaded inserts in mounting bosses",
        "per_boss":    1,
    },
    "snap_fit_no_screw": {
        "description": "Snap-fit clip (printed integral)",
        "purpose":     "Lid / bottom case retention",
        "count_note":  "Integral — no hardware needed",
    },
}

# Waveshare LCD HAT specific hardware
WAVESHARE_HAT_BOM = {
    "GPIO_header_40pin": {
        "description": "2×20 GPIO stacking header, 11mm height",
        "qty":         1,
        "purpose":     "HAT attachment to Raspberry Pi",
        "note":        "Use tall/stacking headers if HAT needs clearance",
    },
    "standoff_M2.5_11mm": {
        "description": "M2.5 × 11mm Brass Standoff (Male-Female)",
        "qty":         4,
        "purpose":     "RPi to HAT PCB spacing",
    },
    "M2.5_screw_4mm": {
        "description": "M2.5 × 4mm Phillips Head Screw",
        "qty":         4,
        "purpose":     "Secure HAT to standoffs",
    },
}

# Display mounting adhesive
ADHESIVE_OPTIONS = [
    {"name": "3M VHB 5952",   "thickness_mm": 1.1,  "note": "High-bond acrylic tape for display"},
    {"name": "B-7000",         "thickness_mm": 0.5,  "note": "Electronics adhesive, flex bond"},
    {"name": "Press-fit bezel","thickness_mm": 0.0,  "note": "Print display bezel lip — no adhesive"},
]


# ---------------------------------------------------------------------------
# Assembly spec generator
# ---------------------------------------------------------------------------

class AssemblySpecGenerator:

    def __init__(self, geom: dict, build: dict, device_spec: dict):
        self.geom   = geom
        self.build  = build
        self.spec   = device_spec

    # -----------------------------------------------------------------------
    # Public
    # -----------------------------------------------------------------------

    def generate(self) -> dict:
        return {
            "device":          self._device_summary(),
            "shell_geometry":  self._geometry_summary(),
            "display_math":    self._display_math(),
            "waveshare_hat":   self._waveshare_hat_specs(),
            "print_guide":     self._print_guide(),
            "bom_hardware":    self._hardware_bom(),
            "bom_filament":    self._filament_bom(),
            "assembly_steps":  self._assembly_steps(),
            "print_settings":  self._print_settings(),
            "tolerances":      self._tolerances(),
        }

    def to_json(self, output_path: str):
        data = self.generate()
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[Specs] JSON → {output_path}")
        return data

    def to_markdown(self, output_path: str):
        data = self.generate()
        md = self._render_markdown(data)
        with open(output_path, "w") as f:
            f.write(md)
        print(f"[Specs] Markdown → {output_path}")
        return md

    # -----------------------------------------------------------------------
    # Section generators
    # -----------------------------------------------------------------------

    def _device_summary(self) -> dict:
        s = self.spec
        return {
            "model":   s.get("model", "Unknown"),
            "vendor":  s.get("vendor", "Unknown"),
            "source":  s.get("source_url", ""),
            "interface": s.get("interface", ""),
            "controller_ic": s.get("controller_ic", ""),
            "voltage_v": s.get("voltage_v", 3.3),
            "compatible_boards": s.get("compatible_boards", []),
        }

    def _geometry_summary(self) -> dict:
        g = self.geom
        return {
            "shell_w_mm":  g.get("shell_width_mm"),
            "shell_h_mm":  g.get("shell_height_mm"),
            "shell_d_mm":  g.get("shell_depth_mm"),
            "pcb_offset_x": g.get("pcb_offset_x"),
            "pcb_offset_y": g.get("pcb_offset_y"),
            "pcb_z_mm":     g.get("pcb_z_from_floor"),
            "corner_radius_mm": g.get("corner_radius"),
            "mounting_boss_count": len(g.get("mounting_bosses", [])),
            "rib_count":    len(g.get("ribs", [])),
            "snap_fit_count": len(g.get("snap_fit_positions", [])),
        }

    def _display_math(self) -> dict:
        g   = self.geom
        s   = self.spec
        rw  = s.get("resolution_w", 0)
        rh  = s.get("resolution_h", 0)
        dw  = s.get("display_width_mm", 0)
        dh  = s.get("display_height_mm", 0)
        dia = s.get("display_diagonal_inch", 0)

        pixel_pitch = round(dw / rw, 6)      if (dw and rw)        else 0
        diag_px     = math.sqrt(rw**2+rh**2) if (rw and rh)        else 0
        ppi         = round(diag_px / dia, 2) if dia                else 0
        diag_mm     = round(math.sqrt(dw**2+dh**2), 4) if (dw and dh) else 0
        aspect_w    = rw // math.gcd(rw, rh)  if (rw and rh)        else 0
        aspect_h    = rh // math.gcd(rw, rh)  if (rw and rh)        else 0

        # Waveshare HAT cutout math
        from parser.dimension_math import DISPLAY_BEZEL, DISPLAY_RECESS_DEPTH
        cutout_w = round(dw - 2 * DISPLAY_BEZEL, 3) if dw else 0
        cutout_h = round(dh - 2 * DISPLAY_BEZEL, 3) if dh else 0

        return {
            "resolution":         f"{rw}×{rh}" if rw else "N/A",
            "aspect_ratio":       f"{aspect_w}:{aspect_h}" if aspect_w else "N/A",
            "display_active_mm":  f"{dw}×{dh}" if dw else "N/A",
            "diagonal_inch":      dia,
            "diagonal_mm":        diag_mm,
            "pixel_pitch_mm":     pixel_pitch,
            "ppi":                ppi,
            "lid_cutout_mm":      f"{cutout_w}×{cutout_h}" if cutout_w else "N/A",
            "bezel_lip_mm":       DISPLAY_BEZEL,
            "recess_depth_mm":    DISPLAY_RECESS_DEPTH,
            "formula_ppi":        "√(res_w²+res_h²) / diagonal_inch",
            "formula_pitch":      "display_active_width_mm / resolution_w",
            "formula_diag_mm":    "√(active_w² + active_h²)",
            "formula_cutout_w":   "active_w_mm − 2 × bezel_lip_mm",
        }

    def _waveshare_hat_specs(self) -> dict:
        """
        Dedicated section for Waveshare LCD HAT specific assembly specs.
        Includes all critical dimensions and stacking math.
        """
        s = self.spec
        from parser.dimension_math import (
            WS_HAT_STACK_HEIGHT_MM, BOTTOM_FLOOR, STANDOFF_HEIGHT,
            LID_THICKNESS, PCB_CLEARANCE, WALL_THICKNESS
        )
        bw = s.get("board_width_mm", 65.0)
        bh = s.get("board_height_mm", 30.1)
        bt = s.get("board_thickness_mm", 1.6)

        gpio_pitch = s.get("gpio_header_pitch_mm", 2.54)
        gpio_rows  = s.get("gpio_rows", 2)
        gpio_cols  = s.get("gpio_cols", 20)
        gpio_total = gpio_rows * gpio_cols
        gpio_width_mm  = (gpio_cols - 1) * gpio_pitch
        gpio_height_mm = (gpio_rows - 1) * gpio_pitch

        return {
            "hat_model":    s.get("model", "Waveshare HAT"),
            "pcb_mm":       f"{bw} × {bh} × {bt}",
            "gpio_header": {
                "pitch_mm":    gpio_pitch,
                "rows":        gpio_rows,
                "cols":        gpio_cols,
                "total_pins":  gpio_total,
                "footprint_mm": f"{gpio_width_mm:.2f} × {gpio_height_mm:.2f}",
                "note": "2×20 header occupies 50.8mm × 2.54mm on PCB",
            },
            "stacking_math": {
                "rpi_pcb_to_hat_pcb_mm":   WS_HAT_STACK_HEIGHT_MM,
                "hat_pcb_thickness_mm":    bt,
                "total_stack_height_mm":   round(WS_HAT_STACK_HEIGHT_MM + bt, 2),
                "shell_floor_to_pcb_mm":   round(BOTTOM_FLOOR + STANDOFF_HEIGHT, 2),
                "total_internal_height_mm": round(BOTTOM_FLOOR + STANDOFF_HEIGHT + bt
                                                   + WS_HAT_STACK_HEIGHT_MM, 2),
                "formula": "floor_t + standoff_h + rpi_t + hat_stack_h",
            },
            "mounting_holes": {
                "count":          len(s.get("mounting_hole_positions_mm", [])),
                "diameter_mm":    s.get("mounting_hole_diameter_mm", 2.9),
                "positions_mm":   s.get("mounting_hole_positions_mm", []),
                "screw_spec":     "M2.5 × 6mm",
                "insert_spec":    "M2.5 × 3.5mm brass heat-set insert",
                "note":           "Use M2.5 standoffs between RPi and printed base",
            },
            "display_positioning": {
                "display_on_board": "Left ~33% of PCB width",
                "ui_on_board":      "Right ~67%: joystick + 3 buttons",
                "bezel_x_mm":       s.get("board_width_mm", 65) * 0.33,
                "note":             "Display center at ~21.5mm from left edge of board",
            },
            "interface":     s.get("interface", "SPI"),
            "controller_ic": s.get("controller_ic", "ST7789"),
            "spi_pins": {
                "MOSI": "GPIO10 (SPI0 MOSI)",
                "MISO": "GPIO9  (SPI0 MISO)",
                "SCLK": "GPIO11 (SPI0 SCLK)",
                "CS":   "GPIO8  (CE0)",
                "DC":   "GPIO25",
                "RST":  "GPIO27",
                "BL":   "GPIO24  (Backlight PWM)",
            },
            "voltage_v":        s.get("voltage_v", 3.3),
            "hat_hardware_bom": WAVESHARE_HAT_BOM,
        }

    def _print_guide(self) -> dict:
        return {
            "orientation": {
                "bottom_case": "Print flat (floor down). No supports needed.",
                "top_lid":     "Print face down. Add support for display recess if any overhang > 45°.",
                "button_cap":  "Print stem up. No supports.",
                "battery_tray":"Print open-side up. No supports.",
                "grip_panel":  "Print flat. Support for contour overhang.",
            },
            "material_recommendations": {
                "structural_parts": "PETG or ABS for durability",
                "display_lid":      "PLA+ or PETG for surface finish",
                "grip_panels":      "TPU 95A for ergonomics",
                "button_caps":      "PLA or PETG (colour matched)",
                "standoffs":        "PLA or PETG (100% infill)",
            },
        }

    def _hardware_bom(self) -> dict:
        boss_count = len(self.geom.get("mounting_bosses", []))
        return {
            "fasteners": {
                "M2.5_screws_6mm": {
                    "qty":     boss_count,
                    "purpose": "PCB standoff to printed boss",
                    "source":  "M2.5×6 DIN7985",
                },
                "M2.5_heatset_inserts": {
                    "qty":     boss_count,
                    "purpose": "Press into printed bosses for reusable threads",
                    "tool":    "Soldering iron or heat-set tool at 200°C",
                },
            },
            "waveshare_hat": WAVESHARE_HAT_BOM,
            "display_mount": {
                "adhesive":  ADHESIVE_OPTIONS[0],
                "alternate": ADHESIVE_OPTIONS[2],
            },
        }

    def _filament_bom(self) -> dict:
        g = self.geom
        sw = g.get("shell_width_mm", 70)
        sh = g.get("shell_height_mm", 35)
        sd = g.get("shell_depth_mm", 20)
        wt = 2.0
        infill = 0.30

        # Rough volume estimate (outer - inner hollow)
        outer_vol = sw * sh * sd
        inner_vol = (sw - 2*wt) * (sh - 2*wt) * (sd - 2.5)
        shell_vol_cm3 = round((outer_vol - inner_vol * (1 - infill)) / 1000, 2)

        mat = MATERIALS["PETG"]
        mass_g   = round(shell_vol_cm3 * mat["density_g_cm3"], 1)
        cost_usd = round(mass_g / 1000 * mat["cost_per_kg_usd"], 2)
        # ~10g/hr at 60mm/s typical
        time_hr  = round(mass_g / 10, 1)

        return {
            "estimated_volume_cm3": shell_vol_cm3,
            "estimated_mass_g":     mass_g,
            "estimated_cost_usd":   cost_usd,
            "estimated_print_hours": time_hr,
            "recommended_material":  "PETG",
            "materials_catalogue":   MATERIALS,
        }

    def _assembly_steps(self) -> list:
        return [
            {
                "step": 1,
                "title": "Prepare printed parts",
                "detail": (
                    "Remove supports. Sand parting lines on shell_bottom and shell_lid "
                    "with 220-grit. Test-fit lid on bottom before inserting hardware."
                ),
            },
            {
                "step": 2,
                "title": "Install heat-set inserts",
                "detail": (
                    "Press M2.5 × 3.5mm brass inserts into each mounting boss on "
                    "shell_bottom using a soldering iron at 200°C. "
                    "Inserts should sit flush or 0.1mm below surface."
                ),
            },
            {
                "step": 3,
                "title": "Install Raspberry Pi / host board",
                "detail": (
                    "Thread M2.5 × 11mm male-female standoffs through RPi mounting holes. "
                    "Secure to shell_bottom bosses with M2.5 × 6mm screws. "
                    "Torque: finger-tight + ¼ turn."
                ),
            },
            {
                "step": 4,
                "title": "Attach Waveshare LCD HAT",
                "detail": (
                    "Align 40-pin GPIO header with Raspberry Pi GPIO pins. "
                    "Press firmly until HAT PCB seats on the 11mm standoffs. "
                    "Secure with M2.5 × 4mm screws through HAT mounting holes. "
                    "Verify display orientation before closing."
                ),
            },
            {
                "step": 5,
                "title": "Route cables",
                "detail": (
                    "Route any USB / power cables through designated port cutouts. "
                    "Use cable clips or hot-glue dots to prevent interference with lid closure."
                ),
            },
            {
                "step": 6,
                "title": "Install button caps",
                "detail": (
                    "Press-fit printed button caps through apertures in shell_lid. "
                    "Shoulder flange retains cap. Confirm travel = 0.5–1.0 mm before bottoming."
                ),
            },
            {
                "step": 7,
                "title": "Mount display",
                "detail": (
                    "Apply 3M VHB 5952 tape to display PCB edges (not active area). "
                    "Align display through lid cutout recess. Apply even pressure 60 s. "
                    "Alternative: rely on printed bezel lip press-fit (no adhesive)."
                ),
            },
            {
                "step": 8,
                "title": "Close and snap lid",
                "detail": (
                    "Align lid snap tabs with shell_bottom receivers. "
                    "Press lid perimeter evenly until all 6 snap tabs click. "
                    "Do not force — if resistance felt, check for cable pinch."
                ),
            },
            {
                "step": 9,
                "title": "Power-on and test",
                "detail": (
                    "Connect power. Verify: display initialises, buttons register, "
                    "joystick responds. Check for light leakage around display bezel. "
                    "If leakage, add black craft foam ring to display back."
                ),
            },
        ]

    def _print_settings(self) -> dict:
        return {
            "structural_shell": {
                "material":      "PETG",
                "nozzle_mm":     0.4,
                "layer_mm":      0.2,
                "infill":        "30% Gyroid",
                "perimeters":    4,
                "top_layers":    5,
                "bottom_layers": 4,
                "print_speed_mms": 50,
                "temp_hotend_c": 235,
                "temp_bed_c":    80,
                "cooling":       "50% fan after layer 3",
                "supports":      "No (orient correctly)",
            },
            "button_caps": {
                "material":      "PLA+",
                "layer_mm":      0.15,
                "infill":        "100% Rectilinear",
                "perimeters":    4,
                "temp_hotend_c": 215,
                "temp_bed_c":    60,
            },
            "grip_panels": {
                "material":      "TPU 95A",
                "layer_mm":      0.25,
                "infill":        "40% Honeycomb",
                "perimeters":    3,
                "print_speed_mms": 25,
                "temp_hotend_c": 225,
                "temp_bed_c":    50,
                "retraction_mm": 1.0,
                "note":          "Reduce speed — TPU prone to stringing",
            },
        }

    def _tolerances(self) -> dict:
        return {
            "pcb_clearance_mm":         0.5,
            "display_bezel_mm":         0.8,
            "button_aperture_extra_mm": 0.4,
            "press_fit_shaft_tol_mm":  -0.1,
            "heat_set_bore_tol_mm":    +0.1,
            "snap_fit_depth_mm":        1.2,
            "snap_fit_height_mm":       2.5,
            "print_shrink_factor":      1.002,  # PETG ~0.2% shrink
            "notes": [
                "All clearances are unilateral (applied to opening, not shaft).",
                "Heat-set insert bore should be 0.1 mm smaller than OD of insert.",
                "Button cap shaft = aperture radius − 0.1 mm (press-fit).",
                "Snap-fit tabs designed for PETG spring-back; reduce depth 0.2mm for PLA.",
                "Waveshare HAT: GPIO header forces 2.54mm pitch constraint — do not violate.",
            ],
        }

    # -----------------------------------------------------------------------
    # Markdown renderer
    # -----------------------------------------------------------------------

    def _render_markdown(self, data: dict) -> str:
        d = data["device"]
        g = data["shell_geometry"]
        dm = data["display_math"]
        ws = data["waveshare_hat"]
        bom = data["bom_hardware"]
        fil = data["bom_filament"]
        steps = data["assembly_steps"]
        ps = data["print_settings"]
        tol = data["tolerances"]

        lines = [
            f"# Assembly Specification: {d['model']}",
            "",
            "## Device",
            f"- **Model:** {d['model']}  |  **Vendor:** {d['vendor']}",
            f"- **Interface:** {d['interface']}  |  **Driver IC:** {d['controller_ic']}",
            f"- **Operating Voltage:** {d['voltage_v']} V",
            f"- **Compatible with:** {', '.join(d['compatible_boards'])}",
            "",
            "## Shell Geometry",
            f"| Dimension | Value |",
            f"|---|---|",
            f"| Shell W × H × D | {g['shell_w_mm']} × {g['shell_h_mm']} × {g['shell_d_mm']} mm |",
            f"| PCB offset (X, Y) | ({g['pcb_offset_x']}, {g['pcb_offset_y']}) mm |",
            f"| PCB Z from floor | {g['pcb_z_mm']} mm |",
            f"| Corner radius | {g['corner_radius_mm']} mm |",
            f"| Mounting bosses | {g['mounting_boss_count']} |",
            f"| Structural ribs | {g['rib_count']} |",
            f"| Snap-fit tabs | {g['snap_fit_count']} |",
            "",
            "## Display Mathematics (Waveshare LCD HAT)",
            f"| Parameter | Value | Formula |",
            f"|---|---|---|",
            f"| Resolution | {dm['resolution']} | — |",
            f"| Aspect Ratio | {dm['aspect_ratio']} | — |",
            f"| Active Area | {dm['display_active_mm']} mm | — |",
            f"| Diagonal (inch) | {dm['diagonal_inch']}\" | — |",
            f"| Diagonal (mm) | {dm['diagonal_mm']} mm | {dm['formula_diag_mm']} |",
            f"| Pixel Pitch | {dm['pixel_pitch_mm']} mm | {dm['formula_pitch']} |",
            f"| PPI | {dm['ppi']} | {dm['formula_ppi']} |",
            f"| Lid Cutout | {dm['lid_cutout_mm']} mm | {dm['formula_cutout_w']} |",
            f"| Bezel Lip | {dm['bezel_lip_mm']} mm | — |",
            f"| Recess Depth | {dm['recess_depth_mm']} mm | — |",
            "",
            "## Waveshare HAT Stacking Math",
            f"| Item | Value |",
            f"|---|---|",
        ]
        sm = ws["stacking_math"]
        for k, v in sm.items():
            if k != "formula":
                lines.append(f"| {k.replace('_', ' ')} | {v} |")
        lines += [
            f"- **Formula:** `{sm['formula']}`",
            "",
            "### GPIO Header",
            f"- Pitch: {ws['gpio_header']['pitch_mm']} mm",
            f"- Configuration: {ws['gpio_header']['rows']}×{ws['gpio_header']['cols']} "
            f"({ws['gpio_header']['total_pins']} pins)",
            f"- Footprint: {ws['gpio_header']['footprint_mm']} mm",
            "",
            "### SPI Pin Assignment",
            "| Signal | GPIO |",
            "|---|---|",
        ]
        for sig, pin in ws["spi_pins"].items():
            lines.append(f"| {sig} | {pin} |")

        lines += [
            "",
            "## Bill of Materials — Hardware",
            "| Item | Qty | Purpose |",
            "|---|---|---|",
        ]
        for k, v in bom["fasteners"].items():
            lines.append(f"| {v.get('description', k)} | {v.get('qty', '—')} | {v.get('purpose', '')} |")
        for k, v in WAVESHARE_HAT_BOM.items():
            lines.append(f"| {v['description']} | {v['qty']} | {v['purpose']} |")

        lines += [
            "",
            "## Bill of Materials — Filament",
            f"- **Estimated volume:** {fil['estimated_volume_cm3']} cm³",
            f"- **Estimated mass:** {fil['estimated_mass_g']} g",
            f"- **Estimated cost:** ${fil['estimated_cost_usd']} USD ({fil['recommended_material']})",
            f"- **Estimated print time:** {fil['estimated_print_hours']} hours",
            "",
            "## Assembly Steps",
        ]
        for s in steps:
            lines.append(f"### Step {s['step']}: {s['title']}")
            lines.append(s['detail'])
            lines.append("")

        lines += [
            "## Print Settings",
        ]
        for part, settings in ps.items():
            lines.append(f"### {part.replace('_', ' ').title()}")
            for k, v in settings.items():
                lines.append(f"- **{k.replace('_', ' ')}:** {v}")
            lines.append("")

        lines += [
            "## Tolerances",
            "| Parameter | Value |",
            "|---|---|",
        ]
        for k, v in tol.items():
            if k != "notes":
                lines.append(f"| {k.replace('_', ' ')} | {v} |")
        lines.append("")
        lines.append("**Notes:**")
        for note in tol["notes"]:
            lines.append(f"- {note}")

        return "\n".join(lines)
