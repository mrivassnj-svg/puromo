"""
dimension_math.py
-----------------
Mathematical engine for computing all 3D shell dimensions from a DeviceSpec.

Covers:
  - Board envelope with tolerance/clearance
  - Display cutout geometry (Waveshare LCD HAT specific math included)
  - GPIO / connector clearance channels
  - Mounting boss positions and dimensions
  - Shell wall thickness calculations
  - Button / joystick aperture math
  - PCB standoff pillar geometry
  - Frame rib placement for structural integrity
"""

import math
import re
from dataclasses import dataclass, field
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Tolerance constants (all in mm unless noted)
# ---------------------------------------------------------------------------
PCB_CLEARANCE          = 0.5    # air gap around PCB inside shell
WALL_THICKNESS         = 2.0    # default shell outer wall
BOTTOM_FLOOR           = 2.5    # floor thickness beneath PCB
LID_THICKNESS          = 1.8    # top lid plate thickness
STANDOFF_HEIGHT        = 3.0    # PCB standoff pillar height above floor
STANDOFF_OD            = 5.0    # standoff outer diameter
STANDOFF_HOLE_D        = 2.9    # M2.5-ish clearance through standoff
SNAP_FIT_DEPTH         = 1.2    # snap-fit ledge depth
SNAP_FIT_HEIGHT        = 2.5    # snap-fit tab height
BUTTON_APERTURE_CLEAR  = 0.4    # clearance around button cap in aperture
DISPLAY_BEZEL          = 0.8    # bezel lip around display cutout
DISPLAY_RECESS_DEPTH   = 1.2    # how deep display window is recessed into lid
CORNER_RADIUS          = 3.0    # outer shell corner radius
HINGE_DIAMETER         = 4.0    # hinge barrel diameter (if clamshell)
RIB_THICKNESS          = 1.2    # internal structural rib thickness
RIB_SPACING            = 20.0   # max mm between ribs
PORT_CLEARANCE         = 1.0    # extra clearance around USB/HDMI cutouts

# Waveshare HAT-specific tolerances
# WS_HAT_STACK_HEIGHT_MM is a FALLBACK only — the math engine reads
# gpio_header_mated_gap_mm directly from DeviceSpec when available.
WS_HAT_STACK_HEIGHT_MM = 11.5   # fallback: standard 11mm header mated gap
WS_HAT_DISPLAY_BEZEL_X = 1.5   # Waveshare side bezel on 1.3"/2" hats
WS_HAT_DISPLAY_BEZEL_Y = 1.5
COMPONENT_CLEARANCE    = 1.5    # safety margin above tallest component


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Point2D:
    x: float
    y: float

    def __iter__(self):
        return iter((self.x, self.y))

    def translate(self, dx: float, dy: float) -> "Point2D":
        return Point2D(self.x + dx, self.y + dy)


@dataclass
class Rect:
    x: float = 0.0    # bottom-left X
    y: float = 0.0    # bottom-left Y
    w: float = 0.0    # width
    h: float = 0.0    # height
    depth: float = 0  # optional Z depth for cutouts

    @property
    def center(self) -> Point2D:
        return Point2D(self.x + self.w / 2, self.y + self.h / 2)

    @property
    def corners(self) -> List[Point2D]:
        return [
            Point2D(self.x,          self.y),
            Point2D(self.x + self.w, self.y),
            Point2D(self.x + self.w, self.y + self.h),
            Point2D(self.x,          self.y + self.h),
        ]


@dataclass
class Circle:
    cx: float = 0.0
    cy: float = 0.0
    r: float = 0.0
    depth: float = 0


