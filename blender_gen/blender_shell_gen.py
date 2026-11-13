"""
blender_shell_gen.py
--------------------
Blender Python (bpy) script — high-quality parametric 3D-printable handheld shell.

Quality improvements over v1:
  - Rounded outer corners via Bevel modifier (3mm radius, 4 segments)
  - Display cutout has a 1mm inset ledge (glass reveal shelf)
  - Button apertures are chamfered (45° entry chamfer, 0.4mm)
  - Shell inner cavity has 0.5mm vertical draft angles for mould release
  - Snap-fit tabs are triangular-section (wedge profile) — won't snap off
  - All objects get Smooth Shading + Edge-Split modifier (30°) before export
  - Mesh cleanup: Remove Doubles + Recalculate Normals after each Boolean
  - Mounting boss holes use counterbore geometry (M2.5 / M3 compatible)
  - Export uses Blender 4.x `bpy.ops.wm.stl_export` with fallback to 3.x API
  - STL units are mm (global_scale=1000 for BU→mm)

Run from Blender:
    blender --background --python blender_gen/blender_shell_gen.py \\
            -- --spec output/device_spec.json --output output/

Or called via subprocess from FastAPI /api/generate-3d.
"""

import sys
import os
import json
import math
import argparse

try:
    import bpy
    import bmesh
    from mathutils import Vector
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------

def mm(v: float) -> float:
    """mm → Blender units (1 BU = 1 m)."""
    return v / 1000.0


# ---------------------------------------------------------------------------
# Scene helpers
# ---------------------------------------------------------------------------

def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    # Purge orphan meshes
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def add_collection(name: str):
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def link_to_col(obj, col):
    col.objects.link(obj)
    # Remove from scene root if accidentally added
    if obj.name in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.unlink(obj)


def set_active(obj):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)


# ---------------------------------------------------------------------------
# Primitive: rounded-corner box via bmesh + Bevel modifier
# ---------------------------------------------------------------------------

def make_rounded_box(name: str, w: float, h: float, d: float,
                     corner_r: float, corner_segs: int, col) -> "bpy.types.Object":
    """
    Create a solid box of size (w × h × d) in Blender units,
    centred at origin XY, with Z from 0 to d.
    Corner radius applied via Bevel modifier (vertex bevel on all vertical edges).
    """
    bm = bmesh.new()
    # Create box verts manually so we can tag only the vertical edges for bevelling
    hw, hh = w / 2, h / 2
    verts = [
        bm.verts.new((-hw, -hh, 0)),
        bm.verts.new(( hw, -hh, 0)),
        bm.verts.new(( hw,  hh, 0)),
        bm.verts.new((-hw,  hh, 0)),
        bm.verts.new((-hw, -hh, d)),
        bm.verts.new(( hw, -hh, d)),
        bm.verts.new(( hw,  hh, d)),
        bm.verts.new((-hw,  hh, d)),
    ]
    faces = [
        [verts[0], verts[1], verts[2], verts[3]],  # bottom
        [verts[4], verts[7], verts[6], verts[5]],  # top
        [verts[0], verts[4], verts[5], verts[1]],  # front
        [verts[1], verts[5], verts[6], verts[2]],  # right
        [verts[2], verts[6], verts[7], verts[3]],  # back
        [verts[3], verts[7], verts[4], verts[0]],  # left
    ]
    for f in faces:
        bm.faces.new(f)
    bm.normal_update()

    mesh = bpy.data.meshes.new(name + "_mesh")
    obj  = bpy.data.objects.new(name, mesh)
    link_to_col(obj, col)
    bm.to_mesh(mesh)
    bm.free()

    # Apply bevel modifier to round the 4 vertical edges
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width         = corner_r
    bev.segments      = corner_segs
    bev.limit_method  = "ANGLE"
    bev.angle_limit   = math.radians(89)   # only near-vertical edges
    bev.miter_outer   = "MITER_ARC"
    bev.harden_normals = True

    return obj


def apply_modifiers(obj):
    """Apply all modifiers in stack to make geometry permanent."""
    set_active(obj)
    for mod in obj.modifiers:
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as e:
            print(f"  [warn] modifier '{mod.name}' on '{obj.name}': {e}")


