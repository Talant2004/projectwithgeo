@echo off
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --onefile ^
  --name CuscutaDetector ^
  --hidden-import rasterio.sample --hidden-import rasterio.vrt ^
  --hidden-import rasterio.features --hidden-import fiona.schema ^
  CuscutaDetector.py
echo.
echo Ready: dist\CuscutaDetector.exe
pause
