# Puromo プロモ

> **Web-crawler → dimension math → modular PCB shell → Blender STL → firmware → PDF report → 3D print server**

Puromo is a full-stack handheld device design system. Point it at a PCB and it computes an exact shell, a modular build, firmware, a PDF report, and a printer package — in that order, as a 7-stage pipeline in the web UI.

---

## ✨ Features

| Layer | What it does |
|-------|-------------|
| 🔍 **Web Crawler** | Looks up device specs. Vendor URL patterns exist for Waveshare / Adafruit / SparkFun / Arduino / Raspberry Pi, with regex extraction on a live crawl and a zero/fallback spec (65×30 mm) when nothing is found. **Exact hardcoded math exists for only 3 Waveshare LCD HAT keys** (see below); every other vendor/board relies on crawled, cached, or manually entered data. |
| 📐 **Dimension Engine** | `DimensionMath.compute()` is the **single source of truth for geometry**. It emits one geometry dict (shell W×H×D, wall/floor/lid/board thickness, standoffs, header geometry + position, display cutout, bosses, apertures, ribs, snap-fits, depth breakdown, and fit/tolerance warnings) that both the Three.js viewer and the Blender generator consume — neither recomputes dimensions itself. |
| 🧊 **Blender Generator** | Headless Blender (`--background --python … --geometry <backend geometry JSON>`) produces `_bottom.stl`, `_lid.stl`, `_pcb.stl`, `_shell.blend`. If Blender is not installed, stage 4 is marked **skipped**, not failed. |
| 🗂 **Modular Entities** | `TopCase`, `BottomCase`, `BatteryTray`, `PortModule`, `ButtonCap`, `JoystickRing`, `GripPanel`, `StandoffPillar` — parameterised from geometry. A build is **12 entities without a battery bay, 13 with one** (for the 1.3″ HAT: TopCase + BottomCase + 3 ButtonCaps + JoystickRing + 2 GripPanels + 4 StandoffPillars, + BatteryTray). |
| 🔌 **Pinout Engine** | Full RPi 40-pin GPIO map. 11 module pinout definitions (ST7789 LCD HAT, SSD1306 OLED, tactile buttons, MCP3008 ADC joystick, LiPo+TP4056, USB-C, micro-HDMI, audio, MicroSD, NeoPixel, MPU-6050). Conflict detection + power budget. Shared bus pins are not conflicts: SPI MOSI/MISO/SCLK (e.g. GPIO10 MOSI / GPIO11 SCLK, shared by the LCD and the MCP3008 joystick) and I2C SDA/SCL may be claimed by several modules for the same bus role — devices are distinguished by chip-select (SPI) or I2C address. The engine returns those pins separately as `shared_buses`, and the UI shows them as shared by design. A chip-select or any other exclusive pin claimed by two modules is still a conflict. |
| ⚡ **Firmware Generator** | Emits **15 manifest files: 14 Python files + `README_FIRMWARE.md`** (`main.py`, `config.py`, `event_bus.py`, 7 drivers — display_st7789, backlight, buttons, joystick, battery, imu_mpu6050, audio — `app/app.py`, `app/state_machine.py`, and the two `__init__.py` files), **plus `firmware_manifest.json` written separately = 16 files on disk**. 11-phase boot sequence, cooperative asyncio scheduler, typed EventBus with **11 event types**. No SSD1306 or NeoPixel driver file is generated. |
| 📄 **PDF Report** | Cover page + table of contents + **10 numbered sections**: architecture, shell dimensions, display math, GPIO visual map, wiring, firmware boot sequence, assembly guide, print settings, safety checklist, sign-off block. |
| 🖨 **Print Server** | 7-printer catalogue (FlashForge Adventurer 5M + 5M Pro, Bambu X1C, Prusa MK4, Creality K1, OctoPrint, Moonraker). One **Package for printer** action builds a ZIP with the STLs found in `output/` + slicer profile + OrcaSlicer machine config + transfer README. Upload/start-print are separate, manual actions in stage 7 — Run Pipeline never starts a printer. |
| 🌐 **Web UI** | Dark SPA organised as a **7-stage stepper** (not tabs — see Usage). Plain rotate/zoom/pan Three.js preview driven entirely by backend geometry. **Server-side saved builds** (JSON in `data/saved_builds/`). |

### Exact math: which boards?

Exact hardcoded specs/math exist **only** for these 3 keys in `WAVESHARE_LCD_HATS`:

| Key | Model |
|-----|-------|
| `1.3inch_lcd_hat` | Waveshare 1.3inch LCD HAT (240×240) |
| `2inch_lcd_hat` | Waveshare 2inch LCD HAT (240×320) |
| `240x240_1.3_hat` | Waveshare 240x240 1.3inch HAT (overlaps the first 1.3″ entry) |

Everything else — other Waveshare products, Adafruit, SparkFun, Arduino, Raspberry Pi boards, and custom PCBs — relies on **crawled regex extraction, the local JSON cache, manual entry, or the 65×30 mm fallback spec**. Treat those dimensions as unverified until you check them.

