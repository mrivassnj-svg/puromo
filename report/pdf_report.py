"""
pdf_report.py
-------------
Generates a complete professional PDF design report for a handheld device build.

Sections:
  1. Cover page — device name, model, branding
  2. Computational Architecture — board, display, MCU, interface
  3. Physical Design — shell dimensions, materials, weight estimate
  4. Display Mathematics — PPI, pixel pitch, cutout formulae
  5. Pinout Reference — full 40-pin header map per module
  6. Wiring Diagram — rendered SVG-to-PDF wire diagram
  7. Component Clearances & Tolerances
  8. Firmware Architecture — boot sequence, driver order, scheduler
  9. Assembly Guide — step-by-step with hardware BOM
  10. 3D Print Settings — per-entity print parameters
  11. Safety Checklist
  12. Notes / Signature block
"""

import io
import math
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import (
    HexColor, black, white, Color
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable, Image
)
from reportlab.platypus.flowables import Flowable
from reportlab.pdfgen import canvas as pdf_canvas

# ── Colour palette ────────────────────────────────────────────────
C_BG        = HexColor("#0d0f14")
C_SURFACE   = HexColor("#151821")
C_CARD      = HexColor("#1c2030")
C_ACCENT    = HexColor("#4f8ef7")
C_ACCENT2   = HexColor("#7c5cd8")
C_GREEN     = HexColor("#22c55e")
C_RED       = HexColor("#ef4444")
C_YELLOW    = HexColor("#f59e0b")
C_TEXT      = HexColor("#e2e8f0")
C_MUTED     = HexColor("#8892a4")
C_BORDER    = HexColor("#2a2f42")
C_WHITE     = white
C_BLACK     = black
C_WIRE = {
    "3V3":  HexColor("#FF0000"),
    "GND":  HexColor("#111111"),
    "MOSI": HexColor("#0000FF"),
    "MISO": HexColor("#00AAFF"),
    "SCLK": HexColor("#CCCC00"),
    "CS":   HexColor("#FF00FF"),
    "DC":   HexColor("#FF8800"),
    "RST":  HexColor("#FF4444"),
    "BL":   HexColor("#88DD00"),
    "GPIO": HexColor("#888888"),
    "SDA":  HexColor("#0000FF"),
    "SCL":  HexColor("#CCCC00"),
    "TX":   HexColor("#00AA00"),
    "RX":   HexColor("#FFFFFF"),
    "5V":   HexColor("#FF6600"),
}

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
INNER_W = PAGE_W - 2 * MARGIN

def _styles():
    s = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle("cover_title",
            fontName="Helvetica-Bold", fontSize=28, textColor=C_WHITE,
            spaceAfter=4*mm, alignment=TA_CENTER),
        "cover_sub":   ParagraphStyle("cover_sub",
            fontName="Helvetica", fontSize=13, textColor=C_MUTED,
            spaceAfter=2*mm, alignment=TA_CENTER),
        "cover_badge": ParagraphStyle("cover_badge",
            fontName="Helvetica-Bold", fontSize=10, textColor=C_ACCENT,
            spaceAfter=1*mm, alignment=TA_CENTER),
        "h1": ParagraphStyle("h1",
            fontName="Helvetica-Bold", fontSize=16, textColor=C_ACCENT,
            spaceBefore=6*mm, spaceAfter=3*mm),
        "h2": ParagraphStyle("h2",
            fontName="Helvetica-Bold", fontSize=12, textColor=C_WHITE,
            spaceBefore=4*mm, spaceAfter=2*mm),
        "h3": ParagraphStyle("h3",
            fontName="Helvetica-Bold", fontSize=10, textColor=C_MUTED,
            spaceBefore=2*mm, spaceAfter=1*mm),
        "body": ParagraphStyle("body",
            fontName="Helvetica", fontSize=9, textColor=C_TEXT,
            spaceAfter=2*mm, leading=14),
        "mono": ParagraphStyle("mono",
            fontName="Courier", fontSize=8, textColor=C_ACCENT,
            spaceAfter=1*mm, leading=12),
        "mono_sm": ParagraphStyle("mono_sm",
            fontName="Courier", fontSize=7, textColor=HexColor("#9fb3cc"),
            spaceAfter=0.5*mm, leading=11),
        "note":  ParagraphStyle("note",
            fontName="Helvetica-Oblique", fontSize=8, textColor=C_MUTED,
            spaceAfter=1*mm, leading=12),
        "cell":  ParagraphStyle("cell",
            fontName="Helvetica", fontSize=8, textColor=C_TEXT,
            leading=11),
        "cellh": ParagraphStyle("cellh",
            fontName="Helvetica-Bold", fontSize=8, textColor=C_ACCENT,
            leading=11),
        "warn":  ParagraphStyle("warn",
            fontName="Helvetica-Bold", fontSize=8, textColor=C_RED,
            spaceAfter=1*mm),
        "safe":  ParagraphStyle("safe",
            fontName="Helvetica-Bold", fontSize=8, textColor=C_GREEN,
            spaceAfter=1*mm),
        "toc":   ParagraphStyle("toc",
            fontName="Helvetica", fontSize=9, textColor=C_TEXT,
            spaceAfter=1*mm, leading=14),
    }

