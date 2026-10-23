FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY server.py .
COPY static ./static
ENV GPKG_PATH=/data/field_roads.gpkg
ENV CACHE_PATH=/data/graph_cache.pkl
ENV GEOSERVER_URL=http://geoserver:8080/geoserver
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
