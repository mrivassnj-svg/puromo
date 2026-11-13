# Puromo プロモ

> **Web-crawler → dimension math → modular PCB shell → Blender STL → firmware → PDF report → 3D print server**

Puromo is a full-stack handheld device design and manufacturing system. Point it at a PCB (Waveshare LCD HAT, Adafruit, SparkFun, or your own custom dimensions) and it builds everything you need to design, print, and program a custom handheld enclosure.

---

## ✨ Features

| Layer | What it does |
|-------|-------------|
| 🔍 **Web Crawler** | Scrapes Waveshare / Adafruit / SparkFun / Arduino / RPi for exact PCB specs. Hardcoded exact math for 3 Waveshare LCD HATs. |
| 📐 **Dimension Engine** | Computes exact shell W×H×D from board + GPIO header variant. Pixel pitch (0.0975 mm), PPI (261.09), display cutout, standoffs, snap-fits, ribs. |
| 🧊 **Blender Generator** | Headless Blender (`--background --python`) produces `_bottom.stl`, `_lid.stl`, `_shell.blend` with Boolean cuts for display, buttons, joystick, ports. |
| 🗂 **Modular Entities** | `TopCase`, `BottomCase`, `BatteryTray`, `PortModule`, `ButtonCap`, `JoystickRing`, `GripPanel`, `StandoffPillar` — all parameterised from geometry. |
| 🔌 **Pinout Engine** | Full RPi 40-pin GPIO map. 11 module pinout definitions (ST7789 LCD HAT, SSD1306 OLED, buttons, MCP3008 ADC joystick, LiPo+TP4056, USB-C, HDMI, audio, MicroSD, NeoPixel, MPU-6050). Conflict detection + power budget. |
| ⚡ **Firmware Generator** | 15 real Python files: `main.py`, `config.py`, `event_bus.py`, 7 drivers, 2 app files, README. 11-phase boot sequence, cooperative asyncio scheduler, typed EventBus. |
| 📄 **PDF Report** | 10-section professional PDF: cover page, architecture, shell dimensions, display math, GPIO visual map, wiring tables, firmware boot sequence, assembly guide, print settings, safety checklist, sign-off block. |
| 🖨 **Print Server** | 7-printer catalogue (FlashForge Adventurer 5M + 5M Pro, Bambu X1C, Prusa MK4, Creality K1, OctoPrint, Moonraker). ZIP package with STLs + OrcaSlicer config + FlashPrint G-code + transfer README. Upload & start print over LAN or FlashCloud. |
| 🌐 **Web UI** | Dark SPA with 10 tabs: 3D View (Three.js, rotate/zoom/pan/explode/wireframe/x-ray), Device Spec, Geometry, Pinout, Wiring SVG, Assembly, Firmware code viewer, Report, Print, Files. Per-part colour picker. Device save-slots (localStorage). Custom PCB entry form. Blender log live-polling. |

---

## 🗂 Project Structure

```
puromo/
├── main.py                     FastAPI backend — 33 routes
├── requirements.txt
├── start.sh                    One-command install + launch
│
├── crawler/
│   └── device_crawler.py       DeviceCrawler, WAVESHARE_LCD_HATS (3),
│                               GPIO_HEADER_VARIANTS (8), COMMON_COMPONENT_HEIGHTS (19)
│
├── parser/
│   └── dimension_math.py       DimensionMath → ShellGeometry (exact W×H×D)
│
├── blender_gen/
│   └── blender_shell_gen.py    Headless Blender → bottom.stl + lid.stl + shell.blend
│
├── modular/
│   └── modular_entities.py     TopCase, BottomCase, BatteryTray, PortModule, ButtonCap…
│
├── specs/
│   ├── assembly_specs.py       BOM, filament estimate, 9-step assembly guide
│   └── pinout_engine.py        40-pin GPIO map, 11 modules, conflict detection, power budget
│
├── firmware/
│   ├── firmware_engine.py      BOOT_PHASES (11), EVENT_TYPES (16), SCHEDULER_DESIGN
│   └── firmware_generator.py   Generates 15 real Python firmware files
│
├── report/
│   └── pdf_report.py           PDFReportGenerator — 10-section professional PDF
│
├── printer/
│   └── printer_server.py       PrintPackager + PrinterClient (FlashForge/OctoPrint/Moonraker)
│
├── ui/
│   └── index.html              Full SPA — Three.js 3D viewer, 10 tabs, all features
│
├── data/
│   └── devices/                JSON cache for crawled specs
│
└── output/                     All generated files land here
    ├── <model>/                Firmware tree (15 .py files)
    ├── <model>_design_report.pdf
    ├── <model>_print_package.zip
    └── <model>_blender.log
```

---

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/mrivassnj-svg/puromo.git
cd puromo