@dataclass
class ShellGeometry:
    """Complete computed shell geometry ready for the Blender generator."""

    # Outer shell envelope
    shell_width_mm:  float = 0.0
    shell_height_mm: float = 0.0
    shell_depth_mm:  float = 0.0
    corner_radius:   float = CORNER_RADIUS

    # PCB placement inside shell
    pcb_offset_x:    float = 0.0  # offset from inner-left wall
    pcb_offset_y:    float = 0.0  # offset from inner-bottom wall
    pcb_z_from_floor: float = STANDOFF_HEIGHT

    # Display cutout (in lid)
    display_cutout:  Rect = field(default_factory=Rect)

    # Mounting bosses (circles in XY, height = standoff height)
    mounting_bosses: List[Circle] = field(default_factory=list)

    # Button apertures
    button_apertures: List[Circle] = field(default_factory=list)

    # Joystick aperture
    joystick_aperture: Circle = field(default_factory=lambda: Circle(0, 0, 0))

    # Snap-fit features
    snap_fit_positions: List[Point2D] = field(default_factory=list)

    # Structural ribs (each is a Rect in XY cross-section)
    ribs: List[Rect] = field(default_factory=list)

    # Port cutouts on edges
    port_cutouts: List[Rect] = field(default_factory=list)

    # Computed display math
    pixel_pitch_mm:  float = 0.0
    ppi:             float = 0.0
    display_diagonal_mm: float = 0.0

    # Camera aperture (in lid) — "the eyes" (framework R2a). Empty (r=0)
    # unless the spec carries a camera_zone.
    camera_aperture: Circle = field(default_factory=lambda: Circle(0, 0, 0))

    # Battery bay footprint in the bottom case (XY rect, depth = pack
    # thickness). Empty (w=0) unless the spec selects a battery.
    battery_bay: Rect = field(default_factory=Rect)

    # Overlap map (framework R5): physical interferences between placed
    # elements, each {a, b, cx, cy, r} in shell coordinates. The pre-print
    # safeguard renders these in the reserved conflict colour.
    overlaps: List[dict] = field(default_factory=list)

    # Assembly notes
    assembly_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core math engine
# ---------------------------------------------------------------------------

