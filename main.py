"""
main.py
-------
Puromo (プロモ) — Handheld 3D Shell Builder

Web-crawler → dimension math → modular PCB shell generator with Blender STL export,
full pinout engine, firmware generator, PDF report, and 3D print server.

Endpoints:
  GET  /api/known-devices          — list known devices (Waveshare LCD HATs + custom)
  POST /api/crawl                  — crawl / lookup a device spec by query
  POST /api/compute-geometry       — run dimension math on a spec → ShellGeometry
  POST /api/generate-build         — produce modular build manifest
  POST /api/assemble-specs         — generate full assembly spec document + BOM
  POST /api/generate-3d            — invoke Blender headless → STL files
  GET  /api/firmware/zip           — download generated firmware as ZIP
  GET  /api/download/{file_path}   — download any generated output file
  GET  /                           — serve the Puromo web UI
"""

import os
import re
import sys
import json
import shutil
import subprocess
import tempfile
import dataclasses
import logging
from pathlib import Path
from typing import Optional, List, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Project module paths
sys.path.insert(0, str(Path(__file__).parent))

from crawler.device_crawler import (
    DeviceCrawler, WAVESHARE_LCD_HATS,
    GPIO_HEADER_VARIANTS, COMMON_COMPONENT_HEIGHTS,
)
from parser.dimension_math import DimensionMath, waveshare_hat_math_summary
from parser.render_plan import build_render_plan, resolve_design
from modular.modular_entities import HandheldBuild
from specs.assembly_specs import AssemblySpecGenerator
from specs.pinout_engine import (
    PinoutEngine, MODULE_PINOUTS, RPI_GPIO, WIRE_COLOURS,
    verify_pinout, triage_conflicts,
)
from firmware.firmware_engine import BOOT_PHASES, DRIVER_DEPS, EVENT_TYPES, SCHEDULER_DESIGN
from firmware.firmware_generator import FirmwareGenerator, topo_sort
from report.pdf_report import PDFReportGenerator
from printer.printer_server import (
    PrintPackager, PrinterClient, PRINTER_CATALOGUE,
    FLASHFORGE_5M_PROFILE, discover_printers_lan
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Puromo (プロモ)",
    version="1.0.0",
    description=(
        "Puromo — Web crawler → dimension math → modular PCB shell generator. "
        "Blender STL export · Full pinout engine · Firmware generator · PDF report · 3D print server."
    ),
)

OUTPUT_DIR  = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

BLENDER_CMD = shutil.which("blender") or "blender"
# Honest availability: BLENDER_CMD falls back to the bare name, so
# detection must consult PATH directly (Requirement 6b — the
# report-images endpoint reports 'unavailable', never fake images).
BLENDER_ON_PATH = shutil.which("blender") is not None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CrawlRequest(BaseModel):
    query:  str
    vendor: str = "waveshare"

class GeometryRequest(BaseModel):
    spec: dict

class BuildRequest(BaseModel):
    spec: dict
    geom: dict
    model_name: str = "handheld"
    include_battery: bool = False
    battery_size_mm: List[float] = [50.0, 20.0, 6.0]

class SpecsRequest(BaseModel):
    spec: dict
    geom: dict
    build: dict

class Generate3DRequest(BaseModel):
    spec: dict
    model_name: str = "handheld"

class PinoutRequest(BaseModel):
    spec: dict

class FirmwareRequest(BaseModel):
    spec: dict
    model_name: str = "handheld"

class ReportRequest(BaseModel):
    device_name:   str
    spec:          dict
    geom:          dict
    pinout:        dict
    firmware_arch: dict
    assembly_specs: dict
    # Framework design state (Requirement 6b): part of the design's
    # hash identity, so §7 embeds only this design's images.
    selection:     Optional[dict] = None

class ReportImagesRequest(BaseModel):
    spec:      dict
    geom:      dict
    specs:     dict
    selection: Optional[dict] = None
    force:     bool = False

class PrintPackageRequest(BaseModel):
    model_name:   str
    stl_files:    List[str] = []
    printer_key:  str = "flashforge_adventurer_5m"
    geom:         Optional[dict] = None
    spec:         Optional[dict] = None

class PrinterConnectRequest(BaseModel):
    printer_key: str
    host:        str
    port:        Optional[int] = None
    api_key:     Optional[str] = None

class PrintStartRequest(BaseModel):
    printer_key: str
    host:        str
    port:        Optional[int] = None
    api_key:     Optional[str] = None
    file_path:   str
    filename:    str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/known-devices")
def known_devices():
    """Return all pre-loaded Waveshare LCD HAT specs."""
    crawler = DeviceCrawler()
    return {
        "count": len(WAVESHARE_LCD_HATS),
        "devices": {
            k: {
                "model": v["model"],
                "board_mm": f"{v['board_width_mm']}×{v['board_height_mm']}",
                "display":  f"{v['display_diagonal_inch']}\" {v['resolution_w']}×{v['resolution_h']}",
                "interface": v["interface"],
            }
            for k, v in WAVESHARE_LCD_HATS.items()
        }
    }


