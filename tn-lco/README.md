# TN-LCO — District Land-Change Observatory (prototype)

A one-day, zero-budget research prototype derived from the TNSLURB proposal
*"One Tamil Nadu Geo-AI Platform"*.

It classifies land cover at two dates from free satellite imagery, detects real
land-cover change, suppresses seasonal false alarms, stores results in PostGIS, and
serves them through a web map with priority alerts and a field-verification loop.

**Study window:** Perundurai–Erode, Tamil Nadu (~38 × 33 km).
**Epochs:** Jan–Apr 2019 vs Jan–Apr 2026.
**Read `BUILD_GUIDE.md` first.**

The map also supports clickable change polygons. A polygon popup shows the
transition, area, centroid coordinates, and a direct Google Maps link for
opening the location in the field.

---

**No Docker. No database server.** Storage is SQLite, which ships inside Python.

## Quick start

```bash
# 1. Python environment - the entire toolchain
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r api/requirements.txt                  # fastapi + uvicorn only

# 2. Create the database file
python scripts/init_db.py

# 3. Simulated ground sensors - 2 years, so the image window is covered
python scripts/simulate_sensors.py --seed --days 730

# 4. Satellite pipeline — run gee/tn_lco_pipeline.py in Google Colab.
#    Set ee.Initialize(project='YOUR-PROJECT-ID').
#    Copy the six output files into ./data/

# 5. Load results
python scripts/load_results.py

# 6. Run
uvicorn api.main:app --reload --port 8000
# open http://localhost:8000
```

Optional live sensor stream, in a second terminal:
```bash
python scripts/simulate_sensors.py --stream
```

---

## Layout

```
gee/tn_lco_pipeline.py     Earth Engine: composites, Random Forest, change filters, exports
db/schema_sqlite.sql       SQLite schema
scripts/init_db.py         Creates data/tnlco.db - run once
api/db.py                  Connection helper, haversine distance, centroid maths
scripts/load_results.py    Loads GEE outputs into SQLite, generates alerts
scripts/simulate_sensors.py  SIMULATED sensor nodes and readings
api/main.py                FastAPI: changes, alerts, sensors, context, verification, report
api/static/index.html      MapLibre web interface, single file, no build step
Coimbatore_LULC_MultiYear/ Standalone 2019–2026 Coimbatore output viewer
api/static/web-ui.html     Desktop operations dashboard UI concept
api/static/mobile-ui.html  Mobile field-verification UI concept
data/                      Earth Engine outputs and tnlco.db land here (git-ignored)
BUILD_GUIDE.md             Hour-by-hour plan, demo script, honest-claims rules
PROMPT_PACK.md             Prompts for building the extensions with an AI assistant
```

---

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Database reachable, change count |
| GET | `/api/overlays` | Corner coordinates and legend for the LULC image overlays |
| GET | `/api/stats` | Class areas per epoch, model agreement, filter effect |
| GET | `/api/summary` | Transition summary table |
| GET | `/api/changes` | Change polygons as GeoJSON. `min_area_ha`, `to_class`, `limit` |
| GET | `/api/changes/{id}/context` | Nearest sensor + seasonal-vs-permanent assessment, over the T2 image window |
| POST | `/api/changes/{id}/verify` | Record a field verdict |
| GET | `/api/verification/scorecard` | Alert precision from verified alerts |
| GET | `/api/alerts` | Ranked alerts, `status` filter |
| GET | `/api/sensors` | Sensor nodes with latest readings |
| GET | `/api/sensors/{id}/readings` | Time series, `days` |
| GET | `/api/report` | Self-generating district report with method limits |

Interactive docs at `/docs`.

Each `/api/changes` feature includes `centroid_lon` and `centroid_lat`.
The web map uses those coordinates for polygon popups and Google Maps links:

```text
https://www.google.com/maps/search/?api=1&query=<latitude>,<longitude>
```

---

## What is real and what is not

Real: Sentinel-2, Sentinel-1, SRTM, the Random Forest, the change polygons, the
class areas, the confusion matrix, the verification loop.

**Simulated: all ground-sensor readings.** No hardware is deployed. The UI marks
this. Do not remove the marking.

Absent: cadastral parcels, ownership records, zoning. Therefore this system detects
**land-cover change**, not encroachment, and makes no statement about legality.

Not built: Digital Twin, mobile application, state-wide coverage, real-time ingestion.

---

## Known limitations

- Training labels come from Google Dynamic World, a model output. Reported agreement
  is not ground-truth accuracy. Use the 60 independently interpreted points for that.
- Minimum reliable object size is about 0.1 ha. Individual buildings are below the
  10 m resolution limit.
- One study window, one district. Spatial transferability is untested.
- No authentication. Do not expose this beyond localhost.
- SQLite has no spatial index or spatial join. Nearest-sensor distance is computed
  in Python and polygon centroids are precomputed at load time. This is adequate
  below a few thousand polygons. Joining changes to village boundaries needs PostGIS.
- The seasonal filter reads sensor data from the second epoch's date window, so the
  simulated history must span that window. Seed with `--days 730`.
- LULC rasters are served as PNG image overlays, so zooming past roughly 1:15,000
  shows resampling artefacts.

---

## Licence and data attribution

Contains modified Copernicus Sentinel data (2019, 2025, 2026), processed by ESA.
Landsat and SRTM courtesy of USGS/NASA. Dynamic World © Google, CC BY 4.0.
Basemap © OpenStreetMap contributors.