class DimensionMath:
    """
    Computes ShellGeometry from a DeviceSpec dict (or DeviceSpec dataclass).
    All math is deterministic and unit-tested.
    """

    def __init__(self, spec: dict):
        self.s = spec if isinstance(spec, dict) else vars(spec)

    # -----------------------------------------------------------------------
    # Public
    # -----------------------------------------------------------------------

    def compute(self) -> ShellGeometry:
        g = ShellGeometry()
        self._compute_envelope(g)
        self._compute_pcb_placement(g)
        self._compute_display_cutout(g)
        self._compute_display_optics(g)
        self._compute_mounting_bosses(g)
        self._compute_button_apertures(g)
        self._compute_joystick_aperture(g)
        self._compute_snap_fits(g)
        self._compute_ribs(g)
        self._compute_camera_aperture(g)
        self._compute_battery_bay(g)
        g.overlaps = self._compute_overlaps(g)
        self._generate_assembly_notes(g)
        return g

    # -----------------------------------------------------------------------
    # Step 1: outer shell envelope
    # -----------------------------------------------------------------------

    def _compute_envelope(self, g: ShellGeometry):
        bw = self._f("board_width_mm")
        bh = self._f("board_height_mm")
        bt = self._f("board_thickness_mm", 1.6)

        # Width and height: PCB + bilateral clearance + 2× wall
        g.shell_width_mm  = round(bw + 2 * PCB_CLEARANCE + 2 * WALL_THICKNESS, 3)
        g.shell_height_mm = round(bh + 2 * PCB_CLEARANCE + 2 * WALL_THICKNESS, 3)

        # ---------------------------------------------------------------
        # Shell DEPTH — derived from actual installed header geometry:
        #
        #   floor_t + standoff_h + host_pcb_t + header_mated_gap + hat_pcb_t
        #           + max(hat_component_h, hat_header_above) + clearance + lid_t
        #
        # gpio_header_mated_gap_mm is the board-to-board gap created by the
        # specific header variant (5.5mm low-profile → 11.5mm standard → 15mm tall).
        # tallest_component_mm is the highest part above the HAT PCB surface
        # (e.g. joystick nub at 8mm, tactile button at 5mm).
        # We take whichever is taller as the cavity we must clear.
        # ---------------------------------------------------------------
        mated_gap   = self._f("gpio_header_mated_gap_mm", WS_HAT_STACK_HEIGHT_MM)
        hat_above   = self._f("gpio_header_above_mm", 8.5)   # header housing on HAT side
        tallest_comp= self._f("tallest_component_mm", 0.0)

        # Height above the HAT PCB that the lid must clear
        above_hat_pcb = max(hat_above, tallest_comp) + COMPONENT_CLEARANCE

        # Total depth stack:
        #  floor → standoff → host PCB → mated gap → HAT PCB → tallest above HAT → lid
        g.shell_depth_mm = round(
            BOTTOM_FLOOR
            + STANDOFF_HEIGHT          # printed standoff pillar
            + bt                        # host PCB thickness (RPi etc.)
            + mated_gap                 # board-to-board gap (header-dependent)
            + bt                        # HAT PCB thickness (same default 1.6mm)
            + above_hat_pcb             # tallest component/header above HAT surface
            + LID_THICKNESS,
            3
        )

        header_variant = self.s.get("gpio_header_variant", "standard_11mm")
        # Human-readable in user-facing notes (Requirement 10):
        # 'standard_11mm' → 'Standard 11 mm'.
        hv_human = re.sub(r"(\d+)mm\b", r"\1 mm",
                          header_variant.replace("_", " ")).capitalize()
        g.assembly_notes.append(
            f"Shell envelope: {g.shell_width_mm} × {g.shell_height_mm} × {g.shell_depth_mm} mm"
        )
        g.assembly_notes.append(
            f"Header variant: {hv_human}  "
            f"mated gap={mated_gap} mm  "
            f"tallest above HAT={round(above_hat_pcb - COMPONENT_CLEARANCE, 1)} mm  "
            f"(+{COMPONENT_CLEARANCE} mm clearance)"
        )

    # -----------------------------------------------------------------------
    # Step 2: PCB placement offset
    # -----------------------------------------------------------------------

    def _compute_pcb_placement(self, g: ShellGeometry):
        g.pcb_offset_x     = WALL_THICKNESS + PCB_CLEARANCE
        g.pcb_offset_y     = WALL_THICKNESS + PCB_CLEARANCE
        g.pcb_z_from_floor = BOTTOM_FLOOR + STANDOFF_HEIGHT

    # -----------------------------------------------------------------------
    # Step 3: display cutout in lid
    # -----------------------------------------------------------------------

    def _compute_display_cutout(self, g: ShellGeometry):
        dw = self._f("display_width_mm")
        dh = self._f("display_height_mm")
        bw = self._f("board_width_mm")
        bh = self._f("board_height_mm")

        if dw == 0 or dh == 0:
            return  # no display

        # Display is typically centred on board (Waveshare HATs: adjust for joystick side)
        # Waveshare 1.3" HAT: display is offset toward the GPIO header side
        vendor = self.s.get("vendor", "").lower()
        if "waveshare" in vendor or self.s.get("controller_ic", "") in ("ST7789", "ST7735"):
            # Display on left portion of board; joystick + buttons on right
            # Board coord: display centered at ~x=bw*0.33, y=bh/2
            disp_cx_on_board = bw * 0.33
            disp_cy_on_board = bh / 2.0
        else:
            disp_cx_on_board = bw / 2.0
            disp_cy_on_board = bh / 2.0

        # Map board coord → shell inner coord → shell outer coord
        inner_cx = g.pcb_offset_x + disp_cx_on_board
        inner_cy = g.pcb_offset_y + disp_cy_on_board

        # Cutout = display active area minus bezel
        cutout_w = dw - 2 * DISPLAY_BEZEL
        cutout_h = dh - 2 * DISPLAY_BEZEL

        g.display_cutout = Rect(
            x=round(inner_cx - cutout_w / 2, 3),
            y=round(inner_cy - cutout_h / 2, 3),
            w=round(cutout_w, 3),
            h=round(cutout_h, 3),
            depth=round(LID_THICKNESS + DISPLAY_RECESS_DEPTH, 3),
        )

        g.assembly_notes.append(
            f"Display cutout: {g.display_cutout.w}×{g.display_cutout.h} mm "
            f"at ({g.display_cutout.x},{g.display_cutout.y}) recess {g.display_cutout.depth} mm"
        )

    # -----------------------------------------------------------------------
    # Step 4: display optics math (pixel pitch, PPI, diagonal)
    # -----------------------------------------------------------------------

    def _compute_display_optics(self, g: ShellGeometry):
        rw  = self._i("resolution_w")
        rh  = self._i("resolution_h")
        dw  = self._f("display_width_mm")
        dh  = self._f("display_height_mm")
        dia = self._f("display_diagonal_inch")

        if dw and dh:
            g.display_diagonal_mm = round(math.sqrt(dw**2 + dh**2), 4)

        if dw and rw:
            g.pixel_pitch_mm = round(dw / rw, 6)

        if dia and rw and rh:
            diag_px = math.sqrt(rw**2 + rh**2)
            g.ppi   = round(diag_px / dia, 2)
        elif g.display_diagonal_mm and rw and rh:
            diag_px = math.sqrt(rw**2 + rh**2)
            g.ppi   = round(diag_px / (g.display_diagonal_mm / 25.4), 2)

        if g.ppi:
            g.assembly_notes.append(
                f"Display optics: {rw}×{rh}px  |  pitch={g.pixel_pitch_mm} mm  |  {g.ppi} PPI"
            )

    # -----------------------------------------------------------------------
    # Step 5: mounting boss geometry
    # -----------------------------------------------------------------------

    def _compute_mounting_bosses(self, g: ShellGeometry):
        hole_d   = self._f("mounting_hole_diameter_mm", STANDOFF_HOLE_D)
        positions = self.s.get("mounting_hole_positions_mm", [])

        if not positions:
            # Autogenerate corners
            bw = self._f("board_width_mm")
            bh = self._f("board_height_mm")
            margin = 3.5
            positions = [
                (margin, margin),
                (bw - margin, margin),
                (margin, bh - margin),
                (bw - margin, bh - margin),
            ]

        for px, py in positions:
            # Map PCB-local coord → shell coord
            sx = round(g.pcb_offset_x + px, 3)
            sy = round(g.pcb_offset_y + py, 3)
            g.mounting_bosses.append(
                Circle(cx=sx, cy=sy, r=round(STANDOFF_OD / 2, 3), depth=STANDOFF_HEIGHT)
            )

        g.assembly_notes.append(
            f"{len(g.mounting_bosses)} mounting bosses, Ø{STANDOFF_OD} mm OD, "
            f"Ø{hole_d} mm through-hole, H={STANDOFF_HEIGHT} mm"
        )

    # -----------------------------------------------------------------------
    # Step 6: button apertures
    # -----------------------------------------------------------------------

    def _compute_button_apertures(self, g: ShellGeometry):
        buttons = self.s.get("button_positions_mm", [])
        # Standard tactile button cap diameter ~4 mm + clearance
        btn_cap_r = 2.0 + BUTTON_APERTURE_CLEAR

        for entry in buttons:
            bx, by = entry[0], entry[1]
            label   = entry[2] if len(entry) > 2 else ""
            sx = round(g.pcb_offset_x + bx, 3)
            sy = round(g.pcb_offset_y + by, 3)
            g.button_apertures.append(
                Circle(cx=sx, cy=sy, r=btn_cap_r, depth=LID_THICKNESS)
            )

        if g.button_apertures:
            g.assembly_notes.append(
                f"{len(g.button_apertures)} button apertures, Ø{btn_cap_r*2:.1f} mm each"
            )

    # -----------------------------------------------------------------------
    # Step 7: joystick aperture
    # -----------------------------------------------------------------------

    def _compute_joystick_aperture(self, g: ShellGeometry):
        jc = self.s.get("joystick_center_mm")
        jd = self._f("joystick_diameter_mm", 0.0)

        if jc and jd:
            jx, jy = jc[0], jc[1]
            # Map to shell coord
            sx = round(g.pcb_offset_x + jx, 3)
            sy = round(g.pcb_offset_y + jy, 3)
            g.joystick_aperture = Circle(
                cx=sx, cy=sy,
                r=round(jd / 2 + BUTTON_APERTURE_CLEAR, 3),
                depth=LID_THICKNESS
            )
            g.assembly_notes.append(
                f"Joystick aperture: Ø{jd + 2*BUTTON_APERTURE_CLEAR:.1f} mm "
                f"at ({sx},{sy})"
            )

    # -----------------------------------------------------------------------
    # Step 8: snap-fit ledge positions
    # -----------------------------------------------------------------------

    def _compute_snap_fits(self, g: ShellGeometry):
        # 1 snap per long side, 2 per short side for rectangular shells
        sw = g.shell_width_mm
        sh = g.shell_height_mm

        positions = [
            Point2D(sw / 2,            0),             # bottom midpoint
            Point2D(sw / 2,            sh),             # top midpoint
            Point2D(0,                 sh / 3),         # left lower
            Point2D(0,                 2 * sh / 3),     # left upper
            Point2D(sw,                sh / 3),         # right lower
            Point2D(sw,                2 * sh / 3),     # right upper
        ]
        g.snap_fit_positions = [
            Point2D(round(p.x, 3), round(p.y, 3)) for p in positions
        ]
        g.assembly_notes.append(
            f"{len(g.snap_fit_positions)} snap-fit tabs  "
            f"(depth={SNAP_FIT_DEPTH} mm, height={SNAP_FIT_HEIGHT} mm)"
        )

    # -----------------------------------------------------------------------
    # Step 9: structural ribs
    # -----------------------------------------------------------------------

    def _compute_ribs(self, g: ShellGeometry):
        sw = g.shell_width_mm
        sh = g.shell_height_mm
        sd = g.shell_depth_mm

        # Horizontal ribs (span X axis)
        y = WALL_THICKNESS + RIB_SPACING
        while y < sh - WALL_THICKNESS:
            g.ribs.append(Rect(
                x=WALL_THICKNESS,
                y=round(y, 3),
                w=round(sw - 2 * WALL_THICKNESS, 3),
                h=RIB_THICKNESS,
                depth=round(sd * 0.6, 3),  # ribs go 60% of internal depth
            ))
            y += RIB_SPACING

        # Vertical ribs (span Y axis)
        x = WALL_THICKNESS + RIB_SPACING
        while x < sw - WALL_THICKNESS:
            g.ribs.append(Rect(
                x=round(x, 3),
                y=WALL_THICKNESS,
                w=RIB_THICKNESS,
                h=round(sh - 2 * WALL_THICKNESS, 3),
                depth=round(sd * 0.6, 3),
            ))
            x += RIB_SPACING

        if g.ribs:
            g.assembly_notes.append(
                f"{len(g.ribs)} internal ribs (t={RIB_THICKNESS} mm, spacing≤{RIB_SPACING} mm)"
            )

    # -----------------------------------------------------------------------
    # Step 10: compile all assembly notes into geometry
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Camera aperture — "the eyes" (framework R2a)
    # -----------------------------------------------------------------------

    CAMERA_ZONES = {
        "front_top":    "front-top edge",
        "front_bottom": "front-bottom edge",
        "lid_centre":   "lid centre",
    }

    def _compute_camera_aperture(self, g: ShellGeometry):
        """Camera lens aperture in the lid, from spec['camera_zone'].
        Zones are shell-coordinate mounting choices made in the Placement
        tab. The CSI-2 ribbon route is recorded as an assembly note; whether
        the chosen zone is SAFE is decided by the overlap map (R5)."""
        zone = self.s.get("camera_zone")
        if zone not in self.CAMERA_ZONES:
            return
        d = self._f("camera_aperture_d_mm", 11.0)
        sw, sh = g.shell_width_mm, g.shell_height_mm
        if zone == "front_top":
            cx, cy = sw / 2, sh - 10.0
        elif zone == "front_bottom":
            cx, cy = sw / 2, 10.0
        else:  # lid_centre
            cx, cy = sw / 2, sh / 2
        g.camera_aperture = Circle(round(cx, 2), round(cy, 2), round(d / 2, 2))
        # Ribbon run estimate: aperture centre → centre of the main board,
        # where its CSI connector sits on this class of board.
        bx = g.pcb_offset_x + self._f("board_width_mm") / 2
        by = g.pcb_offset_y + self._f("board_height_mm") / 2
        run = math.hypot(cx - bx, cy - by)
        g.assembly_notes.append(
            f"Camera (the eyes): aperture Ø{d} mm at the "
            f"{self.CAMERA_ZONES[zone]} ({cx:.1f}, {cy:.1f}) mm. CSI-2 "
            f"15-pin ribbon routes from the camera board to the main "
            f"board's CSI connector — straight-line run ≈ {run:.0f} mm; "
            "keep the ribbon clear of the battery bay, use a gentle bend "
            "(radius ≥ 5 mm), and do not crease it."
        )

    # -----------------------------------------------------------------------
    # Battery bay (framework R4) — footprint of the chosen pack in the
    # bottom case, using the same +1 mm clearance as modular BottomCase.
    # -----------------------------------------------------------------------

    def _compute_battery_bay(self, g: ShellGeometry):
        if not self.s.get("include_battery"):
            return
        bs = self.s.get("battery_size_mm") or [50, 20, 6]
        w = float(bs[0]) + 1.0
        h = float(bs[1]) + 1.0
        d = float(bs[2]) if len(bs) > 2 else 6.0
        x = (g.shell_width_mm - w) / 2
        y = WALL_THICKNESS + 1.0
        g.battery_bay = Rect(round(x, 2), round(y, 2), round(w, 2),
                             round(h, 2), d)

    # -----------------------------------------------------------------------
    # Overlap map (framework R5) — physical interferences between placed
    # elements, in shell XY coordinates. Layer rules: lid elements (display
    # cutout, buttons, joystick, camera) are checked against each other;
    # mounting bosses are full-height columns and are checked against
    # everything; the battery bay sits in the bottom case and is checked
    # against the bosses. Each overlap is {a, b, cx, cy, r}: the centre and
    # radius of the conflicting zone, for the reserved-colour render marker.
    # -----------------------------------------------------------------------

    @staticmethod
    def _ov_circle_circle(n1, c1, n2, c2):
        if c1.r <= 0 or c2.r <= 0:
            return None
        dist = math.hypot(c2.cx - c1.cx, c2.cy - c1.cy)
        if dist >= c1.r + c2.r:
            return None
        pen = c1.r + c2.r - dist
        return {"a": n1, "b": n2,
                "cx": round((c1.cx + c2.cx) / 2, 2),
                "cy": round((c1.cy + c2.cy) / 2, 2),
                "r": round(max(1.5, pen / 2 + 1.0), 2)}

    @staticmethod
    def _ov_circle_rect(nc, c, nr, rect):
        if c.r <= 0 or rect.w <= 0 or rect.h <= 0:
            return None
        nx = min(max(c.cx, rect.x), rect.x + rect.w)
        ny = min(max(c.cy, rect.y), rect.y + rect.h)
        dist = math.hypot(c.cx - nx, c.cy - ny)
        if dist >= c.r:
            return None
        return {"a": nc, "b": nr,
                "cx": round((c.cx + nx) / 2, 2),
                "cy": round((c.cy + ny) / 2, 2),
                "r": round(max(1.5, (c.r - dist) / 2 + 1.0), 2)}

    def _compute_overlaps(self, g: ShellGeometry) -> List[dict]:
        out = []

        def add(hit):
            if hit:
                out.append(hit)

        lid_circles = [(f"button_aperture_{i + 1}", c)
                       for i, c in enumerate(g.button_apertures)]
        if g.joystick_aperture.r > 0:
            lid_circles.append(("joystick_aperture", g.joystick_aperture))
        if g.camera_aperture.r > 0:
            lid_circles.append(("camera_aperture", g.camera_aperture))

        # Lid elements among themselves
        for i in range(len(lid_circles)):
            for j in range(i + 1, len(lid_circles)):
                add(self._ov_circle_circle(lid_circles[i][0],
                                           lid_circles[i][1],
                                           lid_circles[j][0],
                                           lid_circles[j][1]))
        # Lid elements vs the display cutout
        for name, c in lid_circles:
            add(self._ov_circle_rect(name, c, "display_cutout",
                                     g.display_cutout))
        # Bosses (full-height columns) vs everything placed
        for i, boss in enumerate(g.mounting_bosses):
            bn = f"mounting_boss_{i + 1}"
            for name, c in lid_circles:
                add(self._ov_circle_circle(bn, boss, name, c))
            add(self._ov_circle_rect(bn, boss, "display_cutout",
                                     g.display_cutout))
            add(self._ov_circle_rect(bn, boss, "battery_bay", g.battery_bay))
        return out

    def _generate_assembly_notes(self, g: ShellGeometry):
        # Notes are already appended inline by each step above.
        # This method serves as a final hook for any cross-step notes.
        vendor = self.s.get("vendor", "")
        ic     = self.s.get("controller_ic", "")
        if "waveshare" in str(vendor).lower() or ic in ("ST7789", "ST7735"):
            g.assembly_notes.append(
                "Waveshare HAT: GPIO 2×20 header forces 2.54 mm pitch constraint — "
                "do not reduce standoff height below GPIO header stack."
            )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _f(self, key: str, default: float = 0.0) -> float:
        return float(self.s.get(key, default) or default)

    def _i(self, key: str, default: int = 0) -> int:
        return int(self.s.get(key, default) or default)


