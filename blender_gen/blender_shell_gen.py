"""
blender_shell_gen.py
--------------------
Blender Python (bpy) script that generates a full parametric 3D printable
handheld shell from a ShellGeometry object.

Run from inside Blender:
    blender --background --python blender_gen/blender_shell_gen.py -- --spec path/to/spec.json

Or called programmatically via subprocess from the UI backend.

Generates:
  - shell_bottom.stl  — lower case with mounting bosses, snap-fit receivers
  - shell_lid.stl     — upper case with display cutout, button/joystick apertures
  - shell_full.blend  — combined Blender project for review / export
"""

import sys
import os
import json
import math
import argparse

# Guard: only import bpy when actually running inside Blender
try:
    import bpy
    import bmesh
    from mathutils import Vector
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Utility helpers (pure math, no bpy)
# ---------------------------------------------------------------------------

def mm2bl(mm: float) -> float:
    """Convert millimetres → Blender units (1 BU = 1 m = 1000 mm)."""
    return mm / 1000.0


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for col in bpy.data.collections:
        bpy.data.collections.remove(col)


def add_collection(name: str):
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def new_mesh_object(name: str, collection):
    mesh = bpy.data.meshes.new(name + "_mesh")
    obj  = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj, mesh


# ---------------------------------------------------------------------------
# Primitive builders (using bmesh)
# ---------------------------------------------------------------------------

def build_box(bm, x, y, z, w, h, d):
    """Add a solid box to bmesh at position (x,y,z) with size (w,h,d)."""
    verts = [
        bm.verts.new(Vector((x,     y,     z))),
        bm.verts.new(Vector((x + w, y,     z))),
        bm.verts.new(Vector((x + w, y + h, z))),
        bm.verts.new(Vector((x,     y + h, z))),
        bm.verts.new(Vector((x,     y,     z + d))),
        bm.verts.new(Vector((x + w, y,     z + d))),
        bm.verts.new(Vector((x + w, y + h, z + d))),
        bm.verts.new(Vector((x,     y + h, z + d))),
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


def build_cylinder(bm, cx, cy, z_bot, radius, height, segments=32):
    """Add an upright cylinder to bmesh."""
    top_verts  = []
    bot_verts  = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        vx = cx + radius * math.cos(angle)
        vy = cy + radius * math.sin(angle)
        bot_verts.append(bm.verts.new(Vector((vx, vy, z_bot))))
        top_verts.append(bm.verts.new(Vector((vx, vy, z_bot + height))))

    # Side faces
    for i in range(segments):
        j = (i + 1) % segments
        bm.faces.new([bot_verts[i], bot_verts[j], top_verts[j], top_verts[i]])

    # Cap faces
    bot_center = bm.verts.new(Vector((cx, cy, z_bot)))
    top_center = bm.verts.new(Vector((cx, cy, z_bot + height)))
    for i in range(segments):
        j = (i + 1) % segments
        bm.faces.new([bot_center, bot_verts[j], bot_verts[i]])
        bm.faces.new([top_center, top_verts[i], top_verts[j]])


# ---------------------------------------------------------------------------
# Boolean operations via Blender modifiers
# ---------------------------------------------------------------------------

def apply_boolean_difference(base_obj, cutter_obj):
    """Subtract cutter_obj from base_obj using a Boolean modifier."""
    mod = base_obj.modifiers.new(name="Bool_Cut", type="BOOLEAN")
    mod.operation  = "DIFFERENCE"
    mod.object     = cutter_obj
    mod.solver     = "EXACT"
    bpy.context.view_layer.objects.active = base_obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter_obj, do_unlink=True)


def make_cutter_box(name, x, y, z, w, h, d, collection):
    """Create a temporary box mesh object used as a Boolean cutter."""
    bm  = bmesh.new()
    build_box(bm, x, y, z, w, h, d)
    obj, mesh = new_mesh_object(name, collection)
    bm.to_mesh(mesh)
    bm.free()
    return obj


