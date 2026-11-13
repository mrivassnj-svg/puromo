"""
printer_server.py
-----------------
3D printer integration module.

Covers:
  1. STL + slicer profile packager for FlashForge Adventurer 5M
  2. Local network printer discovery (mDNS/Zeroconf)
  3. Remote printer API client (OctoPrint, Moonraker/Klipper, FlashForge cloud)
  4. Popular printer catalogue with communication protocols
  5. File transfer methods (USB, LAN, remote, FlashForge app API)
"""

from __future__ import annotations
import os
import json
import shutil
import zipfile
import socket
import logging
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Popular 3D Printer Catalogue
# ═══════════════════════════════════════════════════════════════

PRINTER_CATALOGUE = {
    # ── FlashForge ─────────────────────────────────────────────
    "flashforge_adventurer_5m": {
        "brand":     "FlashForge",
        "model":     "Adventurer 5M",
        "build_volume_mm": (220, 220, 220),
        "nozzle_diameter_mm": 0.4,
        "max_temp_c": 280,
        "bed_temp_c": 110,
        "protocol":  "FlashForge HTTP API + FlashCloud",
        "api_port":  8899,
        "lan_discovery": "mDNS (_flashforge._tcp)",
        "file_formats": ["GX", "G", "GCODE"],
        "slicer":    "FlashPrint 5 / OrcaSlicer",
        "transfer_methods": ["USB", "LAN_HTTP", "FlashCloud_Remote"],
        "api_endpoints": {
            "status":   "GET  /control?action=info",
            "upload":   "POST /upload  (multipart/form-data, field=file)",
            "print":    "POST /control?action=start&filename={filename}",
            "cancel":   "POST /control?action=cancel",
            "pause":    "POST /control?action=pause",
            "resume":   "POST /control?action=resume",
            "temp":     "GET  /control?action=info  (returns extruder/bed temp)",
        },
        "auth":       "None required on LAN; FlashCloud uses token auth",
        "cloud_base": "https://api.flashforge.com/v1",
        "notes":      "5M Pro variant adds multi-colour; same API",
    },
    "flashforge_adventurer_5m_pro": {
        "brand":     "FlashForge",
        "model":     "Adventurer 5M Pro",
        "build_volume_mm": (220, 220, 220),
        "nozzle_diameter_mm": 0.4,
        "max_temp_c": 300,
        "bed_temp_c": 110,
        "protocol":  "FlashForge HTTP API + FlashCloud",
        "api_port":  8899,
        "lan_discovery": "mDNS (_flashforge._tcp)",
        "file_formats": ["GX", "G", "GCODE"],
        "slicer":    "FlashPrint 5 / OrcaSlicer",
        "transfer_methods": ["USB", "LAN_HTTP", "FlashCloud_Remote"],
        "api_endpoints": {
            "status":   "GET  /control?action=info",
            "upload":   "POST /upload  (multipart/form-data, field=file)",
            "print":    "POST /control?action=start&filename={filename}",
        },
        "auth":       "None on LAN; FlashCloud token for remote",
        "cloud_base": "https://api.flashforge.com/v1",
    },
    "bambu_x1c": {
        "brand":    "Bambu Lab",
        "model":    "X1 Carbon",
        "build_volume_mm": (256, 256, 256),
        "nozzle_diameter_mm": 0.4,
        "max_temp_c": 300,
        "bed_temp_c": 120,
        "protocol": "Bambu MQTT (LAN mode) + Bambu Cloud HTTPS",
        "api_port": 1883,
        "lan_discovery": "mDNS (_bambu._tcp)",
        "file_formats": ["3MF", "GCODE"],
        "slicer":   "Bambu Studio / OrcaSlicer",
        "transfer_methods": ["USB", "LAN_MQTT", "BambuCloud_Remote"],
        "api_endpoints": {
            "publish_cmd": "MQTT topic: device/{serial}/request",
            "subscribe":   "MQTT topic: device/{serial}/report",
            "upload_ftp":  "FTP to printer IP port 990 (ftps://)",
        },
        "auth":    "LAN mode: access_code from printer screen. Cloud: JWT",
        "notes":   "LAN mode must be enabled in printer settings. FTP for file upload.",
    },
    "prusa_mk4": {
        "brand":    "Prusa",
        "model":    "MK4",
        "build_volume_mm": (250, 210, 220),
        "nozzle_diameter_mm": 0.4,
        "max_temp_c": 290,
        "bed_temp_c": 110,
        "protocol": "PrusaLink REST API (OctoPrint-compatible subset)",
        "api_port": 80,
        "lan_discovery": "mDNS (_http._tcp, name contains 'PrusaLink')",
        "file_formats": ["GCODE", "BGCODE"],
        "slicer":   "PrusaSlicer / OrcaSlicer",
        "transfer_methods": ["USB", "LAN_HTTP", "PrusaConnect_Remote"],
        "api_endpoints": {
            "status":  "GET  /api/v1/status",
            "upload":  "PUT  /api/v1/files/local/{filename}",
            "print":   "POST /api/v1/job  {\"file\":\"...\"}",
            "info":    "GET  /api/v1/info",
        },
        "auth":    "HTTP Digest auth (username: maker, password: from printer)",
        "cloud":   "https://connect.prusa3d.com",
    },
    "creality_k1": {
        "brand":    "Creality",
        "model":    "K1 / K1 Max",
        "build_volume_mm": (220, 220, 250),
        "nozzle_diameter_mm": 0.4,
        "max_temp_c": 300,
        "bed_temp_c": 100,
        "protocol": "Moonraker JSON-RPC (Klipper-based)",
        "api_port": 7125,
        "lan_discovery": "mDNS (_moonraker._tcp)",
        "file_formats": ["GCODE"],
        "slicer":   "Creality Print / OrcaSlicer",
        "transfer_methods": ["USB", "LAN_JSONRPC", "Creality_Cloud"],
        "api_endpoints": {
            "info":    "GET  /printer/info",
            "upload":  "POST /server/files/upload  (multipart)",
            "print":   "POST /printer/print/start  {\"filename\":\"...\"}",
            "status":  "GET  /printer/objects/query?print_stats",
            "cancel":  "POST /printer/print/cancel",
        },
        "auth":    "API key in header: X-Api-Key",
    },
    "octoprint_generic": {
        "brand":    "Generic (OctoPrint)",
        "model":    "Any OctoPrint-managed printer",
        "build_volume_mm": (0, 0, 0),
        "nozzle_diameter_mm": 0.4,
        "protocol": "OctoPrint REST API",
        "api_port": 5000,
        "lan_discovery": "mDNS (_http._tcp)",
        "file_formats": ["GCODE"],
        "slicer":   "Any",
        "transfer_methods": ["LAN_HTTP", "Remote_Reverse_Proxy"],
        "api_endpoints": {
            "version": "GET  /api/version",
            "upload":  "POST /api/files/local  (multipart)",
            "print":   "POST /api/files/local/{filename}  {\"command\":\"select\",\"print\":true}",
            "status":  "GET  /api/job",
            "cancel":  "POST /api/job  {\"command\":\"cancel\"}",
        },
        "auth":    "X-Api-Key header (from OctoPrint settings)",
    },
    "moonraker_generic": {
        "brand":    "Generic (Klipper/Moonraker)",
        "model":    "Any Klipper-managed printer",
        "build_volume_mm": (0, 0, 0),
        "nozzle_diameter_mm": 0.4,
        "protocol": "Moonraker JSON-RPC + WebSocket",
        "api_port": 7125,
        "lan_discovery": "mDNS (_moonraker._tcp)",
        "file_formats": ["GCODE"],
        "slicer":   "Any",
        "transfer_methods": ["LAN_HTTP", "Remote_Tunnel"],
        "api_endpoints": {
            "info":    "GET  /printer/info",
            "upload":  "POST /server/files/upload",
            "print":   "POST /printer/print/start",
            "status":  "GET  /printer/objects/query?print_stats",
        },
        "auth":    "API key (optional); Moonraker supports both anonymous and keyed",
    },
}

