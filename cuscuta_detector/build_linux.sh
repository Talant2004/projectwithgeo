#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --onefile \
  --name CuscutaDetector \
  --hidden-import rasterio.sample --hidden-import rasterio.vrt \
  --hidden-import rasterio.features --hidden-import fiona.schema \
  CuscutaDetector.py
echo "Ready: dist/CuscutaDetector"
# copy to website downloads if present
DEST="$(dirname "$0")/../web/downloads"
if [ -d "$DEST" ]; then
  cp -f dist/CuscutaDetector "$DEST/CuscutaDetector-linux"
  echo "Copied to $DEST/CuscutaDetector-linux"
fi
