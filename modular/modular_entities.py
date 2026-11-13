"""
modular_entities.py
-------------------
Modular shell entity library.

Each entity is a self-contained parametric segment that can be:
  - Combined with others to form a full handheld shell
  - Independently exported as an STL for mix-and-match assembly
  - Parameterised at instantiation time from a ShellGeometry

Entities:
  TopCase         — upper shell with display/button apertures
  BottomCase      — lower shell with mounting bosses, battery tray option
  BatteryTray     — snap-in battery compartment module
  PortModule      — USB-C / micro-USB / HDMI edge cutout frame
  ButtonCap       — printable button cap (press-fit into apertures)
  JoystickRing    — decorative/functional ring around joystick aperture
  GripPanel       — ergonomic side grip extension
  StandoffPillar  — isolated standoff for direct import into assemblies
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import math
import json


# ---------------------------------------------------------------------------
# Base entity
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """Base class for all modular shell entities."""
    name: str = ""
    material_mm: float = 2.0        # wall / print thickness
    infill_percent: int = 30         # recommended FDM infill
    layer_height_mm: float = 0.2    # recommended layer height
    support_needed: bool = False
    color_hex: str = "#1a1a2e"

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    def print_specs(self) -> str:
        return (
            f"{self.name}  |  "
            f"wall={self.material_mm} mm  "
            f"infill={self.infill_percent}%  "
            f"layer={self.layer_height_mm} mm  "
            f"support={'yes' if self.support_needed else 'no'}"
        )


# ---------------------------------------------------------------------------
# Top case (lid)
# ---------------------------------------------------------------------------

@dataclass
class TopCase(Entity):
    name: str = "TopCase"
    shell_width_mm:   float = 70.0
    shell_height_mm:  float = 35.0
    lid_thickness_mm: float = 1.8
    corner_radius_mm: float = 3.0
    display_cutout_x: float = 0.0
    display_cutout_y: float = 0.0
    display_cutout_w: float = 0.0
    display_cutout_h: float = 0.0
    display_recess_mm: float = 1.2
    button_apertures: List[Dict] = field(default_factory=list)   # [{cx,cy,r}]
    joystick_aperture: Dict = field(default_factory=dict)        # {cx,cy,r}
    snap_tabs: List[Dict] = field(default_factory=list)          # [{x,y,edge}]
    support_needed: bool = False
    infill_percent: int = 25

    @classmethod
    def from_geometry(cls, geom: dict) -> "TopCase":
        dc = geom.get("display_cutout", {})
        return cls(
            shell_width_mm    = geom.get("shell_width_mm", 70),
            shell_height_mm   = geom.get("shell_height_mm", 35),
            lid_thickness_mm  = geom.get("lid_thickness_mm", 1.8),
            corner_radius_mm  = geom.get("corner_radius", 3.0),
            display_cutout_x  = dc.get("x", 0),
            display_cutout_y  = dc.get("y", 0),
            display_cutout_w  = dc.get("w", 0),
            display_cutout_h  = dc.get("h", 0),
            display_recess_mm = dc.get("depth", 1.2),
            button_apertures  = [
                {"cx": b["cx"], "cy": b["cy"], "r": b["r"]}
                for b in geom.get("button_apertures", [])
            ],
            joystick_aperture = geom.get("joystick_aperture", {}),
            snap_tabs         = [
                {"x": p["x"], "y": p["y"]}
                for p in geom.get("snap_fit_positions", [])
            ],
        )

    def volume_estimate_cm3(self) -> float:
        """Rough material volume estimate for cost/time estimation."""
        outer = self.shell_width_mm * self.shell_height_mm * self.lid_thickness_mm
        # subtract display window and button holes
        cutout = self.display_cutout_w * self.display_cutout_h * self.lid_thickness_mm
        btn_vol = sum(
            math.pi * b["r"]**2 * self.lid_thickness_mm
            for b in self.button_apertures
        )
        joy_vol = 0.0
        if self.joystick_aperture.get("r"):
            joy_vol = math.pi * self.joystick_aperture["r"]**2 * self.lid_thickness_mm
        return round((outer - cutout - btn_vol - joy_vol) / 1000, 3)  # mm³→cm³


# ---------------------------------------------------------------------------
# Bottom case
# ---------------------------------------------------------------------------

@dataclass
class BottomCase(Entity):
    name: str = "BottomCase"
    shell_width_mm:   float = 70.0
    shell_height_mm:  float = 35.0
    shell_depth_mm:   float = 20.0
    wall_thickness_mm: float = 2.0
    floor_thickness_mm: float = 2.5
    mounting_bosses:  List[Dict] = field(default_factory=list)  # [{cx,cy,r,depth}]
    ribs:             List[Dict] = field(default_factory=list)
    has_battery_bay:  bool = False
    battery_bay_w:    float = 0.0
    battery_bay_h:    float = 0.0
    battery_bay_d:    float = 0.0
    support_needed:   bool = False
    infill_percent:   int = 35

    @classmethod
    def from_geometry(cls, geom: dict, include_battery: bool = False,
                      battery_size: tuple = (50, 20, 6)) -> "BottomCase":
        inst = cls(
            shell_width_mm    = geom.get("shell_width_mm", 70),
            shell_height_mm   = geom.get("shell_height_mm", 35),
            shell_depth_mm    = geom.get("shell_depth_mm", 20),
            wall_thickness_mm = geom.get("wall_thickness_mm", 2.0),
            floor_thickness_mm= geom.get("floor_thickness_mm", 2.5),
            mounting_bosses   = [
                {"cx": b["cx"], "cy": b["cy"], "r": b["r"], "depth": b["depth"]}
                for b in geom.get("mounting_bosses", [])
            ],
            ribs = geom.get("ribs", []),
            has_battery_bay   = include_battery,
        )
        if include_battery:
            inst.battery_bay_w = battery_size[0] + 1.0  # 0.5 mm clearance per side
            inst.battery_bay_h = battery_size[1] + 1.0
            inst.battery_bay_d = battery_size[2] + 0.5
        return inst


# ---------------------------------------------------------------------------
# Battery tray
# ---------------------------------------------------------------------------

@dataclass
class BatteryTray(Entity):
    name: str = "BatteryTray"
    tray_width_mm:  float = 51.0    # battery_w + 1 mm clearance
    tray_height_mm: float = 21.0
    tray_depth_mm:  float = 7.0
    wall_mm:        float = 1.2
    has_lid:        bool = True
    contact_width_mm: float = 3.0
    support_needed: bool = False
    infill_percent: int = 20

    @classmethod
    def for_battery(cls, w_mm: float, h_mm: float, d_mm: float) -> "BatteryTray":
        return cls(
            tray_width_mm  = w_mm + 1.0,
            tray_height_mm = h_mm + 1.0,
            tray_depth_mm  = d_mm + 0.5,
        )

    def outer_dimensions(self) -> tuple:
        wt = self.wall_mm
        return (
            self.tray_width_mm  + 2 * wt,
            self.tray_height_mm + 2 * wt,
            self.tray_depth_mm  + wt,     # open top
        )


# ---------------------------------------------------------------------------
# Port module (edge cutout frame)
# ---------------------------------------------------------------------------

PORT_TYPES = {
    "usb_c":       {"w_mm": 9.0,  "h_mm": 3.5, "label": "USB-C"},
    "micro_usb":   {"w_mm": 8.0,  "h_mm": 2.5, "label": "Micro USB"},
    "mini_usb":    {"w_mm": 7.5,  "h_mm": 3.0, "label": "Mini USB"},
    "hdmi_full":   {"w_mm": 15.0, "h_mm": 5.5, "label": "HDMI"},
    "hdmi_mini":   {"w_mm": 11.2, "h_mm": 3.5, "label": "Mini HDMI"},
    "hdmi_micro":  {"w_mm": 6.4,  "h_mm": 2.9, "label": "Micro HDMI"},
    "audio_3.5":   {"w_mm": 6.5,  "h_mm": 6.5, "label": "3.5mm Jack"},
    "sd_card":     {"w_mm": 15.0, "h_mm": 2.5, "label": "MicroSD"},
    "gpio_40pin":  {"w_mm": 51.0, "h_mm": 5.5, "label": "GPIO 40-pin"},
    "gpio_26pin":  {"w_mm": 33.0, "h_mm": 5.5, "label": "GPIO 26-pin"},
}

@dataclass
class PortModule(Entity):
    name: str = "PortModule"
    port_type: str = "usb_c"
    cutout_w_mm: float = 9.0
    cutout_h_mm: float = 3.5
    frame_thickness_mm: float = 2.0
    frame_lip_mm: float = 1.0      # lip around cutout inside shell
    edge: str = "bottom"           # bottom | top | left | right
    position_mm: float = 0.0       # position along edge

    @classmethod
    def for_port(cls, port_type: str, edge: str = "bottom",
                 position_mm: float = 10.0) -> "PortModule":
        pdata = PORT_TYPES.get(port_type, PORT_TYPES["usb_c"])
        return cls(
            port_type      = port_type,
            cutout_w_mm    = pdata["w_mm"] + 0.4,  # print clearance
            cutout_h_mm    = pdata["h_mm"] + 0.4,
            edge           = edge,
            position_mm    = position_mm,
        )

    def cutout_rect(self) -> dict:
        return {
            "port_type": self.port_type,
            "w_mm": self.cutout_w_mm,
            "h_mm": self.cutout_h_mm,
            "edge": self.edge,
            "pos_mm": self.position_mm,
        }


# ---------------------------------------------------------------------------
# Button cap
# ---------------------------------------------------------------------------

@dataclass
class ButtonCap(Entity):
    name: str = "ButtonCap"
    aperture_r_mm: float = 2.4      # radius of hole in lid
    cap_r_mm: float = 2.3           # press-fit radius (aperture - 0.1 tolerance)
    cap_height_mm: float = 2.0      # above lid surface
    stem_r_mm: float = 1.0          # stem that presses tactile switch
    stem_height_mm: float = 2.5
    shoulder_r_mm: float = 3.5      # retaining shoulder radius
    color_hex: str = "#e63946"
    infill_percent: int = 100       # solid for strength
    support_needed: bool = False

    @classmethod
    def for_aperture(cls, aperture_r_mm: float) -> "ButtonCap":
        return cls(
            aperture_r_mm  = aperture_r_mm,
            cap_r_mm       = aperture_r_mm - 0.1,
            shoulder_r_mm  = aperture_r_mm + 1.0,
        )

    def total_height_mm(self) -> float:
        return self.cap_height_mm + self.stem_height_mm


# ---------------------------------------------------------------------------
# Joystick ring
# ---------------------------------------------------------------------------

@dataclass
class JoystickRing(Entity):
    name: str = "JoystickRing"
    inner_r_mm: float = 6.5        # just clears joystick nub
    outer_r_mm: float = 9.0
    ring_height_mm: float = 1.5
    color_hex: str = "#457b9d"
    infill_percent: int = 100

    @classmethod
    def for_joystick(cls, joystick_d_mm: float) -> "JoystickRing":
        inner = joystick_d_mm / 2 + 0.5
        return cls(inner_r_mm=inner, outer_r_mm=inner + 2.5)


# ---------------------------------------------------------------------------
# Grip panel
# ---------------------------------------------------------------------------

@dataclass
class GripPanel(Entity):
    name: str = "GripPanel"
    panel_w_mm:     float = 15.0
    panel_h_mm:     float = 35.0
    thickness_mm:   float = 4.0
    contour_depth:  float = 2.0    # ergonomic hollow depth
    attachment: str = "snap"       # snap | screw | glue
    side: str = "left"             # left | right
    support_needed: bool = True    # has overhang contour
    infill_percent: int = 40

    @classmethod
    def for_shell(cls, shell_height_mm: float, side: str = "left") -> "GripPanel":
        return cls(
            panel_h_mm = shell_height_mm,
            side       = side,
        )


# ---------------------------------------------------------------------------
# Standoff pillar (isolated, importable)
# ---------------------------------------------------------------------------

@dataclass
class StandoffPillar(Entity):
    name: str = "StandoffPillar"
    od_mm: float = 5.0
    id_mm: float = 2.9
    height_mm: float = 3.0
    base_flange_mm: float = 1.0    # extra flange bonded to floor
    infill_percent: int = 100
    support_needed: bool = False

    def screw_spec(self) -> str:
        """Returns the M-screw spec that fits this standoff."""
        # M2 = 2.0mm, M2.5 = 2.5mm, M3 = 3.0mm
        for size in (2.0, 2.5, 3.0):
            if self.id_mm <= size + 0.3:
                return f"M{size:.0f} × {self.height_mm:.0f}mm"
        return f"Custom Ø{self.id_mm}mm"


# ---------------------------------------------------------------------------
# Assembly — compose all entities into a build manifest
# ---------------------------------------------------------------------------

@dataclass
class HandheldBuild:
    """
    Represents a complete handheld device build composed of modular entities.
    Serialises to JSON for the UI and STL pipeline.
    """
    model_name: str = "handheld"
    top_case:   Optional[TopCase]    = None
    bottom_case: Optional[BottomCase] = None
    battery_tray: Optional[BatteryTray] = None
    ports:       List[PortModule]    = field(default_factory=list)
    buttons:     List[ButtonCap]     = field(default_factory=list)
    joystick_ring: Optional[JoystickRing] = None
    grip_panels: List[GripPanel]     = field(default_factory=list)
    standoffs:   List[StandoffPillar] = field(default_factory=list)

    @classmethod
    def from_geometry(cls, geom: dict, model_name: str = "handheld",
                      include_battery: bool = False,
                      battery_size_mm: tuple = (50, 20, 6)) -> "HandheldBuild":

        build = cls(model_name=model_name)
        build.top_case    = TopCase.from_geometry(geom)
        build.bottom_case = BottomCase.from_geometry(geom, include_battery, battery_size_mm)

        if include_battery:
            build.battery_tray = BatteryTray.for_battery(*battery_size_mm)

        # Button caps
        for ba in geom.get("button_apertures", []):
            build.buttons.append(ButtonCap.for_aperture(ba["r"]))

        # Joystick ring
        joy = geom.get("joystick_aperture", {})
        if joy.get("r", 0) > 0:
            build.joystick_ring = JoystickRing.for_joystick(joy["r"] * 2)

        # Standoffs from mounting bosses
        for boss in geom.get("mounting_bosses", []):
            build.standoffs.append(StandoffPillar(
                od_mm=boss.get("r", 2.5) * 2,
                height_mm=boss.get("depth", 3.0)
            ))

        # Grip panels
        build.grip_panels = [
            GripPanel.for_shell(geom.get("shell_height_mm", 35), "left"),
            GripPanel.for_shell(geom.get("shell_height_mm", 35), "right"),
        ]

        return build

    def entity_list(self) -> List[Entity]:
        entities = []
        if self.top_case:    entities.append(self.top_case)
        if self.bottom_case: entities.append(self.bottom_case)
        if self.battery_tray: entities.append(self.battery_tray)
        entities.extend(self.ports)
        entities.extend(self.buttons)
        if self.joystick_ring: entities.append(self.joystick_ring)
        entities.extend(self.grip_panels)
        entities.extend(self.standoffs)
        return entities

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "entities": [e.to_dict() for e in self.entity_list()],
            "entity_count": len(self.entity_list()),
        }

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"[Build] Saved manifest → {path}")
