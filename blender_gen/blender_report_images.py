"""
blender_report_images.py
------------------------
Blender Python (bpy) script — renders the PDF report's assembly-step
images (framework Requirement 6b) from a render plan produced by
parser/render_plan.py.

It builds the design's ACTUAL board set as replicas in one scene —
printed shell parts (reusing blender_shell_gen's builders), the main
board, the HAT board at the correct mated gap (reusing
build_pcb_assembly — the deferred Requirement 2c multi-board stack),
plus inserts, battery, camera and cabling per the design — then, for
each plan frame, sets group visibility + lid position and renders a
PNG. A first-time builder reading report §7 sees their own boards,
in assembly order — never a generic device.

Run from Blender (invoked by POST /api/report-images, same pattern
as /api/generate-3d):

    blender --background --python blender_gen/blender_report_images.py \\
            -- --plan output/report_plan_<design_hash>.json \\
               [--engine EEVEE|WORKBENCH|CYCLES]

The render engine is selectable via --engine or the environment
variable PUROMO_RENDER_ENGINE (CLI wins; default EEVEE). The backend
drives an engine fallback chain at process level (EEVEE can die
natively — a GPU/OpenGL segfault in background mode on some machines
— and a segfault cannot be caught in-process), retrying the whole
script with WORKBENCH, then CYCLES, and caching the winner.

Outputs: <plan.output_dir>/step_NN.png per frame + manifest.json.
Without bpy the module still imports (BLENDER_AVAILABLE=False) so its
pure layout helpers stay testable; main() then refuses to run.

Presentation (Requirement 6c): a bright studio rig — neutral light
backdrop, a ground plane with a soft contact shadow, three-point
lighting with real fill — and a PER-FRAME camera: each plan frame's
camera intent (top/kit, below, home, above, front, hero) is turned
into a position auto-framed on the objects visible in that frame, so
the photo set reads as one story: loose parts → stack → closed,
working device (screen lit in the final frame).
"""

import sys
import os
import json
import math
import argparse

try:
    import bpy
    from mathutils import Vector
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if BLENDER_AVAILABLE:
    import blender_shell_gen as bsg
    from blender_shell_gen import (
        mm, clear_scene, add_collection, _add_box, _add_cylinder,
        _mat_col, build_shell_bottom, build_shell_lid, build_pcb_assembly,
    )


# ---------------------------------------------------------------------------
# Render engine selection (pure — unit-testable anywhere)
# ---------------------------------------------------------------------------

ENGINES = ("EEVEE", "WORKBENCH", "CYCLES")


def resolve_engine(requested=None) -> str:
    """Engine choice: explicit argument > PUROMO_RENDER_ENGINE env >
    EEVEE. Unknown names fall back to EEVEE with a warning — the
    backend only ever passes valid names; this guards manual runs."""
    cand = requested or os.environ.get("PUROMO_RENDER_ENGINE") or "EEVEE"
    cand = str(cand).strip().upper()
    if cand not in ENGINES:
        print(f"[ReportImages] unknown engine {cand!r} — "
              f"falling back to EEVEE")
        return "EEVEE"
    return cand


# Per-group fallback colours for the WORKBENCH engine (which shades by
# object colour, not materials). Mirrors the colours the scene
# builders bake into materials where they define them.
WORKBENCH_GROUP_COLORS = {
    "printed_parts": (0.45, 0.47, 0.52, 1.0),   # shell grey
    "inserts":       (0.72, 0.52, 0.20, 1.0),   # brass
    "host_board":    (0.02, 0.30, 0.08, 1.0),   # PCB green
    "hat_board":     (0.03, 0.22, 0.06, 1.0),   # PCB green (HAT)
    "cables":        (0.05, 0.05, 0.06, 1.0),   # rubber black
    "button_caps":   (0.95, 0.55, 0.15, 1.0),   # accent
    "display_lid":   (0.05, 0.10, 0.20, 1.0),   # display glass
    "camera":        (0.05, 0.10, 0.25, 1.0),   # camera PCB blue
    "battery":       (0.15, 0.35, 0.75, 1.0),   # pack wrap blue
}


# ---------------------------------------------------------------------------
# Pure layout helpers (no bpy — unit-testable anywhere)
# ---------------------------------------------------------------------------

def stack_layout(plan: dict) -> dict:
    """Z layout of the assembly stack, in mm, corner-origin frame
    (z up from the case floor's bottom face). Follows the verified
    DimensionMath depth budget: floor + standoff → host board →
    mated gap → HAT board → components → lid at shell_depth."""
    geom = plan.get("geom") or {}
    spec = plan.get("spec") or {}
    ft = float(geom.get("floor_thickness_mm", 2.5))
    soh = float(geom.get("standoff_height_mm", 3.0))
    lt = float(geom.get("lid_thickness_mm", 1.8))
    gap = float(spec.get("gpio_header_mated_gap_mm")
                or geom.get("hat_stack_height_mm") or 11.5)
    host = plan.get("host_board") or {}
    host_t = float(host.get("thickness_mm") or 1.6)
    host_z = ft + soh
    host_top = host_z + host_t
    hat_z = host_top + gap
    depth = float(geom.get("shell_depth_mm") or 32.0)
    # build_shell_lid bakes its bottom face at
    # FT + SOH + board_thickness + hat_stack (its own convention);
    # the shift below moves it to the true closed position.
    bt = float(geom.get("board_thickness_mm", spec.get(
        "board_thickness_mm", 1.6)))
    hsh = float(geom.get("hat_stack_height_mm", gap))
    lid_baked_bottom = ft + soh + bt + hsh
    lid_closed_bottom = depth - lt
    return {
        "floor_mm": ft,
        "host_z_mm": host_z,
        "host_top_mm": host_top,
        "gap_mm": gap,
        "hat_z_mm": hat_z,
        "lid_closed_bottom_mm": lid_closed_bottom,
        "lid_shift_mm": lid_closed_bottom - lid_baked_bottom,
        "explode_mm": round(depth * 0.65, 2),
        "depth_mm": depth,
    }


