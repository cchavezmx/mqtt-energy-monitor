#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv-linux"

cd "$SCRIPT_DIR"

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "Error: se requiere Python 3.8 o superior." >&2
  echo "Version encontrada: $(python3 --version 2>&1)" >&2
  echo "Actualiza Raspberry Pi OS antes de compilar." >&2
  exit 1
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --index-url https://pypi.org/simple --upgrade pip
"$VENV_DIR/bin/python" -m pip install --index-url https://pypi.org/simple -r requirements.txt
"$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name PZEM-Monitor \
  pzem_monitor.py

echo "Ejecutable creado en: $SCRIPT_DIR/dist/PZEM-Monitor"
