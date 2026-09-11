"""
Create the SQLite database. Run once:  python scripts/init_db.py
Nothing is installed. sqlite3 ships with Python.
"""
import os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.environ.get("TNLCO_DB", os.path.join(ROOT, "data", "tnlco.db"))
SCHEMA = os.path.join(ROOT, "db", "schema_sqlite.sql")

CLASSES = [(0, "Water", "#1f6feb"), (1, "Forest/Trees", "#1a7f37"),
           (2, "Cropland", "#a4d65e"), (3, "Built-up", "#d1242f"),
           (4, "Barren", "#bf8700"), (5, "Shrub/Grass", "#7d8590")]


def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if not os.path.exists(SCHEMA):
        sys.exit(f"missing {SCHEMA}")
    con = sqlite3.connect(DB)
    con.executescript(open(SCHEMA).read())
    con.executemany("INSERT OR IGNORE INTO lulc_class VALUES (?,?,?)", CLASSES)
    con.commit()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
    con.close()
    print(f"Database ready: {DB}")
    print("Objects:", ", ".join(tables))


if __name__ == "__main__":
    main()