def frame_lid_offset_mm(plan: dict, frame: dict) -> float:
    """Extra Z (mm) applied to the lid and everything mounted on it
    for this frame: exploded hover until the close step, then 0."""
    if frame.get("lid") == "exploded":
        return stack_layout(plan)["explode_mm"]
    return 0.0


def frame_visible_groups(frame: dict) -> set:
    return set(frame.get("visible_groups") or [])


# ---------------------------------------------------------------------------
# Per-frame camera (pure — unit-testable anywhere)
# ---------------------------------------------------------------------------
# Azimuth is measured around Z from the scene's front edge (-Y, the
# side the original fixed camera looked from), positive toward +X;
# elevation is above the target's horizon. The device front — the
# edge the viewer reads first — is -Y; the USB cable exits at +Y.
CAMERA_VIEWS = {
    "home":  {"azimuth_deg": -38.0, "elevation_deg": 32.0,
              "distance_scale": 1.0},
    "above": {"azimuth_deg": -38.0, "elevation_deg": 58.0,
              "distance_scale": 1.0},
    "below": {"azimuth_deg": -30.0, "elevation_deg": -32.0,
              "distance_scale": 1.0},
    "front": {"azimuth_deg": 0.0,   "elevation_deg": 10.0,
              "distance_scale": 1.0},
    "top":   {"azimuth_deg": -38.0, "elevation_deg": 85.0,
              "distance_scale": 1.0},
    "hero":  {"azimuth_deg": -32.0, "elevation_deg": 22.0,
              "distance_scale": 0.92},
}
CAMERA_FOV_DEG = 42.0


def frame_camera(bbox, view, fov_deg=CAMERA_FOV_DEG):
    """Camera (location, target) framing a bounding box from a named
    view. bbox = ((x0,y0,z0),(x1,y1,z1)) in scene units. The camera
    sits on a sphere around the bbox centre at a distance that fits
    the bbox's bounding sphere in the (vertical) field of view, with
    a 1.25 margin, clamped to a sane range."""
    (x0, y0, z0), (x1, y1, z1) = bbox
    centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0)
    radius = 0.5 * math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2
                             + (z1 - z0) ** 2)
    radius = max(radius, 1e-4)
    spec = CAMERA_VIEWS.get(view) or CAMERA_VIEWS["home"]
    az = math.radians(spec["azimuth_deg"])
    el = math.radians(spec["elevation_deg"])
    fit = radius / math.tan(math.radians(fov_deg) / 2.0)
    dist = fit * 1.25 * spec["distance_scale"]
    dist = min(max(dist, radius * 1.7), radius * 9.0)
    direction = (math.cos(el) * math.sin(az),
                 -math.cos(el) * math.cos(az),
                 math.sin(el))
    location = tuple(c + dist * d for c, d in zip(centre, direction))
    return location, centre


def _bbox_radius(bbox):
    (x0, y0, z0), (x1, y1, z1) = bbox
    return 0.5 * math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2
                           + (z1 - z0) ** 2)


def sane_bbox(bbox, fallback):
    """Guard the per-frame camera measurement (Requirement 10).
    A measured bbox whose size or centre is wildly off the
    device's own fallback box cannot be the subject — an
    unevaluated curve or a stale matrix once framed the whole
    device as a speck in an empty frame. Falls back instead."""
    if bbox is None:
        return fallback
    fr = _bbox_radius(fallback)
    if fr <= 0:
        return bbox
    if _bbox_radius(bbox) > 2.5 * fr:
        return fallback
    (ax0, ay0, az0), (ax1, ay1, az1) = bbox
    (bx0, by0, bz0), (bx1, by1, bz1) = fallback
    ac = ((ax0 + ax1) / 2, (ay0 + ay1) / 2, (az0 + az1) / 2)
    bc = ((bx0 + bx1) / 2, (by0 + by1) / 2, (bz0 + bz1) / 2)
    dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ac, bc)))
    if dist > 2.0 * fr:
        return fallback
    return bbox


# ---------------------------------------------------------------------------
# Scene construction (bpy only)
# ---------------------------------------------------------------------------