# ---------------------------------------------------------------------------
# Standalone utility: Waveshare HAT math summary
# ---------------------------------------------------------------------------

def waveshare_hat_math_summary(hat_key: str = "1.3inch_lcd_hat") -> dict:
    """
    Returns a full mathematical breakdown of a Waveshare LCD HAT's
    critical dimensions for 3D shell design.
    """
    from crawler.device_crawler import WAVESHARE_LCD_HATS
    data = WAVESHARE_LCD_HATS.get(hat_key, {})
    if not data:
        return {"error": f"Unknown HAT key: {hat_key}"}

    rw  = data.get("resolution_w", 0)
    rh  = data.get("resolution_h", 0)
    dw  = data.get("display_width_mm", 0)
    dh  = data.get("display_height_mm", 0)
    dia = data.get("display_diagonal_inch", 0)
    bw  = data.get("board_width_mm", 0)
    bh  = data.get("board_height_mm", 0)

    pixel_pitch = round(dw / rw, 6) if (dw and rw) else 0
    diag_px     = math.sqrt(rw**2 + rh**2) if (rw and rh) else 0
    ppi         = round(diag_px / dia, 2) if dia else 0
    diag_mm     = round(math.sqrt(dw**2 + dh**2), 4) if (dw and dh) else 0

    shell_w = round(bw + 2 * PCB_CLEARANCE + 2 * WALL_THICKNESS, 3)
    shell_h = round(bh + 2 * PCB_CLEARANCE + 2 * WALL_THICKNESS, 3)
    shell_d = round(BOTTOM_FLOOR + STANDOFF_HEIGHT + 1.6 + WS_HAT_STACK_HEIGHT_MM + LID_THICKNESS, 3)

    cutout_w = round(dw - 2 * DISPLAY_BEZEL, 3)
    cutout_h = round(dh - 2 * DISPLAY_BEZEL, 3)

    # Waveshare 1.3" HAT display centering on board
    disp_cx_board = bw * 0.33
    disp_cy_board = bh / 2.0
    inner_cx = WALL_THICKNESS + PCB_CLEARANCE + disp_cx_board
    inner_cy = WALL_THICKNESS + PCB_CLEARANCE + disp_cy_board

    return {
        "model": data.get("model"),
        "pcb": {
            "width_mm":       bw,
            "height_mm":      bh,
            "thickness_mm":   data.get("board_thickness_mm", 1.6),
        },
        "display": {
            "diagonal_inch":   dia,
            "diagonal_mm":     diag_mm,
            "active_area_mm":  f"{dw} × {dh}",
            "resolution_px":   f"{rw} × {rh}",
            "pixel_pitch_mm":  pixel_pitch,
            "ppi":             ppi,
        },
        "shell_envelope": {
            "width_mm":  shell_w,
            "height_mm": shell_h,
            "depth_mm":  shell_d,
        },
        "lid_display_cutout": {
            "width_mm":          cutout_w,
            "height_mm":         cutout_h,
            "center_on_shell_mm": (round(inner_cx, 3), round(inner_cy, 3)),
            "recess_depth_mm":   DISPLAY_RECESS_DEPTH,
        },
        "mounting": {
            "standoff_od_mm":     STANDOFF_OD,
            "standoff_hole_mm":   STANDOFF_HOLE_D,
            "standoff_height_mm": STANDOFF_HEIGHT,
            "count":              len(data.get("mounting_hole_positions_mm", [])),
        },
        "gpio": {
            "pitch_mm":  data.get("gpio_header_pitch_mm", 2.54),
            "rows":      data.get("gpio_rows", 2),
            "cols":      data.get("gpio_cols", 20),
            "total_pins": data.get("gpio_rows", 2) * data.get("gpio_cols", 20),
        },
        "formulas_used": {
            "pixel_pitch":  "display_width_mm / resolution_w",
            "ppi":          "sqrt(res_w^2 + res_h^2) / display_diagonal_inch",
            "diag_mm":      "sqrt(display_width_mm^2 + display_height_mm^2)",
            "shell_width":  "board_width + 2*PCB_CLEARANCE + 2*WALL_THICKNESS",
            "shell_height": "board_height + 2*PCB_CLEARANCE + 2*WALL_THICKNESS",
            "shell_depth":  "BOTTOM_FLOOR + STANDOFF_H + PCB_T + HAT_STACK_H + LID_T",
            "cutout_w":     "display_width_mm - 2*DISPLAY_BEZEL",
            "cutout_h":     "display_height_mm - 2*DISPLAY_BEZEL",
        },
    }