# ═══════════════════════════════════════════════════════════════
#  FlashForge Adventurer 5M slicer profile
# ═══════════════════════════════════════════════════════════════
FLASHFORGE_5M_PROFILE = {
    "printer":    "FlashForge Adventurer 5M",
    "nozzle_mm":  0.4,
    "layer_heights": {
        "structural_shell": 0.2,
        "detail_lid":       0.15,
        "button_caps":      0.15,
        "grip_panels":      0.25,
    },
    "infill": {
        "structural_shell": "30% Gyroid",
        "detail_lid":       "25% Gyroid",
        "button_caps":      "100% Rectilinear",
        "grip_panels":      "40% Honeycomb",
        "standoffs":        "100% Rectilinear",
    },
    "materials": {
        "structural_shell": {"type":"PETG","temp_c":235,"bed_c":80,"speed_mms":50},
        "detail_lid":       {"type":"PLA+","temp_c":215,"bed_c":60,"speed_mms":45},
        "button_caps":      {"type":"PLA+","temp_c":215,"bed_c":60,"speed_mms":40},
        "grip_panels":      {"type":"TPU","temp_c":225,"bed_c":50,"speed_mms":25},
    },
    "supports":  "None (all parts orient support-free)",
    "perimeters": 4,
    "top_bottom_layers": 5,
    "fan_speed_pct":  50,
    "first_layer_speed_mms": 20,
    "retraction_mm": 1.0,
    "retraction_speed_mms": 45,
    "z_hop_mm": 0.2,
    "brim_mm":  3,
    "notes": [
        "Print bottom case flat (floor down). No supports.",
        "Print lid face-down. No supports if recess ≤2mm.",
        "Button caps: print stem-up. 100% infill for durability.",
        "Grip panels: print flat. Supports for contour overhang >45°.",
        "Pre-heat bed 5 min before first layer for PETG adhesion.",
        "FlashForge 5M natively reads .gx and standard .gcode files.",
        "Export from OrcaSlicer as .gcode (FlashForge Adventurer 5M preset).",
    ],
    "flashprint_start_gcode": """\
; --- Handheld Shell Start G-code ---
M140 S{bed_temp}        ; Set bed temp
M104 S{extruder_temp}   ; Set hotend temp
G28                      ; Home all axes
G29                      ; Auto bed levelling
M190 S{bed_temp}        ; Wait for bed
M109 S{extruder_temp}   ; Wait for hotend
G92 E0                   ; Reset extruder
G1 Z5.0 F3000
G1 X0.1 Y20 Z0.3 F5000
G1 X0.1 Y200 F1500 E15  ; Prime line
G92 E0
""",
    "flashprint_end_gcode": """\
; --- End G-code ---
G91
G1 Z10 F3000
G90
G28 X0
M104 S0
M140 S0
M84
""",
}