@app.post("/api/crawl")
def crawl_device(req: CrawlRequest):
    """Crawl or look up a device spec by query string."""
    crawler = DeviceCrawler(cache_dir=str(OUTPUT_DIR / "cache"))
    try:
        spec = crawler.get_device(req.query, req.vendor)
        return {"status": "ok", "spec": spec.to_dict()}
    except Exception as e:
        logger.exception("Crawl failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/compute-geometry")
def compute_geometry(req: GeometryRequest):
    """Run dimension math on a spec dict → ShellGeometry."""
    try:
        math_engine = DimensionMath(req.spec)
        geom = math_engine.compute()
        geom_dict = dataclasses.asdict(geom)
        # Pass raw spec board/component fields through so the UI can render
        # exact-position board replicas without a second API call.
        PASSTHROUGH = [
            "board_width_mm", "board_height_mm", "board_thickness_mm",
            "display_width_mm", "display_height_mm",
            "display_offset_x_mm", "display_offset_y_mm",
            "joystick_center_mm", "joystick_diameter_mm",
            "button_positions_mm", "mounting_hole_positions_mm",
            "mounting_hole_diameter_mm",
            "gpio_header_above_mm", "gpio_rows", "gpio_cols",
            "gpio_header_pitch_mm", "controller_ic", "model",
        ]
        for key in PASSTHROUGH:
            if key in req.spec and key not in geom_dict:
                geom_dict[key] = req.spec[key]
        return {"status": "ok", "geometry": geom_dict}
    except Exception as e:
        logger.exception("Geometry compute failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-build")
def generate_build(req: BuildRequest):
    """Compose the modular entity build manifest."""
    try:
        build = HandheldBuild.from_geometry(
            geom           = req.geom,
            model_name     = req.model_name,
            include_battery= req.include_battery,
            battery_size_mm= tuple(req.battery_size_mm),
        )
        return {"status": "ok", "build": build.to_dict()}
    except Exception as e:
        logger.exception("Build generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/assemble-specs")
def assemble_specs(req: SpecsRequest):
    """Generate full assembly spec JSON + Markdown."""
    try:
        gen = AssemblySpecGenerator(req.geom, req.build, req.spec)
        specs_data = gen.generate()

        model_slug = req.spec.get("model", "device").replace(" ", "_").lower()
        json_path = OUTPUT_DIR / f"{model_slug}_specs.json"
        md_path   = OUTPUT_DIR / f"{model_slug}_assembly.md"

        gen.to_json(str(json_path))
        gen.to_markdown(str(md_path))

        return {
            "status": "ok",
            "specs":  specs_data,
            "files": {
                "json":     json_path.name,
                "markdown": md_path.name,
            },
        }
    except Exception as e:
        logger.exception("Spec generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-3d")
async def generate_3d(req: Generate3DRequest, bg: BackgroundTasks):
    """
    Write a spec JSON then invoke Blender in background to produce STL files.
    Returns immediately with job info; check /api/download/{filename} when ready.
    """
    model_slug = req.spec.get("model", "handheld").replace(" ", "_").lower()
    spec_path  = OUTPUT_DIR / f"{model_slug}_spec.json"

    # Save spec for Blender to read
    with open(spec_path, "w") as f:
        json.dump(req.spec, f, indent=2)

    blender_script = Path(__file__).parent / "blender_gen" / "blender_shell_gen.py"

    def run_blender():
        cmd = [
            BLENDER_CMD,
            "--background",
            "--python", str(blender_script),
            "--",
            "--spec",   str(spec_path),
            "--output", str(OUTPUT_DIR),
        ]
        logger.info(f"[Blender] {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log_path = OUTPUT_DIR / f"{model_slug}_blender.log"
        with open(log_path, "w") as lf:
            lf.write(result.stdout + "\n" + result.stderr)
        logger.info(f"[Blender] exit={result.returncode}  log={log_path}")

    bg.add_task(run_blender)

    return {
        "status": "queued",
        "message": "Blender job queued. STL files will appear in /output/.",
        "expected_files": [
            f"{model_slug}_bottom.stl",
            f"{model_slug}_lid.stl",
            f"{model_slug}_pcb.stl",
            f"{model_slug}_shell.blend",
        ],
        "log_file": f"{model_slug}_blender.log",
    }


@app.get("/api/outputs")
def list_outputs():
    """List all generated output files."""
    files = []
    for f in sorted(OUTPUT_DIR.iterdir()):
        if f.is_file():
            files.append({
                "name":     f.name,
                "size_kb":  round(f.stat().st_size / 1024, 1),
                "ext":      f.suffix,
            })
    return {"files": files}


@app.get("/api/download/{file_path:path}")
def download_file(file_path: str):
    path = OUTPUT_DIR / file_path
    # Security: must resolve inside OUTPUT_DIR
    try:
        path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=path.name)


@app.get("/api/hat-math/{hat_key}")
def hat_math(hat_key: str):
    """Return full Waveshare HAT math breakdown for a known HAT key."""
    result = waveshare_hat_math_summary(hat_key)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/hat-keys")
