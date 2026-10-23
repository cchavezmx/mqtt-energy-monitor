#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXECUTABLE="$SCRIPT_DIR/dist/PZEM-Monitor"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/pzem-monitor.desktop"

if [[ ! -x "$EXECUTABLE" ]]; then
  echo "No existe el ejecutable: $EXECUTABLE" >&2
  echo "Ejecuta primero: $SCRIPT_DIR/build_linux.sh" >&2
  exit 1
fi

mkdir -p "$AUTOSTART_DIR"
{
  echo "[Desktop Entry]"
  echo "Type=Application"
  echo "Name=PZEM Monitor"
  printf 'Exec=%s\n' "$EXECUTABLE"
  printf 'Path=%s\n' "$SCRIPT_DIR"
  echo "Terminal=false"
  echo "X-GNOME-Autostart-enabled=true"
  echo "Comment=Monitor de energia PZEM"
} > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"

echo "Inicio automatico instalado en: $DESKTOP_FILE"
echo "La aplicacion se abrira al iniciar la sesion grafica."