def make_cutter_cylinder(name, cx, cy, z_bot, radius, height, collection, segments=32):
    bm  = bmesh.new()
    build_cylinder(bm, cx, cy, z_bot, radius, height, segments)
    obj, mesh = new_mesh_object(name, collection)
    bm.to_mesh(mesh)
    bm.free()
    return obj


# ---------------------------------------------------------------------------
# Shell bottom (lower case)
# ---------------------------------------------------------------------------

def build_shell_bottom(geom: dict, col) -> "bpy.types.Object":
    """
    Builds the lower shell half:
      - Outer box
      - Hollow interior (Boolean subtract inner box)
      - Mounting bosses / standoffs
      - Snap-fit receiver sockets
      - Structural ribs
    """
    sw  = mm2bl(geom["shell_width_mm"])
    sh  = mm2bl(geom["shell_height_mm"])
    # bottom half depth = from floor to split line
    bt  = mm2bl(geom.get("board_thickness_mm", 1.6))
    standoff_h = mm2bl(geom.get("standoff_height_mm", 3.0))
    floor_t    = mm2bl(geom.get("floor_thickness_mm", 2.5))
    split_z    = floor_t + standoff_h + bt     # Z of parting line
    wall_t     = mm2bl(geom.get("wall_thickness_mm", 2.0))

    # --- Outer solid ---
    bm = bmesh.new()
    build_box(bm, 0, 0, 0, sw, sh, split_z)
    obj, mesh = new_mesh_object("Shell_Bottom", col)
    bm.to_mesh(mesh)
    bm.free()

    # --- Hollow out interior ---
    inner_clearance = mm2bl(geom.get("pcb_clearance_mm", 0.5))
    ix = wall_t
    iy = wall_t
    iw = sw - 2 * wall_t
    ih = sh - 2 * wall_t
    id_ = split_z - floor_t
    cutter = make_cutter_box("Interior_Cut", ix, iy, floor_t, iw, ih, id_ + 0.001, col)
    apply_boolean_difference(obj, cutter)

    # --- Mounting bosses (standoffs) ---
    boss_r  = mm2bl(geom.get("standoff_od_mm", 5.0)) / 2
    hole_r  = mm2bl(geom.get("standoff_hole_mm", 2.9)) / 2
    for boss in geom.get("mounting_bosses", []):
        bx = mm2bl(boss["cx"])
        by = mm2bl(boss["cy"])
        # Add boss cylinder
        bm2 = bmesh.new()
        build_cylinder(bm2, bx, by, floor_t, boss_r, standoff_h)
        boss_mesh = bpy.data.meshes.new("boss_mesh")
        boss_obj  = bpy.data.objects.new("Boss_Temp", boss_mesh)
        col.objects.link(boss_obj)
        bm2.to_mesh(boss_mesh)
        bm2.free()
        # Join boss to shell bottom
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        boss_obj.select_set(True)
        bpy.ops.object.join()
        obj.select_set(False)
        # Drill mounting hole through boss + floor
        hole_cut = make_cutter_cylinder(
            "BossHole_Cut", bx, by, 0, hole_r, floor_t + standoff_h + 0.001, col
        )
        apply_boolean_difference(obj, hole_cut)

    # --- Structural ribs ---
    rib_t = mm2bl(geom.get("rib_thickness_mm", 1.2))
    for rib in geom.get("ribs", []):
        rx = mm2bl(rib["x"])
        ry = mm2bl(rib["y"])
        rw = mm2bl(rib["w"])
        rh = mm2bl(rib["h"])
        rd = mm2bl(rib["depth"]) * 0.5  # ribs in bottom half only
        bm3 = bmesh.new()
        build_box(bm3, rx, ry, floor_t, rw, rh, rd)
        rib_mesh = bpy.data.meshes.new("rib_mesh")
        rib_obj  = bpy.data.objects.new("Rib_Temp", rib_mesh)
        col.objects.link(rib_obj)
        bm3.to_mesh(rib_mesh)
        bm3.free()
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        rib_obj.select_set(True)
        bpy.ops.object.join()
        obj.select_set(False)

    return obj