---

## 🗂 Project Structure

```
puromo/
├── main.py                     FastAPI backend — 34 route decorators (33 under /api/ + GET /)
├── requirements.txt
├── start.sh                    One-command install + launch (uses .venv when system pip is PEP 668 externally-managed)
│
├── crawler/
│   └── device_crawler.py       DeviceCrawler, WAVESHARE_LCD_HATS (3 exact keys),
│                               GPIO_HEADER_VARIANTS (8), COMMON_COMPONENT_HEIGHTS (19), VENDOR_PATTERNS (5 vendors)
│
├── parser/
│   └── dimension_math.py       DimensionMath → ShellGeometry (the geometry single source of truth)
│
├── blender_gen/
│   └── blender_shell_gen.py    Headless Blender → bottom/lid/pcb STL + shell.blend; consumes --geometry JSON
│
├── modular/
│   └── modular_entities.py     TopCase, BottomCase, BatteryTray, PortModule, ButtonCap…
│
├── specs/
│   ├── assembly_specs.py       BOM, filament estimate, 9-step assembly guide
│   └── pinout_engine.py        40-pin GPIO map, 11 modules, conflict detection, power budget
│
├── firmware/
│   ├── firmware_engine.py      BOOT_PHASES (11), EVENT_TYPES (11), SCHEDULER_DESIGN
│   └── firmware_generator.py   Generates 14 Python files + README_FIRMWARE.md (+ firmware_manifest.json)
│
├── report/
│   └── pdf_report.py           PDFReportGenerator — cover + TOC + 10 numbered sections
│
├── printer/
│   └── printer_server.py       PrintPackager + PrinterClient (FlashForge/OctoPrint/Moonraker…)
│
├── ui/
│   └── index.html              SPA — 7-stage stepper, Three.js preview (backend geometry only)
│
├── data/
│   ├── devices/                JSON cache for crawled specs (created by start.sh)
│   ├── saved_builds/           Server-side saved builds, one JSON per build (created by start.sh / the API)
│   └── templates/              Created by start.sh (currently unused)
│
└── output/                     Generated files land here
    ├── waveshare_1_3_lcd_hat/  Sample firmware tree: 14 .py files, 16 files total
    ├── test_handheld_print_package/  Sample print-package contents
    ├── <model>_geometry.json   Backend geometry JSON passed to Blender
    ├── <model>_design_report.pdf
    ├── <model>_print_package.zip
    └── <model>_blender.log
```

---

## 🚀 Quick Start

```bash
git clone https://github.com/mrivassnj-svg/puromo.git
cd puromo
chmod +x start.sh
./start.sh            # installs deps (into .venv if system pip is externally managed) and launches
# Open http://localhost:8765
```

**Optional — Blender for STL generation:**
```bash
blender --version   # verify Blender 3.x+ is on PATH; without it stage 4 is skipped
```

---

## 🖥 Usage — the 7-stage stepper

The UI is a stepper, one stage per pipeline step. Each stage shows its inputs, a status (**not run / running / done / failed / skipped**), its outputs, and a **re-run** button. **▶ Run Pipeline** (sidebar) runs all 7 stages in order, stops on the first failure with that stage's error, and continues past a skipped stage 4.

| Stage | Inputs | Outputs / panels |
|-------|--------|------------------|
| **1. Crawl / Lookup** | A known Waveshare HAT (sidebar), a search query + vendor, or the Custom PCB form | Device spec JSON (Device Spec panel) |
| **2. Dimensions** | Device spec | Backend ShellGeometry; **3D preview + Geometry table + 2D top-down preview**; **fit/tolerance warnings** from the dimension engine |
| **3. Modular Build** | Spec + geometry | Build manifest (**12 entities, 13 with battery**); **Assembly panel with BOM + filament estimate**; **Pinout + Wiring panels** (placed here because they describe how the modular build is wired and assembled) |
| **4. Blender STL** | Spec + backend geometry JSON | STL/.blend files; **Files panel + Blender log**. *Skipped: Blender not found* when Blender is absent |
| **5. Firmware** | Spec | Firmware tree viewer + architecture (boot phases, driver order, scheduler, EventBus) |
| **6. PDF Report** | Spec + geometry + pinout + assembly specs | PDF design report |
| **7. Print** | STLs in `output/` + printer choice | **Package for printer** (single action → ZIP). Printer connect / upload / start-print are separate manual actions here. **Saved Builds** also live in this stage |

Pinout conflicts and the power budget are also shown in an alert area above the stages, so you don't have to open stage 3 to see them.

### Save your build
In **stage 7 → Saved Builds**: name the build and save it. Builds are stored **on the server** as JSON in `data/saved_builds/`, so they can be loaded from any browser or device that can reach this server. (This replaces the old `localStorage` saves, which only ever worked in the one browser that saved them.)

