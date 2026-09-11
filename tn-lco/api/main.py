"""
TN-LCO prototype API. SQLite only - no server to install, no Docker.
Run:  uvicorn api.main:app --reload --port 8000
Then: http://localhost:8000
"""
import json, os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.db import connect, rows, haversine_km, DB_PATH

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")

app = FastAPI(title="TN-LCO Prototype API", version="0.2.0",
              description="District land-change observatory - research prototype")


def fc(records, geom_key="geojson"):
    """Records carrying a geometry -> a GeoJSON FeatureCollection."""
    feats = []
    for r in records:
        geom = r.pop(geom_key)
        if isinstance(geom, str):
            geom = json.loads(geom)
        feats.append({"type": "Feature", "geometry": geom, "properties": r})
    return {"type": "FeatureCollection", "features": feats}


def point(lon, lat):
    return {"type": "Point", "coordinates": [lon, lat]}


_WINDOW = None


def epoch_window():
    """
    The sensor context window. It is the date range of the SECOND satellite
    epoch, not 'the last 60 days'. Ground conditions matter at the moment the
    image was taken, not at the moment somebody opens the dashboard.
    """
    global _WINDOW
    if _WINDOW is None:
        start, end, label = "2026-01-01", "2026-04-30", "2026 Jan-Apr"
        p = os.path.join(DATA, "stats.json")
        if os.path.exists(p):
            with open(p) as fh:
                raw = json.load(fh)
            start = raw.get("t2_start", start)
            end = raw.get("t2_end", end)
            label = raw.get("t2_label", label)
        _WINDOW = {"start": start, "end": end, "label": label}
    return _WINDOW


# ---------------------------------------------------------------- meta ------
@app.get("/api/health")
def health():
    if not os.path.exists(DB_PATH):
        raise HTTPException(503, "database file missing - run python scripts/init_db.py")
    con = connect()
    try:
        n = con.execute("SELECT count(*) FROM change_event").fetchone()[0]
        s = con.execute("SELECT count(*) FROM sensor_reading").fetchone()[0]
    finally:
        con.close()
    return {"status": "ok", "database": DB_PATH, "change_events": n, "sensor_readings": s}


@app.get("/api/overlays")
def overlays():
    p = os.path.join(DATA, "overlay_bounds.json")
    if not os.path.exists(p):
        raise HTTPException(404, "overlay_bounds.json missing - run the GEE notebook")
    with open(p) as f:
        return json.load(f)


@app.get("/api/stats")
def stats():
    out = {"areas": {}, "agreement": None, "filter": None}
    con = connect()
    try:
        for r in rows(con.execute("""
                SELECT s.epoch_label, s.class_id, l.name, l.color,
                       round(s.area_km2, 3) AS area_km2
                FROM lulc_stat s JOIN lulc_class l ON l.class_id = s.class_id
                ORDER BY s.epoch_label, s.class_id""")):
            out["areas"].setdefault(r["epoch_label"], []).append(
                {"class_id": r["class_id"], "name": r["name"],
                 "color": r["color"], "area_km2": r["area_km2"]})
    finally:
        con.close()

    p = os.path.join(DATA, "stats.json")
    if os.path.exists(p):
        with open(p) as f:
            raw = json.load(f)
        out["agreement"] = raw.get("agreement_t2")
        out["filter"] = {"raw_pixels": raw.get("change_pixels_raw"),
                         "kept_pixels": raw.get("change_pixels_kept"),
                         "scenes_t1": raw.get("scenes_t1"),
                         "scenes_t2": raw.get("scenes_t2")}
    return out


@app.get("/api/summary")
def summary():
    con = connect()
    try:
        return rows(con.execute("SELECT * FROM v_change_summary"))
    finally:
        con.close()


