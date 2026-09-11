Put the Earth Engine outputs here:

  lulc_t1.png
  lulc_t2.png
  overlay_bounds.json
  changes.geojson
  stats.json
  validation_points.geojson

Then run:  python scripts/load_results.py

tnlco.db (the SQLite database) is also created in this folder by
scripts/init_db.py. Delete it to start clean.