def hat_keys():
    return {"keys": list(WAVESHARE_LCD_HATS.keys())}


@app.get("/api/search-devices")
def search_devices(q: str = "", vendor: str = "waveshare"):
    """
    Fuzzy-search the known device database.
    Returns all matching HATs ranked by relevance score.
    Also attempts a Waveshare fuzzy lookup for instant best-match.
    """
    crawler = DeviceCrawler()
    if not q:
        # Return all known devices ranked by name
        return {
            "query": q,
            "best_match": None,
            "results": [
                {"key": k, "model": v["model"], "score": 0,
                 "board_mm": f"{v['board_width_mm']}×{v['board_height_mm']}",
                 "display": f"{v['display_diagonal_inch']}\" {v['resolution_w']}×{v['resolution_h']}"}
                for k, v in WAVESHARE_LCD_HATS.items()
            ],
        }

    from crawler.device_crawler import _fuzzy_lookup
    ranked = crawler.search_known_devices(q)
    best_key = _fuzzy_lookup(q)
    return {
        "query": q,
        "best_match": best_key,
        "results": [
            {
                "key": key,
                "model": model,
                "score": score,
                "board_mm": f"{WAVESHARE_LCD_HATS[key]['board_width_mm']}×{WAVESHARE_LCD_HATS[key]['board_height_mm']}",
                "display": f"{WAVESHARE_LCD_HATS[key]['display_diagonal_inch']}\" {WAVESHARE_LCD_HATS[key]['resolution_w']}×{WAVESHARE_LCD_HATS[key]['resolution_h']}",
            }
            for key, model, score in ranked
        ],
    }


@app.get("/api/header-variants")
def header_variants():
    """Return all GPIO header variants with their physical dimensions."""
    return {
        "variants": {
            k: {
                "label":        v["label"],
                "above_mm":     v["above_mm"],
                "below_mm":     v["below_mm"],
                "mated_gap_mm": v["mated_gap_mm"],
                "note":         v["note"],
            }
            for k, v in GPIO_HEADER_VARIANTS.items()
        }
    }


@app.get("/api/component-heights")
def component_heights():
    """Return all known through-hole/SMD component heights."""
    return {"components": COMMON_COMPONENT_HEIGHTS}


@app.post("/api/pinout")
def generate_pinout(req: PinoutRequest):
    """Generate full pinout tables, GPIO map, conflicts and wiring for a spec."""
    try:
        engine = PinoutEngine(req.spec)
        result = engine.generate()
        return {"status": "ok", "pinout": result}
    except Exception as e:
        logger.exception("Pinout generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/module-list")
def module_list():
    """Return all available module pinout definitions."""
    return {
        "modules": {
            k: {
                "label":     v["label"],
                "interface": v["interface"],
                "voltage":   v["voltage"],
                "pin_count": len(v["pins"]),
            }
            for k, v in MODULE_PINOUTS.items()
        }
    }


@app.get("/api/gpio-map")
def gpio_map():
    """Return the full RPi 40-pin GPIO reference map."""
    return {"gpio": list(RPI_GPIO.values())}


# ---------------------------------------------------------------------------
@app.post("/api/firmware/generate")
def generate_firmware(req: FirmwareRequest):
    """Generate full firmware file tree from spec."""
    try:
        spec = dict(req.spec)
        spec["model"] = req.model_name
        gen = FirmwareGenerator(spec, output_root=str(OUTPUT_DIR))
        manifest = gen.generate()
        # Return manifest with relative file paths for UI
        manifest["files_list"] = [
            {"name": k, "path": v.replace(str(OUTPUT_DIR)+"/", "")}
            for k, v in manifest["files"].items()
        ]
        return {"status": "ok", "manifest": manifest}
    except Exception as e:
        logger.exception("Firmware generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/firmware/architecture")
def firmware_architecture():
    """Return boot phases, dependency graph, scheduler design and event types."""
    return {
        "boot_phases":      BOOT_PHASES,
        "driver_deps":      DRIVER_DEPS,
        "driver_load_order": topo_sort(DRIVER_DEPS),
        "scheduler":        SCHEDULER_DESIGN,
        "event_types":      EVENT_TYPES,
    }


@app.get("/api/firmware/file")
def firmware_file(path: str):
    """Return contents of a generated firmware file."""
    # path is relative to OUTPUT_DIR
    full = OUTPUT_DIR / path
    # Security: must be inside OUTPUT_DIR
    try:
        full.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not full.exists() or not full.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return {"path": path, "content": full.read_text()}


@app.get("/api/firmware/zip")
def firmware_zip(model_name: str = "handheld"):
    """
    Bundle the generated firmware folder for model_name into a ZIP and return it.
    The firmware folder is OUTPUT_DIR/<model_slug>/.
    """
    slug = model_name.replace(" ", "_").lower()
    fw_dir = OUTPUT_DIR / slug
    if not fw_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Firmware folder not found: {slug}/. Generate firmware first.")
    zip_path = OUTPUT_DIR / f"{slug}_firmware.zip"
    import zipfile
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in fw_dir.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(OUTPUT_DIR))
    return FileResponse(str(zip_path), filename=f"{slug}_firmware.zip",
                        media_type="application/zip")