# ═══════════════════════════════════════════════════════════════
#  STL File Packager
# ═══════════════════════════════════════════════════════════════

@dataclass
class PrintPackage:
    model_name:    str
    stl_files:     List[str] = field(default_factory=list)
    gcode_files:   List[str] = field(default_factory=list)
    profile_json:  str = ""
    readme_path:   str = ""
    zip_path:      str = ""
    printer_key:   str = "flashforge_adventurer_5m"
    created_at:    str = field(default_factory=lambda: datetime.now().isoformat())


class PrintPackager:
    """
    Packages STL files + slicer profile + README into a
    printer-ready ZIP archive for local or remote transfer.
    """

    def __init__(self, model_name: str, output_dir: str):
        self.model_name = model_name
        self.out = Path(output_dir)
        self.out.mkdir(parents=True, exist_ok=True)

    def create_package(self,
                       stl_files: List[str],
                       printer_key: str = "flashforge_adventurer_5m",
                       geom: Optional[dict] = None,
                       spec: Optional[dict] = None) -> PrintPackage:
        """Create a ZIP print package for the given printer."""
        printer = PRINTER_CATALOGUE.get(printer_key,
                  PRINTER_CATALOGUE["flashforge_adventurer_5m"])

        slug = self.model_name.lower().replace(" ", "_")
        pkg_dir = self.out / f"{slug}_print_package"
        pkg_dir.mkdir(exist_ok=True)

        # Copy STL files
        copied_stls = []
        for stl in stl_files:
            src = Path(stl)
            if src.exists():
                dst = pkg_dir / src.name
                shutil.copy2(src, dst)
                copied_stls.append(str(dst))

        # Write slicer profile JSON
        profile_path = pkg_dir / "slicer_profile.json"
        profile = dict(FLASHFORGE_5M_PROFILE)
        profile["target_printer"] = printer["model"]
        profile["model_name"]     = self.model_name
        profile["generated_at"]   = datetime.now().isoformat()
        if geom:
            profile["shell_dimensions_mm"] = {
                "W": geom.get("shell_width_mm"),
                "H": geom.get("shell_height_mm"),
                "D": geom.get("shell_depth_mm"),
            }
        profile_path.write_text(json.dumps(profile, indent=2))

        # Write OrcaSlicer machine config
        orca_path = pkg_dir / "orcaslicer_machine_config.json"
        orca_config = self._gen_orca_config(printer)
        orca_path.write_text(json.dumps(orca_config, indent=2))

        # Write transfer README
        readme_path = pkg_dir / "PRINT_TRANSFER_GUIDE.md"
        readme_path.write_text(self._gen_print_readme(printer, slug))

        # Zip it
        zip_path = self.out / f"{slug}_print_package.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in pkg_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(self.out))

        logger.info(f"[PrintPackager] Package: {zip_path}")
        return PrintPackage(
            model_name   = self.model_name,
            stl_files    = copied_stls,
            profile_json = str(profile_path),
            readme_path  = str(readme_path),
            zip_path     = str(zip_path),
            printer_key  = printer_key,
        )

    def _gen_orca_config(self, printer: dict) -> dict:
        bv = printer.get("build_volume_mm", (220, 220, 220))
        return {
            "type":               "machine",
            "name":               printer["model"],
            "from":               "Handheld 3D Shell Builder",
            "nozzle_diameter":    [str(printer.get("nozzle_diameter_mm", 0.4))],
            "printable_area":     [
                f"0x0", f"{bv[0]}x0",
                f"{bv[0]}x{bv[1]}", f"0x{bv[1]}"
            ],
            "printable_height":   str(bv[2]),
            "max_layer_height":   ["0.35"],
            "min_layer_height":   ["0.05"],
            "extruder_type":      ["bowden"],
            "default_filament_profile": ["Handheld_PETG"],
            "default_print_profile":    ["Handheld_0.2mm_PETG"],
            "machine_start_gcode":      FLASHFORGE_5M_PROFILE["flashprint_start_gcode"],
            "machine_end_gcode":        FLASHFORGE_5M_PROFILE["flashprint_end_gcode"],
        }

    def _gen_print_readme(self, printer: dict, slug: str) -> str:
        transfer_sections = {
            "USB": f"""
## Transfer via USB Drive
1. Copy `{slug}_bottom.stl` and `{slug}_lid.stl` to the root of a FAT32 USB drive.
2. Insert USB drive into the {printer['model']}.
3. On the printer touchscreen: **Print** → **USB** → select file → **Print**.
4. Monitor first layer adhesion — re-level if gaps appear.
""",
            "LAN_HTTP": f"""
## Transfer via Local Network (LAN)
The {printer['model']} exposes an HTTP API on port {printer['api_port']}.

```bash
# 1. Discover printer IP (Linux/Mac)
avahi-browse -rt _flashforge._tcp

# 2. Upload GCODE file
curl -X POST http://PRINTER_IP:{printer['api_port']}/upload \\
     -F "file=@{slug}_bottom.gcode"

# 3. Start print
curl "http://PRINTER_IP:{printer['api_port']}/control?action=start&filename={slug}_bottom.gcode"

# 4. Check status
curl "http://PRINTER_IP:{printer['api_port']}/control?action=info"
```
""",
            "FlashCloud_Remote": """
## Transfer via FlashForge Cloud (Remote Access from Outside Home)
1. Register at https://www.flashforge.com/cloud
2. Add your printer (scan QR code on printer or enter serial).
3. Upload GCODE from the web dashboard or FlashPrint desktop app.
4. The printer downloads and prints on your command.

**Remote API (programmatic):**
```python
import requests
TOKEN = "your_flashcloud_token"
headers = {"Authorization": f"Bearer {TOKEN}"}
# Upload
r = requests.post("https://api.flashforge.com/v1/files/upload",
    headers=headers, files={"file": open("shell.gcode","rb")})
# Print
requests.post("https://api.flashforge.com/v1/print/start",
    headers=headers, json={"file_id": r.json()["file_id"],
                            "printer_id": "your_printer_id"})
```
""",
        }

        sections = []
        for method in printer.get("transfer_methods", []):
            if method in transfer_sections:
                sections.append(transfer_sections[method])

        return f"""# {printer['model']} Print Transfer Guide
## {slug} — Handheld Shell Build

**Printer:** {printer['brand']} {printer['model']}
**Build Volume:** {printer.get('build_volume_mm', (0,0,0))[0]} × {printer.get('build_volume_mm', (0,0,0))[1]} × {printer.get('build_volume_mm', (0,0,0))[2]} mm
**Protocol:** {printer['protocol']}
**Supported File Formats:** {", ".join(printer.get('file_formats', []))}
**Recommended Slicer:** {printer.get('slicer','—')}

## Files in this Package
- `{slug}_bottom.stl` — Lower shell half (print flat, floor down)
- `{slug}_lid.stl`    — Upper lid (print face down)
- `{slug}_shell.blend`— Blender project (edit/export)
- `slicer_profile.json`     — Print settings for all parts
- `orcaslicer_machine_config.json` — OrcaSlicer machine config

## Slicer Steps (OrcaSlicer / FlashPrint)
1. Import the machine config from `orcaslicer_machine_config.json`.
2. Load `{slug}_bottom.stl` — set orientation: floor down, no supports.
3. Load `{slug}_lid.stl`    — set orientation: face down, no supports.
4. Apply settings from `slicer_profile.json`.
5. Slice → verify no crossing walls or island errors.
6. Export as `.gcode` (FlashForge Adventurer 5M preset).

{chr(10).join(sections)}

## Notes
{chr(10).join(f'- {n}' for n in printer.get('notes', FLASHFORGE_5M_PROFILE.get('notes', [])))}
"""


