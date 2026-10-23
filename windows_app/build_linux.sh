#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv-linux"

cd "$SCRIPT_DIR"

python3 -m venv "$VENV_DIR"
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
  "$VENV_DIR/bin/python" -m pip install --index-url https://pypi.org/simple --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --index-url https://pypi.org/simple -r requirements.txt
else
  echo "Python antiguo detectado: $(python3 --version 2>&1)"
  echo "Se utilizara PyInstaller 3.6 compatible con Python 3.5."
  "$VENV_DIR/bin/python" -m pip install --index-url https://pypi.org/simple \
    "pip<21" "setuptools<51" "wheel<0.38"
  "$VENV_DIR/bin/python" -m pip install --index-url https://pypi.org/simple \
    "pyserial==3.5" "pyinstaller==3.6"
fi
"$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name PZEM-Monitor \
  pzem_monitor.py

echo "Ejecutable creado en: $SCRIPT_DIR/dist/PZEM-Monitor"