# ---------------------------------------------------------------------------
# ── PDF Report ─────────────────────────────────────────────────────────────
@app.post("/api/report/generate")
def generate_report(req: ReportRequest):
    """Generate the full PDF design report."""
    try:
        slug = req.device_name.lower().replace(" ", "_")
        pdf_path = str(OUTPUT_DIR / f"{slug}_design_report.pdf")
        # Requirement 6b + consolidation: §7 embeds assembly images
        # ONLY from this design's directory, where the design ID
        # comes from the single derivation (resolve_design) — the
        # same derivation the render side used. Legacy image sets
        # are re-keyed to it by _consolidate_image_store().
        _consolidate_image_store()
        resolved = _resolve(req.spec, req.selection)
        design_hash = resolved["design_hash"]
        # Requirement 10: §1 names the design's chosen main board,
        # not just the HAT's compatibility list.
        brain_name = ""
        if req.selection and req.selection.get("brain_id"):
            for b in _load_registry("brains"):
                if b.get("id") == req.selection["brain_id"]:
                    brain_name = b.get("name", "")
                    break
        gen = PDFReportGenerator(
            device_name    = req.device_name,
            brain_name     = brain_name,
            spec           = resolved["spec"],
            geom           = req.geom,
            pinout         = req.pinout,
            firmware_arch  = req.firmware_arch,
            assembly_specs = req.assembly_specs,
            output_path    = pdf_path,
            design_hash    = design_hash,
            images_dir     = str(OUTPUT_DIR / "report_images"
                                 / design_hash),
        )
        gen.generate()
        return {
            "status":    "ok",
            "pdf_file":  Path(pdf_path).name,
            "pdf_path":  pdf_path,
            "design_hash": design_hash,
            "images_design_hash": design_hash,
        }
    except Exception as e:
        logger.exception("PDF generation failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Report assembly images (Requirement 6b) ─────────────────────────
# In-memory job table, mirroring the /api/generate-3d background
# pattern. Images themselves live on disk under
# output/report_images/<design_hash>/ and outlive the job table.
REPORT_IMAGE_JOBS: dict = {}


def _report_image_dir(design_hash: str) -> Path:
    return OUTPUT_DIR / "report_images" / design_hash


def _resolve(spec, selection):
    """The one derivation, with the server's registries — every
    identity decision in this file goes through here."""
    return resolve_design(spec, selection,
                          _load_registry("modules"),
                          _load_registry("power_sources"),
                          GPIO_HEADER_VARIANTS)


_IMAGE_STORE_CONSOLIDATED = False


def _consolidate_image_store():
    """One-time re-keying of the on-disk image store (consolidation
    P3): plans written before the single derivation carry snapshot
    hashes. Each stored plan's (spec, canonical selection) is run
    through resolve_design and its image directory renamed to the
    consolidated design ID, so sets rendered earlier — e.g. the
    Pi's 9 CYCLES frames — belong to the same identity the report
    now computes. Idempotent; runs once per process."""
    global _IMAGE_STORE_CONSOLIDATED
    if _IMAGE_STORE_CONSOLIDATED:
        return
    _IMAGE_STORE_CONSOLIDATED = True
    for plan_path in sorted(OUTPUT_DIR.glob("report_plan_*.json")):
        try:
            plan = json.loads(plan_path.read_text())
            old_h = plan.get("design_hash")
            if not old_h:
                continue
            sel = plan.get("selection") or {}
            choices = {
                "brain_id": sel.get("brain_id"),
                "module_ids": sel.get("module_ids") or [],
                "power_source_id": sel.get("power_source_id"),
                "camera_zone": sel.get("camera_zone"),
                "header_variant": sel.get("header_variant"),
            }
            new_h = _resolve(plan.get("spec") or {},
                             choices)["design_hash"]
            if new_h == old_h and plan.get("choices") is not None:
                continue
            plan["design_hash"] = new_h
            plan["choices"] = choices
            if new_h != old_h:
                old_dir = _report_image_dir(old_h)
                new_dir = _report_image_dir(new_h)
                if (old_dir.exists()
                        and any(old_dir.glob("step_*.png"))
                        and not new_dir.exists()):
                    old_dir.rename(new_dir)
                    man = new_dir / "manifest.json"
                    if man.exists():
                        m = json.loads(man.read_text())
                        m["design_hash"] = new_h
                        man.write_text(json.dumps(m, indent=2))
                (OUTPUT_DIR / f"report_plan_{new_h}.json").write_text(
                    json.dumps(plan, indent=2))
                plan_path.unlink(missing_ok=True)
                logger.info(
                    f"[Consolidate] image set re-keyed {old_h} → "
                    f"{new_h}")
            else:
                plan_path.write_text(json.dumps(plan, indent=2))
        except Exception:
            logger.exception("[Consolidate] plan re-key failed: %s",
                             plan_path)


# Engine fallback chain (Pi errata, Blender 5.2.2): the imaging run
# died natively inside EEVEE — a GPU/OpenGL segfault in background
# mode cannot be caught in-process — so the job retries the whole
# Blender process with the next engine. The first engine to produce
# the full frame set is cached on disk and tried first next time.
REPORT_IMAGE_ENGINES = ["EEVEE", "WORKBENCH", "CYCLES"]
ENGINE_CACHE_PATH = OUTPUT_DIR / "report_images" / ".engine_cache"


def _read_engine_cache():
    try:
        name = ENGINE_CACHE_PATH.read_text().strip().upper()
    except Exception:
        return None
    return name if name in REPORT_IMAGE_ENGINES else None


def _write_engine_cache(engine: str) -> None:
    try:
        ENGINE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENGINE_CACHE_PATH.write_text(engine + "\n")
    except Exception as exc:
        logger.warning(f"[ReportImages] engine cache write failed: {exc}")


def _engine_attempt_order() -> list:
    cached = _read_engine_cache()
    order = [cached] if cached else []
    return order + [e for e in REPORT_IMAGE_ENGINES if e not in order]


def _manifest_engine(img_dir: Path):
    """Engine recorded in the rendered images' manifest.json, if any
    (persists across server restarts, unlike the job table)."""
    try:
        m = json.loads((img_dir / "manifest.json").read_text())
        eng = str(m.get("engine") or "").strip().upper()
        return eng if eng in REPORT_IMAGE_ENGINES else None
    except Exception:
        return None


@app.post("/api/report-images")
async def report_images(req: ReportImagesRequest, bg: BackgroundTasks):
    """
    Build the render plan for this design and, if Blender is
    available, queue the imaging script (same pattern as
    /api/generate-3d). If Blender is NOT available, say so —
    status 'unavailable' — and never fake images. With
    force=true (what the Report tab's button sends) an already-
    rendered design is rendered again instead of answered 'ready'.
    """
    _consolidate_image_store()
    steps = (req.specs or {}).get("assembly_steps", [])
    plan = build_render_plan(
        req.spec, req.geom, steps,
        selection=req.selection,
        brains=_load_registry("brains"),
        output_root=str(OUTPUT_DIR.resolve()),
        modules=_load_registry("modules"),
        power_sources=_load_registry("power_sources"),
        header_variants=GPIO_HEADER_VARIANTS,
    )
    h = plan["design_hash"]
    img_dir = _report_image_dir(h)
    total = len(plan["frames"])
    present = sum(1 for f in plan["frames"]
                  if (img_dir / f["filename"]).exists())
    base = {
        "design_hash": h,
        "frames": total,
        "images_present": present,
        "image_dir": f"report_images/{h}",
        "blender_available": BLENDER_ON_PATH,
    }
    if total == 0:
        return {"status": "error",
                "reason": "No assembly steps in the specs — run the "
                          "pipeline (assemble-specs) first.",
                **base}
    if present == total and not req.force:
        eng = _manifest_engine(img_dir)
        return {"status": "ready",
                "message": "Assembly images already rendered for "
                           "this design."
                           + (f" ({eng})" if eng else ""),
                "engine": eng,
                **base}
    if not BLENDER_ON_PATH:
        return {"status": "unavailable",
                "reason": "Blender executable not found on the "
                          "server PATH, so assembly images cannot "
                          "be rendered here. The PDF report still "
                          "generates — its assembly section will "
                          "note that images were not generated.",
                **base}

    plan_path = OUTPUT_DIR / f"report_plan_{h}.json"
    plan_path.write_text(json.dumps(plan, indent=2))
    script = Path(__file__).parent / "blender_gen" / "blender_report_images.py"
    REPORT_IMAGE_JOBS[h] = {"status": "generating"}

    def run_images():
        # Process-level engine fallback chain: each attempt is a
        # fresh Blender process (a native crash in one engine cannot
        # be caught, only outlived). Partial output is kept — the
        # status logic reports the best attempt's count honestly.
        backup_dir = None
        if req.force:
            # Re-render of the same design (e.g. a better rig): move
            # the old set aside so per-attempt counts measure THIS
            # run — but never destroy it. If the new render fails,
            # the previous set is restored below; a failed re-render
            # must not leave the design with no images at all.
            existing = [img_dir / f["filename"] for f in plan["frames"]
                        if (img_dir / f["filename"]).exists()]
            if (img_dir / "manifest.json").exists():
                existing.append(img_dir / "manifest.json")
            if existing:
                backup_dir = img_dir / ".prev"
                shutil.rmtree(backup_dir, ignore_errors=True)
                backup_dir.mkdir(parents=True, exist_ok=True)
                for old in existing:
                    old.replace(backup_dir / old.name)
        log_path = OUTPUT_DIR / f"report_images_{h}.log"
        chunks = []
        best, best_engine = 0, None
        for attempt, engine in enumerate(_engine_attempt_order(), 1):
            cmd = [BLENDER_CMD, "--background", "--python", str(script),
                   "--", "--plan", str(plan_path), "--engine", engine]
            logger.info(f"[ReportImages] attempt {attempt} "
                        f"engine={engine}: {' '.join(cmd)}")
            try:
                result = subprocess.run(cmd, capture_output=True,
                                        text=True, timeout=1200)
                tail = result.stdout + "\n" + result.stderr
            except Exception as exc:  # attempt could not even start
                tail = f"imaging job failed to run: {exc}"
            got = sum(1 for f in plan["frames"]
                      if (img_dir / f["filename"]).exists())
            chunks.append(f"===== attempt {attempt} engine={engine}"
                          f" -> {got}/{total} images =====\n{tail}")
            logger.info(f"[ReportImages] {h} attempt {attempt} "
                        f"({engine}): {got}/{total} images")
            if got > best:
                best, best_engine = got, engine
            if got == total:
                _write_engine_cache(engine)
                break
        restored = False
        if backup_dir is not None:
            if best == total:
                # New set complete: the backed-up old set goes away.
                shutil.rmtree(backup_dir, ignore_errors=True)
            else:
                # New render fell short: put the previous set back,
                # replacing any partial output from this run.
                for old in sorted(backup_dir.iterdir()):
                    old.replace(img_dir / old.name)
                shutil.rmtree(backup_dir, ignore_errors=True)
                restored = True
                chunks.append(
                    f"===== re-render fell short ({best}/{total}); "
                    f"previous image set restored =====")
        log_path.write_text("\n".join(chunks))
        if best == total:
            REPORT_IMAGE_JOBS[h] = {"status": "ready",
                                    "engine": best_engine,
                                    "images_present": best}
        elif restored:
            REPORT_IMAGE_JOBS[h] = {
                "status": "ready",
                "engine": _manifest_engine(img_dir),
                "images_present": total,
                "detail": f"Re-render produced only {best}/{total} "
                          f"images — the previous image set was "
                          f"restored unchanged. See "
                          f"report_images_{h}.log",
            }
        else:
            REPORT_IMAGE_JOBS[h] = {
                "status": "error",
                "engine": best_engine,
                "images_present": best,
                "detail": f"Blender produced {best}/{total} images — "
                          f"see report_images_{h}.log",
            }

    bg.add_task(run_images)
    return {"status": "queued",
            "message": "Blender imaging job queued.",
            "status_url": f"/api/report-images/status/{h}",
            **base}


class DesignIdRequest(BaseModel):
    spec: dict
    selection: Optional[dict] = None


@app.post("/api/design-id")
def design_id(req: DesignIdRequest):
    """The design's identity, from the single derivation — the UI
    shows this so a design change (and whether images exist for
    THIS design) is visible before any PDF is generated."""
    _consolidate_image_store()
    h = _resolve(req.spec, req.selection)["design_hash"]
    img_dir = _report_image_dir(h)
    present = (sum(1 for _ in img_dir.glob("step_*.png"))
               if img_dir.exists() else 0)
    return {"design_hash": h, "images_present": present}


@app.get("/api/report-images/status/{design_hash}")
def report_images_status(design_hash: str):
    """Status of a design's assembly images:
    ready / generating / not_generated / unavailable / error."""
    if not re.fullmatch(r"[0-9a-f]{16}", design_hash or ""):
        raise HTTPException(status_code=404,
                            detail="unknown design hash")
    img_dir = _report_image_dir(design_hash)
    filenames: List[str] = []
    plan_path = OUTPUT_DIR / f"report_plan_{design_hash}.json"
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text())
            filenames = [f["filename"] for f in plan.get("frames", [])]
        except Exception:
            filenames = []
    if not filenames:
        manifest = img_dir / "manifest.json"
        if manifest.exists():
            try:
                m = json.loads(manifest.read_text())
                filenames = [f["filename"] for f in m.get("frames", [])]
            except Exception:
                filenames = []
    if not filenames and img_dir.exists():
        # No plan/manifest on record (e.g. server restarted before any
        # job ran): the PNGs on disk are the ground truth.
        filenames = sorted(p.name for p in img_dir.glob("step_*.png"))
    present = [fn for fn in filenames if (img_dir / fn).exists()]
    total = len(filenames)
    job = REPORT_IMAGE_JOBS.get(design_hash) or {}
    if job.get("status") == "generating":
        status = "generating"
    elif total and len(present) == total:
        status = "ready"
    elif job.get("status") == "error":
        status = "error"
    elif not BLENDER_ON_PATH and not present:
        status = "unavailable"
    else:
        status = "not_generated"
    return {
        "status": status,
        "design_hash": design_hash,
        "images": present,
        "images_present": len(present),
        "frames": total,
        "engine": job.get("engine") or _manifest_engine(img_dir),
        "detail": job.get("detail"),
        "blender_available": BLENDER_ON_PATH,
    }