def _inject_geom_constants(plan: dict) -> dict:
    """Same tolerance-constant injection generate_shell() performs,
    so the shell builders receive the fields they expect."""
    geom = dict(plan.get("geom") or {})
    spec = plan.get("spec") or {}
    geom.update({
        "standoff_od_mm":     5.0,
        "standoff_hole_mm":   2.5,
        "standoff_height_mm": geom.get("standoff_height_mm", 3.0),
        "floor_thickness_mm": geom.get("floor_thickness_mm", 2.5),
        "lid_thickness_mm":   geom.get("lid_thickness_mm", 1.8),
        "wall_thickness_mm":  geom.get("wall_thickness_mm", 2.0),
        "pcb_clearance_mm":   0.5,
        "rib_thickness_mm":   1.2,
        "corner_radius_mm":   geom.get("corner_radius_mm", 3.0),
        "hat_stack_height_mm": spec.get(
            "hat_stack_height_mm",
            spec.get("gpio_header_mated_gap_mm", 11.5)),
        "board_thickness_mm": spec.get("board_thickness_mm", 1.6),
        "snap_depth_mm":  1.2,
        "snap_height_mm": 2.5,
    })
    return geom


def build_host_board(plan: dict, geom: dict, z_mm: float, col) -> list:
    """Replica of the main (host) board: PCB at its registry/spec
    dimensions, 40-pin header along the board edge at the HAT-builder's
    reference position, SoC, and edge ports (class-appropriate:
    USB-C + micro-HDMI + USB-A/Ethernet for 85 mm boards, micro-USB +
    mini-HDMI for 65 mm boards). Board-origin frame, like
    build_pcb_assembly."""
    hb = plan.get("host_board") or {}
    W = float(hb.get("w_mm") or 65)
    H = float(hb.get("h_mm") or 30)
    T = float(hb.get("thickness_mm") or 1.6)
    z = mm(z_mm)
    objs = []
    m_pcb = _mat_col("Host_FR4", 0.02, 0.16, 0.05, roughness=0.65)
    m_blk = _mat_col("Host_Black", 0.05, 0.05, 0.08, roughness=0.6)
    m_gold = _mat_col("Host_Gold", 0.85, 0.75, 0.25,
                      metallic=0.95, roughness=0.15)
    m_metal = _mat_col("Host_Metal", 0.70, 0.70, 0.73,
                       metallic=0.7, roughness=0.3)

    objs.append(_add_box("Host_PCB", 0, 0, z, mm(W), mm(H), mm(T),
                         m_pcb, col))
    # 40-pin header: plastic base + pin field, at the HAT builder's
    # reference position (x=7.1, y=3.5 on the board).
    objs.append(_add_box("Host_HeaderBase", mm(7.1), mm(3.5),
                         z + mm(T), mm(51.0), mm(5.1), mm(2.5),
                         m_blk, col))
    objs.append(_add_box("Host_HeaderPins", mm(7.6), mm(4.0),
                         z + mm(T) + mm(2.5), mm(50.0), mm(4.1),
                         mm(6.0), m_gold, col))
    # SoC + a secondary IC
    objs.append(_add_box("Host_SoC", mm(W * 0.42), mm(H * 0.38),
                         z + mm(T), mm(12.0), mm(12.0), mm(1.2),
                         m_blk, col))
    objs.append(_add_box("Host_IC", mm(W * 0.42) + mm(16.0),
                         mm(H * 0.38), z + mm(T), mm(7.0), mm(7.0),
                         mm(1.0), m_blk, col))
    # Edge ports along the y=0 edge
    if W >= 80:
        objs.append(_add_box("Host_USBC", mm(30.0), -mm(0.6), z,
                             mm(8.9), mm(7.4), mm(3.2), m_metal, col))
        objs.append(_add_box("Host_HDMI0", mm(41.5), -mm(0.6), z,
                             mm(7.2), mm(6.0), mm(2.6), m_metal, col))
        objs.append(_add_box("Host_HDMI1", mm(50.5), -mm(0.6), z,
                             mm(7.2), mm(6.0), mm(2.6), m_metal, col))
        objs.append(_add_box("Host_USBA", mm(W) - mm(15.0), mm(2.0), z,
                             mm(13.5), mm(13.0), mm(6.0), m_metal, col))
        objs.append(_add_box("Host_Ethernet", mm(W) - mm(15.5),
                             mm(H) - mm(16.5), z, mm(15.5), mm(16.0),
                             mm(13.0), m_metal, col))
    else:
        objs.append(_add_box("Host_MicroUSB_PWR", mm(35.0), -mm(0.6), z,
                             mm(7.5), mm(5.6), mm(2.6), m_metal, col))
        objs.append(_add_box("Host_MicroUSB_OTG", mm(46.0), -mm(0.6), z,
                             mm(7.5), mm(5.6), mm(2.6), m_metal, col))
        objs.append(_add_box("Host_MiniHDMI", mm(22.0), -mm(0.6), z,
                             mm(11.0), mm(6.5), mm(3.2), m_metal, col))
    return objs


