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

# ── pip / venv selection ──────────────────────────────
# On PEP 668 ("externally managed") systems, system pip refuses installs.
# In that case (or when a .venv already exists) use a project .venv.
PYTHON="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
  echo "✓ Using existing .venv"
elif ! python3 -m pip --version &>/dev/null; then
  echo "✗ pip not found. Run: python3 -m ensurepip --upgrade"
  exit 1
fi
echo "✓ pip available (via $PYTHON)"

# ── Install Python dependencies ───────────────────────
echo ""
echo "Installing Python dependencies…"
if ! "$PYTHON" -m pip install -q -r requirements.txt 2>/tmp/puromo-pip.log; then
  if [ "$PYTHON" = "python3" ]; then
    echo "⚠  System pip refused the install (externally-managed / PEP 668?) — falling back to .venv"
    cat /tmp/puromo-pip.log || true
    python3 -m venv .venv
    PYTHON=".venv/bin/python"
    "$PYTHON" -m pip install -q -r requirements.txt
  else
    cat /tmp/puromo-pip.log || true
    exit 1
  fi
fi
echo "✓ Python dependencies installed"

# ── Blender check (optional) ──────────────────────────
echo ""
if command -v blender &>/dev/null; then
  BLENDER_VER=$(blender --version 2>/dev/null | head -1 || echo "unknown")
  echo "✓ Blender found: $BLENDER_VER"
  echo "  STL export via Blender will be available."
else
  echo "⚠  Blender not found in PATH — STL stage will be marked skipped."
  echo "   Install Blender 3.x+ from https://www.blender.org/download/"
  echo "   then add to PATH, or set BLENDER=/path/to/blender in environment."
fi

# ── ReportLab check (PDF) ─────────────────────────────
if "$PYTHON" -c "import reportlab" &>/dev/null; then
  echo "✓ ReportLab (PDF generation) available"
else
  echo "⚠  ReportLab not found — installing…"
  "$PYTHON" -m pip install -q reportlab
fi

# ── Output directory ──────────────────────────────────
mkdir -p output data/devices data/templates data/saved_builds
echo "✓ Output directories ready"

echo ""
echo "═══════════════════════════════════════════════"
echo "  Starting server on http://0.0.0.0:${PORT}"
echo "  Open in browser: http://localhost:${PORT}"
echo "═══════════════════════════════════════════════"
echo ""

"$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" $RELOAD