# ── Print Package ───────────────────────────────────────────────────────────
@app.post("/api/print/package")
def create_print_package(req: PrintPackageRequest):
    """Create a printer-ready ZIP package (STLs + slicer profile + README)."""
    try:
        packager = PrintPackager(req.model_name, str(OUTPUT_DIR))
        pkg = packager.create_package(
            stl_files   = req.stl_files,
            printer_key = req.printer_key,
            geom        = req.geom,
            spec        = req.spec,
        )
        import dataclasses
        return {"status": "ok", "package": dataclasses.asdict(pkg)}
    except Exception as e:
        logger.exception("Print package failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/print/printers")
def list_printers():
    """Return the full printer catalogue."""
    return {
        "printers": {
            k: {
                "brand":    v["brand"],
                "model":    v["model"],
                "protocol": v["protocol"],
                "api_port": v.get("api_port"),
                "transfer": v.get("transfer_methods", []),
                "formats":  v.get("file_formats", []),
                "slicer":   v.get("slicer", ""),
                "build_mm": v.get("build_volume_mm", [0,0,0]),
                "auth":     v.get("auth",""),
                "api_endpoints": v.get("api_endpoints", {}),
            }
            for k, v in PRINTER_CATALOGUE.items()
        }
    }


@app.get("/api/print/slicer-profile/{printer_key}")
def slicer_profile(printer_key: str):
    """Return the slicer profile for a printer."""
    printer = PRINTER_CATALOGUE.get(printer_key)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not found")
    return {"printer": printer["model"], "profile": FLASHFORGE_5M_PROFILE}


