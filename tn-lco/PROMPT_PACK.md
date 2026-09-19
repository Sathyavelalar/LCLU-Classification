# Prompt Pack — building the extensions with an AI assistant

You are building with an AI coding assistant. These prompts are written to produce
correct code on the first attempt. Two rules govern all of them.

**Rule 1 — always paste the contract, never describe it.** Paste the actual schema
block, the actual endpoint signature, the actual JSON keys. An assistant that has to
guess your column names will invent plausible wrong ones, and you will lose twenty
minutes finding out.

**Rule 2 — forbid the drift explicitly.** Assistants add authentication scaffolding,
Kubernetes manifests, abstract base classes and Redis caches when nobody asked.
Every prompt below ends with a constraint block. Keep it.

---

## P1 — Debugging the Earth Engine pipeline

```
I am running this Earth Engine Python script in Colab. It fails at <SECTION> with:

<paste the FULL traceback>

Here is the failing section only:
<paste 20-40 lines>

Context: ee.Initialize succeeded. AOI is a rectangle around 77.45,11.15 to
77.80,11.45. I am using COPERNICUS/S2_SR_HARMONIZED and GOOGLE/DYNAMICWORLD/V1.

Give me the corrected section and one sentence on the cause.
Constraints: do not restructure the script, do not switch to the JavaScript API,
do not add Export.image.toDrive if getThumbURL is already being used.
```

## P2 — Adding a third epoch

```
This script builds two epochs (2019, 2026) and compares them. Add a third epoch,
Jan-Apr 2022, using the same build_stack() and train_and_classify() functions.

Then produce a three-epoch transition summary: for each ordered pair of epochs,
the area in hectares converted from each class to each other class.

Paste of the relevant script section:
<paste from "stack_t1, n1 = build_stack" through the stats dictionary>

Constraints: reuse the existing functions unchanged. Add to the existing stats
dictionary rather than creating a second one. Keep the same export filenames plus
lulc_t3.png. Do not change the class scheme.
```

## P3 — U-Net upgrade (month 2, not day 1)

```
I have a Random Forest LULC baseline in Earth Engine achieving 0.84 overall accuracy
on 60 independently interpreted reference points, over a 38x33 km window in Tamil
Nadu, 6 classes: Water, Forest/Trees, Cropland, Built-up, Barren, Shrub/Grass.

Write a PyTorch training script that:
- reads 256x256 tiles from a Sentinel-2 GeoTIFF stack of 19 bands
- uses segmentation_models_pytorch Unet with a resnet34 encoder, in_channels=19
- uses weighted cross-entropy, with weights from inverse class frequency
- splits train and test by SPATIAL BLOCKS, not random pixels, because random
  splitting inflates accuracy through spatial autocorrelation
- reports overall accuracy, per-class F1 and mIoU on the held-out blocks
- trains on a single GPU with 8 GB VRAM

Constraints: one file, under 200 lines. No Lightning, no Hydra, no wandb.
Use argparse. Print a comparison line against the 0.84 RF baseline at the end.
```

## P4 — ESP32 firmware for one real node

```
Write Arduino firmware for an ESP32 that reads:
- capacitive soil moisture sensor v2.0 on GPIO 34 (analog)
- DHT22 temperature and humidity on GPIO 4
- tipping-bucket rain gauge on GPIO 27 (interrupt, 0.2 mm per tip)

It must POST every 15 minutes to:
  POST http://<HOST>:8000/api/ingest
  {"code":"PDR-01","soil_moisture":<float 0-60 volumetric %>,
   "rainfall_mm":<float>,"temperature_c":<float>,"humidity_pct":<float>}

Requirements: deep sleep between readings for battery operation, WiFi credentials
in a separate config header, buffer up to 96 readings in RTC memory when WiFi is
down and flush on reconnect, median of 5 samples for the analog read, and a
two-point calibration map from raw ADC to volumetric percent with the constants at
the top of the file.

Constraints: single .ino plus config.h. No MQTT, no TLS, no OTA. Comment the
calibration procedure in three lines.
```

Then, for the matching API endpoint:

```
Add POST /api/ingest to this FastAPI app. It accepts the JSON above, looks up the
sensor by `code`, rejects unknown codes with 404, inserts into sensor_reading, and
sets that sensor's is_simulated to FALSE on first real reading.

Here is the existing schema:
<paste the sensor and sensor_reading tables from db/schema.sql>

Here is an existing endpoint to match the style:
<paste the verify() function from api/main.py>

Constraints: match the existing psycopg style with the db() context manager. Do not
add authentication, an ORM, or a background task queue. Under 30 lines.
```

