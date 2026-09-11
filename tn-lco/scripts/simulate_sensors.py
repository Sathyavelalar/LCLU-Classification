"""
SIMULATED ground-sensor stream.

There is no hardware. This generates physically plausible readings so that the
seasonal false-positive filter can be demonstrated end to end. Every node is
stored with is_simulated = 1 and the web interface shows a SIMULATED badge.
Do not remove that badge.

Usage:
  python scripts/simulate_sensors.py --seed        # nodes + 2 years of history
  python scripts/simulate_sensors.py --stream      # append a reading every 5 s
"""
import argparse, math, os, random, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api.db import connect

# Four nodes inside the Perundurai-Erode window.
NODES = [
    ("PDR-01", "Perundurai agricultural plot", 77.588, 11.276),
    ("PDR-02", "Chennimalai foothill",         77.607, 11.163),
    ("ERD-01", "Erode peri-urban edge",        77.717, 11.341),
    ("BHV-01", "Bhavani river bank",           77.681, 11.447),
]


def rainfall_for(day_of_year):
    """Tamil Nadu bimodal rainfall: south-west (Jun-Sep), north-east (Oct-Dec)."""
    sw = 6.0 * math.exp(-((day_of_year - 200) ** 2) / 1800)
    ne = 14.0 * math.exp(-((day_of_year - 305) ** 2) / 1200)
    base = sw + ne
    return round(max(0.0, random.gauss(base, base * 0.9)), 1) if random.random() < 0.35 else 0.0


def synth(ts, soil_prev):
    doy = ts.timetuple().tm_yday
    rain = rainfall_for(doy)
    soil = soil_prev + rain * 1.6 - 0.9            # rises with rain, decays otherwise
    soil = max(6.0, min(52.0, soil + random.gauss(0, 0.6)))
    temp = (27 + 6 * math.sin((doy - 100) / 365 * 2 * math.pi)
            + 4 * math.sin((ts.hour - 9) / 24 * 2 * math.pi) + random.gauss(0, 0.8))
    hum = max(25.0, min(98.0, 55 + soil * 0.7 - (temp - 27) * 1.4 + random.gauss(0, 3)))
    return soil, rain, round(temp, 1), round(hum, 1)


def ensure_nodes(cur):
    for code, name, lon, lat in NODES:
        cur.execute("""INSERT OR IGNORE INTO sensor (code, name, lon, lat, is_simulated)
                       VALUES (?,?,?,?,1)""", (code, name, lon, lat))
    return [(r["id"], r["code"]) for r in cur.execute("SELECT id, code FROM sensor ORDER BY id")]


def seed(days=730):
    con = connect(); cur = con.cursor()
    nodes = ensure_nodes(cur)
    cur.execute("DELETE FROM sensor_reading")
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = 0
    for sid, _ in nodes:
        soil = 22.0
        for h in range(days * 24, 0, -6):          # one reading every 6 hours
            ts = now - timedelta(hours=h)
            soil, rain, temp, hum = synth(ts, soil)
            cur.execute("""INSERT OR IGNORE INTO sensor_reading
                           (sensor_id, ts, soil_moisture, rainfall_mm,
                            temperature_c, humidity_pct) VALUES (?,?,?,?,?,?)""",
                        (sid, ts.isoformat(), round(soil, 1), rain, temp, hum))
            rows += 1
    con.commit(); con.close()
    print(f"Seeded {rows} SIMULATED readings across {len(NODES)} nodes.")


def stream():
    print("Streaming SIMULATED readings. Ctrl-C to stop.")
    con = connect(); cur = con.cursor()
    nodes = ensure_nodes(cur); con.commit()
    state = {sid: 22.0 for sid, _ in nodes}
    try:
        while True:
            ts = datetime.now(timezone.utc)
            for sid, code in nodes:
                soil, rain, temp, hum = synth(ts, state[sid])
                state[sid] = soil
                cur.execute("""INSERT OR IGNORE INTO sensor_reading
                               (sensor_id, ts, soil_moisture, rainfall_mm,
                                temperature_c, humidity_pct) VALUES (?,?,?,?,?,?)""",
                            (sid, ts.isoformat(), round(soil, 1), rain, temp, hum))
                print(f"  {code} soil={soil:5.1f}%  rain={rain:4.1f}mm  T={temp:4.1f}C")
            con.commit()
            time.sleep(5)
    except KeyboardInterrupt:
        con.close(); print("\nstopped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--days", type=int, default=730)
    a = ap.parse_args()
    if a.seed:
        seed(a.days)
    elif a.stream:
        stream()
    else:
        ap.print_help()
