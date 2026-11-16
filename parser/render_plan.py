"""
render_plan.py
--------------
Pure-Python render planning for the PDF report's assembly-step images
(framework Requirement 6b). NO bpy imports — this module is importable
and unit-testable anywhere, and is the single source of truth shared by:

  * the FastAPI backend   (POST /api/report-images — builds the plan,
                           hands it to the Blender imaging script),
  * blender_gen/blender_report_images.py (executes the plan under bpy),
  * report/pdf_report.py  (embeds the rendered PNGs in §7 — looked up
                           ONLY under the current design's hash).

Core honesty rules (Requirement 6):
  * Every frame depicts the ACTUAL selected board set. Groups for
    hardware the design does not have (no HAT, no camera, wired power)
    never appear in any frame.
  * Images are keyed by design_hash — a stable hash of the canonical
    spec + selection — so a stale image rendered for a DIFFERENT design
    can never be embedded in this design's report.

Frame model: one frame per assembly step. Steps map to cumulative
object groups by title keywords (the step titles come from
specs/assembly_specs.py and are stable):

    prepare / print   → printed_parts   (bottom case + lid shell)
    insert            → inserts         (heat-set brass inserts)
    raspberry / host  → host_board      (the main board replica)
    hat               → hat_board       (the HAT replica, if any)
    cable             → cables          (USB / power cabling)
    button            → button_caps     (printed caps in the lid)
    display           → display_lid     (display glass in the lid)
    close / snap      → closed_shell    (state: lid moves to closed)
    power-on / test   → final           (state: finished device)

Two groups ride along with related steps because the assembly-step
titles never name them: `battery` joins at the host-board step (the
pack is fitted in the bottom case with the main board) and `camera`
joins at the display step (the camera board mounts in the lid with
the display). Both only when the design actually has that hardware.

The lid shell is part of printed_parts (it is a printed part, present
from step 1); each frame carries a `lid` state — "exploded" (hovering
above the case, the standard SOP diagram view) until the close step,
then "closed". Button caps, camera and display glass are positioned
on the lid, so the imaging script moves them with the lid state.

Camera intent (Requirement 6c): each frame also carries a `camera`
dict {view, layout, screen_on} derived from the group the step
introduces, so the photograph serves the step being documented:

    printed parts step → view "top",  layout "kit" (parts laid out)
    inserts step       → view "below" (bosses seen from underneath)
    HAT step           → view "above" (watching the board seat)
    display step       → view "front" (nearly face-on to the lid)
    power-on step      → view "hero", screen_on=True (screen lit)
    everything else    → view "home" (the consistent 3/4 SOP angle)

The imaging script turns the intent into an actual camera position
per frame, auto-framed on the objects visible in that frame.
`screen_on` is presentation only: exactly one frame — the last —
has it set.
"""

import hashlib
import json

# All object groups a frame may reference, in canonical order.
GROUPS = [
    "printed_parts",
    "inserts",
    "host_board",
    "hat_board",
    "cables",
    "button_caps",
    "display_lid",
    "camera",
    "battery",
    "closed_shell",
    "final",
]

# Groups that are assembly STATES, not object collections: the imaging
# script interprets them (lid position), they own no geometry.
STATE_GROUPS = ("closed_shell", "final")

# (title keywords, group) — every matching rule contributes its group
# to the step at which the title first matches; groups then persist
# cumulatively into all later frames.
_STEP_RULES = [
    (("prepare", "print"), "printed_parts"),
    (("insert",), "inserts"),
    (("raspberry", "host"), "host_board"),
    (("hat",), "hat_board"),
    (("cable",), "cables"),
    (("button",), "button_caps"),
    (("display",), "display_lid"),
    (("close", "snap"), "closed_shell"),
    (("power-on", "power on", "test"), "final"),
]


# ---------------------------------------------------------------------------
# Design identity
# ---------------------------------------------------------------------------

def has_hat(spec: dict, selection: dict = None) -> bool:
    """Does this design include a HAT board stacked on the main board?

    Resolution order: explicit selection flag → a HAT module in the
    selected registry module ids → spec heuristics (the brain-only flow
    stamps '(brain only)' into the model name; crawled HAT specs name
    the HAT and are not Raspberry Pi vendor specs)."""
    sel = selection or {}
    if sel.get("has_hat") is not None:
        return bool(sel["has_hat"])
    mids = sel.get("module_ids") or []
    if any(str(m).startswith("hat_") for m in mids):
        return True
    model = str(spec.get("model") or "").lower()
    if "(brain only)" in model:
        return False
    if "hat" in model:
        return True
    if str(spec.get("vendor") or "").lower() == "raspberrypi":
        return False
    return bool(spec.get("display_diagonal_inch"))


