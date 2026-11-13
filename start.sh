#!/usr/bin/env bash
# start.sh — Install dependencies and launch the Handheld 3D Shell Builder
# Usage: ./start.sh [--port 8765] [--no-reload]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8765
RELOAD="--reload"

while [[ $# -gt 0 ]]; do
  case $1 in
    --port) PORT="$2"; shift 2 ;;
    --no-reload) RELOAD=""; shift ;;
    *) shift ;;
  esac
done

echo "╔══════════════════════════════════════════════╗"
echo "║   Puromo プロモ — Handheld 3D Shell Builder  ║"
echo "║   v1.0  github.com/mrivassnj-svg/puromo      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Python check ──────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "✗ python3 not found. Install Python 3.9+ first."
  exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PY_VER"

# ── pip check ─────────────────────────────────────────
if ! python3 -m pip --version &>/dev/null; then
  echo "✗ pip not found. Run: python3 -m ensurepip --upgrade"
  exit 1
fi
echo "✓ pip available"

# ── Install Python dependencies ───────────────────────
echo ""
echo "Installing Python dependencies…"
python3 -m pip install -q -r requirements.txt
echo "✓ Python dependencies installed"

# ── Blender check (optional) ──────────────────────────
echo ""
if command -v blender &>/dev/null; then
  BLENDER_VER=$(blender --version 2>/dev/null | head -1 || echo "unknown")
  echo "✓ Blender found: $BLENDER_VER"
  echo "  STL export via Blender will be available."
else
  echo "⚠  Blender not found in PATH — STL generation will be queued but won't run."
  echo "   Install Blender 3.x+ from https://www.blender.org/download/"
  echo "   then add to PATH, or set BLENDER=/path/to/blender in environment."
fi

# ── ReportLab check (PDF) ─────────────────────────────
if python3 -c "import reportlab" &>/dev/null; then
  echo "✓ ReportLab (PDF generation) available"
else
  echo "⚠  ReportLab not found — installing…"
  python3 -m pip install -q reportlab
fi

# ── Output directory ──────────────────────────────────
mkdir -p output data/devices data/templates
echo "✓ Output directories ready"

echo ""
echo "═══════════════════════════════════════════════"
echo "  Starting server on http://0.0.0.0:${PORT}"
echo "  Open in browser: http://localhost:${PORT}"
echo "═══════════════════════════════════════════════"
echo ""

uvicorn main:app --host 0.0.0.0 --port "$PORT" $RELOAD