@app.post("/api/print/ping")
def ping_printer(req: PrinterConnectRequest):
    """Test connectivity to a local/remote printer."""
    try:
        client = PrinterClient(req.printer_key, req.host,
                               api_key=req.api_key, port=req.port)
        return {"status": "ok", "result": client.ping()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/print/upload")
def upload_to_printer(req: PrintStartRequest):
    """Upload a GCODE/STL file directly to a printer."""
    try:
        client = PrinterClient(req.printer_key, req.host,
                               api_key=req.api_key, port=req.port)
        result = client.upload_file(req.file_path)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/print/start")
def start_printer_job(req: PrintStartRequest):
    """Start a print job on the connected printer."""
    try:
        client = PrinterClient(req.printer_key, req.host,
                               api_key=req.api_key, port=req.port)
        fname = req.filename or Path(req.file_path).name
        result = client.start_print(fname)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/print/status")
def printer_status(req: PrinterConnectRequest):
    """Get current printer status."""
    try:
        client = PrinterClient(req.printer_key, req.host,
                               api_key=req.api_key, port=req.port)
        return {"status": "ok", "result": client.get_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/print/discover")
def discover_printers():
    """Scan local network for 3D printers."""
    found = discover_printers_lan(timeout_s=4)
    return {"status": "ok", "found": found, "count": len(found)}


# ---------------------------------------------------------------------------
# Framework registries + capability engine (Requirements 1–4, additive)
# ---------------------------------------------------------------------------

REGISTRY_DIR = Path(__file__).parent / "data" / "registries"


def _load_registry(name: str) -> list:
    p = REGISTRY_DIR / f"{name}.json"
    try:
        return json.loads(p.read_text())
    except Exception as exc:  # missing/corrupt registry → empty, never crash
        logger.warning("registry %s unavailable: %s", name, exc)
        return []


def _registries() -> dict:
    return {
        "brains": _load_registry("brains"),
        "modules": _load_registry("modules"),
        "power_sources": _load_registry("power_sources"),
    }


class CapabilityRequest(BaseModel):
    brain_id: str
    module_ids: List[str] = []     # modules to evaluate
    selected_ids: List[str] = []   # modules already in the design (context)


class VerifyRequest(BaseModel):
    spec: dict


def _module_reasons(brain: dict, module: dict, selected: list,
                    modules_by_id: dict) -> list:
    """Why `module` cannot run on `brain` given the already-selected modules.
    Empty list = capable. Checks, in order: interface, power, footprint."""
    reasons = []
    req = module.get("requires") or {}
    ifaces = brain.get("interfaces") or {}
    for iface, need in (req.get("interfaces") or {}).items():
        have = ifaces.get(iface)
        if isinstance(need, (int, float)) and not isinstance(need, bool):
            if not have or have < need:
                reasons.append(
                    f"Interface: needs {iface.upper()} x{int(need)} — "
                    f"{brain['name']} provides {int(have or 0)}")
        elif need and not have:
            reasons.append(
                f"Interface: needs {iface.replace('_', ' ')} — "
                f"not present on {brain['name']}")
    # Power: board base draw + every selected module (incl. this one).
    sel = [modules_by_id[m] for m in selected if m in modules_by_id]
    if module["id"] not in [m["id"] for m in sel]:
        sel = sel + [module]
    mods_ma = sum(int((m.get("requires") or {}).get("pins_ma") or 0) for m in sel)
    total = int(brain.get("base_draw_ma") or 0) + mods_ma
    budget = int(brain.get("max_current_ma") or 0)
    if budget and total > budget:
        reasons.append(
            f"Power: total draw {total} mA (board base "
            f"{int(brain.get('base_draw_ma') or 0)} mA + modules {mods_ma} mA) "
            f"exceeds the {budget} mA supply rating of the {brain['name']}")
    # Footprint: only where the registry fixes a size (stacked/mounted parts).
    fw, fh = req.get("footprint_w_mm"), req.get("footprint_h_mm")
    if fw and fh:
        if fw > brain["board_w_mm"] + 1.0 or fh > brain["board_h_mm"] + 1.0:
            reasons.append(
                f"Fit: footprint {fw} x {fh} mm exceeds the "
                f"{brain['name']} board ({brain['board_w_mm']} x "
                f"{brain['board_h_mm']} mm)")
    return reasons


def _alternative_brain(current: dict, module: dict, selected: list,
                       brains: list, modules_by_id: dict):
    """Most probable alternative board for an incapable module: a board where
    the module IS capable and the most already-selected modules stay capable;
    ties broken by smallest board-area change, then toward the toolchain's
    default host board (the pinout engine's power_budget defaults to the
    Pi 4), then registry order."""
    best, best_key = None, None
    cur_area = current["board_w_mm"] * current["board_h_mm"]
    for idx, b in enumerate(brains):
        if b["id"] == current["id"]:
            continue
        if _module_reasons(b, module, selected, modules_by_id):
            continue
        preserved = sum(
            1 for mid in selected
            if mid in modules_by_id
            and not _module_reasons(b, modules_by_id[mid], selected,
                                    modules_by_id))
        area_change = abs(b["board_w_mm"] * b["board_h_mm"] - cur_area)
        key = (preserved, -area_change, 1 if b["id"] == "pi_4_b" else 0, -idx)
        if best_key is None or key > best_key:
            best, best_key = b, key
    if best is None:
        return None
    return {
        "brain_id": best["id"],
        "brain_name": best["name"],
        "preserves": best_key[0],
        "selected_total": len([m for m in selected if m in modules_by_id]),
    }


@app.get("/api/registries")
def get_registries():
    """Verified registries: main boards (brains), feature modules, power
    sources. Seeded from code/vendor facts — see data/registries/*.json."""
    regs = _registries()
    return {"status": "ok", **regs,
            "counts": {k: len(v) for k, v in regs.items()}}


@app.post("/api/capability")
def post_capability(req: CapabilityRequest):
    """Capability map: for each requested module, capable or incapable FOR
    THE SELECTED BRAIN, with reasons; plus the most probable alternative
    brain for each incapable module."""
    regs = _registries()
    brains_by_id = {b["id"]: b for b in regs["brains"]}
    modules_by_id = {m["id"]: m for m in regs["modules"]}
    brain = brains_by_id.get(req.brain_id)
    if brain is None:
        raise HTTPException(404, f"unknown brain_id: {req.brain_id}")
    results, alternatives = [], {}
    for mid in req.module_ids:
        module = modules_by_id.get(mid)
        if module is None:
            results.append({"module_id": mid, "capable": False,
                            "reasons": [f"Unknown module id: {mid}"]})
            continue
        reasons = _module_reasons(brain, module, req.selected_ids,
                                  modules_by_id)
        results.append({"module_id": mid, "capable": not reasons,
                        "reasons": reasons})
        if reasons:
            alt = _alternative_brain(brain, module, req.selected_ids,
                                     regs["brains"], modules_by_id)
            if alt:
                alternatives[mid] = alt
    sel_ma = sum(int((modules_by_id[m].get("requires") or {}).get("pins_ma")
                     or 0)
                 for m in req.selected_ids if m in modules_by_id)
    return {
        "status": "ok",
        "brain": {"id": brain["id"], "name": brain["name"]},
        "results": results,
        "alternatives": alternatives,
        "totals": {
            "base_draw_ma": int(brain.get("base_draw_ma") or 0),
            "selected_modules_ma": sel_ma,
            "total_ma": int(brain.get("base_draw_ma") or 0) + sel_ma,
            "budget_ma": int(brain.get("max_current_ma") or 0),
        },
    }


@app.post("/api/pinout/verify")
def post_pinout_verify(req: VerifyRequest):
    """Five-check pinout verification + conflict triage (Requirements 2e/2f).
    Runs the pinout engine on the spec, then checks the result for internal
    consistency five ways, and gives each conflict an industry-style verdict:
    'DIY simple fix' or 'Not worth the time'."""
    result = PinoutEngine(req.spec).generate()
    checks = verify_pinout(result)
    return {
        "status": "ok",
        "checks": checks,
        "passed": sum(1 for c in checks if c["pass"]),
        "total": len(checks),
        "all_pass": all(c["pass"] for c in checks),
        "conflicts": result.get("conflicts", []),
        "triage": triage_conflicts(result),
        "power_budget": result.get("power_budget", {}),
        "selected_modules": result.get("selected_modules", []),
    }


# ---------------------------------------------------------------------------
# Serve the HTML UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    ui_path = Path(__file__).parent / "ui" / "index.html"
    if ui_path.exists():
        return HTMLResponse(ui_path.read_text())
    return HTMLResponse("<h1>UI not found — place index.html in ui/</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=True)