# ── Dark table style ──────────────────────────────────────────────
def dark_table_style(header=True):
    cmds = [
        ("BACKGROUND",  (0,0), (-1, 0 if header else -1), C_CARD),
        ("TEXTCOLOR",   (0,0), (-1,-1), C_TEXT),
        ("FONTNAME",    (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("GRID",        (0,0), (-1,-1), 0.4, C_BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_SURFACE, C_CARD]),
        ("LEFTPADDING",  (0,0),(-1,-1), 4),
        ("RIGHTPADDING", (0,0),(-1,-1), 4),
        ("TOPPADDING",   (0,0),(-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0,0), (-1,0), C_BG),
            ("TEXTCOLOR",  (0,0), (-1,0), C_ACCENT),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,0), 8),
        ]
    return TableStyle(cmds)

def accent_bar(c_obj, x, y, w, h=1.5*mm, colour=C_ACCENT):
    """Draw a coloured accent bar on the canvas."""
    c_obj.setFillColor(colour)
    c_obj.rect(x, y, w, h, fill=1, stroke=0)


# ═══════════════════════════════════════════════════════════════
#  Page template callbacks
# ═══════════════════════════════════════════════════════════════
def _page_header_footer(canvas, doc):
    canvas.saveState()
    # Dark background
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Top accent bar
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H - 4*mm, PAGE_W, 4*mm, fill=1, stroke=0)
    # Footer bar
    canvas.setFillColor(C_SURFACE)
    canvas.rect(0, 0, PAGE_W, 10*mm, fill=1, stroke=0)
    canvas.setFillColor(C_BORDER)
    canvas.rect(0, 10*mm, PAGE_W, 0.3*mm, fill=1, stroke=0)
    # Footer text
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 7)
    model = getattr(doc, "_model_name", "Handheld Device")
    canvas.drawString(MARGIN, 3.5*mm, f"Handheld 3D Shell Builder  |  {model}")
    canvas.drawRightString(PAGE_W - MARGIN, 3.5*mm,
        f"Page {doc.page}  |  Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    canvas.restoreState()

def _human_variant(v: str) -> str:
    """'standard_11mm' → 'Standard 11 mm'; 'soldered_direct' →
    'Soldered direct'. Internal variant IDs never reach the page."""
    import re as _re
    s = _re.sub(r"(\d+)mm\b", r"\1 mm", str(v).replace("_", " "))
    return s[:1].upper() + s[1:] if s else s


def _cover_page(canvas, doc):
    canvas.saveState()
    # Full dark background
    canvas.setFillColor(C_BG)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Gradient-like top block
    canvas.setFillColor(C_SURFACE)
    canvas.rect(0, PAGE_H*0.55, PAGE_W, PAGE_H*0.45, fill=1, stroke=0)
    # Accent bar
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, PAGE_H*0.55 - 3*mm, PAGE_W, 3*mm, fill=1, stroke=0)
    # Accent2 bar
    canvas.setFillColor(C_ACCENT2)
    canvas.rect(0, PAGE_H*0.55 - 6*mm, PAGE_W, 3*mm, fill=1, stroke=0)
    # Footer
    canvas.setFillColor(C_SURFACE)
    canvas.rect(0, 0, PAGE_W, 15*mm, fill=1, stroke=0)
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(MARGIN, 5*mm,
        f"Generated by Handheld 3D Shell Builder  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    canvas.drawRightString(PAGE_W - MARGIN, 5*mm, "CONFIDENTIAL — Design Document")
    canvas.restoreState()


# ═══════════════════════════════════════════════════════════════
#  Wire colour swatch flowable
# ═══════════════════════════════════════════════════════════════
class WireSwatch(Flowable):
    def __init__(self, role, colour_hex, label, width=60*mm, height=5*mm):
        super().__init__()
        self.role = role
        self.colour = HexColor(colour_hex) if isinstance(colour_hex, str) else colour_hex
        self.label = label
        self.width = width
        self.height = height

    def draw(self):
        self.canv.setFillColor(self.colour)
        self.canv.rect(0, 1*mm, 8*mm, 3*mm, fill=1, stroke=0)
        self.canv.setFillColor(C_TEXT)
        self.canv.setFont("Helvetica-Bold", 7)
        self.canv.drawString(10*mm, 1.8*mm, f"{self.role}")
        self.canv.setFillColor(C_MUTED)
        self.canv.setFont("Helvetica", 7)
        self.canv.drawString(26*mm, 1.8*mm, self.label)


# ═══════════════════════════════════════════════════════════════
#  GPIO pin diagram flowable
# ═══════════════════════════════════════════════════════════════
class GPIODiagram(Flowable):
    """Renders the 40-pin RPi header as a visual 2×20 grid."""
    PIN_W = 7*mm
    PIN_H = 5*mm
    COLS  = 20
    ROWS  = 2

    def __init__(self, gpio_map, claimed_map, width=INNER_W):
        super().__init__()
        self.gpio_map   = gpio_map    # list of pin dicts
        self.claimed    = claimed_map # gpio_key → {module, role, colour}
        self.width      = width
        self.height     = self.ROWS * self.PIN_H + 20*mm
        self._pin_w     = min(self.PIN_W, (width - 10*mm) / self.COLS)

    def draw(self):
        c = self.canv
        # Header label
        c.setFillColor(C_MUTED)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(0, self.height - 5*mm, "RPi 40-Pin GPIO Header (Physical Pin Layout)")
        c.setFont("Helvetica", 6)
        c.drawString(0, self.height - 9*mm,
            "■ Claimed  □ Free  Numbers = Physical pin")

        for i, pin in enumerate(self.gpio_map[:40]):
            col = i % self.COLS
            row = i // self.COLS
            x = col * self._pin_w
            y = (1 - row) * self.PIN_H + 2*mm

            claimed_by = pin.get("claimed_by")
            if claimed_by:
                try:
                    fill = HexColor(claimed_by["colour"])
                except Exception:
                    fill = C_ACCENT
                txt_col = C_WHITE
            else:
                fill = C_CARD
                txt_col = C_MUTED

            c.setFillColor(fill)
            c.setStrokeColor(C_BORDER)
            c.rect(x+0.5, y+0.5, self._pin_w-1, self.PIN_H-1,
                   fill=1, stroke=1)
            c.setFillColor(txt_col)
            c.setFont("Helvetica-Bold", 5.5)
            c.drawCentredString(x + self._pin_w/2,
                                y + self.PIN_H*0.55,
                                str(pin["phys"]))
            c.setFont("Helvetica", 4.5)
            name_short = pin["name"].replace("GPIO","G").replace(" ","")[:7]
            c.drawCentredString(x + self._pin_w/2,
                                y + self.PIN_H*0.2,
                                name_short)


# ═══════════════════════════════════════════════════════════════
#  Main PDF Generator
# ═══════════════════════════════════════════════════════════════
class PDFReportGenerator:

    def __init__(self,
                 device_name: str,
                 spec: dict,
                 geom: dict,
                 pinout: dict,
                 firmware_arch: dict,
                 assembly_specs: dict,
                 output_path: str,
                 design_hash: str = "",
                 images_dir: Optional[str] = None,
                 brain_name: str = ""):
        self.device_name    = device_name
        self.spec           = spec
        self.geom           = geom
        self.pinout         = pinout
        self.fw             = firmware_arch
        self.asm            = assembly_specs
        self.output_path    = output_path
        # Requirement 6b: assembly-step images live under
        # output/report_images/<design_hash>/ — §7 looks them up
        # ONLY there, so one design's renders can never illustrate
        # another design's report.
        self.design_hash    = design_hash
        self.images_dir     = images_dir
        # Requirement 10: the design's chosen main board, named by
        # main.py from the selection + brains registry ("" when the
        # design has no explicit brain choice).
        self.brain_name     = brain_name
        self.st             = _styles()

    def generate(self) -> str:
        doc = SimpleDocTemplate(
            self.output_path,
            pagesize=A4,
            leftMargin=MARGIN, rightMargin=MARGIN,
            topMargin=20*mm, bottomMargin=15*mm,
            title=f"{self.device_name} — Design Report",
            author="Handheld 3D Shell Builder",
        )
        doc._model_name = self.device_name

        story = []
        story += self._cover()
        story += self._toc()
        story += self._section_architecture()
        story += self._section_physical()
        story += self._section_display_math()
        story += self._section_pinout()
        story += self._section_wiring()
        story += self._section_firmware()
        story += self._section_assembly()
        story += self._section_print_settings()
        story += self._section_safety()
        story += self._section_signature()

        def _first_page(c, d):
            _cover_page(c, d)
        def _later_pages(c, d):
            _page_header_footer(c, d)

        doc.build(story,
                  onFirstPage=_first_page,
                  onLaterPages=_later_pages)
        return self.output_path

    # ── Cover ────────────────────────────────────────────────────
    def _cover(self):
        st = self.st
        model   = self.spec.get("model","—")
        vendor  = self.spec.get("vendor","—").title()
        iface   = self.spec.get("interface","—")
        ic      = self.spec.get("controller_ic","—")
        rw, rh  = self.spec.get("resolution_w",0), self.spec.get("resolution_h",0)
        sw      = self.geom.get("shell_width_mm",0)
        sh      = self.geom.get("shell_height_mm",0)
        sd      = self.geom.get("shell_depth_mm",0)
        return [
            Spacer(1, 60*mm),
            Paragraph(self.device_name, st["cover_title"]),
            Paragraph(model, st["cover_sub"]),
            Paragraph(f"{vendor}  ·  {iface}  ·  {ic}", st["cover_badge"]),
            Spacer(1, 6*mm),
            Paragraph(
                f"Shell: {sw}×{sh}×{sd} mm  ·  "
                f"Display: {rw}×{rh}px  ·  "
                f"PPI: {self.geom.get('ppi','—')}",
                st["cover_badge"]
            ),
            Spacer(1, 4*mm),
            Paragraph("Design Document — Confidential", st["cover_badge"]),
            Paragraph(datetime.now().strftime("%B %d, %Y"), st["cover_sub"]),
            PageBreak(),
        ]

    # ── Table of Contents ────────────────────────────────────────
    def _toc(self):
        sections = [
            ("1", "Computational Architecture"),
            ("2", "Physical Design & Shell Geometry"),
            ("3", "Display Mathematics"),
            ("4", "Pinout Reference — GPIO Header Map"),
            ("5", "Wiring Diagram & Wire Colours"),
            ("6", "Firmware Architecture & Boot Sequence"),
            ("7", "Assembly Guide & Bill of Materials"),
            ("8", "3D Print Settings"),
            ("9", "Safety Checklist"),
            ("10","Notes & Sign-Off"),
        ]
        rows = [["§", "Section"]] + [[s, t] for s, t in sections]
        tbl = Table(rows, colWidths=[12*mm, INNER_W-12*mm])
        tbl.setStyle(dark_table_style())
        return [
            Paragraph("Contents", self.st["h1"]),
            tbl,
            PageBreak(),
        ]

    # ── 1. Architecture ──────────────────────────────────────────
    def _section_architecture(self):
        st = self.st
        s  = self.spec
        g  = self.geom
        rows = [
            ["Field", "Value"],
            ["Device Name",        self.device_name],
            ["PCB Model",          s.get("model","—")],
            ["Vendor",             s.get("vendor","—").title()],
            ["Controller IC",      s.get("controller_ic","—")],
            ["Communication Bus",  s.get("interface","—")],
            ["Operating Voltage",  f"{s.get('voltage_v',3.3)} V"],
            *( [["Host Board (your design)", self.brain_name]] if self.brain_name else [] ),
            ["HAT Compatibility",  ", ".join(s.get("compatible_boards",[]) or ["—"])],
            ["GPIO Header",        f"{s.get('gpio_rows',2)}×{s.get('gpio_cols',20)} "
                                   f"({s.get('gpio_rows',2)*s.get('gpio_cols',20)} pins)  "
                                   f"pitch {s.get('gpio_header_pitch_mm',2.54)} mm"],
            ["Header Variant",     _human_variant(s.get("gpio_header_variant","standard_11mm"))],
            ["Mated Stack Gap",    f"{s.get('gpio_header_mated_gap_mm',11.5)} mm"],
            ["PCB Dimensions",     f"{s.get('board_width_mm',0)}×"
                                   f"{s.get('board_height_mm',0)}×"
                                   f"{s.get('board_thickness_mm',1.6)} mm"],
            ["Tallest Component",  f"{s.get('tallest_component_mm',0)} mm  "
                                   f"({s.get('tallest_component_label','—')})"],
            ["Header Above HAT",   f"{s.get('gpio_header_above_mm',8.5)} mm  "
                                   f"(stack clearance uses the greater of "
                                   f"component height and header height)"],
        ]
        tbl = Table(rows, colWidths=[55*mm, INNER_W-55*mm])
        tbl.setStyle(dark_table_style())

        arch_text = (
            f"The <b>{self.device_name}</b> is built around the <b>{s.get('model','—')}</b> "
            f"({s.get('vendor','—').title()}), a {s.get('interface','SPI')}-interfaced "
            f"display/input HAT controlled by the <b>{s.get('controller_ic','ST7789')}</b> IC. "
            f"The host Raspberry Pi connects via its 40-pin GPIO header using a "
            f"<b>{_human_variant(s.get('gpio_header_variant','standard_11mm'))}</b> female header "
            f"creating a {s.get('gpio_header_mated_gap_mm',11.5)} mm board-to-board gap. "
            f"All logic signals operate at <b>3.3 V</b> — 5 V levels will damage the HAT. "
            f"The complete shell envelope measures "
            f"<b>{g.get('shell_width_mm',0)} × {g.get('shell_height_mm',0)} × "
            f"{g.get('shell_depth_mm',0)} mm</b> and accommodates the full component stack "
            f"with {1.5} mm clearance above the tallest component."
        )
        return [
            Paragraph("1. Computational Architecture", st["h1"]),
            Paragraph(arch_text, st["body"]),
            Spacer(1, 2*mm),
            tbl,
            PageBreak(),
        ]

    # ── 2. Physical Design ───────────────────────────────────────
    def _section_physical(self):
        st = self.st
        g  = self.geom
        notes = g.get("assembly_notes", [])
        rows = [
            ["Dimension", "Value", "Formula"],
            ["Shell Width",   f"{g.get('shell_width_mm',0)} mm",
             "board_w + 2×clearance + 2×wall"],
            ["Shell Height",  f"{g.get('shell_height_mm',0)} mm",
             "board_h + 2×clearance + 2×wall"],
            ["Shell Depth",   f"{g.get('shell_depth_mm',0)} mm",
             "floor + standoff + PCB + gap + HAT + lid"],
            ["Corner Radius", f"{g.get('corner_radius',3)} mm", "constant"],
            ["Wall Thickness","2.0 mm", "constant"],
            ["Floor Thickness","2.5 mm","constant"],
            ["Lid Thickness", "1.8 mm", "constant"],
            ["Standoff Height","3.0 mm","constant"],
            ["PCB Offset X",  f"{g.get('pcb_offset_x',0)} mm",
             "wall + clearance"],
            ["PCB Offset Y",  f"{g.get('pcb_offset_y',0)} mm",
             "wall + clearance"],
            ["PCB Z (floor)", f"{g.get('pcb_z_from_floor',0)} mm",
             "floor + standoff"],
            ["Mounting Bosses",
             f"{len(g.get('mounting_bosses',[]))} × Ø5.0mm OD / Ø2.9mm ID",
             "corners + 3.5mm inset"],
            ["Snap-fit Tabs",
             f"{len(g.get('snap_fit_positions',[]))}",
             "perimeter midpoints"],
            ["Structural Ribs",
             f"{len(g.get('ribs',[]))}",
             "≤20mm spacing"],
        ]
        tbl = Table(rows, colWidths=[45*mm, 35*mm, INNER_W-80*mm])
        tbl.setStyle(dark_table_style())

        notes_items = [Paragraph(f"• {n}", st["note"]) for n in notes]
        return [
            Paragraph("2. Physical Design & Shell Geometry", st["h1"]),
            tbl,
            Spacer(1, 3*mm),
            Paragraph("Design Notes:", st["h2"]),
            *notes_items,
            PageBreak(),
        ]

    # ── 3. Display Mathematics ───────────────────────────────────
    def _section_display_math(self):
        st = self.st
        s  = self.spec
        g  = self.geom
        rw, rh = s.get("resolution_w",240), s.get("resolution_h",240)
        dw, dh = s.get("display_width_mm",0), s.get("display_height_mm",0)
        dia    = s.get("display_diagonal_inch",0)
        ppi    = g.get("ppi",0)
        pitch  = g.get("pixel_pitch_mm",0)
        diag_mm= g.get("display_diagonal_mm",0)

        dc = g.get("display_cutout",{})
        rows = [
            ["Parameter","Value","Formula"],
            ["Resolution",         f"{rw}×{rh} px",           "—"],
            ["Active Area",        f"{dw}×{dh} mm",           "—"],
            ["Diagonal (inch)",    f"{dia}\"",                 "—"],
            ["Diagonal (mm)",      f"{diag_mm} mm",           "√(w²+h²)"],
            ["Pixel Pitch",        f"{pitch} mm/px",          "active_w / res_w"],
            ["PPI",                f"{ppi}",                  "√(rx²+ry²) / dia_in"],
            ["Lid Cutout W",       f"{dc.get('w',0)} mm",     "active_w − 2×bezel(0.8mm)"],
            ["Lid Cutout H",       f"{dc.get('h',0)} mm",     "active_h − 2×bezel(0.8mm)"],
            ["Cutout Center X",    f"{dc.get('x',0)+dc.get('w',0)/2:.1f} mm",
             "pcb_offset_x + board_w×0.33"],
            ["Cutout Center Y",    f"{dc.get('y',0)+dc.get('h',0)/2:.1f} mm",
             "pcb_offset_y + board_h/2"],
            ["Recess Depth",       "1.2 mm",                  "constant"],
            ["Bezel Lip",          "0.8 mm",                  "constant"],
        ]
        tbl = Table(rows, colWidths=[48*mm, 35*mm, INNER_W-83*mm])
        tbl.setStyle(dark_table_style())
        return [
            Paragraph("3. Display Mathematics", st["h1"]),
            Paragraph(
                f"The {dia}\" display ({rw}×{rh}px) achieves <b>{ppi} PPI</b> with a "
                f"pixel pitch of <b>{pitch} mm</b>. The lid cutout of "
                f"<b>{dc.get('w',0)}×{dc.get('h',0)} mm</b> provides a 0.8 mm bezel "
                f"lip around the active area for mechanical retention.",
                st["body"]
            ),
            tbl,
            PageBreak(),
        ]

    # ── 4. Pinout Reference ──────────────────────────────────────
    def _section_pinout(self):
        st = self.st
        p  = self.pinout
        story = [Paragraph("4. Pinout Reference — GPIO Header Map", st["h1"])]

        # GPIO header diagram
        gpio_map    = p.get("gpio_map", [])
        claimed_map = {pin["gpio_key"]: pin.get("claimed_by")
                       for pin in gpio_map if pin.get("claimed_by")}
        story.append(GPIODiagram(gpio_map, claimed_map, width=INNER_W))
        story.append(Spacer(1, 4*mm))

        # Per-module wiring tables
        for mod_key, tbl_data in p.get("wiring_tables", {}).items():
            story.append(Paragraph(tbl_data["label"], st["h2"]))
            story.append(Paragraph(
                f"Interface: <b>{tbl_data['interface']}</b>  ·  "
                f"Voltage: <b>{tbl_data['voltage']} V</b>",
                st["note"]
            ))
            rows = [["Module Pin","BCM","Phys","GPIO Name","Wire","Req","Note"]]
            for row in tbl_data["rows"]:
                rows.append([
                    row["module_pin"],
                    str(row["bcm"]),
                    str(row["phys_pin"]),
                    Paragraph(row["gpio_name"], st["cell"]),
                    row["wire_name"],
                    "✓" if row["required"] else "opt",
                    Paragraph(row["note"], st["cell"]),
                ])
            t = Table(rows,
                      colWidths=[18*mm,10*mm,10*mm,40*mm,22*mm,8*mm,
                                 INNER_W-108*mm])
            t.setStyle(dark_table_style())
            story.append(t)

            # Extra signals (joystick directions, etc.)
            extras = tbl_data.get("extra_signals", {})
            if extras:
                story.append(Paragraph("Additional Signals:", st["h3"]))
                ex_rows = [["Signal","BCM","Wire","Note"]]
                for sig, info in extras.items():
                    ex_rows.append([
                        sig,
                        str(info.get("gpio_key","—")),
                        info.get("role","—"),
                        Paragraph(info.get("note",""), st["cell"]),
                    ])
                t2 = Table(ex_rows,
                           colWidths=[22*mm,20*mm,18*mm,INNER_W-60*mm])
                t2.setStyle(dark_table_style())
                story.append(t2)

            story.append(Spacer(1, 2*mm))

        story.append(PageBreak())
        return story

    # ── 5. Wiring Diagram ────────────────────────────────────────
    def _section_wiring(self):
        st  = self.st
        p   = self.pinout
        wc  = p.get("wire_colours", {})
        story = [Paragraph("5. Wiring Diagram & Wire Colour Code", st["h1"])]

        # Wire colour legend table
        story.append(Paragraph("Wire Colour Reference (IEC 60757 + Electronics Convention)", st["h2"]))
        rows = [["Signal Role","Colour","Hex","Usage Note"]]
        WIRE_NOTES = {
            "3V3":  "3.3V power rail — all HAT logic",
            "5V":   "5V power — boost converter output, NOT GPIO",
            "GND":  "Common ground — all modules share",
            "MOSI": "SPI data master→slave (DIN on display)",
            "MISO": "SPI data slave→master (ADC output)",
            "SCLK": "SPI clock — shared by all SPI devices",
            "CS":   "Chip Select — unique per SPI device (active LOW)",
            "DC":   "Data/Command select for display (HIGH=data)",
            "RST":  "Hardware reset — active LOW, pulse ≥10µs",
            "BL":   "Backlight PWM — GPIO24 via transistor",
            "SCL":  "I2C clock",
            "SDA":  "I2C data",
            "TX":   "UART transmit",
            "RX":   "UART receive",
            "GPIO": "General purpose I/O (button, LED, etc.)",
        }
        for role, data in wc.items():
            rows.append([
                role,
                data["name"],
                f"#{data['hex']}",
                WIRE_NOTES.get(role, ""),
            ])
        t = Table(rows, colWidths=[18*mm, 25*mm, 18*mm, INNER_W-61*mm])
        t.setStyle(dark_table_style())

        # Add colour cell to left column
        for i, row in enumerate(rows[1:], 1):
            hex_val = row[2].lstrip("#")
            try:
                fill = HexColor(f"#{hex_val}")
            except Exception:
                fill = C_BORDER
            t.setStyle(TableStyle([
                ("BACKGROUND", (0,i),(0,i), fill),
                ("TEXTCOLOR",  (0,i),(0,i), C_WHITE),
                ("FONTNAME",   (0,i),(0,i), "Helvetica-Bold"),
            ]))

        story.append(t)
        story.append(Spacer(1, 4*mm))

        # Wiring safety rules — module-specific rules appear only
        # when the module is in THIS design (Requirement 10).
        story.append(Paragraph("Critical Wiring Rules:", st["h2"]))
        _sel = set(p.get("selected_modules") or [])
        rules = [
            "NEVER connect 5V to any HAT GPIO signal line — maximum 3.3V.",
            "ALWAYS wire all GND lines to a common ground point before powering up.",
            "CS pins MUST be HIGH (deasserted) on ALL SPI devices before changing the active device.",
            "RST pulse: hold LOW ≥ 10µs, then HIGH; wait ≥ 120ms before first SPI command.",
            "SPI MOSI/MISO/SCLK are shared — only the CS line distinguishes devices.",
            "I2C pull-up resistors (4.7kΩ to 3.3V) required when adding non-HAT I2C devices.",
        ]
        if "neopixel_ws2812b" in _sel:
            rules.append("NeoPixel/WS2812B: add 300–500Ω on data line; 1000µF cap across 5V/GND at strip.")
        if "lipo_tp4056" in _sel:
            rules.append("Battery: NEVER connect backwards. Always use a battery with a protection PCB.")
        for r in rules:
            story.append(Paragraph(f"⚠  {r}", st["warn"]))

        story.append(PageBreak())
        return story

    # ── 6. Firmware Architecture ─────────────────────────────────
    # Requirement 10: the firmware section describes THIS design's
    # firmware. The architecture payload is the full framework
    # reference (every driver/task/event the firmware knows); the
    # report keeps only what the design's selected modules use,
    # plus the always-present core. With no module information in
    # the payload, nothing is filtered (legacy payloads).
    _FW_DRIVER_SCOPE = {
        "display_st7789":  {"waveshare_lcd_hat_st7789"},
        "display_ssd1306": {"i2c_oled_ssd1306"},
        "backlight_pwm":   {"waveshare_lcd_hat_st7789"},
        "buttons":         {"waveshare_lcd_hat_st7789", "tactile_button"},
        "joystick_adc":    {"analog_joystick_mcp3008"},
        "imu_mpu6050":     {"imu_mpu6050"},
        "audio_pwm":       {"audio_jack_3_5mm"},
        "neopixel":        {"neopixel_ws2812b"},
        "sd_card":         {"microsd_spi"},
        "battery_monitor": {"lipo_tp4056"},
        "spi_bus":         {"waveshare_lcd_hat_st7789",
                            "analog_joystick_mcp3008", "microsd_spi"},
        "i2c_bus":         {"i2c_oled_ssd1306", "imu_mpu6050"},
    }
    _FW_TASK_SCOPE = {
        "display_render_task": {"waveshare_lcd_hat_st7789",
                                "i2c_oled_ssd1306"},
        "input_poll_task":     {"waveshare_lcd_hat_st7789",
                                "tactile_button",
                                "analog_joystick_mcp3008"},
        "battery_task":        {"lipo_tp4056"},
        "imu_task":            {"imu_mpu6050"},
    }
    _FW_EVENT_SCOPE = {
        "ButtonPressed":  {"waveshare_lcd_hat_st7789", "tactile_button"},
        "ButtonReleased": {"waveshare_lcd_hat_st7789", "tactile_button"},
        "JoystickMoved":  {"waveshare_lcd_hat_st7789",
                           "analog_joystick_mcp3008"},
        "DisplayReady":   {"waveshare_lcd_hat_st7789",
                           "i2c_oled_ssd1306"},
        "DisplayFrame":   {"waveshare_lcd_hat_st7789",
                           "i2c_oled_ssd1306"},
        "BatteryUpdate":  {"lipo_tp4056"},
        "IMUData":        {"imu_mpu6050"},
        "AudioPlay":      {"audio_jack_3_5mm"},
        "NeopixelSet":    {"neopixel_ws2812b"},
    }

    def _fw_scoped(self, scope_map, name, selected):
        """True when `name` is core (unscoped) or its scope
        intersects the design's selected modules."""
        need = scope_map.get(name)
        return need is None or bool(need & selected)

    def _section_firmware(self):
        st = self.st
        fw = self.fw
        selected = set(self.pinout.get("selected_modules") or [])
        story = [Paragraph("6. Firmware Architecture & Boot Sequence", st["h1"])]

        story.append(Paragraph(
            "The firmware uses a <b>cooperative asyncio</b> scheduler on a single thread. "
            "All hardware initialisation follows a strict 11-phase boot sequence. "
            "Inter-module communication occurs exclusively through a typed "
            "<b>EventBus</b> — no direct driver-to-driver function calls after boot completes.",
            st["body"]
        ))

        # Boot phases
        story.append(Paragraph("Boot Sequence", st["h2"]))
        rows = [["Phase","Name","Delay","Gate"]]
        for p in fw.get("boot_phases",[]):
            if (selected and p["name"] == "BATTERY_MONITOR_START"
                    and "lipo_tp4056" not in selected):
                continue  # wired design: no battery monitor phase
            rows.append([
                str(p["phase"]),
                p["name"],
                f"{p['delay_ms']}ms",
                Paragraph(p["gate"], st["cell"]),
            ])
        t = Table(rows, colWidths=[12*mm,50*mm,15*mm,INNER_W-77*mm])
        t.setStyle(dark_table_style())
        story.append(t)
        story.append(Spacer(1,3*mm))

        # Driver load order
        story.append(Paragraph("Driver Load Order (Topological)", st["h2"]))
        order = fw.get("driver_load_order",[])
        if selected:
            order = [d for d in order
                     if self._fw_scoped(self._FW_DRIVER_SCOPE, d, selected)]
        order_text = "  →  ".join(order)
        story.append(Paragraph(order_text, st["mono_sm"]))
        story.append(Spacer(1,3*mm))

        # Scheduler tasks
        story.append(Paragraph("Cooperative Task Scheduler", st["h2"]))
        sched = fw.get("scheduler",{})
        rows2 = [[Paragraph(h, st["cellh"]) for h in
                  ["Task","Interval","Priority","CPU Budget","Description"]]]
        for t_data in sched.get("tasks",[]):
            if selected and not self._fw_scoped(
                    self._FW_TASK_SCOPE, t_data["name"], selected):
                continue
            rows2.append([
                t_data["name"],
                f"{t_data['interval_ms']}ms",
                t_data["priority"],
                f"{t_data['cpu_budget_pct']}%",
                Paragraph(t_data["description"], st["cell"]),
            ])
        t2 = Table(rows2,
                   colWidths=[38*mm,15*mm,15*mm,17*mm,INNER_W-85*mm])
        t2.setStyle(dark_table_style())
        story.append(t2)
        story.append(Spacer(1,3*mm))

        # EventBus events
        story.append(Paragraph("EventBus Message Types", st["h2"]))
        rows3 = [["Event Type","Fields"]]
        for ev, data in fw.get("event_types",{}).items():
            if selected and not self._fw_scoped(
                    self._FW_EVENT_SCOPE, ev, selected):
                continue
            rows3.append([ev, Paragraph(
                ", ".join(data["fields"]) if data["fields"] else "—",
                st["cell"])])
        t3 = Table(rows3, colWidths=[40*mm, INNER_W-40*mm])
        t3.setStyle(dark_table_style())
        story.append(t3)

        story.append(PageBreak())
        return story

    # ── 7. Assembly Guide ────────────────────────────────────────
    def _section_assembly(self):
        st = self.st
        a  = self.asm
        story = [Paragraph("7. Assembly Guide & Bill of Materials", st["h1"])]

        # Hardware BOM
        story.append(Paragraph("Hardware BOM", st["h2"]))
        fas  = a.get("bom_hardware",{}).get("fasteners",{})
        ws   = a.get("bom_hardware",{}).get("waveshare_hat",{})
        rows = [["Item","Qty","Purpose"]]
        for key, d in (list(fas.items()) + list(ws.items())):
            name = (d.get("description") or d.get("name")
                    or key.replace("_", " "))
            rows.append([
                Paragraph(name, st["cell"]),
                str(d.get("qty","—")),
                Paragraph(d.get("purpose",""), st["cell"]),
            ])
        t = Table(rows, colWidths=[68*mm,12*mm,INNER_W-80*mm])
        t.setStyle(dark_table_style())
        story.append(t)
        story.append(Spacer(1,3*mm))

        # Filament BOM
        story.append(Paragraph("Filament & Print Cost Estimate", st["h2"]))
        fil = a.get("bom_filament",{})
        rows2 = [
            ["Metric","Value"],
            ["Estimated Volume",   f"{fil.get('estimated_volume_cm3',0)} cm³"],
            ["Estimated Mass",     f"{fil.get('estimated_mass_g',0)} g"],
            ["Material",          fil.get("recommended_material","PETG")],
            ["Estimated Cost",    f"${fil.get('estimated_cost_usd',0)} USD"],
            ["Estimated Print Time",f"{fil.get('estimated_print_hours',0)} hours"],
        ]
        t2 = Table(rows2, colWidths=[50*mm, INNER_W-50*mm])
        t2.setStyle(dark_table_style())
        story.append(t2)
        story.append(Spacer(1,3*mm))

        # Assembly steps — each with its rendered image when one
        # exists for THIS design (Requirement 6b: the images are the
        # visual SOP). Lookup is strictly inside this design's
        # hash-keyed image directory.
        story.append(Paragraph("Step-by-Step Assembly", st["h2"]))
        steps = a.get("assembly_steps",[])
        img_paths = []
        for s in steps:
            p = None
            if self.images_dir:
                try:
                    fname = f"step_{int(s.get('step', 0)):02d}.png"
                except (TypeError, ValueError):
                    fname = None
                if fname:
                    cand = Path(self.images_dir) / fname
                    if cand.exists():
                        p = cand
            img_paths.append(p)
        have = sum(1 for p in img_paths if p)
        if steps and have == 0:
            story.append(Paragraph(
                "<i>Assembly images not generated for this design yet "
                "— generate them from the Report tab "
                f"(requires Blender). Design: {self.design_hash}</i>",
                st["body"]))
            story.append(Spacer(1,2*mm))
        elif steps and have < len(steps):
            story.append(Paragraph(
                f"<i>Assembly images: {have} of {len(steps)} steps "
                "have an image for this design — regenerate them "
                "from the Report tab to complete the set "
                "(requires Blender).</i>",
                st["body"]))
            story.append(Spacer(1,2*mm))
        for step, img in zip(steps, img_paths):
            story.append(Paragraph(
                f"<b>Step {step['step']}: {step['title']}</b>",
                st["h3"]
            ))
            if img:
                story.append(Image(str(img), width=110*mm,
                                   height=82.5*mm, hAlign="CENTER"))
                story.append(Spacer(1,2*mm))
            story.append(Paragraph(step["detail"], st["body"]))

        story.append(PageBreak())
        return story

    # ── 8. Print Settings ────────────────────────────────────────
    def _section_print_settings(self):
        st = self.st
        a  = self.asm
        story = [Paragraph("8. 3D Print Settings", st["h1"])]
        for part, settings in a.get("print_settings",{}).items():
            story.append(Paragraph(part.replace("_"," ").title(), st["h2"]))
            rows = [["Parameter","Value"]]
            for k, v in settings.items():
                rows.append([k.replace("_"," "), str(v)])
            t = Table(rows, colWidths=[50*mm, INNER_W-50*mm])
            t.setStyle(dark_table_style())
            story.append(t)
            story.append(Spacer(1,2*mm))
        story.append(PageBreak())
        return story

    # ── 9. Safety Checklist ──────────────────────────────────────
    def _section_safety(self):
        st = self.st
        p  = self.pinout
        story = [Paragraph("9. Safety Checklist", st["h1"])]
        story.append(Paragraph(
            "Complete each item before powering on the assembled device.",
            st["body"]
        ))
        for item in p.get("safety_checklist",[]):
            icon = "⚠" if item["status"]=="critical" else "□"
            style = st["warn"] if item["status"]=="critical" else st["body"]
            story.append(Paragraph(f"{icon}  {item['check']}", style))

        # Conflicts
        conflicts = p.get("conflicts",[])
        if conflicts:
            story.append(Paragraph("⚠ GPIO Conflicts Detected:", st["warn"]))
            for c in conflicts:
                story.append(Paragraph(f"  {c['note']}", st["warn"]))
        else:
            story.append(Paragraph("✓ No GPIO conflicts detected.", st["safe"]))

        # Power budget
        pb = p.get("power_budget",{})
        story.append(Spacer(1,3*mm))
        story.append(Paragraph("Power Budget Summary:", st["h2"]))
        rows = [
            ["Item","Value"],
            ["Host board TDP",     f"{pb.get('host_tdp_ma',0)} mA"],
            ["All modules total",  f"{pb.get('modules_total_ma',0)} mA"],
            ["Grand total",        f"{pb.get('grand_total_ma',0)} mA = {pb.get('grand_total_w',0)} W"],
            ["Recommended PSU",    f"{pb.get('recommended_psu_a',0)} A @ 5V (30% headroom)"],
        ]
        t = Table(rows, colWidths=[50*mm, INNER_W-50*mm])
        t.setStyle(dark_table_style())
        story.append(t)

        story.append(PageBreak())
        return story

    # ── 10. Signature ────────────────────────────────────────────
    def _section_signature(self):
        st = self.st
        rows = [
            ["Field",""],
            ["Device Name:",     "___________________________________"],
            ["Designer:",        "___________________________________"],
            ["Date:",            "___________________________________"],
            ["Revision:",        "___________________________________"],
            ["Print Material:",  "___________________________________"],
            ["Host Board:",      "___________________________________"],
            ["Notes:",""],
            ["",""],
            ["",""],
            ["",""],
            ["Approved By:",     "___________________________________"],
            ["Signature:",       "___________________________________"],
        ]
        t = Table(rows, colWidths=[45*mm, INNER_W-45*mm])
        t.setStyle(TableStyle([
            ("TEXTCOLOR",   (0,0),(-1,-1), C_TEXT),
            ("FONTNAME",    (0,0),(-1,-1), "Helvetica"),
            ("FONTSIZE",    (0,0),(-1,-1), 9),
            ("GRID",        (0,0),(-1,-1), 0.3, C_BORDER),
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_SURFACE, C_CARD]),
            ("LEFTPADDING",  (0,0),(-1,-1), 5),
            ("TOPPADDING",   (0,0),(-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("BACKGROUND",   (0,0),(-1,0), C_BG),
            ("TEXTCOLOR",    (0,0),(-1,0), C_ACCENT),
            ("FONTNAME",     (0,0),(-1,0), "Helvetica-Bold"),
        ]))
        return [
            Paragraph("10. Notes & Sign-Off", st["h1"]),
            Paragraph(
                "Complete this section before archiving or sending the design to production.",
                st["body"]
            ),
            t,
            Spacer(1,8*mm),
            Paragraph(
                "Generated by Handheld 3D Shell Builder  |  "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                st["note"]
            ),
        ]