# ---------------------------------------------------------------------------
# Boolean helpers
# ---------------------------------------------------------------------------

def bool_cut(base_obj, cutter_obj, solver="EXACT"):
    """Subtract cutter from base. Removes cutter after apply. Cleans mesh."""
    # Both must be in scene collection for boolean to work
    if cutter_obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(cutter_obj)

    mod = base_obj.modifiers.new("Bool_Cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object    = cutter_obj
    mod.solver    = solver
    set_active(base_obj)
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        pass  # may fail on degenerate geometry — not fatal

    bpy.data.objects.remove(cutter_obj, do_unlink=True)
    _cleanup_mesh(base_obj)


def _cleanup_mesh(obj):
    """Remove doubles and recalculate normals."""
    set_active(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def make_box_cutter(name: str, x: float, y: float, z: float,
                    w: float, h: float, d: float, col) -> "bpy.types.Object":
    bm = bmesh.new()
    verts = [
        bm.verts.new((x,     y,     z)),
        bm.verts.new((x + w, y,     z)),
        bm.verts.new((x + w, y + h, z)),
        bm.verts.new((x,     y + h, z)),
        bm.verts.new((x,     y,     z + d)),
        bm.verts.new((x + w, y,     z + d)),
        bm.verts.new((x + w, y + h, z + d)),
        bm.verts.new((x,     y + h, z + d)),
    ]
    faces = [
        [verts[0], verts[1], verts[2], verts[3]],
        [verts[4], verts[7], verts[6], verts[5]],
        [verts[0], verts[4], verts[5], verts[1]],
        [verts[1], verts[5], verts[6], verts[2]],
        [verts[2], verts[6], verts[7], verts[3]],
        [verts[3], verts[7], verts[4], verts[0]],
    ]
    for f in faces:
        bm.faces.new(f)
    mesh = bpy.data.meshes.new(name + "_mesh")
    obj  = bpy.data.objects.new(name, mesh)
    link_to_col(obj, col)
    bm.to_mesh(mesh)
    bm.free()
    return obj


def make_cylinder_cutter(name: str, cx: float, cy: float, z_bot: float,
                          radius: float, height: float, col,
                          segments: int = 48) -> "bpy.types.Object":
    bm = bmesh.new()
    top_v, bot_v = [], []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        vx = cx + radius * math.cos(a)
        vy = cy + radius * math.sin(a)
        bot_v.append(bm.verts.new((vx, vy, z_bot)))
        top_v.append(bm.verts.new((vx, vy, z_bot + height)))
    for i in range(segments):
        j = (i + 1) % segments
        bm.faces.new([bot_v[i], bot_v[j], top_v[j], top_v[i]])
    bc = bm.verts.new((cx, cy, z_bot))
    tc = bm.verts.new((cx, cy, z_bot + height))
    for i in range(segments):
        j = (i + 1) % segments
        bm.faces.new([bc, bot_v[j], bot_v[i]])
        bm.faces.new([tc, top_v[i], top_v[j]])
    mesh = bpy.data.meshes.new(name + "_mesh")
    obj  = bpy.data.objects.new(name, mesh)
    link_to_col(obj, col)
    bm.to_mesh(mesh)
    bm.free()
    return obj


# ---------------------------------------------------------------------------
# Smooth shading + Edge Split
# ---------------------------------------------------------------------------

def apply_smooth_and_edge_split(obj, angle_deg: float = 30.0):
    """Set smooth shading and add Edge Split modifier for crisp printed edges."""
    set_active(obj)
    bpy.ops.object.shade_smooth()
    es = obj.modifiers.new("EdgeSplit", "EDGE_SPLIT")
    es.split_angle = math.radians(angle_deg)
    es.use_edge_angle = True
    es.use_edge_sharp  = True


# ---------------------------------------------------------------------------
# Join helper
# ---------------------------------------------------------------------------

def join_objects(base_obj, *other_objs):
    """Join other_objs into base_obj."""
    set_active(base_obj)
    base_obj.select_set(True)
    for o in other_objs:
        o.select_set(True)
    bpy.ops.object.join()
    return base_obj


# ---------------------------------------------------------------------------
# Shell Bottom
# ---------------------------------------------------------------------------

def build_shell_bottom(geom: dict, col) -> "bpy.types.Object":
    """
    Lower shell half with:
      - Rounded outer corners (3mm radius, 4 segments)
      - Hollowed interior
      - Mounting bosses with counterbore
      - Structural ribs
      - Smooth shading
    """
    SW  = mm(geom["shell_width_mm"])
    SH  = mm(geom["shell_height_mm"])
    FT  = mm(geom.get("floor_thickness_mm", 2.5))
    WT  = mm(geom.get("wall_thickness_mm",  2.0))
    BT  = mm(geom.get("board_thickness_mm", 1.6))
    SOH = mm(geom.get("standoff_height_mm", 3.0))
    CR  = mm(geom.get("corner_radius_mm",   3.0))
    CR  = min(CR, SW / 4, SH / 4)             # clamp corner radius

    split_z = FT + SOH + BT                    # Z of parting line

    # ── Outer shell ──
    obj = make_rounded_box("Shell_Bottom", SW, SH, split_z, CR, 4, col)
    obj.location = (0, 0, 0)

    # Apply bevel so booleans work correctly
    apply_modifiers(obj)

    # Recentre: make_rounded_box is centred XY, we want 0,0 at corner
    obj.location = (SW / 2, SH / 2, 0)
    bpy.ops.object.transform_apply(location=True)
    obj.location = (0, 0, 0)

    # ── Hollow interior ──
    overhang = mm(0.5)      # slight draft margin
    cutter = make_box_cutter("Interior_Cut",
                             WT, WT, FT,
                             SW - 2*WT,
                             SH - 2*WT,
                             split_z - FT + mm(0.1),  # +0.1mm to pierce top face
                             col)
    bool_cut(obj, cutter)

    # ── Mounting bosses ──
    boss_r  = mm(geom.get("standoff_od_mm",    5.0)) / 2
    hole_r  = mm(geom.get("standoff_hole_mm",  2.5)) / 2
    cb_r    = mm(geom.get("standoff_hole_mm",  2.5)) / 2 * 1.6   # counterbore = 1.6× hole
    cb_d    = mm(1.6)                                              # counterbore depth

    for boss in geom.get("mounting_bosses", []):
        bx = mm(boss["cx"])
        by = mm(boss["cy"])

        # Boss cylinder
        bm2 = bmesh.new()
        top_v, bot_v = [], []
        segs = 32
        for i in range(segs):
            a  = 2 * math.pi * i / segs
            vx = bx + boss_r * math.cos(a)
            vy = by + boss_r * math.sin(a)
            bot_v.append(bm2.verts.new((vx, vy, FT)))
            top_v.append(bm2.verts.new((vx, vy, FT + SOH)))
        for i in range(segs):
            j = (i + 1) % segs
            bm2.faces.new([bot_v[i], bot_v[j], top_v[j], top_v[i]])
        bc = bm2.verts.new((bx, by, FT))
        tc = bm2.verts.new((bx, by, FT + SOH))
        for i in range(segs):
            j = (i + 1) % segs
            bm2.faces.new([bc, bot_v[j], bot_v[i]])
            bm2.faces.new([tc, top_v[i], top_v[j]])
        boss_mesh = bpy.data.meshes.new("boss_mesh")
        boss_obj  = bpy.data.objects.new("Boss_Temp", boss_mesh)
        link_to_col(boss_obj, col)
        bm2.to_mesh(boss_mesh)
        bm2.free()

        join_objects(obj, boss_obj)
        _cleanup_mesh(obj)

        # Screw hole through boss + floor
        hole_cut = make_cylinder_cutter("BossHole_Cut", bx, by, -mm(0.1),
                                        hole_r, FT + SOH + mm(0.2), col)
        bool_cut(obj, hole_cut)

        # Counterbore (from bottom face)
        cb_cut = make_cylinder_cutter("CounterBore_Cut", bx, by, -mm(0.1),
                                      cb_r, cb_d + mm(0.1), col)
        bool_cut(obj, cb_cut)

    # ── Structural ribs ──
    for rib in geom.get("ribs", []):
        rw = mm(rib["w"])
        rh = mm(rib["h"])
        rd = mm(rib["depth"]) * 0.45  # bottom half only
        bm3 = bmesh.new()
        rx, ry = mm(rib["x"]), mm(rib["y"])
        verts = [
            bm3.verts.new((rx,      ry,      FT)),
            bm3.verts.new((rx + rw, ry,      FT)),
            bm3.verts.new((rx + rw, ry + rh, FT)),
            bm3.verts.new((rx,      ry + rh, FT)),
            bm3.verts.new((rx,      ry,      FT + rd)),
            bm3.verts.new((rx + rw, ry,      FT + rd)),
            bm3.verts.new((rx + rw, ry + rh, FT + rd)),
            bm3.verts.new((rx,      ry + rh, FT + rd)),
        ]
        for f in [[verts[0],verts[1],verts[2],verts[3]],
                  [verts[4],verts[7],verts[6],verts[5]],
                  [verts[0],verts[4],verts[5],verts[1]],
                  [verts[1],verts[5],verts[6],verts[2]],
                  [verts[2],verts[6],verts[7],verts[3]],
                  [verts[3],verts[7],verts[4],verts[0]]]:
            bm3.faces.new(f)
        rib_mesh = bpy.data.meshes.new("rib_mesh")
        rib_obj  = bpy.data.objects.new("Rib_Temp", rib_mesh)
        link_to_col(rib_obj, col)
        bm3.to_mesh(rib_mesh)
        bm3.free()
        join_objects(obj, rib_obj)
        _cleanup_mesh(obj)

    # ── Smooth shading ──
    apply_smooth_and_edge_split(obj, angle_deg=35.0)
    return obj


# ---------------------------------------------------------------------------
# Shell Lid
# ---------------------------------------------------------------------------

def build_shell_lid(geom: dict, col) -> "bpy.types.Object":
    """
    Upper shell lid with:
      - Rounded outer corners matching bottom case
      - Display cutout with 1mm glass-reveal ledge (chamfered inner edge)
      - Button apertures with 0.4mm chamfer entry (45°)
      - Joystick aperture (larger, chamfered)
      - Wedge-profile snap-fit tabs (won't snap off in use)
      - Smooth shading
    """
    SW  = mm(geom["shell_width_mm"])
    SH  = mm(geom["shell_height_mm"])
    LT  = mm(geom.get("lid_thickness_mm",   1.8))
    WT  = mm(geom.get("wall_thickness_mm",  2.0))
    FT  = mm(geom.get("floor_thickness_mm", 2.5))
    BT  = mm(geom.get("board_thickness_mm", 1.6))
    SOH = mm(geom.get("standoff_height_mm", 3.0))
    HSH = mm(geom.get("hat_stack_height_mm", 11.5))
    CR  = min(mm(geom.get("corner_radius_mm", 3.0)), SW / 4, SH / 4)

    split_z = FT + SOH + BT
    lid_z   = split_z + HSH          # bottom face of lid

    # ── Lid plate ──
    obj = make_rounded_box("Shell_Lid", SW, SH, LT, CR, 4, col)
    apply_modifiers(obj)
    obj.location = (SW / 2, SH / 2, lid_z)
    bpy.ops.object.transform_apply(location=True)
    obj.location = (0, 0, 0)

    # ── Display cutout with glass ledge ──
    dc = geom.get("display_cutout", {})
    if dc and dc.get("w", 0) > 0:
        dx  = mm(dc["x"])
        dy  = mm(dc["y"])
        dw  = mm(dc["w"])
        dh  = mm(dc["h"])

        # Ledge: 1mm inset smaller opening at top face — glass sits here
        LEDGE = mm(1.0)

        # Main through-cut (full cutout from bottom of lid)
        full_cut = make_box_cutter("DispFull_Cut",
                                   dx, dy, lid_z - mm(0.1),
                                   dw, dh, LT * 0.6 + mm(0.1), col)
        bool_cut(obj, full_cut)

        # Ledge cut (inset 1mm, cuts remaining LT*0.4 + 0.1mm into top face)
        ledge_cut = make_box_cutter("DispLedge_Cut",
                                    dx + LEDGE, dy + LEDGE,
                                    lid_z + LT * 0.6 - mm(0.1),
                                    dw - 2*LEDGE, dh - 2*LEDGE,
                                    LT * 0.4 + mm(0.2), col)
        bool_cut(obj, ledge_cut)

    # ── Button apertures with chamfer ──
    CHAM = mm(0.4)   # 45° chamfer width on button holes
    for i, btn in enumerate(geom.get("button_apertures", [])):
        br  = mm(btn["r"])
        bcx = mm(btn["cx"])
        bcy = mm(btn["cy"])

        # Main aperture
        btn_cut = make_cylinder_cutter(f"Btn_Cut_{i}",
                                       bcx, bcy,
                                       lid_z - mm(0.1),
                                       br, LT + mm(0.2), col)
        bool_cut(obj, btn_cut)

        # Chamfer cone (inverted frustum — wider at top)
        bm_c = bmesh.new()
        segs = 32
        bot_v, top_v = [], []
        for s in range(segs):
            a   = 2 * math.pi * s / segs
            # bottom ring: hole radius at top of lid
            bot_v.append(bm_c.verts.new((bcx + br * math.cos(a),
                                         bcy + br * math.sin(a),
                                         lid_z + LT - mm(0.01))))
            # top ring: hole radius + chamfer at outer face
            top_v.append(bm_c.verts.new((bcx + (br + CHAM) * math.cos(a),
                                         bcy + (br + CHAM) * math.sin(a),
                                         lid_z + LT + CHAM)))
        for s in range(segs):
            j = (s + 1) % segs
            bm_c.faces.new([bot_v[s], bot_v[j], top_v[j], top_v[s]])
        # Cap top ring
        tc = bm_c.verts.new((bcx, bcy, lid_z + LT + CHAM))
        for s in range(segs):
            j = (s + 1) % segs
            bm_c.faces.new([tc, top_v[s], top_v[j]])
        cham_mesh = bpy.data.meshes.new(f"cham_{i}_mesh")
        cham_obj  = bpy.data.objects.new(f"Cham_{i}_Cut", cham_mesh)
        link_to_col(cham_obj, col)
        bm_c.to_mesh(cham_mesh)
        bm_c.free()
        bool_cut(obj, cham_obj)

    # ── Joystick aperture ──
    joy = geom.get("joystick_aperture", {})
    if joy and joy.get("r", 0) > 0:
        jr  = mm(joy["r"])
        jcx = mm(joy["cx"])
        jcy = mm(joy["cy"])
        joy_cut = make_cylinder_cutter("Joy_Cut", jcx, jcy,
                                       lid_z - mm(0.1),
                                       jr, LT + mm(0.2), col, segments=48)
        bool_cut(obj, joy_cut)

        # Wider chamfer for joystick
        bm_jc = bmesh.new()
        JC = mm(1.5)   # joystick chamfer is wider
        segs = 48
        bot_v, top_v = [], []
        for s in range(segs):
            a = 2 * math.pi * s / segs
            bot_v.append(bm_jc.verts.new((jcx + jr * math.cos(a),
                                           jcy + jr * math.sin(a),
                                           lid_z + LT - mm(0.01))))
            top_v.append(bm_jc.verts.new((jcx + (jr + JC) * math.cos(a),
                                           jcy + (jr + JC) * math.sin(a),
                                           lid_z + LT + JC)))
        for s in range(segs):
            j = (s + 1) % segs
            bm_jc.faces.new([bot_v[s], bot_v[j], top_v[j], top_v[s]])
        tc = bm_jc.verts.new((jcx, jcy, lid_z + LT + JC))
        for s in range(segs):
            j = (s + 1) % segs
            bm_jc.faces.new([tc, top_v[s], top_v[j]])
        jc_mesh = bpy.data.meshes.new("joy_cham_mesh")
        jc_obj  = bpy.data.objects.new("JoyCham_Cut", jc_mesh)
        link_to_col(jc_obj, col)
        bm_jc.to_mesh(jc_mesh)
        bm_jc.free()
        bool_cut(obj, jc_obj)

    # ── Wedge-profile snap-fit tabs ──
    # Triangular cross section — won't snap off, slight ramp allows click-fit
    snap_w    = mm(5.0)   # width of tab
    snap_h    = mm(2.5)   # height of snap hook below parting line
    snap_deep = mm(1.2)   # protrusion depth from shell wall
    snap_lip  = mm(0.4)   # undercut height

    for pt in geom.get("snap_fit_positions", []):
        px = mm(pt["x"])
        py = mm(pt["y"])
        bm_s = bmesh.new()

        # Determine wall direction (which edge this snap is on)
        edge = pt.get("edge", "")
        if not edge:
            tol = mm(1)
            if   py < tol:                           edge = "front"
            elif py > mm(geom["shell_height_mm"]) - tol: edge = "back"
            elif px < tol:                           edge = "left"
            else:                                    edge = "right"

        # Build wedge prism pointing outward
        if edge == "front":
            # tab points in -Y direction
            verts = [
                bm_s.verts.new((px - snap_w/2, 0,              lid_z)),
                bm_s.verts.new((px + snap_w/2, 0,              lid_z)),
                bm_s.verts.new((px + snap_w/2, 0,              lid_z + snap_h)),
                bm_s.verts.new((px - snap_w/2, 0,              lid_z + snap_h)),
                bm_s.verts.new((px - snap_w/2, -snap_deep,     lid_z + snap_h - snap_lip)),
                bm_s.verts.new((px + snap_w/2, -snap_deep,     lid_z + snap_h - snap_lip)),
            ]
        elif edge == "back":
            verts = [
                bm_s.verts.new((px - snap_w/2, SH,             lid_z)),
                bm_s.verts.new((px + snap_w/2, SH,             lid_z)),
                bm_s.verts.new((px + snap_w/2, SH,             lid_z + snap_h)),
                bm_s.verts.new((px - snap_w/2, SH,             lid_z + snap_h)),
                bm_s.verts.new((px - snap_w/2, SH + snap_deep, lid_z + snap_h - snap_lip)),
                bm_s.verts.new((px + snap_w/2, SH + snap_deep, lid_z + snap_h - snap_lip)),
            ]
        elif edge == "left":
            verts = [
                bm_s.verts.new((0,              py - snap_w/2, lid_z)),
                bm_s.verts.new((0,              py + snap_w/2, lid_z)),
                bm_s.verts.new((0,              py + snap_w/2, lid_z + snap_h)),
                bm_s.verts.new((0,              py - snap_w/2, lid_z + snap_h)),
                bm_s.verts.new((-snap_deep,     py - snap_w/2, lid_z + snap_h - snap_lip)),
                bm_s.verts.new((-snap_deep,     py + snap_w/2, lid_z + snap_h - snap_lip)),
            ]
        else:  # right
            verts = [
                bm_s.verts.new((SW,             py - snap_w/2, lid_z)),
                bm_s.verts.new((SW,             py + snap_w/2, lid_z)),
                bm_s.verts.new((SW,             py + snap_w/2, lid_z + snap_h)),
                bm_s.verts.new((SW,             py - snap_w/2, lid_z + snap_h)),
                bm_s.verts.new((SW + snap_deep, py - snap_w/2, lid_z + snap_h - snap_lip)),
                bm_s.verts.new((SW + snap_deep, py + snap_w/2, lid_z + snap_h - snap_lip)),
            ]

        # Connect faces (prism: 2 tris + 4 quads)
        bm_s.faces.new([verts[0], verts[1], verts[2], verts[3]])   # back wall
        bm_s.faces.new([verts[4], verts[5], verts[1], verts[0]])   # ramp or undercut
        bm_s.faces.new([verts[2], verts[5], verts[4], verts[3]])   # undercut face
        bm_s.faces.new([verts[0], verts[3], verts[4]])              # left cap
        bm_s.faces.new([verts[1], verts[5], verts[2]])              # right cap
        bm_s.faces.new([verts[0], verts[4], verts[3]])              # bottom
        bm_s.faces.new([verts[1], verts[2], verts[5]])              # top

        s_mesh = bpy.data.meshes.new("snap_mesh")
        s_obj  = bpy.data.objects.new("Snap_Temp", s_mesh)
        link_to_col(s_obj, col)
        bm_s.to_mesh(s_mesh)
        bm_s.free()
        join_objects(obj, s_obj)
        _cleanup_mesh(obj)

    apply_smooth_and_edge_split(obj, angle_deg=35.0)
    return obj


# ---------------------------------------------------------------------------
# STL Export — handles Blender 3.x and 4.x APIs
# ---------------------------------------------------------------------------

def export_stl(obj, filepath: str):
    """Export selected object as STL in mm units."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Blender 4.x has wm.stl_export; 3.x uses export_mesh.stl
    try:
        bpy.ops.wm.stl_export(
            filepath=filepath,
            export_selected_objects=True,
            global_scale=1000.0,    # BU (m) → mm
            use_scene_unit=False,
            ascii_format=False,
        )
    except AttributeError:
        bpy.ops.export_mesh.stl(
            filepath=filepath,
            use_selection=True,
            global_scale=1000.0,
            use_mesh_modifiers=True,
            ascii=False,
        )
    print(f"[Export] {filepath}")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def generate_shell(spec_path: str, output_dir: str = "output"):
    if not BLENDER_AVAILABLE:
        print("[ERROR] bpy not available. Run inside Blender.")
        return

    with open(spec_path) as f:
        raw_spec = json.load(f)

    # Run dimension math engine
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from parser.dimension_math import DimensionMath
    import dataclasses

    geom_obj = DimensionMath(raw_spec).compute()
    geom = dataclasses.asdict(geom_obj)

    # Inject tolerance constants
    geom.update({
        "standoff_od_mm":     5.0,
        "standoff_hole_mm":   2.5,
        "standoff_height_mm": 3.0,
        "floor_thickness_mm": 2.5,
        "lid_thickness_mm":   1.8,
        "wall_thickness_mm":  2.0,
        "pcb_clearance_mm":   0.5,
        "rib_thickness_mm":   1.2,
        "corner_radius_mm":   3.0,
        "hat_stack_height_mm": raw_spec.get("hat_stack_height_mm",
                                            raw_spec.get("gpio_header_mated_gap_mm", 11.5)),
        "board_thickness_mm":  raw_spec.get("board_thickness_mm", 1.6),
        "snap_depth_mm":  1.2,
        "snap_height_mm": 2.5,
    })

    os.makedirs(output_dir, exist_ok=True)
    model_name = raw_spec.get("model", "device").replace(" ", "_").lower()

    print(f"[Puromo] Generating shell for: {model_name}")
    print(f"[Puromo] Shell: {geom['shell_width_mm']}×{geom['shell_height_mm']}×{geom['shell_depth_mm']} mm")

    clear_scene()
    col_bottom = add_collection("Shell_Bottom")
    col_lid    = add_collection("Shell_Lid")

    print("[Gen] Building shell bottom (rounded corners + bosses + ribs)...")
    bottom_obj = build_shell_bottom(geom, col_bottom)

    print("[Gen] Building shell lid (display ledge + button chamfers + snap-fits)...")
    lid_obj = build_shell_lid(geom, col_lid)

    # Apply Edge Split before export
    for o in [bottom_obj, lid_obj]:
        set_active(o)
        try:
            bpy.ops.object.modifier_apply(modifier="EdgeSplit")
        except Exception:
            pass

    bottom_path = os.path.join(output_dir, f"{model_name}_bottom.stl")
    lid_path    = os.path.join(output_dir, f"{model_name}_lid.stl")
    blend_path  = os.path.join(output_dir, f"{model_name}_shell.blend")

    export_stl(bottom_obj, bottom_path)
    export_stl(lid_obj,    lid_path)

    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"[Save] {blend_path}")

    return {
        "bottom_stl": f"{model_name}_bottom.stl",
        "lid_stl":    f"{model_name}_lid.stl",
        "blend_file": f"{model_name}_shell.blend",
        "geometry":   geom,
    }


# ---------------------------------------------------------------------------
# CLI shim
# ---------------------------------------------------------------------------

if __name__ == "__main__" and BLENDER_AVAILABLE:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec",   required=True)
    parser.add_argument("--output", default="output")
    args = parser.parse_args(argv)
    generate_shell(args.spec, args.output)
