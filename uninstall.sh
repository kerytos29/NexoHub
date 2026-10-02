#!/usr/bin/env bash
# Atajo para desinstalar NexoHub sin tener que acordarse del flag.
# Hace exactamente lo mismo que "./install.sh --uninstall": borra el
# lanzador (~/.local/bin/nexohub), la entrada de menu (.desktop), el
# icono, y pregunta si tambien borrar ~/.local/share/nexohub (donde
# vive el config.yaml de la instalacion).
#
# Uso:
#   ./uninstall.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/install.sh" ]; then
    echo "No se encontro install.sh junto a este script (se busco en $SCRIPT_DIR)." >&2
    exit 1
fi

exec "$SCRIPT_DIR/install.sh" --uninstall
