# Adventurer 5M Print Transfer Guide
## test_handheld — Handheld Shell Build

**Printer:** FlashForge Adventurer 5M
**Build Volume:** 220 × 220 × 220 mm
**Protocol:** FlashForge HTTP API + FlashCloud
**Supported File Formats:** GX, G, GCODE
**Recommended Slicer:** FlashPrint 5 / OrcaSlicer

## Files in this Package
- `test_handheld_bottom.stl` — Lower shell half (print flat, floor down)
- `test_handheld_lid.stl`    — Upper lid (print face down)
- `test_handheld_shell.blend`— Blender project (edit/export)
- `slicer_profile.json`     — Print settings for all parts
- `orcaslicer_machine_config.json` — OrcaSlicer machine config

## Slicer Steps (OrcaSlicer / FlashPrint)
1. Import the machine config from `orcaslicer_machine_config.json`.
2. Load `test_handheld_bottom.stl` — set orientation: floor down, no supports.
3. Load `test_handheld_lid.stl`    — set orientation: face down, no supports.
4. Apply settings from `slicer_profile.json`.
5. Slice → verify no crossing walls or island errors.
6. Export as `.gcode` (FlashForge Adventurer 5M preset).


## Transfer via USB Drive
1. Copy `test_handheld_bottom.stl` and `test_handheld_lid.stl` to the root of a FAT32 USB drive.
2. Insert USB drive into the Adventurer 5M.
3. On the printer touchscreen: **Print** → **USB** → select file → **Print**.
4. Monitor first layer adhesion — re-level if gaps appear.


## Transfer via Local Network (LAN)
The Adventurer 5M exposes an HTTP API on port 8899.

```bash
# 1. Discover printer IP (Linux/Mac)
avahi-browse -rt _flashforge._tcp

# 2. Upload GCODE file
curl -X POST http://PRINTER_IP:8899/upload \
     -F "file=@test_handheld_bottom.gcode"

# 3. Start print
curl "http://PRINTER_IP:8899/control?action=start&filename=test_handheld_bottom.gcode"

# 4. Check status
curl "http://PRINTER_IP:8899/control?action=info"
```


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


## Notes
- 5
- M
-  
- P
- r
- o
-  
- v
- a
- r
- i
- a
- n
- t
-  
- a
- d
- d
- s
-  
- m
- u
- l
- t
- i
- -
- c
- o
- l
- o
- u
- r
- ;
-  
- s
- a
- m
- e
-  
- A
- P
- I
