/* global L */
(function () {
  const WORKSPACE = "povilika";
  const GS = "/geoserver";

  const el = (id) => document.getElementById(id);
  const status = (msg, kind) => {
    const s = el("status");
    s.textContent = msg || "";
    s.className = kind || "";
  };

  const authHeader = () => {
    const u = el("apiUser").value.trim();
    const p = el("apiPass").value;
    if (!u || !p) return null;
    return "Basic " + btoa(unescape(encodeURIComponent(u + ":" + p)));
  };

  // Persist credentials lightly
  try {
    const saved = JSON.parse(localStorage.getItem("povilika_web_auth") || "{}");
    if (saved.u) el("apiUser").value = saved.u;
    if (saved.p) el("apiPass").value = saved.p;
  } catch (_) {}
  function saveAuth() {
    localStorage.setItem(
      "povilika_web_auth",
      JSON.stringify({ u: el("apiUser").value.trim(), p: el("apiPass").value })
    );
  }

  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  });
  const esri = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Tiles &copy; Esri" }
  );

  const map = L.map("map", {
    center: [45.5, 75.0],
    zoom: 7,
    layers: [osm],
    zoomControl: true,
  });

  let surveys = [];
  const rasterLayers = new Map(); // layerName -> L.TileLayer
  const vectorLayers = new Map();
  let fociLayer = null;
  let fociFeatures = [];
  let selectedFids = new Set();
  let activeSurveyLayer = null;
  let sideControl = null;
  let swipeLeftLayer = null;
  let swipeRightLayer = null;

  function tmsUrl(layerName) {
    return (
      GS +
      "/gwc/service/tms/1.0.0/" +
      WORKSPACE +
      ":" +
      layerName +
      "@EPSG:900913@png/{z}/{x}/{y}.png"
    );
  }

  function wmsUrl(layerName) {
    return (
      GS +
      "/" +
      WORKSPACE +
      "/wms?service=WMS&version=1.1.1&request=GetMap&layers=" +
      encodeURIComponent(WORKSPACE + ":" + layerName) +
      "&styles=&format=image/png&transparent=true&srs=EPSG:3857" +
      "&width=256&height=256&bbox={bbox}"
    );
  }

  function makeRasterLayer(name) {
    // Prefer GWC TMS; fallback path uses Leaflet TMS flag
    return L.tileLayer(tmsUrl(name), {
      tms: true,
      maxZoom: 22,
      opacity: 0.95,
      bounds: null,
      errorTileUrl:
        "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
    });
  }

  async function wfs(typeName, cql) {
    const params = new URLSearchParams({
      service: "WFS",
      version: "1.1.0",
      request: "GetFeature",
      typeName: WORKSPACE + ":" + typeName,
      outputFormat: "application/json",
      srsName: "EPSG:4326",
    });
    if (cql) params.set("CQL_FILTER", cql);
    const r = await fetch(GS + "/" + WORKSPACE + "/wfs?" + params.toString());
    if (!r.ok) throw new Error("WFS " + r.status);
    const data = await r.json();
    return data.features || [];
  }

  function surveyProps(f) {
    const p = f.properties || {};
    return {
      layerName: p.layer_name,
      farm: p.farm || "",
      field: p.field || "",
      date: (p.survey_date || "").slice(0, 10),
      kind: p.kind || "raster",
      title: p.title || p.layer_name,
      feature: f,
    };
  }

  function bboxFromGeom(geom) {
    if (!geom) return null;
    const coords = [];
    const walk = (c) => {
      if (typeof c[0] === "number") coords.push(c);
      else c.forEach(walk);
    };
    walk(geom.coordinates);
    if (!coords.length) return null;
    let minLat = 90,
      maxLat = -90,
      minLon = 180,
      maxLon = -180;
    coords.forEach(([lon, lat]) => {
      minLat = Math.min(minLat, lat);
      maxLat = Math.max(maxLat, lat);
      minLon = Math.min(minLon, lon);
      maxLon = Math.max(maxLon, lon);
    });
    return L.latLngBounds([minLat, minLon], [maxLat, maxLon]);
  }

  function renderSurveyList() {
    const box = el("surveyList");
    if (!surveys.length) {
      box.innerHTML = '<span class="meta">Съёмок нет. Загрузите файл ниже или через povilika_gui.</span>';
      return;
    }
    surveys.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    box.innerHTML = "";
    surveys.forEach((s) => {
      const label = document.createElement("label");
      label.className = "row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.dataset.layer = s.layerName;
      cb.dataset.kind = s.kind;
      cb.addEventListener("change", () => toggleSurvey(s, cb.checked));
      const span = document.createElement("span");
      span.innerHTML =
        "<strong>" +
        escapeHtml(s.farm) +
        " / " +
        escapeHtml(s.field) +
        "</strong><br/><span class=\"meta\">" +
        escapeHtml(s.date) +
        " · " +
        escapeHtml(s.kind) +
        " · " +
        escapeHtml(s.layerName) +
        "</span>";
      label.appendChild(cb);
      label.appendChild(span);
      box.appendChild(label);
    });
    fillSwipeSelects();
  }

  function escapeHtml(t) {
    return String(t)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fillSwipeSelects() {
    const names = [...rasterLayers.keys()];
    ["swipeLeft", "swipeRight"].forEach((id) => {
      const sel = el(id);
      const cur = sel.value;
      sel.innerHTML = '<option value="">—</option>';
      names.forEach((n) => {
        const o = document.createElement("option");
        o.value = n;
        o.textContent = n;
        sel.appendChild(o);
      });
      if (names.includes(cur)) sel.value = cur;
    });
  }

  function toggleSurvey(s, on) {
    if (s.kind === "raster") {
      if (on) {
        if (!rasterLayers.has(s.layerName)) {
          const lyr = makeRasterLayer(s.layerName);
          rasterLayers.set(s.layerName, lyr);
          lyr.addTo(map);
        }
        activeSurveyLayer = s.layerName;
        const b = bboxFromGeom(s.feature.geometry);
        if (b && b.isValid()) map.fitBounds(b.pad(0.15));
        loadFoci(s.layerName, s);
      } else {
        const lyr = rasterLayers.get(s.layerName);
        if (lyr) {
          map.removeLayer(lyr);
          rasterLayers.delete(s.layerName);
        }
        if (activeSurveyLayer === s.layerName) {
          activeSurveyLayer = [...rasterLayers.keys()].slice(-1)[0] || null;
          if (activeSurveyLayer) {
            const surv = surveys.find((x) => x.layerName === activeSurveyLayer);
            if (surv) loadFoci(activeSurveyLayer, surv);
          } else clearFoci();
        }
      }
      fillSwipeSelects();
    } else {
      // vector survey — WMS overlay
      if (on) {
        if (!vectorLayers.has(s.layerName)) {
          const lyr = L.tileLayer.wms(GS + "/" + WORKSPACE + "/wms", {
            layers: WORKSPACE + ":" + s.layerName,
            format: "image/png",
            transparent: true,
            version: "1.1.1",
            attribution: s.layerName,
          });
          vectorLayers.set(s.layerName, lyr);
          lyr.addTo(map);
        }
        const b = bboxFromGeom(s.feature.geometry);
        if (b && b.isValid()) map.fitBounds(b.pad(0.15));
      } else {
        const lyr = vectorLayers.get(s.layerName);
        if (lyr) {
          map.removeLayer(lyr);
          vectorLayers.delete(s.layerName);
        }
      }
    }
  }

  function clearFoci() {
    if (fociLayer) {
      map.removeLayer(fociLayer);
      fociLayer = null;
    }
    fociFeatures = [];
    selectedFids = new Set();
    el("fociList").innerHTML = "Нет данных";
  }

  function areaM2(props) {
    const ha = Number(props.area_ha || 0);
    return ha * 10000;
  }

  function centroid(geom) {
    const ring =
      geom && geom.type === "Polygon"
        ? geom.coordinates[0]
        : geom && geom.type === "MultiPolygon"
          ? geom.coordinates[0][0]
          : null;
    if (!ring || !ring.length) return [0, 0];
    const pts = ring[0][0] === ring[ring.length - 1][0] ? ring.slice(0, -1) : ring;
    let sx = 0,
      sy = 0;
    pts.forEach(([x, y]) => {
      sx += x;
      sy += y;
    });
    return [sx / pts.length, sy / pts.length];
  }

  function popupHtml(f, survey) {
    const p = f.properties || {};
    const [lon, lat] = centroid(f.geometry);
    const m2 = areaM2(p);
    const date = (p.created_at || (survey && survey.date) || "").toString().slice(0, 10);
    return (
      "<strong>Очаг повилики</strong><br/>" +
      "Центр (WGS-84): " +
      lat.toFixed(6) +
      "°, " +
      lon.toFixed(6) +
      "°<br/>" +
      "Площадь: " +
      m2.toFixed(1) +
      " м² (" +
      (m2 / 10000).toFixed(4) +
      " га)<br/>" +
      "Дата: " +
      escapeHtml(date) +
      "<br/>" +
      "Источник: " +
      escapeHtml(p.source || "—")
    );
  }

  async function loadFoci(layerName, survey) {
    status("Загрузка очагов…");
    try {
      const feats = await wfs("foci", "survey_layer = '" + layerName.replace(/'/g, "''") + "'");
      fociFeatures = feats;
      selectedFids = new Set();
      if (fociLayer) map.removeLayer(fociLayer);
      fociLayer = L.geoJSON(
        { type: "FeatureCollection", features: feats },
        {
          style: {
            color: "#c62828",
            weight: 2,
            fillColor: "#e53935",
            fillOpacity: 0.35,
          },
          onEachFeature: (f, layer) => {
            layer.bindPopup(() => popupHtml(f, survey));
          },
        }
      );
      if (el("chkFoci").checked) fociLayer.addTo(map);
      renderFociList(survey);
      status("Очагов: " + feats.length, "ok");
    } catch (e) {
      status("Ошибка очагов: " + e.message, "error");
    }
  }

  function renderFociList(survey) {
    const box = el("fociList");
    if (!fociFeatures.length) {
      box.innerHTML = "Очагов нет";
      return;
    }
    box.innerHTML = "";
    fociFeatures.forEach((f, i) => {
      const fid = f.id || f.properties?.id || String(i);
      const label = document.createElement("label");
      label.className = "row";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedFids.has(fid);
      cb.addEventListener("change", () => {
        if (cb.checked) selectedFids.add(fid);
        else selectedFids.delete(fid);
      });
      const [lon, lat] = centroid(f.geometry);
      const span = document.createElement("span");
      span.textContent =
        "#" +
        (i + 1) +
        " · " +
        areaM2(f.properties || {}).toFixed(0) +
        " м² · " +
        lat.toFixed(5) +
        ", " +
        lon.toFixed(5);
      label.appendChild(cb);
      label.appendChild(span);
      box.appendChild(label);
    });
  }

  async function loadSurveys() {
    status("Загрузка каталога…");
    try {
      const feats = await wfs("surveys");
      surveys = feats.map(surveyProps);
      renderSurveyList();
      status("Съёмок: " + surveys.length, "ok");
    } catch (e) {
      status("Нет связи с GeoServer (" + e.message + "). Проверьте /geoserver/", "error");
      el("surveyList").textContent = "Ошибка загрузки";
    }
  }

  // Basemap
  document.querySelectorAll('input[name="base"]').forEach((r) => {
    r.addEventListener("change", () => {
      if (r.value === "osm") {
        map.removeLayer(esri);
        if (!map.hasLayer(osm)) osm.addTo(map);
      } else {
        map.removeLayer(osm);
        if (!map.hasLayer(esri)) esri.addTo(map);
      }
    });
  });

  el("chkFoci").addEventListener("change", () => {
    if (!fociLayer) return;
    if (el("chkFoci").checked) fociLayer.addTo(map);
    else map.removeLayer(fociLayer);
  });

  el("chkSelectAll").addEventListener("change", () => {
    if (el("chkSelectAll").checked) {
      fociFeatures.forEach((f, i) => selectedFids.add(f.id || f.properties?.id || String(i)));
    } else {
      selectedFids.clear();
    }
    const surv = surveys.find((x) => x.layerName === activeSurveyLayer);
    renderFociList(surv);
  });

  el("btnReload").addEventListener("click", loadSurveys);
  el("btnFit").addEventListener("click", () => {
    const layers = [];
    if (fociLayer) layers.push(fociLayer);
    rasterLayers.forEach((l) => layers.push(l));
    if (layers.length) {
      const g = L.featureGroup(layers.filter((l) => l.getBounds));
      try {
        const b = g.getBounds();
        if (b.isValid()) map.fitBounds(b.pad(0.1));
      } catch (_) {
        map.setView([45.5, 75], 7);
      }
    }
  });

  el("btnMenu").addEventListener("click", () => el("panel").classList.toggle("open"));

  el("btnSwipeOn").addEventListener("click", () => {
    const left = el("swipeLeft").value;
    const right = el("swipeRight").value;
    if (!left || !right || left === right) {
      status("Выберите два разных растровых слоя", "error");
      return;
    }
    if (sideControl) {
      map.removeControl(sideControl);
      sideControl = null;
    }
    // Hide normal overlays for swipe pair; recreate dedicated layers
    rasterLayers.forEach((lyr) => map.removeLayer(lyr));
    if (swipeLeftLayer) map.removeLayer(swipeLeftLayer);
    if (swipeRightLayer) map.removeLayer(swipeRightLayer);
    swipeLeftLayer = makeRasterLayer(left);
    swipeRightLayer = makeRasterLayer(right);
    swipeLeftLayer.addTo(map);
    swipeRightLayer.addTo(map);
    sideControl = L.control.sideBySide(swipeLeftLayer, swipeRightLayer);
    sideControl.addTo(map);
    status("Шторка: " + left + " | " + right, "ok");
  });

  el("btnSwipeOff").addEventListener("click", () => {
    if (sideControl) {
      map.removeControl(sideControl);
      sideControl = null;
    }
    if (swipeLeftLayer) {
      map.removeLayer(swipeLeftLayer);
      swipeLeftLayer = null;
    }
    if (swipeRightLayer) {
      map.removeLayer(swipeRightLayer);
      swipeRightLayer = null;
    }
    rasterLayers.forEach((lyr) => lyr.addTo(map));
    status("Шторка выключена");
  });

  el("btnJson").addEventListener("click", () => {
    const selected = fociFeatures.filter((f, i) => {
      const fid = f.id || f.properties?.id || String(i);
      return selectedFids.size === 0 ? true : selectedFids.has(fid);
    });
    if (!selected.length) {
      status("Нет очагов для экспорта", "error");
      return;
    }
    const fc = { type: "FeatureCollection", features: selected };
    const blob = new Blob([JSON.stringify(fc, null, 2)], { type: "application/geo+json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "foci_" + (activeSurveyLayer || "export") + ".json";
    a.click();
    URL.revokeObjectURL(a.href);
    status("JSON скачан (" + selected.length + ")", "ok");
  });

  el("btnAct").addEventListener("click", async () => {
    saveAuth();
    const auth = authHeader();
    if (!auth) {
      status("Укажите логин и пароль API", "error");
      return;
    }
    if (!activeSurveyLayer) {
      status("Включите растровую съёмку", "error");
      return;
    }
    const surv = surveys.find((x) => x.layerName === activeSurveyLayer);
    const fids = [...selectedFids];
    status("Формирование акта…");
    try {
      const r = await fetch("/api/report/act", {
        method: "POST",
        headers: {
          Authorization: auth,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          survey_layer: activeSurveyLayer,
          farm: surv ? surv.farm : "",
          field: surv ? surv.field : "",
          survey_date: surv ? surv.date : "",
          fids: fids,
        }),
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || r.status);
      }
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "akt_" + activeSurveyLayer + ".pdf";
      a.click();
      URL.revokeObjectURL(a.href);
      status("PDF акт скачан", "ok");
    } catch (e) {
      status("Акт: " + e.message, "error");
    }
  });

  el("btnUpload").addEventListener("click", async () => {
    saveAuth();
    const auth = authHeader();
    if (!auth) {
      status("Укажите логин и пароль API", "error");
      return;
    }
    const file = el("upFile").files[0];
    if (!file) {
      status("Выберите файл", "error");
      return;
    }
    const farm = el("upFarm").value.trim();
    const field = el("upField").value.trim();
    const date = el("upDate").value;
    if (!farm || !field || !date) {
      status("Заполните хозяйство, поле и дату", "error");
      return;
    }
    const fd = new FormData();
    fd.append("farm", farm);
    fd.append("field", field);
    fd.append("date", date);
    fd.append("replace", el("upReplace").checked ? "true" : "false");
    fd.append("file", file);
    status("Загрузка " + file.name + "… это может занять время");
    try {
      const r = await fetch("/api/upload", {
        method: "POST",
        headers: { Authorization: auth },
        body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || JSON.stringify(data) || r.status);
      status(
        "Опубликовано: " + (data.layers || []).join(", ") + "\n" + (data.warning || ""),
        "ok"
      );
      await loadSurveys();
    } catch (e) {
      status("Загрузка: " + e.message, "error");
    }
  });

  // unused helper kept for clarity / future XYZ WMS
  void wmsUrl;

  loadSurveys();
})();