# ------------------------------------------------------------- changes ------
@app.get("/api/changes")
def changes(min_area_ha: float = Query(0.1, ge=0),
            to_class: Optional[int] = None,
            limit: int = Query(1000, le=5000)):
    sql = """SELECT c.id, c.from_class, c.to_class,
                    f.name AS from_name, t.name AS to_name, t.color AS to_color,
                    round(c.area_ha,3) AS area_ha,
                    round(c.ndbi_delta,3) AS ndbi_delta,
                    round(c.vv_delta,3)   AS vv_delta,
                    c.confidence, c.geojson,
                    COALESCE((SELECT v.verdict FROM verification v
                              WHERE v.change_event_id = c.id
                              ORDER BY v.observed_at DESC LIMIT 1), 'unverified') AS verdict
             FROM change_event c
             JOIN lulc_class f ON f.class_id = c.from_class
             JOIN lulc_class t ON t.class_id = c.to_class
             WHERE c.area_ha >= ?"""
    params = [min_area_ha]
    if to_class is not None:
        sql += " AND c.to_class = ?"
        params.append(to_class)
    sql += " ORDER BY c.area_ha DESC LIMIT ?"
    params.append(limit)
    con = connect()
    try:
        return JSONResponse(fc(rows(con.execute(sql, params))))
    finally:
        con.close()