def canonical_selection(spec: dict, selection: dict = None) -> dict:
    """Normalise the design selection (framework design state) into a
    fixed-shape dict for hashing. Fields the caller didn't supply are
    filled from the spec itself — syncSpecFromDesign() folds the
    selection into the spec (selected_modules, camera_zone, battery),
    so both the report-images endpoint and the report endpoint derive
    the same canonical selection for the same design."""
    sel = selection or {}
    return {
        "brain_id": sel.get("brain_id") or spec.get("brain_id"),
        "module_ids": sorted(str(m) for m in (sel.get("module_ids") or [])),
        "power_source_id": sel.get("power_source_id"),
        "camera_zone": sel.get("camera_zone") or spec.get("camera_zone"),
        "has_hat": has_hat(spec, sel),
        "pinout_modules": sorted(str(m)
                                 for m in (spec.get("selected_modules")
                                           or [])),
        "include_battery": bool(spec.get("include_battery")),
        "battery_size_mm": spec.get("battery_size_mm"),
    }


def design_hash(spec: dict, selection: dict = None) -> str:
    """Stable identity of a design: sha256 of the canonical
    spec + selection JSON, truncated to 16 hex chars. Identical input
    → identical hash; any design change → a different hash, so images
    rendered for one design can never be looked up for another."""
    payload = {"spec": spec,
               "selection": canonical_selection(spec, selection)}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Board-set resolution
# ---------------------------------------------------------------------------

def resolve_host_board(spec: dict, selection: dict = None,
                       brains: list = None) -> dict:
    """The main (host) board of the design, with real dimensions.

    Brain-only design → the spec board IS the host. HAT design with a
    selected brain → the registry brain's dimensions. HAT design with
    no brain selected → fall back to the HAT footprint (the reference
    pairing for these HATs) and SAY SO in `source` — never silently."""
    sel = selection or {}
    if not has_hat(spec, sel):
        return {
            "name": spec.get("model") or "Main board",
            "w_mm": spec.get("board_width_mm"),
            "h_mm": spec.get("board_height_mm"),
            "thickness_mm": spec.get("board_thickness_mm", 1.6),
            "source": "spec board (brain-only design — the board in "
                      "the spec IS the main board)",
        }
    bid = sel.get("brain_id") or spec.get("brain_id")
    if brains and bid:
        for b in brains:
            if b.get("id") == bid:
                return {
                    "name": b["name"],
                    "w_mm": b["board_w_mm"],
                    "h_mm": b["board_h_mm"],
                    "thickness_mm": 1.4,
                    "source": f"registry brain {bid}",
                }
    return {
        "name": "Host board (not specified)",
        "w_mm": spec.get("board_width_mm"),
        "h_mm": spec.get("board_height_mm"),
        "thickness_mm": 1.4,
        "source": "fallback: HAT footprint — no main board was "
                  "selected in the design state",
    }


def _camera_info(spec: dict, geom: dict, selection: dict) -> dict:
    ap = (geom or {}).get("camera_aperture") or {}
    selected = "camera_module_3" in (selection.get("module_ids") or [])
    present = bool(ap.get("r")) or selected
    return {
        "present": present,
        "zone": selection.get("camera_zone") or spec.get("camera_zone"),
        "aperture": ({"cx": ap.get("cx"), "cy": ap.get("cy"),
                      "r": ap.get("r")} if ap.get("r") else None),
    }


def _battery_info(spec: dict, geom: dict, selection: dict) -> dict:
    bay = (geom or {}).get("battery_bay") or {}
    psid = selection.get("power_source_id")
    present = bool(bay.get("w")) or bool(spec.get("include_battery")) \
        or (psid not in (None, "wired_usb"))
    size = spec.get("battery_size_mm")
    return {
        "present": present,
        "size_mm": list(size) if size else None,
        "bay": ({"x": bay.get("x"), "y": bay.get("y"),
                 "w": bay.get("w"), "h": bay.get("h")}
                if bay.get("w") else None),
        "power_source_id": psid,
    }


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------

