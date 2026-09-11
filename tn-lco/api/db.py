"""Shared SQLite helpers. No server, no ORM, no connection pool."""
import math, os, sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("TNLCO_DB", os.path.join(ROOT, "data", "tnlco.db"))


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")   # concurrent read while simulator writes
    con.execute("PRAGMA foreign_keys=ON")
    return con


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def haversine_km(lon1, lat1, lon2, lat2):
    """Great-circle distance. Replaces PostGIS ST_Distance for 4 sensor nodes."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def centroid_of(geom):
    """Mean of the exterior ring vertices. Adequate for sub-hectare polygons."""
    t = geom.get("type")
    if t == "Polygon":
        ring = geom["coordinates"][0]
    elif t == "MultiPolygon":
        ring = geom["coordinates"][0][0]
    else:
        raise ValueError(f"unsupported geometry type: {t}")
    n = len(ring)
    return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
