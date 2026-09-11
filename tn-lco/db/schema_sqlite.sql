-- TN-LCO prototype schema (SQLite).
-- Applied automatically by scripts/init_db.py. No server, no install.
-- Geometry is stored as GeoJSON text. Centroids are stored as plain columns so
-- that every query the prototype needs is ordinary SQL.

PRAGMA journal_mode = WAL;      -- lets the sensor simulator write while the API reads
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS lulc_class (
    class_id INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    color    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_area (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    min_lon  REAL, min_lat REAL, max_lon REAL, max_lat REAL
);

CREATE TABLE IF NOT EXISTS lulc_stat (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_label TEXT NOT NULL,
    class_id    INTEGER REFERENCES lulc_class(class_id),
    area_km2    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS change_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    geojson      TEXT NOT NULL,          -- GeoJSON geometry, EPSG:4326
    centroid_lon REAL NOT NULL,
    centroid_lat REAL NOT NULL,
    from_class   INTEGER REFERENCES lulc_class(class_id),
    to_class     INTEGER REFERENCES lulc_class(class_id),
    area_ha      REAL NOT NULL,
    ndbi_delta   REAL,
    vv_delta     REAL,
    confidence   TEXT DEFAULT 'medium',
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_change_to   ON change_event (to_class);
CREATE INDEX IF NOT EXISTS ix_change_area ON change_event (area_ha DESC);

CREATE TABLE IF NOT EXISTS alert (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    change_event_id INTEGER REFERENCES change_event(id) ON DELETE CASCADE,
    priority        INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    status          TEXT DEFAULT 'open',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS verification (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    change_event_id INTEGER REFERENCES change_event(id) ON DELETE CASCADE,
    verdict         TEXT NOT NULL,
    note            TEXT,
    observed_at     TEXT DEFAULT (datetime('now')),
    observer        TEXT DEFAULT 'field-demo'
);

-- is_simulated MUST stay 1 until real hardware is deployed.
CREATE TABLE IF NOT EXISTS sensor (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    lon          REAL NOT NULL,
    lat          REAL NOT NULL,
    is_simulated INTEGER NOT NULL DEFAULT 1,
    installed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sensor_reading (
    sensor_id     INTEGER REFERENCES sensor(id) ON DELETE CASCADE,
    ts            TEXT NOT NULL,          -- ISO-8601 UTC
    soil_moisture REAL,
    rainfall_mm   REAL,
    temperature_c REAL,
    humidity_pct  REAL,
    PRIMARY KEY (sensor_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_reading_ts ON sensor_reading (ts DESC);

CREATE VIEW IF NOT EXISTS v_change_summary AS
SELECT f.name AS from_class, t.name AS to_class,
       count(*) AS n_polygons, round(sum(c.area_ha), 2) AS total_ha
FROM change_event c
JOIN lulc_class f ON f.class_id = c.from_class
JOIN lulc_class t ON t.class_id = c.to_class
GROUP BY f.name, t.name
ORDER BY sum(c.area_ha) DESC;