# ---------------------------------------------------------------------------
# Shell lid (upper case)
# ---------------------------------------------------------------------------

def build_shell_lid(geom: dict, col) -> "bpy.types.Object":
    """
    Builds the upper shell lid:
      - Thin plate
      - Display cutout (recessed window)
      - Button apertures
      - Joystick aperture
      - Snap-fit tabs
    """
    sw      = mm2bl(geom["shell_width_mm"])
    sh      = mm2bl(geom["shell_height_mm"])
    lid_t   = mm2bl(geom.get("lid_thickness_mm", 1.8))
    wall_t  = mm2bl(geom.get("wall_thickness_mm", 2.0))

    # Total shell depth — lid starts at split_z
    bt        = mm2bl(geom.get("board_thickness_mm", 1.6))
    standoff_h = mm2bl(geom.get("standoff_height_mm", 3.0))
    floor_t    = mm2bl(geom.get("floor_thickness_mm", 2.5))
    hat_stack  = mm2bl(geom.get("hat_stack_height_mm", 11.5))
    split_z    = floor_t + standoff_h + bt
    lid_z_bot  = split_z + hat_stack

    bm = bmesh.new()
    build_box(bm, 0, 0, lid_z_bot, sw, sh, lid_t)
    obj, mesh = new_mesh_object("Shell_Lid", col)
    bm.to_mesh(mesh)
    bm.free()

    # --- Display cutout ---
    dc = geom.get("display_cutout", {})
    if dc and dc.get("w", 0) > 0:
        dx  = mm2bl(dc["x"])
        dy  = mm2bl(dc["y"])
        dw  = mm2bl(dc["w"])
        dh  = mm2bl(dc["h"])
        drd = mm2bl(dc.get("depth", lid_t + 0.001))
        cutter = make_cutter_box(
            "Display_Cut",
            dx, dy, lid_z_bot - 0.0001,
            dw, dh, drd + 0.0001, col
        )
        apply_boolean_difference(obj, cutter)

    # --- Button apertures ---
    for i, btn in enumerate(geom.get("button_apertures", [])):
        br  = mm2bl(btn["r"])
        bcx = mm2bl(btn["cx"])
        bcy = mm2bl(btn["cy"])
        btn_cut = make_cutter_cylinder(
            f"Btn_Cut_{i}", bcx, bcy, lid_z_bot - 0.0001, br, lid_t + 0.0002, col
        )
        apply_boolean_difference(obj, btn_cut)

    # --- Joystick aperture ---
    joy = geom.get("joystick_aperture", {})
    if joy and joy.get("r", 0) > 0:
        jr  = mm2bl(joy["r"])
        jcx = mm2bl(joy["cx"])
        jcy = mm2bl(joy["cy"])
        joy_cut = make_cutter_cylinder(
            "Joy_Cut", jcx, jcy, lid_z_bot - 0.0001, jr, lid_t + 0.0002, col
        )
        apply_boolean_difference(obj, joy_cut)

    # --- Snap-fit tabs on lid perimeter ---
    snap_depth  = mm2bl(geom.get("snap_depth_mm",  1.2))
    snap_height = mm2bl(geom.get("snap_height_mm", 2.5))
    snap_width  = mm2bl(5.0)   # 5 mm wide tab

    for pt in geom.get("snap_fit_positions", []):
        px = mm2bl(pt["x"])
        py = mm2bl(pt["y"])
        bm_s = bmesh.new()
        # Determine which edge this snap is on and orient tab accordingly
        if abs(pt["y"] - 0) < 1e-3:                     # bottom edge
            build_box(bm_s, px - snap_width/2, -snap_depth,
                      lid_z_bot, snap_width, snap_depth, snap_height)
        elif abs(pt["y"] - geom["shell_height_mm"]) < 1e-3:  # top edge
            build_box(bm_s, px - snap_width/2, mm2bl(geom["shell_height_mm"]),
                      lid_z_bot, snap_width, snap_depth, snap_height)
        elif abs(pt["x"] - 0) < 1e-3:                   # left edge
            build_box(bm_s, -snap_depth, py - snap_width/2,
                      lid_z_bot, snap_depth, snap_width, snap_height)
        else:                                             # right edge
            build_box(bm_s, mm2bl(geom["shell_width_mm"]), py - snap_width/2,
                      lid_z_bot, snap_depth, snap_width, snap_height)

        s_mesh = bpy.data.meshes.new("snap_mesh")
        s_obj  = bpy.data.objects.new("Snap_Temp", s_mesh)
        col.objects.link(s_obj)
        bm_s.to_mesh(s_mesh)
        bm_s.free()
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        s_obj.select_set(True)
        bpy.ops.object.join()
        obj.select_set(False)

    return obj


