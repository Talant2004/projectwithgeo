#!/bin/bash
# Один сервер: PostGIS + GeoServer + FieldNav (маршруты) + шлюз nginx.
set -euo pipefail
cd "$(dirname "$0")"
docker compose up -d
echo "Ожидание GeoServer…"
for i in $(seq 40); do
  if curl -sf -o /dev/null -m 3 http://127.0.0.1:8080/geoserver/web/; then
    echo "GeoServer готов"
    break
  fi
  sleep 3
done
echo "Веб-модуль: http://127.0.0.1:8088/"
echo "FieldNav:   http://127.0.0.1:8000/  (или /fieldnav/ через шлюз)"
echo "GeoServer:  http://127.0.0.1:8088/geoserver/"
docker compose ps
