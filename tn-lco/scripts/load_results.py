"""
Load Earth Engine outputs from data/ into SQLite.

Usage:
    python scripts/load_results.py

Idempotent:
    clears the model-output tables and reloads.
"""

import json
import os
import sys

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from api.db import connect, centroid_of


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


CLASS_NAMES = {
    0: "Water",
    1: "Forest/Trees",
    2: "Cropland",
    3: "Built-up",
    4: "Barren",
    5: "Shrub/Grass"
}


PIXEL_KM2 = 100 / 1_000_000  # one 10 m pixel


def read(name):
    """
    Read a JSON/GeoJSON file from the data directory.
    """

    path = os.path.join(DATA, name)

    if not os.path.exists(path):
        sys.exit(
            f"MISSING: {path}\n"
            "Run the Earth Engine notebook first, "
            "then copy its six output files into data/"
        )

    with open(path) as f:
        return json.load(f)


def priority_and_reason(from_c, to_c, area_ha):
    """
    Rank changes for field verification.
    Tuned for PRECISION, not recall.
    """

    if to_c == 3 and from_c in (1, 2):
        return (
            1,
            f"Conversion of "
            f"{CLASS_NAMES[from_c].lower()} to built-up, "
            f"{area_ha:.2f} ha"
        )

    if to_c == 3:
        return (
            2,
            f"New built-up area, {area_ha:.2f} ha"
        )

    if from_c == 0:
        return (
            1,
            f"Loss of water body, {area_ha:.2f} ha"
        )

    if from_c == 1 and to_c in (2, 4, 5):
        return (
            2,
            f"Tree cover loss to "
            f"{CLASS_NAMES[to_c].lower()}, "
            f"{area_ha:.2f} ha"
        )

    return (
        3,
        f"{CLASS_NAMES[from_c]} to "
        f"{CLASS_NAMES[to_c]}, "
        f"{area_ha:.2f} ha"
    )


def main():

    # ------------------------------------------------------------
    # Read GEE outputs
    # ------------------------------------------------------------

    stats = read("stats.json")
    changes = read("changes.geojson")


    # ------------------------------------------------------------
    # Connect to SQLite
    # ------------------------------------------------------------

    con = connect()
    cur = con.cursor()


    # ------------------------------------------------------------
    # Clear previous model outputs
    # ------------------------------------------------------------

    for table in (
        "verification",
        "alert",
        "change_event",
        "lulc_stat",
        "study_area"
    ):
        cur.execute(f"DELETE FROM {table}")


    # ------------------------------------------------------------
    # Study area
    # ------------------------------------------------------------

    b = stats["aoi_bbox"]

    cur.execute(
        """
        INSERT INTO study_area
        (name, min_lon, min_lat, max_lon, max_lat)
        VALUES (?,?,?,?,?)
        """,
        (
            stats["aoi_name"],
            b[0],
            b[1],
            b[2],
            b[3]
        )
    )


    # ------------------------------------------------------------
    # LULC statistics
    # ------------------------------------------------------------

    for key, label in (
        ("areas_t1_pixels", stats["t1_label"]),
        ("areas_t2_pixels", stats["t2_label"])
    ):

        for cid, px in stats[key].items():

            cur.execute(
                """
                INSERT INTO lulc_stat
                (epoch_label, class_id, area_km2)
                VALUES (?,?,?)
                """,
                (
                    label,
                    int(float(cid)),
                    float(px) * PIXEL_KM2
                )
            )


    # ------------------------------------------------------------
    # Change polygons
    # ------------------------------------------------------------

    n_alerts = 0
    skipped = 0


    for feat in changes["features"]:

        # --------------------------------------------------------
        # Properties
        # --------------------------------------------------------

        props = feat.get("properties", {})

        trans = props.get("transition")


        # Missing transition
        if trans is None:
            skipped += 1
            continue


        trans = int(trans)

        from_c = trans // 10
        to_c = trans % 10


        # Invalid transition
        if (
            from_c not in CLASS_NAMES
            or to_c not in CLASS_NAMES
            or from_c == to_c
        ):
            skipped += 1
            continue


        # --------------------------------------------------------
        # Area
        # --------------------------------------------------------

        area_ha = float(
            props.get("area_ha") or 0
        )


        # --------------------------------------------------------
        # Geometry
        #
        # IMPORTANT:
        # Some exported GeoJSON features may have null geometry.
        # Skip those instead of crashing the entire loader.
        # --------------------------------------------------------

        geom = feat.get("geometry")

        if not geom:
            skipped += 1
            continue


        # --------------------------------------------------------
        # Calculate centroid
        # --------------------------------------------------------

        try:

            lon, lat = centroid_of(geom)

        except ValueError:

            skipped += 1
            continue


        # --------------------------------------------------------
        # Insert change event
        # --------------------------------------------------------

        cur.execute(
            """
            INSERT INTO change_event
            (
                geojson,
                centroid_lon,
                centroid_lat,
                from_class,
                to_class,
                area_ha,
                ndbi_delta,
                vv_delta,
                confidence
            )
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                json.dumps(geom),
                lon,
                lat,
                from_c,
                to_c,
                area_ha,
                props.get("ndbi_d"),
                props.get("vv_d"),
                "high" if area_ha >= 0.5 else "medium"
            )
        )


        # Get ID of inserted change event
        cid = cur.lastrowid


        # --------------------------------------------------------
        # Generate alert for sufficiently large changes
        # --------------------------------------------------------

        if area_ha >= 0.25:

            prio, reason = priority_and_reason(
                from_c,
                to_c,
                area_ha
            )

            cur.execute(
                """
                INSERT INTO alert
                (change_event_id, priority, reason)
                VALUES (?,?,?)
                """,
                (
                    cid,
                    prio,
                    reason
                )
            )

            n_alerts += 1


    # ------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------

    con.commit()


    # ------------------------------------------------------------
    # Final counts
    # ------------------------------------------------------------

    n = cur.execute(
        "SELECT count(*) FROM change_event"
    ).fetchone()[0]


    con.close()


    # ------------------------------------------------------------
    # Output
    # ------------------------------------------------------------

    print(
        f"Loaded {n} change polygons "
        f"and {n_alerts} alerts "
        f"({skipped} features skipped)."
    )

    print(
        f"Raw changed pixels : "
        f"{stats.get('change_pixels_raw')}"
    )

    print(
        f"Kept after filters : "
        f"{stats.get('change_pixels_kept')}"
    )


if __name__ == "__main__":
    main()