# Install & launch (Python 3.9+)
chmod +x start.sh
./start.sh

# Open browser
open http://localhost:8765
```

**Optional — Blender for STL generation:**
```bash
# Install Blender 3.x+ and add to PATH
# https://www.blender.org/download/
blender --version   # verify
```

---

## 🖥 Usage

### 1. Select a device
- Click a known **Waveshare LCD HAT** in the sidebar, or
- Type any model name and hit **Crawl / Lookup**, or
- Expand **Custom PCB Entry** and type your board dimensions manually

### 2. Configure the build
- Choose **GPIO Header Variant** (standard 11mm, low-profile, tall, pogo, ribbon…)
- Set a **Model Name**
- Optionally enable **Battery Bay**

### 3. Run Pipeline
Click **▶ Run Pipeline** — the system executes all 4 stages in sequence:
1. Dimension math → shell geometry
2. Modular build manifest (12–15 entities)
3. Assembly specs + BOM
4. Pinout + wiring diagram

### 4. Explore results
| Tab | Contents |
|-----|---------|
| **3D View** | Live WebGL shell with display cutout, PCB, buttons, joystick, mounting bosses. Explode, wireframe, x-ray, per-part colour picker. |
| **Geometry** | Shell envelope table + 2D top-down canvas preview |
| **Pinout** | 40-pin GPIO colour map, per-module wiring tables, conflicts, power budget, safety checklist |
| **Wiring** | Interactive SVG wiring diagram with Bezier wires from RPi header to each module |
| **Assembly** | Hardware BOM, filament estimate, step-by-step assembly guide, tolerances |
| **Firmware** | Generate 15 Python files. View any file with syntax highlighting. Download as ZIP. |
| **Report** | Generate a professional PDF design report (cover page → sign-off block) |
| **Print** | Select printer, test connection, create STL+profile ZIP, upload & start print |
| **Files** | Browse all generated output files, live Blender log polling |

### 5. Save your build
In the **Files** tab → **Saved Builds**: name your build and save it to localStorage. Load it back instantly from any device on the same browser.

---

## 🔌 Supported GPIO Header Variants

| Key | Label | Above PCB | Mated gap |
|-----|-------|-----------|-----------|
| `standard_11mm` | Standard 2.54mm (11mm) | 11.0 mm | 8.5 mm |
| `tall_15mm` | Tall header (15mm) | 15.0 mm | 12.5 mm |
| `low_profile_5mm` | Low-profile (5mm) | 5.0 mm | 2.5 mm |
| `low_profile_8mm` | Low-profile (8mm) | 8.0 mm | 5.5 mm |
| `stacking_20mm` | Stacking header (20mm) | 20.0 mm | 17.5 mm |
| `soldered_direct` | Direct solder (no header) | 1.5 mm | 0.0 mm |
| `gpio_ribbon` | GPIO ribbon cable | 2.0 mm | 2.0 mm |
| `pogo_pin` | Pogo-pin connector | 3.0 mm | 1.5 mm |

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

For the 1.3" HAT (240×240 ST7789):

```
pixel_pitch = display_active_width / resolution_w
            = 23.4 mm / 240 px = 0.0975 mm/px

PPI = 25.4 / pixel_pitch = 25.4 / 0.0975 = 260.5 ≈ 261.09 PPI

shell_depth = floor_thickness (2.5)
            + pcb_thickness (1.6)
            + gpio_header_mated_gap (8.5 for standard)
            + display_clearance (1.0)
            + lid_thickness (1.8)
            + margin (1.0)
            = ~32.0 mm for standard 11mm header
```

---

## ⚡ Firmware Architecture

Generated firmware follows a strict **11-phase boot sequence**:

```
Phase 0 — Power rail settle         (100 ms)
Phase 1 — GPIO init                 (50 ms)
Phase 2 — SPI bus init              (20 ms)
Phase 3 — I2C bus init              (20 ms)
Phase 4 — Display controller init   (150 ms)
Phase 5 — Backlight enable          (10 ms)
Phase 6 — Input devices init        (50 ms)
Phase 7 — Battery monitor init      (30 ms)
Phase 8 — IMU init                  (100 ms)
Phase 9 — Audio init                (50 ms)
Phase 10 — Application start        (0 ms)
```

All inter-module communication goes through the typed **EventBus** (16 event types). No direct driver-to-driver calls.

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

```
fastapi>=0.104
uvicorn[standard]>=0.24
requests>=2.31
beautifulsoup4>=4.12
lxml>=4.9
pydantic>=2.0
reportlab>=4.0
```

**Optional:**
- `blender` 3.x+ in `PATH` — for headless STL generation
- Python 3.9+

---

## 📄 License

MIT — do whatever you want with it.

---

<div align="center">
  <b>Puromo プロモ</b> — Built with 🤖 IBM Bob
</div>