def _ensure_lid_window(plan: dict, geom: dict, lid, layout: dict):
    """Guarantee the lid's display window is open in the render
    scene. Raycasts from above the closed lid down the cutout
    centre: passing through (or hitting the glass stack below)
    means the window exists; hitting the lid means it does not,
    and a single full-depth box cut opens it. All guarded — any
    failure leaves the lid exactly as built and logs why."""
    dc = geom.get("display_cutout") or {}
    if not dc.get("w", 0):
        return
    try:
        _update_depsgraph()
        lt = float(geom.get("lid_thickness_mm", 1.8))
        cx = float(dc["x"]) + float(dc["w"]) / 2.0
        cy = float(dc["y"]) + float(dc["h"]) / 2.0
        top_z = layout["lid_closed_bottom_mm"] + lt
        origin = (mm(cx), mm(cy), mm(top_z + 5.0))
        dg = bpy.context.evaluated_depsgraph_get()
        hit, _loc, _n, _i, obj, _m = bpy.context.scene.ray_cast(
            dg, origin, (0.0, 0.0, -1.0))
        if hit and obj is not None and obj.name == lid.name:
            cols_lid = lid.users_collection[0] \
                if lid.users_collection else None
            cutter = bsg.make_box_cutter(
                "DispWindow_Heal", mm(float(dc["x"])),
                mm(float(dc["y"])),
                mm(layout["lid_closed_bottom_mm"] - 0.1),
                mm(float(dc["w"])), mm(float(dc["h"])),
                mm(lt + 0.2), cols_lid)
            bsg.bool_cut(lid, cutter)
            _update_depsgraph()
            print("[ReportImages] lid display window was closed "
                  "— opened it for the render scene")
        else:
            print("[ReportImages] lid display window verified open")
    except Exception as e:
        print(f"[ReportImages] lid window check skipped: {e}")