@app.get("/api/changes/{change_id}/context")
def change_context(change_id: int):
    """
    The AIoT step. Find the nearest ground sensor, read the rainfall and soil
    moisture around it, and judge whether the change is likely seasonal.

    Rule: a vegetation loss that coincides with a dry spell is probably a crop
    cycle, not a land-use change.
    """
    win = epoch_window()
    con = connect()
    try:
        ev = con.execute("""SELECT id, from_class, to_class, area_ha,
                                   centroid_lon, centroid_lat
                            FROM change_event WHERE id = ?""", (change_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "change event not found")

        nodes = rows(con.execute("SELECT id, code, name, lon, lat, is_simulated FROM sensor"))
        if not nodes:
            return {"change_id": change_id, "sensor": None,
                    "assessment": "no ground sensor available",
                    "explanation": "Seed the simulated nodes first."}

        for n in nodes:
            n["dist_km"] = haversine_km(ev["centroid_lon"], ev["centroid_lat"],
                                        n["lon"], n["lat"])
        node = min(nodes, key=lambda n: n["dist_km"])

        met = con.execute("""SELECT COALESCE(sum(rainfall_mm),0)   AS rain,
                                    COALESCE(avg(soil_moisture),0) AS soil,
                                    count(*)                       AS n
                             FROM sensor_reading
                             WHERE sensor_id = ? AND ts >= ? AND ts <= ?""",
                          (node["id"], win["start"], win["end"] + "T23:59:59")).fetchone()
    finally:
        con.close()

    if not met["n"]:
        return {"change_id": change_id,
                "sensor": {"code": node["code"], "name": node["name"],
                           "is_simulated": bool(node["is_simulated"])},
                "sensor_distance_km": round(node["dist_km"], 1),
                "window": win, "assessment": "no_ground_data",
                "explanation": (f"No sensor readings inside the image window "
                                f"{win['start']} to {win['end']}. Seed a longer history: "
                                f"python scripts/simulate_sensors.py --seed --days 730"),
                "data_note": "sensor data is SIMULATED in this prototype"}

    rain, soil = float(met["rain"]), float(met["soil"])
    veg_loss = ev["from_class"] in (1, 2, 5) and ev["to_class"] in (4, 5)

    if veg_loss and rain < 40 and soil < 18:
        verdict, note = "likely_seasonal", (
            f"Vegetation loss coincides with a dry spell ({rain:.0f} mm rain, "
            f"{soil:.0f}% mean soil moisture across the {win['label']} image window). "
            "Downgrade: probably a crop cycle, not a land-use change.")
    elif ev["to_class"] == 3:
        verdict, note = "likely_permanent", (
            "Conversion to built-up. Built surfaces do not revert with season. "
            "Ground moisture context does not weaken this detection.")
    else:
        verdict, note = "inconclusive", (
            f"{rain:.0f} mm rain and {soil:.0f}% mean soil moisture across the "
            f"{win['label']} image window do not clearly explain this change. "
            "Send for field verification.")

    return {"change_id": change_id,
            "sensor": {"code": node["code"], "name": node["name"],
                       "is_simulated": bool(node["is_simulated"])},
            "sensor_distance_km": round(node["dist_km"], 1),
            "window": win,
            "rain_mm": round(rain, 1),
            "mean_soil_moisture_pct": round(soil, 1),
            "assessment": verdict, "explanation": note,
            "data_note": "sensor data is SIMULATED in this prototype"}


# -------------------------------------------------------------- alerts ------
class Verdict(BaseModel):
    verdict: str                    # confirmed | rejected | unclear
    note: Optional[str] = None
    observer: Optional[str] = "field-demo"


@app.get("/api/alerts")
def alerts(status: Optional[str] = None):
    sql = """SELECT a.id, a.priority, a.reason, a.status, a.created_at,
                    c.id AS change_id, round(c.area_ha,2) AS area_ha,
                    c.centroid_lon, c.centroid_lat,
                    f.name AS from_name, t.name AS to_name
             FROM alert a
             JOIN change_event c ON c.id = a.change_event_id
             JOIN lulc_class f ON f.class_id = c.from_class
             JOIN lulc_class t ON t.class_id = c.to_class"""
    params = []
    if status:
        sql += " WHERE a.status = ?"
        params.append(status)
    sql += " ORDER BY a.priority ASC, c.area_ha DESC LIMIT 200"
    con = connect()
    try:
        recs = rows(con.execute(sql, params))
    finally:
        con.close()
    for r in recs:
        r["geojson"] = point(r.pop("centroid_lon"), r.pop("centroid_lat"))
    return JSONResponse(fc(recs))


@app.post("/api/changes/{change_id}/verify")
def verify(change_id: int, body: Verdict):
    if body.verdict not in ("confirmed", "rejected", "unclear"):
        raise HTTPException(400, "verdict must be confirmed, rejected or unclear")
    con = connect()
    try:
        if not con.execute("SELECT 1 FROM change_event WHERE id = ?", (change_id,)).fetchone():
            raise HTTPException(404, "change event not found")
        con.execute("""INSERT INTO verification (change_event_id, verdict, note, observer)
                       VALUES (?,?,?,?)""",
                    (change_id, body.verdict, body.note, body.observer))
        new_status = {"confirmed": "confirmed", "rejected": "rejected",
                      "unclear": "open"}[body.verdict]
        con.execute("UPDATE alert SET status = ? WHERE change_event_id = ?",
                    (new_status, change_id))
        con.commit()
    finally:
        con.close()
    return {"change_id": change_id, "verdict": body.verdict, "status": "recorded"}


@app.get("/api/verification/scorecard")
def scorecard():
    """Precision of the alerting system, over alerts you have verified."""
    con = connect()
    try:
        recs = rows(con.execute("""
            SELECT verdict, count(*) AS n FROM (
              SELECT change_event_id, verdict,
                     row_number() OVER (PARTITION BY change_event_id
                                        ORDER BY observed_at DESC, id DESC) AS rn
              FROM verification) x
            WHERE rn = 1 GROUP BY verdict"""))
    finally:
        con.close()
    counts = {r["verdict"]: r["n"] for r in recs}
    conf, rej = counts.get("confirmed", 0), counts.get("rejected", 0)
    total = conf + rej
    return {"confirmed": conf, "rejected": rej, "unclear": counts.get("unclear", 0),
            "precision": round(conf / total, 3) if total else None,
            "note": "precision = confirmed / (confirmed + rejected), verified alerts only"}


# ------------------------------------------------------------- sensors ------
@app.get("/api/sensors")
def sensors():
    con = connect()
    try:
        recs = rows(con.execute("""
            SELECT s.id, s.code, s.name, s.is_simulated, s.lon, s.lat,
                   (SELECT round(r.soil_moisture,1) FROM sensor_reading r
                     WHERE r.sensor_id = s.id ORDER BY r.ts DESC LIMIT 1) AS soil_moisture,
                   (SELECT round(r.temperature_c,1) FROM sensor_reading r
                     WHERE r.sensor_id = s.id ORDER BY r.ts DESC LIMIT 1) AS temperature_c,
                   (SELECT max(r.ts) FROM sensor_reading r
                     WHERE r.sensor_id = s.id) AS last_seen
            FROM sensor s ORDER BY s.id"""))
    finally:
        con.close()
    for r in recs:
        r["is_simulated"] = bool(r["is_simulated"])
        r["geojson"] = point(r.pop("lon"), r.pop("lat"))
    return JSONResponse(fc(recs))


@app.get("/api/sensors/{sensor_id}/readings")
def readings(sensor_id: int, days: int = Query(60, ge=1, le=365)):
    con = connect()
    try:
        recs = rows(con.execute("""
            SELECT ts, soil_moisture, rainfall_mm, temperature_c, humidity_pct
            FROM sensor_reading
            WHERE sensor_id = ? AND ts > datetime('now', ?)
            ORDER BY ts""", (sensor_id, f"-{days} days")))
    finally:
        con.close()
    return {"sensor_id": sensor_id, "n": len(recs), "simulated": True, "readings": recs}


# -------------------------------------------------------------- report ------
@app.get("/api/report", response_class=HTMLResponse)
def report():
    s = stats()
    con = connect()
    try:
        summ = rows(con.execute("SELECT * FROM v_change_summary LIMIT 12"))
        area_row = con.execute("SELECT name FROM study_area LIMIT 1").fetchone()
    finally:
        con.close()
    area = area_row["name"] if area_row else "study area"
    epochs = list(s["areas"].keys())
    trs = "".join(
        f"<tr><td>{r['from_class']}</td><td>{r['to_class']}</td>"
        f"<td style='text-align:right'>{r['n_polygons']}</td>"
        f"<td style='text-align:right'>{r['total_ha']}</td></tr>" for r in summ)
    agree = (s.get("agreement") or {}).get("overall")
    f = s.get("filter") or {}
    return f"""<!doctype html><meta charset=utf-8>
<title>TN-LCO district report</title>
<style>body{{font:14px/1.6 system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 20px}}
table{{border-collapse:collapse;width:100%;margin:14px 0}}
td,th{{border:1px solid #ddd;padding:6px 10px}} th{{background:#f6f8fa;text-align:left}}
.warn{{background:#fff8c5;border-left:4px solid #d4a72c;padding:10px 14px;margin:18px 0}}</style>
<h1>Land-change report - {area}</h1>
<p>Epochs compared: <b>{' vs '.join(epochs) if epochs else 'not loaded'}</b>.
Sentinel-2 scenes used: {f.get('scenes_t1','?')} and {f.get('scenes_t2','?')}.</p>
<h2>Detected transitions</h2>
<table><tr><th>From</th><th>To</th><th>Polygons</th><th>Total ha</th></tr>{trs}</table>
<h2>Filter effect</h2>
<p>Raw changed pixels: <b>{f.get('raw_pixels','?')}</b>.
Retained after minimum-mapping-unit, spectral corroboration, cross-season
persistence and data-quality filters: <b>{f.get('kept_pixels','?')}</b>.</p>
<h2>Method limits</h2>
<div class=warn>
<p><b>This is a research prototype. Read before using any number above.</b></p>
<ul>
<li>Training labels come from Google Dynamic World, a model output, not field survey.
The reported agreement ({agree if agree else 'n/a'}) measures agreement with that
product, not correctness on the ground.</li>
<li>Ground-sensor data in this prototype is <b>simulated</b>. No hardware is deployed.</li>
<li>A detected change means the land cover changed. It does <b>not</b> mean the change
was unauthorised. That needs cadastral and approval records which this prototype
does not have.</li>
<li>Minimum reliable object size is about 0.1 ha. Individual buildings are below the
resolution limit of 10 m imagery.</li>
</ul></div>
"""


# ---------------------------------------------------------------- static ----
os.makedirs(DATA, exist_ok=True)
app.mount("/data", StaticFiles(directory=DATA), name="data")
app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
