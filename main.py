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
from modular.modular_entities import HandheldBuild
from specs.assembly_specs import AssemblySpecGenerator
from specs.pinout_engine import PinoutEngine, MODULE_PINOUTS, RPI_GPIO, WIRE_COLOURS
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
        return {"status": "ok", "geometry": dataclasses.asdict(geom)}
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
        gen = PDFReportGenerator(
            device_name    = req.device_name,
            spec           = req.spec,
            geom           = req.geom,
            pinout         = req.pinout,
            firmware_arch  = req.firmware_arch,
            assembly_specs = req.assembly_specs,
            output_path    = pdf_path,
        )
        gen.generate()
        return {
            "status":    "ok",
            "pdf_file":  Path(pdf_path).name,
            "pdf_path":  pdf_path,
        }
    except Exception as e:
        logger.exception("PDF generation failed")
        raise HTTPException(status_code=500, detail=str(e))


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