## P5 — Markov + cellular automata prediction

```
I have three LULC classified rasters for the same area at 2013, 2019 and 2026,
6 classes, 10 m pixels, as numpy arrays of the same shape. I also have distance-to-
road, distance-to-existing-built-up, slope and elevation arrays of the same shape.

Write a Python script that:
1. builds the Markov transition probability matrix from 2013 to 2019
2. trains a RandomForestClassifier to predict transition potential to built-up,
   using the four driver arrays, calibrated on the 2013 to 2019 transitions
3. allocates the Markov-predicted quantity of change to the highest-potential
   cells using a cellular automata rule with a 5x5 neighbourhood
4. predicts 2026 and validates against the OBSERVED 2026 map using Figure of Merit,
   and quantity disagreement and allocation disagreement as defined by
   Pontius and Millones

Constraints: numpy and scikit-learn only. One file. Explain in a comment why Figure
of Merit is reported rather than overall accuracy, which is misleading for change
prediction because most of the map does not change.
```

## P6 — Cloud-Optimized GeoTIFF tiles, replacing the PNG overlay

```
My prototype serves classified LULC rasters as PNG image overlays with four corner
coordinates in MapLibre. I want to replace that with proper tiling.

Give me:
1. the gdal_translate command to convert an exported GeoTIFF to a Cloud-Optimized
   GeoTIFF with the correct settings for a categorical uint8 raster with 6 classes
   (nearest-neighbour overviews, not average)
2. the TiTiler service block to add to this docker-compose.yml, reading from a
   mounted local ./data directory
3. the exact MapLibre raster source URL including a discrete colormap for the
   6 classes, matching these colours:
   0 #1f6feb, 1 #1a7f37, 2 #a4d65e, 3 #d1242f, 4 #bf8700, 5 #7d8590

Current compose file:
<paste docker-compose.yml>

Constraints: keep the db service unchanged. Do not introduce nginx, GeoServer or
a separate tile cache.
```

## P7 — Making the map production-shaped

```
This is my single-file MapLibre interface: <paste api/static/index.html>

Add ONLY these three things:
1. a true swipe comparison between the two epoch layers, with a draggable vertical
   divider, using a clip rectangle rather than opacity
2. a "download GeoJSON" button that fetches the current filtered /api/changes query
   with the active filters and saves it as a file
3. keyboard navigation through the alert list with the up and down arrow keys, which
   flies the map to each alert in turn

Constraints: no build step, no npm, no framework. Keep it a single HTML file with
inline JS. Do not restyle anything. Do not add new dependencies beyond the MapLibre
CDN already present.
```

## P8 — Swapping SQLite for MongoDB (only if you must)

You said MongoDB is available. SQLite is the better default here because it needs no
connection string, no driver and no running service, and because the schema maps
directly onto PostGIS later. If you still want Mongo, the change is confined to one
file:

```
This prototype stores everything in SQLite through a single helper module.
Rewrite ONLY api/db.py and the two scripts so the same data lives in MongoDB,
keeping every API response byte-identical.

Current helper: <paste api/db.py>
Current schema: <paste db/schema_sqlite.sql>
One consumer for style: <paste the changes() and change_context() functions from api/main.py>

Collections: change_event, alert, verification, sensor, sensor_reading,
lulc_stat, lulc_class, study_area.
Store geometry as embedded GeoJSON and add a 2dsphere index on change_event.geometry
and sensor.location, then use $near for the nearest-sensor query instead of the
Python haversine.

Constraints: pymongo only, no ODM, no Beanie, no Motor. Keep the connect() name and
the same call sites so api/main.py changes as little as possible. Connection string
from the MONGO_URL environment variable, defaulting to mongodb://localhost:27017.
```

What Mongo buys you: a real `$near` geospatial query. What it costs: a running
service, a driver, and a schema that no longer maps onto PostGIS. For a one-day
prototype that is a bad trade. Do it in month two, or better, go straight to PostGIS.

---

## Prompts that will waste your day

Do not use these. They are how one-day prototypes die.

- *"Build me a GeoAI platform for Tamil Nadu land use monitoring"* — you get 2,000
  lines of plausible scaffolding that does not run and cannot be debugged.
- *"Add authentication and user roles"* — half a day, zero demo value on localhost.
- *"Make it production ready"* — unbounded, and undefined.
- *"Use the latest model architecture"* — you have no labelled data. Architecture is
  not your bottleneck.
- *"Set up Kubernetes / Kafka / a microservice split"* — for one user on one laptop.
