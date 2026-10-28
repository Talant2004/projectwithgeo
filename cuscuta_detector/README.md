# CuscutaDetector 1.0

Отдельное приложение для поиска очагов повилики (*Cuscuta*) на RGB-ортофотоплане.

- Вход: GeoTIFF RGB  
- Алгоритм: цветовая маска в **HSV** (жёлто-оранжевый диапазон)  
- Правка кистью, буфер, экспорт **KML** для агродрона  

Не требует сервера «Повилика» — работает локально на компьютере.

## Windows (.exe)

1. Установите [Python 3.11+](https://www.python.org/downloads/) (галочка «Add to PATH»).
2. В этой папке:

```bat
build_windows.bat
```

Готовый файл: `dist\CuscutaDetector.exe`  
Его можно положить на сайт в `web/downloads/`.

## Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python CuscutaDetector.py
# или сборка:
bash build_linux.sh
```

## Запуск из исходников (без сборки)

```bash
pip install -r requirements.txt
python CuscutaDetector.py
```

Откройте RGB `.tif` / `.tiff` → чувствительность → «Рассчитать» → «Сохранить KML».