# ---------------------------------------------------------------------------
# STL Export
# ---------------------------------------------------------------------------

def export_stl(obj, filepath: str):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_mesh.stl(
        filepath=filepath,
        use_selection=True,
        global_scale=1000.0,  # Convert BU (meters) → mm in STL
        use_mesh_modifiers=True,
    )
    print(f"[Export] {filepath}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_shell(spec_path: str, output_dir: str = "output"):
    """
    Main entry. Loads a spec JSON, runs math, generates 3D shell pieces,
    exports STL files and saves the .blend project.
    """
    if not BLENDER_AVAILABLE:
        print("[ERROR] bpy not available. Run this script inside Blender.")
        return

    with open(spec_path) as f:
        raw_spec = json.load(f)

    # Run the dimension math engine
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from parser.dimension_math import DimensionMath
    import dataclasses
    geom_obj = DimensionMath(raw_spec).compute()
    geom = dataclasses.asdict(geom_obj)

    # Inject tolerance constants into geom dict for Blender functions
    geom.update({
        "standoff_od_mm":    5.0,
        "standoff_hole_mm":  2.9,
        "standoff_height_mm": 3.0,
        "floor_thickness_mm": 2.5,
        "lid_thickness_mm":   1.8,
        "wall_thickness_mm":  2.0,
        "pcb_clearance_mm":   0.5,
        "rib_thickness_mm":   1.2,
        "hat_stack_height_mm": raw_spec.get("hat_stack_height_mm", 11.5),
        "board_thickness_mm":  raw_spec.get("board_thickness_mm", 1.6),
        "snap_depth_mm":  1.2,
        "snap_height_mm": 2.5,
    })

    os.makedirs(output_dir, exist_ok=True)
    model_name = raw_spec.get("model", "device").replace(" ", "_").lower()

    clear_scene()
    col_bottom = add_collection("Shell_Bottom")
    col_lid    = add_collection("Shell_Lid")

    print("[Gen] Building shell bottom...")
    bottom_obj = build_shell_bottom(geom, col_bottom)

    print("[Gen] Building shell lid...")
    lid_obj = build_shell_lid(geom, col_lid)

    # Export STLs
    export_stl(bottom_obj, os.path.join(output_dir, f"{model_name}_bottom.stl"))
    export_stl(lid_obj,    os.path.join(output_dir, f"{model_name}_lid.stl"))

    # Save .blend
    blend_path = os.path.join(output_dir, f"{model_name}_shell.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"[Save] {blend_path}")

    return {
        "bottom_stl": f"{model_name}_bottom.stl",
        "lid_stl":    f"{model_name}_lid.stl",
        "blend_file": f"{model_name}_shell.blend",
        "geometry":   geom,
    }


# ---------------------------------------------------------------------------
# CLI shim for Blender's --python argument
# ---------------------------------------------------------------------------

if __name__ == "__main__" and BLENDER_AVAILABLE:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    parser = argparse.ArgumentParser(description="Generate handheld shell STL from spec JSON")
    parser.add_argument("--spec",    required=True, help="Path to device spec JSON")
    parser.add_argument("--output",  default="output", help="Output directory for STL/blend files")
    args = parser.parse_args(argv)
    generate_shell(args.spec, args.output)