# Camera views a frame may request (Requirement 6c). The imaging
# script maps each name to azimuth/elevation/distance around the
# frame's visible bounding box.
FRAME_VIEWS = ("top", "below", "home", "above", "front", "hero")


def _step_camera(new_groups: set) -> dict:
    """Camera intent for one assembly step, from the group(s) the
    step introduces (Requirement 6c). Priority order matters when a
    title matches several rules: the finale outranks the kit, which
    outranks inserts, the HAT seating and the display."""
    if "final" in new_groups:
        return {"view": "hero", "layout": "assembly",
                "screen_on": True}
    if "printed_parts" in new_groups:
        return {"view": "top", "layout": "kit",
                "screen_on": False}
    if "inserts" in new_groups:
        return {"view": "below", "layout": "assembly",
                "screen_on": False}
    if "hat_board" in new_groups:
        return {"view": "above", "layout": "assembly",
                "screen_on": False}
    if "display_lid" in new_groups:
        return {"view": "front", "layout": "assembly",
                "screen_on": False}
    return {"view": "home", "layout": "assembly",
            "screen_on": False}


def build_render_plan(spec: dict, geom: dict, assembly_steps: list,
                      selection: dict = None, brains: list = None,
                      output_root: str = "output") -> dict:
    """Build the full render plan for a design.

    spec            — the design's device spec dict (for a HAT design
                      this is the HAT spec; for brain-only, the board).
    geom            — geometry dict from /api/compute-geometry.
    assembly_steps  — specs.assembly_steps from /api/assemble-specs.
    selection       — framework design state {brain_id, module_ids,
                      power_source_id, camera_zone, has_hat} (optional;
                      gaps are derived from the spec).
    brains          — brain registry list (for host dimensions).
    output_root     — server output dir; image dir is derived from it.
    """
    geom = geom or {}
    sel = canonical_selection(spec, selection)
    hat = sel["has_hat"]
    camera = _camera_info(spec, geom, sel)
    battery = _battery_info(spec, geom, sel)
    host = resolve_host_board(spec, selection, brains)
    h = design_hash(spec, selection)

    # Groups that may NEVER appear for this design (absent hardware).
    banned = set()
    if not hat:
        banned.add("hat_board")
    if not camera["present"]:
        banned.add("camera")
    if not battery["present"]:
        banned.add("battery")

    frames = []
    cumulative = set()
    for i, step in enumerate(assembly_steps or []):
        title = str(step.get("title") or "")
        low = title.lower()
        new_groups = set()
        for keywords, group in _STEP_RULES:
            if any(k in low for k in keywords):
                new_groups.add(group)
        # Ride-along hardware (see module docstring).
        if "host_board" in new_groups and battery["present"]:
            new_groups.add("battery")
        if "display_lid" in new_groups and camera["present"]:
            new_groups.add("camera")
        cumulative |= new_groups
        if i == 0:
            cumulative.add("printed_parts")  # sane fallback, step 1
        visible = [g for g in GROUPS
                   if g in cumulative and g not in banned]
        step_index = step.get("step", i + 1)
        try:
            step_index = int(step_index)
        except (TypeError, ValueError):
            step_index = i + 1
        frames.append({
            "step_index": step_index,
            "title": title,
            "visible_groups": visible,
            "lid": ("closed" if ("closed_shell" in cumulative
                                 or "final" in cumulative)
                    else "exploded"),
            "camera": _step_camera(new_groups),
            "filename": f"step_{step_index:02d}.png",
        })

    # The story ends on the finished, working device: if no step
    # introduced the `final` state (so no frame is flagged), upgrade
    # the last frame to the hero shot so exactly one frame — the
    # last — carries screen_on.
    if frames and not any(f["camera"]["screen_on"] for f in frames):
        frames[-1]["camera"] = {"view": "hero", "layout": "assembly",
                                "screen_on": True}

    return {
        "version": 1,
        "design_hash": h,
        "model": spec.get("model"),
        "spec": spec,
        "geom": geom,
        "selection": sel,
        "has_hat": hat,
        "host_board": host,
        "camera": camera,
        "battery": battery,
        "groups": list(GROUPS),
        "state_groups": list(STATE_GROUPS),
        "frames": frames,
        "output_dir": f"{output_root}/report_images/{h}",
    }