### Removed features
Removed because they did not serve the pipeline (or duplicated geometry): the **Deck / Groq cyberdeck archetypes** (9 hardcoded builds with their own BOM/wiring, unrelated to the backend), the **per-part colour picker**, the **explode toggle**, the **wireframe/x-ray render modes**, and the **Bezier curves** in the wiring diagram (now straight lines). The 3D preview keeps rotate/zoom/pan, with the lid shown at a fixed offset so the PCB layer stays visible.

---

## 🔌 Supported GPIO Header Variants

Values are `GPIO_HEADER_VARIANTS` in `crawler/device_crawler.py` (`above_mm` = header housing above the HAT PCB, `mated_gap_mm` = board-to-board gap):

| Key | Label | Above PCB | Mated gap |
|-----|-------|-----------|-----------|
| `standard_11mm` | Standard 11mm (RPi HAT default) | 8.5 mm | 11.5 mm |
| `tall_15mm` | Tall 15mm stacking header | 12.0 mm | 15.0 mm |
| `low_profile_5mm` | Low-profile 5mm header | 2.5 mm | 5.5 mm |
| `low_profile_8mm` | Low-profile 8mm header | 5.5 mm | 8.5 mm |
| `stacking_20mm` | Stacking 20mm header | 17.0 mm | 20.0 mm |
| `soldered_direct` | Soldered direct (no header) | 0.0 mm | 1.0 mm |
| `gpio_ribbon` | GPIO ribbon cable + 11mm standoffs | 2.5 mm | 11.5 mm |
| `pogo_pin` | Pogo-pin connector | 3.0 mm | 4.0 mm |

---

## 🖨 Supported Printers

| Printer | Protocol | Transfer |
|---------|----------|----------|
| FlashForge Adventurer 5M | FlashForge HTTP | LAN upload, FlashCloud |
| FlashForge Adventurer 5M Pro | FlashForge HTTP | LAN upload, FlashCloud |
| Bambu Lab X1C | Bambu FTP | LAN FTP, Bambu Cloud |
| Prusa MK4 | PrusaLink REST | LAN upload |
| Creality K1 | Creality HTTP | LAN upload |
| OctoPrint (generic) | OctoPrint REST | LAN API, OctoEverywhere |
| Moonraker / Klipper | Moonraker REST | LAN API, Cloudflare Tunnel |

---

## 📐 Waveshare Display Math

For the 1.3″ HAT (240×240 ST7789). **PPI has exactly one definition in this project** — the diagonal formula used by `DimensionMath`:

```
pixel_pitch = display_active_width / resolution_w
            = 23.4 mm / 240 px = 0.0975 mm/px

PPI = sqrt(resolution_w² + resolution_h²) / display_diagonal_inch
    = sqrt(240² + 240²) / 1.3 = 261.09 PPI
```

(`25.4 / pixel_pitch = 260.51` is a *different* quantity computed from the active width; it is not an approximation of 261.09 and is not used as PPI anywhere in the code or this README.)

Shell depth for the same HAT with the standard 11 mm header — the real `DimensionMath` stack (verified: it produces **70.0 × 35.1 × 32.0 mm**):

```
shell_depth = floor_thickness            2.5
            + standoff_height            3.0
            + host_pcb_thickness         1.6
            + gpio_header_mated_gap     11.5   (standard_11mm)
            + hat_pcb_thickness          1.6
            + above_hat_pcb             10.0   = max(header_above 8.5, tallest_component 8.0) + 1.5 clearance
            + lid_thickness              1.8
            = 32.0 mm
```

The geometry JSON also carries this as `depth_breakdown`, so the UI, Blender, and this README cannot drift apart. `waveshare_hat_math_summary()` delegates to `DimensionMath.compute()` — there is no second depth formula.

---

## ⚡ Firmware Architecture

Generated firmware follows an **11-phase boot sequence** (phases 0–10 in `firmware_engine.BOOT_PHASES`; delays there are 50, 1, 5, 150, 50, 10, 5, 20, 10, 1, 0 ms — see the generated `README_FIRMWARE.md` for the per-phase table).

All inter-module communication goes through the typed **EventBus (11 event types)**. No direct driver-to-driver calls.

---

## 🛡 Safety Checklist (auto-generated per build)

- All 3.3V signals verified — no 5V GPIO exposure
- SPI bus CS lines are unique per device (CE0 for LCD, CE1 for ADC)
- I2C addresses unique (`i2cdetect -y 1` to confirm)
- Power supply rated ≥ recommended PSU amps
- Battery has built-in protection PCB
- All GND lines connected to common ground
- No GPIO sink current exceeds 16 mA per pin
- Total GPIO current < 50 mA across all pins
- RST pulse ≥ 10 µs applied before LCD init

---

## 📦 Requirements

Exactly as in `requirements.txt`:

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=5.2.0
pydantic>=2.0.0
reportlab>=4.0
```

**Optional:**
- `blender` 3.x+ in `PATH` — for headless STL generation (stage 4 is skipped without it)
- Python 3.9+

---

## 📄 License

MIT — do whatever you want with it.

---

<div align="center">
  <b>Puromo プロモ</b> — Built with 🤖 IBM Bob
</div>