def build_scene(plan: dict) -> dict:
    """Build every group of the design's board set. Returns state:
    {groups: {name: [objects]}, lid_riders: [objects],
     lid_base_z: {id(obj): base location.z in BU}}"""
    geom = _inject_geom_constants(plan)
    spec = plan.get("spec") or {}
    layout = stack_layout(plan)
    groups = {g: [] for g in plan.get("groups", [])
              if g not in plan.get("state_groups", [])}
    cols = {g: add_collection("GRP_" + g) for g in groups}
    lid_base_z = {}

    # ── Printed parts: bottom case + lid shell ──
    bottom = build_shell_bottom(geom, cols["printed_parts"])
    groups["printed_parts"].append(bottom)
    lid = build_shell_lid(geom, cols["printed_parts"])
    groups["printed_parts"].append(lid)
    lid_base_z[id(lid)] = mm(layout["lid_shift_mm"])
    lid.location.z = lid_base_z[id(lid)]
    lid_riders = [lid]

    # ── Lid display window (Requirement 10): the staged boolean
    # cuts in build_shell_lid do not open the window in the
    # rendered scene on the user's Blender (every Pi frame shows a
    # solid lid over the display, so the glass — and the hero's
    # lit screen — can never be seen). Verify with a raycast down
    # the cutout centre; if the lid blocks it, open the window
    # with one full-depth cut. A no-op when the window is open.
    _ensure_lid_window(plan, geom, lid, layout)

    # ── Heat-set inserts in the case bosses (shell coords) ──
    m_brass = _mat_col("Brass_Insert", 0.72, 0.52, 0.20,
                       metallic=0.9, roughness=0.3)
    ft = layout["floor_mm"]
    for b in geom.get("mounting_bosses", []):
        ins = _add_cylinder("Insert", mm(b["cx"]), mm(b["cy"]),
                            mm(ft - 0.5), mm(2.1), mm(3.5),
                            m_brass, cols["inserts"])
        groups["inserts"].append(ins)

    # ── Main board replica ──
    groups["host_board"] += build_host_board(
        plan, geom, layout["host_z_mm"], cols["host_board"])

    # ── HAT board replica (multi-board stack, R2c) ──
    if plan.get("has_hat"):
        spec_for_pcb = dict(spec)
        if "display_offset_x_mm" not in spec_for_pcb:
            dc = geom.get("display_cutout", {})
            spec_for_pcb["display_offset_x_mm"] = dc.get("x", 2.0)
            spec_for_pcb["display_offset_y_mm"] = dc.get("y", 2.0)
        groups["hat_board"] += build_pcb_assembly(
            spec_for_pcb, geom, mm(layout["hat_z_mm"]),
            cols["hat_board"])

    # ── Battery pack in its bay (shell coords) ──
    bat = plan.get("battery") or {}
    if bat.get("present") and bat.get("bay"):
        bay = bat["bay"]
        thick = (bat.get("size_mm") or [0, 0, 6.0])[2]
        m_pack = _mat_col("Batt_Pack", 0.10, 0.10, 0.13, roughness=0.5)
        m_wrap = _mat_col("Batt_Wrap", 0.15, 0.35, 0.75, roughness=0.4)
        pack = _add_box("Battery_Pack", mm(bay["x"] + 0.5),
                        mm(bay["y"] + 0.5), mm(ft),
                        mm(bay["w"] - 1.0), mm(bay["h"] - 1.0),
                        mm(float(thick)), m_pack, cols["battery"])
        wrap = _add_box("Battery_Wrap", mm(bay["x"] + 0.5),
                        mm(bay["y"] + bay["h"] * 0.38), mm(ft),
                        mm(bay["w"] - 1.0), mm(bay["h"] * 0.24),
                        mm(float(thick)) + mm(0.1), m_wrap,
                        cols["battery"])
        groups["battery"] += [pack, wrap]

    # ── Button caps: printed caps through the lid apertures ──
    m_cap = _mat_col("Printed_Cap", 0.92, 0.92, 0.94, roughness=0.5)
    lt = float(geom.get("lid_thickness_mm", 1.8))
    lid_bottom = layout["lid_closed_bottom_mm"]
    for a in geom.get("button_apertures", []):
        cap = _add_cylinder("Printed_BtnCap", mm(a["cx"]), mm(a["cy"]),
                            mm(lid_bottom - 0.4),
                            mm(max(a["r"] - 0.3, 1.0)), mm(lt + 1.4),
                            m_cap, cols["button_caps"])
        groups["button_caps"].append(cap)
        lid_riders.append(cap)
        lid_base_z[id(cap)] = 0.0
    # Joystick cap through its aperture, if any
    joy = geom.get("joystick_aperture") or {}
    if joy.get("r"):
        jcap = _add_cylinder("Printed_JoyCap", mm(joy["cx"]),
                             mm(joy["cy"]), mm(lid_bottom - 0.4),
                             mm(3.2), mm(lt + 4.5),
                             m_cap, cols["button_caps"])
        groups["button_caps"].append(jcap)
        lid_riders.append(jcap)
        lid_base_z[id(jcap)] = 0.0

    # ── Display glass in the lid cutout ──
    screen_ui = None
    dc = geom.get("display_cutout") or {}
    if dc.get("w", 0) > 0:
        m_glass = _mat_col("Disp_Glass", 0.05, 0.10, 0.20,
                           roughness=0.05)
        glass = _add_box("Display_Glass", mm(dc["x"]), mm(dc["y"]),
                         mm(lid_bottom + 0.3), mm(dc["w"]),
                         mm(dc["h"]), mm(1.0), m_glass,
                         cols["display_lid"])
        groups["display_lid"].append(glass)
        lid_riders.append(glass)
        lid_base_z[id(glass)] = 0.0
        # Dim UI inset on the glass (R6c): visible only in the
        # screen_on finale, where the glass itself is swapped for an
        # emissive "lit screen" material. Not a group member — the
        # frame loop owns its visibility.
        m_ui = _mat_col("Disp_UI", 0.10, 0.16, 0.22,
                        roughness=0.4, emission_strength=0.35)
        screen_ui = _add_box(
            "Display_UI", mm(dc["x"] + dc["w"] * 0.18),
            mm(dc["y"] + dc["h"] * 0.24), mm(lid_bottom + 1.32),
            mm(dc["w"] * 0.64), mm(dc["h"] * 0.46), mm(0.15),
            m_ui, cols["display_lid"])
        screen_ui.hide_render = True
        screen_ui.hide_viewport = True
        lid_riders.append(screen_ui)
        lid_base_z[id(screen_ui)] = 0.0

    # ── Camera — "the eyes": board on the lid at the aperture,
    #    lens through it, CSI ribbon routed toward board centre ──
    cam = plan.get("camera") or {}
    if cam.get("present") and cam.get("aperture"):
        ap = cam["aperture"]
        cx, cy = float(ap["cx"]), float(ap["cy"])
        m_campcb = _mat_col("Cam_PCB", 0.05, 0.10, 0.25, roughness=0.5)
        m_lens = _mat_col("Cam_Lens", 0.02, 0.02, 0.03, roughness=0.15)
        m_ribbon = _mat_col("CSI_Ribbon", 0.90, 0.88, 0.80,
                            roughness=0.5)
        board = _add_box("Camera_Board", mm(cx - 12.5), mm(cy - 12.0),
                         mm(layout["depth_mm"]), mm(25.0), mm(24.0),
                         mm(1.2), m_campcb, cols["camera"])
        lens = _add_cylinder("Camera_Lens", mm(cx), mm(cy),
                             mm(layout["depth_mm"]), mm(ap["r"]),
                             mm(2.6), m_lens, cols["camera"])
        # Ribbon: from the camera board toward the shell centre
        H = float(geom.get("shell_height_mm") or 35)
        direction = -1.0 if cy > H / 2 else 1.0
        rib_len = abs(cy - H / 2) + 8.0
        rib_y = cy if direction < 0 else cy
        if direction < 0:
            rib_y = cy - rib_len
        ribbon = _add_box("CSI_Ribbon", mm(cx - 8.0), mm(rib_y),
                          mm(layout["depth_mm"] + 0.4), mm(16.0),
                          mm(rib_len), mm(0.15), m_ribbon,
                          cols["camera"])
        groups["camera"] += [board, lens, ribbon]
        for o in (board, lens, ribbon):
            lid_riders.append(o)
            lid_base_z[id(o)] = 0.0

    # ── Cabling: USB power cable to the case edge ──
    W = float(geom.get("shell_width_mm") or 70)
    H = float(geom.get("shell_height_mm") or 35)
    m_cable = _mat_col("Cable_Rubber", 0.04, 0.04, 0.05, roughness=0.8)
    plug = _add_box("USB_Plug", mm(W * 0.5 - 4.5), mm(H - 1.0),
                    mm(6.0), mm(9.0), mm(10.0), mm(3.4),
                    _mat_col("USB_PlugBody", 0.08, 0.08, 0.10,
                             roughness=0.6), cols["cables"])
    groups["cables"].append(plug)
    curve = bpy.data.curves.new("USB_Cable", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = mm(1.7)
    curve.bevel_resolution = 3
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(2)
    pts = [(W * 0.5, H + 4.0, 7.5),
           (W * 0.5 + 6.0, H + 22.0, 4.0),
           (W * 0.5 + 20.0, H + 46.0, 1.5)]
    for bp, (px, py, pz) in zip(spline.bezier_points, pts):
        bp.co = (mm(px), mm(py), mm(pz))
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"
    cable_obj = bpy.data.objects.new("USB_Cable", curve)
    cols["cables"].objects.link(cable_obj)
    cable_obj.data.materials.append(m_cable)
    groups["cables"].append(cable_obj)

    # ── Studio ground (R6c): the workbench every frame stands on.
    # Not a group member — the frame loop only hides it for the
    # from-below inserts shot. A shadow catcher where the engine
    # supports it (soft contact shadow on the bright backdrop),
    # otherwise a plain matte light-grey plane.
    env_col = add_collection("ENV")
    m_ground = _mat_col("Studio_Ground", 0.78, 0.78, 0.79,
                        roughness=0.95)
    ground = _add_box("Studio_Ground", mm(-W * 0.9), mm(-H * 2.2),
                      -mm(2.0), mm(W * 2.8), mm(H * 5.4), mm(2.0),
                      m_ground, env_col)
    try:
        ground.is_shadow_catcher = True
    except Exception:
        pass
    try:
        ground.cycles.is_shadow_catcher = True
    except Exception:
        pass

    return {"groups": groups, "lid_riders": lid_riders,
            "lid_base_z": lid_base_z, "layout": layout,
            "ground": ground, "screen_ui": screen_ui}


def _material_base_color(obj):
    """Best-effort Principled Base Color of the object's first
    material that has one, else None. Used by the WORKBENCH path,
    which shades by object colour instead of materials."""
    data = getattr(obj, "data", None)
    mats = getattr(data, "materials", None) or []
    for mat in mats:
        if mat is None:
            continue
        nodes = getattr(getattr(mat, "node_tree", None), "nodes", None)
        if nodes is None:
            continue
        bsdf = None
        get = getattr(nodes, "get", None)
        if callable(get):
            bsdf = get("Principled BSDF")
        if bsdf is None:
            try:
                bsdf = nodes["Principled BSDF"]
            except Exception:
                bsdf = None
        if bsdf is None:
            continue
        try:
            val = bsdf.inputs["Base Color"].default_value
            return (float(val[0]), float(val[1]), float(val[2]), 1.0)
        except Exception:
            continue
    return None


def apply_object_colors(state: dict):
    """Set obj.color on every scene object (WORKBENCH 'OBJECT'
    colour mode): the object's material Base Color when discoverable,
    else the per-group default."""
    for gname, objs in state["groups"].items():
        default = WORKBENCH_GROUP_COLORS.get(
            gname, (0.6, 0.6, 0.6, 1.0))
        for o in objs:
            try:
                o.color = _material_base_color(o) or default
            except Exception:
                pass


def setup_render_rig(plan: dict, engine: str = "EEVEE"):
    """Studio lighting rig + per-engine render settings (R6c).

    Bright neutral backdrop, three-point lighting with real fill,
    noise control tuned per engine. The camera itself is positioned
    per frame (point_camera + frame_camera); here it only gets its
    lens: a fixed vertical FOV matching frame_camera's fit maths."""
    scene = bpy.context.scene
    if engine == "WORKBENCH":
        # No GPU shader pipeline: shades by object colour under a
        # studio rig. This is the fallback that survives machines
        # where EEVEE's OpenGL context dies.
        scene.render.engine = "BLENDER_WORKBENCH"
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "OBJECT"
        shading.show_shadows = True
        shading.show_cavity = True
        try:
            # Use the same bright world colour as the other engines
            # instead of the default dark viewport backdrop.
            shading.background_type = "WORLD"
        except Exception:
            pass
    elif engine == "CYCLES":
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        # Noise control (R6c): the Pi's winning engine must produce
        # print-clean images — more samples + adaptive sampling, and
        # denoising whenever the build offers it. A denoiser config
        # problem must never kill the render: fall back to none.
        scene.cycles.samples = 128
        try:
            scene.cycles.use_adaptive_sampling = True
            scene.cycles.adaptive_threshold = 0.02
        except Exception:
            pass
        try:
            scene.cycles.denoiser = "OPENIMAGEDENOISE"
            scene.cycles.use_denoising = True
        except Exception:
            scene.cycles.use_denoising = False
    else:
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
        except Exception:
            scene.render.engine = "BLENDER_EEVEE"
        try:
            scene.eevee.taa_render_samples = 64
        except Exception:
            pass
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"

    # Bright neutral studio backdrop for every engine (R6c) — no
    # near-black anywhere; the dark shell parts must read against it.
    world = bpy.data.worlds.new("ReportWorld")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.62, 0.64, 0.66, 1.0)
        bg.inputs[1].default_value = 1.0

    # Colour management: AgX + a slight exposure lift so mid-tone
    # parts separate from the backdrop (guarded — property names
    # vary across Blender versions).
    if engine in ("EEVEE", "CYCLES"):
        try:
            scene.view_settings.view_transform = "AgX"
        except Exception:
            pass
        # Requirement 10: the 0.25 exposure lift blew the white
        # shell out to near-transparency against the bright
        # backdrop — highlights must hold for a printable read.
        try:
            scene.view_settings.exposure = 0.0
        except Exception:
            pass

    def sun(name, energy, rx, rz):
        data = bpy.data.lights.new(name, "SUN")
        data.energy = energy
        obj = bpy.data.objects.new(name, data)
        obj.rotation_euler = (math.radians(rx), 0, math.radians(rz))
        scene.collection.objects.link(obj)
        return obj

    sun("KeyLight", 3.2, 55, 35)
    sun("FillLight", 1.8, 70, -120)
    sun("RimLight", 2.6, 120, 180)

    cam_data = bpy.data.cameras.new("ReportCamera")
    cam_data.clip_end = 10.0
    try:
        cam_data.sensor_fit = "VERTICAL"
        cam_data.angle = math.radians(CAMERA_FOV_DEG)
    except Exception:
        pass
    cam_obj = bpy.data.objects.new("ReportCamera", cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    return cam_obj


def visible_bbox(state: dict):
    """World-space bounding box ((min),(max)) of the objects a frame
    will actually show — group members not hidden for render. The
    ground, lights and camera are not group members and never count
    toward framing. Returns None when nothing is visible."""
    lo = [math.inf, math.inf, math.inf]
    hi = [-math.inf, -math.inf, -math.inf]
    found = False
    for objs in state["groups"].values():
        for o in objs:
            if getattr(o, "hide_render", False):
                continue
            try:
                corners = o.bound_box
                mw = o.matrix_world
            except Exception:
                continue
            for corner in corners:
                try:
                    v = mw @ Vector(corner)
                    vals = (v[0], v[1], v[2])
                except Exception:
                    continue
                for i in range(3):
                    lo[i] = min(lo[i], vals[i])
                    hi[i] = max(hi[i], vals[i])
                found = True
    return (tuple(lo), tuple(hi)) if found else None


def _fallback_bbox(plan: dict):
    geom = plan.get("geom") or {}
    W = float(geom.get("shell_width_mm") or 70)
    H = float(geom.get("shell_height_mm") or 35)
    D = float(geom.get("shell_depth_mm") or 32)
    return ((0.0, 0.0, 0.0), (mm(W), mm(H), mm(D)))


def point_camera(cam_obj, location, target):
    """Place the camera at `location` (scene units) looking at
    `target` — the per-frame position comes from frame_camera()."""
    cam_obj.location = (location[0], location[1], location[2])
    direction = Vector(target) - Vector(cam_obj.location)
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y") \
        .to_euler()


def apply_frame(state: dict, plan: dict, frame: dict):
    visible = frame_visible_groups(frame)
    for gname, objs in state["groups"].items():
        show = gname in visible
        for o in objs:
            o.hide_render = not show
            o.hide_viewport = not show
    off = mm(frame_lid_offset_mm(plan, frame))
    for o in state["lid_riders"]:
        o.location.z = state["lid_base_z"].get(id(o), 0.0) + off


# ---------------------------------------------------------------------------
# Kit layout (step 1) + screen-on finale — presentation-only frame
# state. Both record exactly what they touch and restore it after
# the frame renders; the assembly itself is never modified.
# ---------------------------------------------------------------------------

def _obj_world_bbox(o):
    """(centre, min_z) of one object's world bounding box, or None."""
    try:
        corners = o.bound_box
        mw = o.matrix_world
    except Exception:
        return None
    pts = []
    for corner in corners:
        try:
            v = mw @ Vector(corner)
            pts.append((v[0], v[1], v[2]))
        except Exception:
            return None
    if not pts:
        return None
    centre = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    return centre, min(p[2] for p in pts)


def _update_depsgraph():
    """Force depsgraph evaluation so matrix_world / bound_box
    reflect the frame state just set. Without this, the first
    frame that shows a not-yet-evaluated object (the USB cable
    curve) measures a phantom bounding box, and kit-layout moves
    are framed on pre-move positions (Requirement 10)."""
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def apply_kit_layout(state: dict, plan: dict):
    """Spread the printed parts as a laid-out kit for the step-1
    photograph: bottom case at the origin area, lid slid +X clear of
    it, button caps (printed parts too, though their assembly step
    comes later) unhidden in a row on the ground in front (-Y).
    Returns the saved state for restore_layout()."""
    _update_depsgraph()
    geom = plan.get("geom") or {}
    W = float(geom.get("shell_width_mm") or 70)
    saved = []

    def record(o):
        saved.append((o, o.location.x, o.location.y, o.location.z,
                      o.hide_render, o.hide_viewport))

    riders = state.get("lid_riders") or []
    if riders:
        lid = riders[0]
        record(lid)
        lid.location.x += mm(W + 18.0)
    caps = list(state["groups"].get("button_caps") or [])

    def cap_x(o):
        bb = _obj_world_bbox(o)
        return bb[0][0] if bb else 0.0

    caps.sort(key=cap_x)
    for i, cap in enumerate(caps):
        bb = _obj_world_bbox(cap)
        if bb is None:
            continue
        (cx, cy, _cz), min_z = bb
        record(cap)
        cap.location.x += mm(8.0 + i * 13.0) - cx
        cap.location.y += mm(-15.0) - cy
        cap.location.z += -min_z
        cap.hide_render = False
        cap.hide_viewport = False
    return saved


def restore_layout(saved):
    for o, x, y, z, hr, hv in saved:
        o.location.x, o.location.y, o.location.z = x, y, z
        o.hide_render = hr
        o.hide_viewport = hv


def apply_screen_on(state: dict):
    """Light the display for the finale frame: swap the glass for an
    emissive material (and a bright object colour for WORKBENCH),
    and reveal the dim UI inset. Returns saved state, or None when
    the design has no display glass."""
    glass = None
    for o in state["groups"].get("display_lid") or []:
        if o.name.startswith("Display_Glass"):
            glass = o
            break
    if glass is None:
        return None
    try:
        color = tuple(glass.color)
    except Exception:
        color = None
    saved = {"glass": glass,
             "materials": list(glass.data.materials),
             "color": color}
    lit = _mat_col("Disp_ScreenOn", 0.72, 0.92, 1.0,
                   roughness=0.25, emission_strength=2.2)
    try:
        glass.data.materials.clear()
        glass.data.materials.append(lit)
    except Exception:
        if glass.data.materials:
            glass.data.materials[0] = lit
        else:
            glass.data.materials.append(lit)
    try:
        glass.color = (0.80, 0.95, 1.0, 1.0)
    except Exception:
        pass
    ui = state.get("screen_ui")
    if ui is not None:
        ui.hide_render = False
        ui.hide_viewport = False
    return saved


def restore_screen_on(state: dict, saved: dict):
    glass = saved["glass"]
    try:
        glass.data.materials.clear()
        for m in saved["materials"]:
            glass.data.materials.append(m)
    except Exception:
        if saved["materials"]:
            glass.data.materials[0] = saved["materials"][0]
    if saved.get("color") is not None:
        try:
            glass.color = saved["color"]
        except Exception:
            pass
    ui = state.get("screen_ui")
    if ui is not None:
        ui.hide_render = True
        ui.hide_viewport = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def render_plan_file(plan_path: str, engine: str = None) -> dict:
    if not BLENDER_AVAILABLE:
        print("[ReportImages] ERROR: bpy not available. "
              "Run inside Blender.")
        return {"ok": False, "reason": "bpy unavailable"}
    engine = resolve_engine(engine)
    print(f"PUROMO_ENGINE {engine}")
    with open(plan_path) as f:
        plan = json.load(f)

    out_dir = plan["output_dir"]
    os.makedirs(out_dir, exist_ok=True)

    print(f"[ReportImages] Design {plan['design_hash']} "
          f"({plan.get('model')}) — {len(plan['frames'])} frames")
    clear_scene()
    state = build_scene(plan)
    if engine == "WORKBENCH":
        apply_object_colors(state)
    if state.get("ground") is not None:
        try:
            state["ground"].color = (0.78, 0.78, 0.79, 1.0)
        except Exception:
            pass
    cam_obj = setup_render_rig(plan, engine)
    scene = bpy.context.scene

    results = []
    for frame in plan["frames"]:
        apply_frame(state, plan, frame)
        intent = frame.get("camera") or {}
        view = intent.get("view") or "home"
        # Kit layout first (it changes what is visible and where),
        # then frame the camera on the result.
        kit_saved = None
        if intent.get("layout") == "kit":
            kit_saved = apply_kit_layout(state, plan)
        ground = state.get("ground")
        if ground is not None:
            # The from-below inserts shot looks up at the bosses —
            # the workbench itself would block it.
            ground.hide_render = (view == "below")
        _update_depsgraph()
        bbox = sane_bbox(visible_bbox(state), _fallback_bbox(plan))
        location, target = frame_camera(bbox, view)
        point_camera(cam_obj, location, target)
        screen_saved = None
        if intent.get("screen_on"):
            screen_saved = apply_screen_on(state)
        path = os.path.join(out_dir, frame["filename"])
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        if screen_saved is not None:
            restore_screen_on(state, screen_saved)
        if kit_saved is not None:
            restore_layout(kit_saved)
        ok = os.path.exists(path) and os.path.getsize(path) > 0
        print(f"[ReportImages] step {frame['step_index']}: "
              f"{frame['filename']} {'OK' if ok else 'FAILED'}")
        if ok:
            stem = frame["filename"].rsplit(".", 1)[0]
            print(f"PUROMO_FRAME_OK {stem} engine={engine}")
        results.append({"step_index": frame["step_index"],
                        "title": frame["title"],
                        "filename": frame["filename"], "ok": ok})

    manifest = {"design_hash": plan["design_hash"],
                "model": plan.get("model"), "engine": engine,
                "frames": results}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    done = sum(1 for r in results if r["ok"])
    print(f"[ReportImages] {done}/{len(results)} frames rendered "
          f"→ {out_dir}")
    return {"ok": done == len(results), "rendered": done,
            "total": len(results), "output_dir": out_dir}


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--engine", default=None,
                        help="EEVEE | WORKBENCH | CYCLES "
                             "(or env PUROMO_RENDER_ENGINE)")
    args = parser.parse_args(argv)
    result = render_plan_file(args.plan, engine=args.engine)
    if not result.get("ok"):
        print(f"[ReportImages] INCOMPLETE: {result}")


if __name__ == "__main__":
    main()