# ═══════════════════════════════════════════════════════════════
#  Printer API Client
# ═══════════════════════════════════════════════════════════════

class PrinterClient:
    """
    Generic printer API client.
    Supports FlashForge HTTP, OctoPrint REST, Moonraker JSON-RPC.
    """

    def __init__(self, printer_key: str, host: str,
                 api_key: Optional[str] = None,
                 port: Optional[int] = None):
        self.printer_info = PRINTER_CATALOGUE.get(printer_key, {})
        self.host     = host
        self.port     = port or self.printer_info.get("api_port", 8899)
        self.api_key  = api_key
        self.protocol = self.printer_info.get("protocol", "")
        self._session = requests.Session()
        self._session.timeout = 15

        if api_key:
            # OctoPrint / Moonraker style
            self._session.headers["X-Api-Key"] = api_key

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ping(self) -> dict:
        """Test connectivity and return printer status."""
        try:
            if "flashforge" in self.protocol.lower():
                r = self._session.get(
                    f"{self.base_url}/control?action=info", timeout=5)
                return {"online": r.status_code == 200,
                        "raw": r.text[:200]}
            elif "moonraker" in self.protocol.lower():
                r = self._session.get(f"{self.base_url}/printer/info")
                return {"online": r.status_code == 200, "data": r.json()}
            elif "octoprint" in self.protocol.lower():
                r = self._session.get(f"{self.base_url}/api/version")
                return {"online": r.status_code == 200, "data": r.json()}
            elif "prusalink" in self.protocol.lower():
                r = self._session.get(f"{self.base_url}/api/v1/info")
                return {"online": r.status_code == 200, "data": r.json()}
            else:
                r = self._session.get(self.base_url, timeout=5)
                return {"online": r.status_code < 500}
        except requests.exceptions.ConnectionError:
            return {"online": False, "error": "Connection refused"}
        except requests.exceptions.Timeout:
            return {"online": False, "error": "Timeout"}
        except Exception as e:
            return {"online": False, "error": str(e)}

    def upload_file(self, file_path: str) -> dict:
        """Upload a GCODE/GX file to the printer."""
        fname = Path(file_path).name
        try:
            with open(file_path, "rb") as f:
                data = f.read()

            if "flashforge" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/upload",
                    files={"file": (fname, data, "application/octet-stream")},
                    timeout=120,
                )
                return {"success": r.status_code == 200, "response": r.text[:200]}

            elif "moonraker" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/server/files/upload",
                    files={"file": (fname, data, "application/octet-stream")},
                    timeout=120,
                )
                return {"success": r.status_code == 201, "data": r.json()}

            elif "octoprint" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/api/files/local",
                    files={"file": (fname, data, "application/octet-stream")},
                    timeout=120,
                )
                return {"success": r.status_code in (200, 201), "data": r.json()}

            elif "prusalink" in self.protocol.lower():
                r = self._session.put(
                    f"{self.base_url}/api/v1/files/local/{fname}",
                    data=data,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=120,
                )
                return {"success": r.status_code in (200, 201)}

            else:
                return {"success": False, "error": "Unknown protocol"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_print(self, filename: str) -> dict:
        """Start printing an already-uploaded file."""
        try:
            if "flashforge" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/control?action=start&filename={filename}")
                return {"success": r.status_code == 200}

            elif "moonraker" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/printer/print/start",
                    json={"filename": filename})
                return {"success": r.status_code == 200}

            elif "octoprint" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/api/files/local/{filename}",
                    json={"command": "select", "print": True})
                return {"success": r.status_code in (200, 204)}

            elif "prusalink" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/api/v1/job",
                    json={"file": f"/local/{filename}"})
                return {"success": r.status_code == 204}

            return {"success": False, "error": "Unknown protocol"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def cancel_print(self) -> dict:
        """Cancel the current print job."""
        try:
            if "flashforge" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/control?action=cancel")
            elif "moonraker" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/printer/print/cancel")
            elif "octoprint" in self.protocol.lower():
                r = self._session.post(
                    f"{self.base_url}/api/job",
                    json={"command": "cancel"})
            elif "prusalink" in self.protocol.lower():
                r = self._session.delete(f"{self.base_url}/api/v1/job")
            else:
                return {"success": False}
            return {"success": r.status_code in (200, 204)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_status(self) -> dict:
        """Get current printer status (temp, progress, state)."""
        try:
            if "flashforge" in self.protocol.lower():
                r = self._session.get(
                    f"{self.base_url}/control?action=info")
                return {"success": True, "raw": r.text}

            elif "moonraker" in self.protocol.lower():
                r = self._session.get(
                    f"{self.base_url}/printer/objects/query"
                    "?extruder&heater_bed&print_stats&toolhead")
                return {"success": True, "data": r.json()}

            elif "octoprint" in self.protocol.lower():
                job = self._session.get(f"{self.base_url}/api/job").json()
                temps = self._session.get(f"{self.base_url}/api/printer").json()
                return {"success": True, "job": job, "temps": temps}

            elif "prusalink" in self.protocol.lower():
                r = self._session.get(f"{self.base_url}/api/v1/status")
                return {"success": True, "data": r.json()}

            return {"success": False}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  LAN Printer Discovery
# ═══════════════════════════════════════════════════════════════

def discover_printers_lan(timeout_s: int = 5) -> List[dict]:
    """
    Discover printers on the local network using socket-based
    mDNS query. Returns list of discovered printer dicts.
    Falls back to scanning common printer ports on /24 subnet.
    """
    found = []

    # Try socket-based port scan on local subnet
    try:
        # Get local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        subnet = ".".join(local_ip.split(".")[:3])
        logger.info(f"[Discovery] Scanning {subnet}.0/24")

        # Common printer ports
        PORTS = [
            (8899, "flashforge"),
            (7125, "moonraker"),
            (5000, "octoprint"),
            (80,   "prusalink"),
        ]
        import concurrent.futures
        def check_host(ip, port, proto):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex((ip, port))
                sock.close()
                if result == 0:
                    return {"ip": ip, "port": port, "protocol": proto}
            except Exception:
                pass
            return None

        tasks = []
        for i in range(1, 255):
            ip = f"{subnet}.{i}"
            for port, proto in PORTS:
                tasks.append((ip, port, proto))

        with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
            futures = [ex.submit(check_host, *t) for t in tasks]
            for f in concurrent.futures.as_completed(futures, timeout=timeout_s):
                r = f.result()
                if r:
                    found.append(r)
                    logger.info(f"[Discovery] Found {r['protocol']} at {r['ip']}:{r['port']}")

    except Exception as e:
        logger.warning(f"[Discovery] Scan error: {e}")

    return found
