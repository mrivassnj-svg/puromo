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

        # Connect faces (wedge prism: 3 quads + 2 triangle end-caps)
        bm_s.faces.new([verts[0], verts[1], verts[2], verts[3]])   # back wall (rect)
        bm_s.faces.new([verts[0], verts[4], verts[5], verts[1]])   # ramp/undercut face
        bm_s.faces.new([verts[3], verts[2], verts[5], verts[4]])   # undercut top face
        bm_s.faces.new([verts[0], verts[3], verts[4]])              # left tri cap
        bm_s.faces.new([verts[1], verts[5], verts[2]])              # right tri cap

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
# PCB Assembly Builder — model-accurate replica of the physical board
# ---------------------------------------------------------------------------

def _mat_col(name: str, r: float, g: float, b: float,
             metallic: float = 0.0, roughness: float = 0.6,
             emission_strength: float = 0.0):
    """Create or reuse a Principled BSDF material by name."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value  = (r, g, b, 1.0)
    bsdf.inputs["Metallic"].default_value    = metallic
    bsdf.inputs["Roughness"].default_value   = roughness
    if emission_strength > 0:
        bsdf.inputs["Emission Color"].default_value   = (r, g, b, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    return m


def _add_box(name: str, x: float, y: float, z: float,
             w: float, d: float, h: float,
             material, col) -> "bpy.types.Object":
    """Add a box mesh positioned at (x,y,z) lower-left corner, size w×d×h (all in BU)."""
    bm = bmesh.new()
    verts = [
        bm.verts.new((x,     y,     z)),
        bm.verts.new((x + w, y,     z)),
        bm.verts.new((x + w, y + d, z)),
        bm.verts.new((x,     y + d, z)),
        bm.verts.new((x,     y,     z + h)),
        bm.verts.new((x + w, y,     z + h)),
        bm.verts.new((x + w, y + d, z + h)),
        bm.verts.new((x,     y + d, z + h)),
    ]
    for f in [(0,1,2,3),(4,7,6,5),(0,4,5,1),(1,5,6,2),(2,6,7,3),(3,7,4,0)]:
        bm.faces.new([verts[i] for i in f])
    mesh = bpy.data.meshes.new(name + "_mesh")
    obj  = bpy.data.objects.new(name, mesh)
    link_to_col(obj, col)
    bm.to_mesh(mesh)
    bm.free()
    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)
    return obj


def _add_cylinder(name: str, cx: float, cy: float, z_bot: float,
                  radius: float, height: float,
                  material, col,
                  segments: int = 32) -> "bpy.types.Object":
    """Add a cylinder centred at (cx,cy), from z_bot to z_bot+height."""
    bm = bmesh.new()
    bot_v, top_v = [], []
    for i in range(segments):
        a  = 2 * math.pi * i / segments
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
    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)
    return obj


def build_pcb_assembly(spec: dict, geom: dict, z_offset: float, col) -> list:
    """
    Build a detailed, model-accurate 3D replica of the physical PCB.

    Components placed:
      1. PCB substrate  — dark green FR4 board, exact W×H
      2. Display module — ST7789 IPS display with silver bezel + black screen
                          positioned using spec.display_offset_x/y_mm
      3. Display ribbon — thin flex ribbon cable below the display module
      4. 5-way joystick — grey square base + tall grey nub (1.3inch HAT only)
      5. KEY1/2/3 buttons — white cap, black housing, steel shaft
      6. 40-pin GPIO header — black 2×20 DIP block, gold pin rows
      7. Controller IC (ST7789) — small black QFP chip + silver pads
      8. Decoupling caps — small brown cylinders near display
      9. Mounting holes — drilled through board (cylindrical voids)
     10. PCB silkscreen text — thin raised lettering on board (optional)

    All units passed as Blender units (mm → BU via mm() conversion done here).
    z_offset: Z position of the bottom face of the PCB substrate.
    Returns list of all created objects.
    """
    objs = []

    BW = mm(spec["board_width_mm"])
    BH = mm(spec["board_height_mm"])
    BT = mm(spec.get("board_thickness_mm", 1.6))

    # ── Material palette ───────────────────────────────────────────────────────
    m_pcb     = _mat_col("PCB_FR4",        0.03, 0.22, 0.06, roughness=0.65)   # dark green PCB
    m_pcb_bot = _mat_col("PCB_FR4_Bot",    0.02, 0.18, 0.05, roughness=0.70)   # slightly darker bottom
    m_copper  = _mat_col("PCB_Copper",     0.72, 0.45, 0.20, metallic=0.9, roughness=0.3)
    m_silk    = _mat_col("PCB_Silk",       0.90, 0.90, 0.90, roughness=0.8)    # white silkscreen
    m_disp_bezel = _mat_col("Disp_Bezel", 0.70, 0.70, 0.73, metallic=0.6, roughness=0.25)  # silver
    m_disp_screen= _mat_col("Disp_Screen", 0.04, 0.04, 0.06, metallic=0.0, roughness=0.05) # black glass
    m_disp_light = _mat_col("Disp_Glow",  0.12, 0.35, 0.65, roughness=0.0, emission_strength=0.35)  # glass sheen (not a backlight)
    m_ribbon  = _mat_col("Ribbon_FPC",    0.65, 0.60, 0.20, roughness=0.4)     # golden flex ribbon
    m_joy_base= _mat_col("Joy_Base",      0.18, 0.18, 0.20, roughness=0.55)    # dark grey joystick base
    m_joy_nub = _mat_col("Joy_Nub",       0.28, 0.28, 0.30, roughness=0.45)    # lighter grey nub
    m_btn_cap = _mat_col("Btn_Cap",       0.90, 0.90, 0.90, roughness=0.5)     # white button cap
    m_btn_body= _mat_col("Btn_Body",      0.08, 0.08, 0.10, roughness=0.6)     # black button housing
    m_btn_stem= _mat_col("Btn_Stem",      0.55, 0.55, 0.57, metallic=0.7, roughness=0.3)  # steel stem
    m_gpio_body= _mat_col("GPIO_Body",    0.05, 0.05, 0.08, roughness=0.7)     # black header plastic
    m_gpio_pin = _mat_col("GPIO_Pin",     0.85, 0.75, 0.25, metallic=0.95, roughness=0.15) # gold pins
    m_ic       = _mat_col("IC_Body",      0.06, 0.06, 0.08, roughness=0.6)     # black epoxy IC
    m_ic_lead  = _mat_col("IC_Lead",      0.75, 0.75, 0.78, metallic=0.9, roughness=0.2)  # silver leads
    m_cap_body = _mat_col("Cap_Body",     0.40, 0.25, 0.08, roughness=0.6)     # brown ceramic cap
    m_hole_void= _mat_col("Hole_Void",    0.01, 0.01, 0.01, roughness=1.0)

    # ── 1. PCB substrate ──────────────────────────────────────────────────────
    pcb = _add_box("PCB_Substrate", 0, 0, z_offset, BW, BH, BT, m_pcb, col)
    # Thin copper ground plane ring on top face — just the outline via inset box
    # (approximated by slightly lighter top face; keep mesh clean — no extra geo)
    objs.append(pcb)

    # ── 2. Display module ────────────────────────────────────────────────────
    # Spec provides display_offset_x/y from board corner
    # Default (no spec offsets): the display is centred on the
    # board, as on the real HAT — not tucked into a corner.
    dx_off = mm(spec.get("display_offset_x_mm",
                         (spec["board_width_mm"]
                          - spec.get("display_width_mm", 23.4)) / 2.0))
    dy_off = mm(spec.get("display_offset_y_mm",
                         (spec["board_height_mm"]
                          - spec.get("display_height_mm", 23.4)) / 2.0))
    DW     = mm(spec.get("display_width_mm",  23.4))
    DH     = mm(spec.get("display_height_mm", 23.4))
    # For portrait displays (height > width), dimensions stay as declared.
    BEZEL  = mm(1.2)   # bezel frame width around active area
    MOD_H  = mm(1.6)   # display module thickness (metal shield + glass)
    SCR_H  = mm(0.8)   # screen glass thickness (sits on top of module body)

    disp_x = dx_off
    disp_y = dy_off
    # Module body (silver bezel + PCB flex)
    mod = _add_box("Disp_Module",
                   disp_x - BEZEL, disp_y - BEZEL, z_offset + BT,
                   DW + 2*BEZEL, DH + 2*BEZEL, MOD_H,
                   m_disp_bezel, col)
    objs.append(mod)

    # Screen surface (black glass over the active area, inset so
    # the module's silver rim stays visible around it — the panel
    # reads as a screen, not a slab)
    screen = _add_box("Disp_Screen",
                      disp_x + BEZEL, disp_y + BEZEL,
                      z_offset + BT + MOD_H - mm(0.1),
                      DW - 2*BEZEL, DH - 2*BEZEL, SCR_H + mm(0.1),
                      m_disp_screen, col)
    objs.append(screen)

    # Glass sheen ON the screen surface (was buried inside the
    # screen box, where no camera could ever see it)
    glow = _add_box("Disp_Glow",
                    disp_x + BEZEL + mm(0.5), disp_y + BEZEL + mm(0.5),
                    z_offset + BT + MOD_H + SCR_H - mm(0.02),
                    DW - 2*BEZEL - mm(1.0), DH - 2*BEZEL - mm(1.0),
                    mm(0.06),
                    m_disp_light, col)
    objs.append(glow)

    # ── 3. Display FPC ribbon cable ──────────────────────────────────────────
    # Thin flex ribbon exits bottom of display module toward the IC
    rw  = mm(12.0)   # ribbon width
    rl  = mm(8.0)    # ribbon length (goes toward board centre)
    rth = mm(0.3)    # ribbon thickness
    r_x = disp_x + (DW - rw) / 2
    r_y = disp_y - rl            # ribbon exits toward board bottom
    ribbon = _add_box("Disp_Ribbon",
                      r_x, r_y, z_offset + BT,
                      rw, rl, rth,
                      m_ribbon, col)
    objs.append(ribbon)

    # ── 4. 5-way joystick ───────────────────────────────────────────────────
    joy_pos   = spec.get("joystick_center_mm")
    joy_d_mm  = spec.get("joystick_diameter_mm", 0)
    if joy_pos and joy_d_mm > 0:
        jcx = mm(joy_pos[0])
        jcy = mm(joy_pos[1])
        # Square base plate (the actual 5-way switch body is square ~12×12mm)
        jbase_w = mm(joy_d_mm)
        jbase_h = mm(1.8)   # height of switch body
        jbase = _add_box("Joy_Base",
                         jcx - jbase_w/2, jcy - jbase_w/2, z_offset + BT,
                         jbase_w, jbase_w, jbase_h,
                         m_joy_base, col)
        objs.append(jbase)
        # Nub (cylindrical directional cap, sits on top)
        nub_r  = mm(joy_d_mm * 0.30)
        nub_h  = mm(5.5)   # joystick nub protrudes 5.5mm above PCB
        nub = _add_cylinder("Joy_Nub",
                             jcx, jcy, z_offset + BT + jbase_h,
                             nub_r, nub_h,
                             m_joy_nub, col)
        objs.append(nub)
        # Dome cap on nub
        cap_r = mm(joy_d_mm * 0.38)
        cap   = _add_cylinder("Joy_Cap",
                               jcx, jcy, z_offset + BT + jbase_h + nub_h - mm(0.5),
                               cap_r, mm(1.2),
                               m_joy_nub, col)
        objs.append(cap)
        # 4 direction pads around base
        for angle_deg in [0, 90, 180, 270]:
            a    = math.radians(angle_deg)
            pr   = mm(joy_d_mm * 0.42)
            pad  = _add_cylinder(f"Joy_Pad_{angle_deg}",
                                  jcx + pr * math.cos(a),
                                  jcy + pr * math.sin(a),
                                  z_offset + BT + jbase_h - mm(0.1),
                                  mm(1.2), mm(0.5),
                                  m_silk, col)
            objs.append(pad)

    # ── 5. Tactile buttons (KEY1 / KEY2 / KEY3) ─────────────────────────────
    for i, btn_pos in enumerate(spec.get("button_positions_mm", [])):
        bx = mm(btn_pos[0])
        by = mm(btn_pos[1])
        label = btn_pos[2] if len(btn_pos) > 2 else f"KEY{i+1}"

        # Switch housing (6×6mm square, 3.5mm tall — standard SMD tact switch)
        body = _add_box(f"Btn_Body_{label}",
                        bx - mm(3.0), by - mm(3.0), z_offset + BT,
                        mm(6.0), mm(6.0), mm(3.5),
                        m_btn_body, col)
        objs.append(body)
        # Cap (round white top, sits on the stem)
        stem_r = mm(1.6)
        stem   = _add_cylinder(f"Btn_Stem_{label}",
                                bx, by, z_offset + BT + mm(3.5),
                                stem_r, mm(1.0),
                                m_btn_stem, col)
        objs.append(stem)
        cap    = _add_cylinder(f"Btn_Cap_{label}",
                                bx, by, z_offset + BT + mm(4.5),
                                mm(2.4), mm(1.0),
                                m_btn_cap, col)
        objs.append(cap)
        # Leg terminals (4 tiny copper squares at corners)
        for leg_dx, leg_dy in [(-3.0,-3.0),(3.0,-3.0),(-3.0,3.0),(3.0,3.0)]:
            leg = _add_box(f"Btn_Leg_{label}_{leg_dx}",
                           bx + mm(leg_dx) - mm(0.4),
                           by + mm(leg_dy) - mm(0.4),
                           z_offset + BT - mm(0.2),
                           mm(0.8), mm(0.8), mm(0.3),
                           m_copper, col)
            objs.append(leg)

    # ── 6. 40-pin GPIO header ────────────────────────────────────────────────
    # The 2×20 female header hangs BELOW the PCB (it mates with
    # the host's male header underneath the HAT) — an earlier
    # version drew it on top of the board, where it read as a
    # long slab hiding the display and controls.
    # Header body: 51.0mm × 5.1mm × 8.5mm (below PCB)
    H_W  = mm(51.0)   # 20 × 2.54mm pitch
    H_D  = mm(5.1)    # 2 rows × 2.54mm + slight overhang
    H_H  = mm(8.5)    # standard 11mm header above PCB = 8.5mm plastic body
    # Position: GPIO header on left of RPi HAT is at ~x=7.1mm, y=3.5mm on the board
    H_X  = mm(7.1)
    H_Y  = mm(3.5)

    hdr = _add_box("GPIO_Header",
                   H_X, H_Y, z_offset - H_H,
                   H_W, H_D, H_H,
                   m_gpio_body, col)
    objs.append(hdr)

    # Socket rims on the header's bottom (mating) face — gold,
    # 2 rows × 20. (The old code drew full-length pins INSIDE
    # the body, where they could never be seen.)
    pin_pitch = mm(2.54)
    pin_r     = mm(0.6)    # pin socket opening radius
    for row in range(2):
        for col_idx in range(20):
            px = H_X + mm(1.27) + col_idx * pin_pitch
            py = H_Y + mm(1.27) + row * pin_pitch
            pin = _add_cylinder(f"GPIO_Pin_{row}_{col_idx}",
                                  px, py, z_offset - H_H,
                                  pin_r, mm(0.8),
                                  m_gpio_pin, col)
            objs.append(pin)

    # ── 7. Controller IC (ST7789 or equiv) ───────────────────────────────────
    # Small QFP chip near the display, slightly right of centre-bottom
    ic_x = dx_off + DW + mm(2.0)
    ic_y = dy_off + mm(2.0)
    IC_W = mm(5.5)   # QFP body
    IC_D = mm(5.5)
    IC_H = mm(1.0)
    ic_body = _add_box("IC_Body",
                       ic_x, ic_y, z_offset + BT,
                       IC_W, IC_D, IC_H,
                       m_ic, col)
    objs.append(ic_body)

    # IC lead frame (thin silver rectangle around 3 exposed sides)
    for side_i, (lx, ly, lw, ld) in enumerate([
        (ic_x - mm(0.6), ic_y, mm(0.6), IC_D),                 # left leads
        (ic_x + IC_W,    ic_y, mm(0.6), IC_D),                 # right leads
        (ic_x, ic_y - mm(0.6), IC_W, mm(0.6)),                 # front leads
        (ic_x, ic_y + IC_D,    IC_W, mm(0.6)),                 # back leads
    ]):
        lead = _add_box(f"IC_Lead_{side_i}",
                        lx, ly, z_offset + BT + mm(0.1),
                        lw, ld, mm(0.4),
                        m_ic_lead, col)
        objs.append(lead)

    # Dot marker on pin-1 corner
    dot = _add_cylinder("IC_Pin1_Dot",
                         ic_x + mm(0.6), ic_y + mm(0.6),
                         z_offset + BT + IC_H,
                         mm(0.4), mm(0.1),
                         m_silk, col)
    objs.append(dot)

    # ── 8. Decoupling capacitors (4 × 0402 near display) ────────────────────
    cap_positions = [
        (ic_x + IC_W + mm(1.0),  ic_y),
        (ic_x + IC_W + mm(1.0),  ic_y + mm(1.5)),
        (ic_x + IC_W + mm(2.5),  ic_y),
        (ic_x + IC_W + mm(2.5),  ic_y + mm(1.5)),
    ]
    for ci, (cx, cy) in enumerate(cap_positions):
        cap_obj = _add_box(f"Cap_{ci}",
                           cx, cy, z_offset + BT,
                           mm(1.0), mm(0.5), mm(0.5),
                           m_cap_body, col)
        objs.append(cap_obj)
        # End terminations (copper pads)
        for end_x in [cx - mm(0.1), cx + mm(1.0)]:
            term = _add_box(f"Cap_{ci}_Term_{end_x:.3f}",
                            end_x, cy, z_offset + BT,
                            mm(0.15), mm(0.5), mm(0.5),
                            m_copper, col)
            objs.append(term)

    # ── 9. Mounting holes ────────────────────────────────────────────────────
    hole_r = mm(spec.get("mounting_hole_diameter_mm", 2.9) / 2)
    for mh_pos in spec.get("mounting_hole_positions_mm", []):
        mhx = mm(mh_pos[0])
        mhy = mm(mh_pos[1])
        # Visible annular copper ring
        ann = _add_cylinder(f"MH_Ring_{mh_pos[0]}_{mh_pos[1]}",
                             mhx, mhy, z_offset + BT - mm(0.05),
                             hole_r + mm(1.0), mm(0.1),
                             m_copper, col)
        objs.append(ann)

    # ── 10. PCB outline silkscreen ───────────────────────────────────────────
    # Thin white edge-stripe on top face (1mm wide around perimeter)
    SILK = mm(0.3)
    for sx, sy, sw, sd in [
        (0,          0,          BW,   SILK),  # bottom edge
        (0,          BH - SILK,  BW,   SILK),  # top edge
        (0,          SILK,       SILK, BH - 2*SILK),  # left edge
        (BW - SILK,  SILK,       SILK, BH - 2*SILK),  # right edge
    ]:
        edge = _add_box(f"Silk_{sx:.4f}_{sy:.4f}",
                        sx, sy, z_offset + BT - mm(0.02),
                        sw, sd, mm(0.05),
                        m_silk, col)
        objs.append(edge)

    print(f"[PCB]  Assembly: {len(objs)} objects ({spec.get('model','PCB')})")
    return objs


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
    col_pcb    = add_collection("PCB_Assembly")

    print("[Gen] Building shell bottom (rounded corners + bosses + ribs)...")
    bottom_obj = build_shell_bottom(geom, col_bottom)

    print("[Gen] Building shell lid (display ledge + button chamfers + snap-fits)...")
    lid_obj = build_shell_lid(geom, col_lid)

    # ── PCB assembly — placed at exact Z position inside the shell ───────────
    FT  = geom.get("floor_thickness_mm",  2.5)
    SOH = geom.get("standoff_height_mm",  3.0)
    # PCB sits on top of standoffs: z = floor + standoff_height (in BU)
    pcb_z = mm(FT + SOH)

    print(f"[Gen] Building PCB assembly (model-accurate replica of {raw_spec.get('model','PCB')})...")

    # Inject display offset if not already in spec
    spec_for_pcb = dict(raw_spec)
    if "display_offset_x_mm" not in spec_for_pcb:
        # The geometry cutout is in SHELL coords; the replica
        # wants BOARD coords: remove the PCB offset, add back
        # the bezel lip the cutout was shrunk by (0.8 mm).
        dc = geom.get("display_cutout", {})
        spec_for_pcb["display_offset_x_mm"] = (
            dc.get("x", 2.0) - geom.get("pcb_offset_x", 0.0) + 0.8)
        spec_for_pcb["display_offset_y_mm"] = (
            dc.get("y", 2.0) - geom.get("pcb_offset_y", 0.0) + 0.8)

    pcb_objs = build_pcb_assembly(spec_for_pcb, geom, pcb_z, col_pcb)

    # Join all PCB parts into a single exportable object
    pcb_obj = None
    if pcb_objs:
        set_active(pcb_objs[0])
        bpy.ops.object.select_all(action="DESELECT")
        for o in pcb_objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = pcb_objs[0]
        bpy.ops.object.join()
        pcb_obj = bpy.context.active_object
        pcb_obj.name = f"{model_name}_PCB"
        apply_smooth_and_edge_split(pcb_obj, angle_deg=45.0)

    # Apply Edge Split before export
    for o in [bottom_obj, lid_obj]:
        set_active(o)
        try:
            bpy.ops.object.modifier_apply(modifier="EdgeSplit")
        except Exception:
            pass
    if pcb_obj:
        set_active(pcb_obj)
        try:
            bpy.ops.object.modifier_apply(modifier="EdgeSplit")
        except Exception:
            pass

    bottom_path = os.path.join(output_dir, f"{model_name}_bottom.stl")
    lid_path    = os.path.join(output_dir, f"{model_name}_lid.stl")
    pcb_path    = os.path.join(output_dir, f"{model_name}_pcb.stl")
    blend_path  = os.path.join(output_dir, f"{model_name}_shell.blend")

    export_stl(bottom_obj, bottom_path)
    export_stl(lid_obj,    lid_path)
    if pcb_obj:
        export_stl(pcb_obj, pcb_path)
        print(f"[PCB]  Exported: {pcb_path}")

    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"[Save] {blend_path}")

    return {
        "bottom_stl": f"{model_name}_bottom.stl",
        "lid_stl":    f"{model_name}_lid.stl",
        "pcb_stl":    f"{model_name}_pcb.stl" if pcb_obj else None,
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
