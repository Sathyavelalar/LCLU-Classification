"""
TN-LCO Prototype - Earth Engine pipeline

Run this in Google Colab. It does the heavy geospatial work server-side.

Outputs:

    lulc_t1.png
    lulc_t2.png
    overlay_bounds.json
    changes.geojson
    stats.json
    validation_points.geojson

Study window:
    Perundurai-Erode, Tamil Nadu

Epochs:
    2019 Jan-Apr
    2026 Jan-Apr

Important:
    This detects land-cover change, NOT illegal/unauthorised construction.
    Training labels come from Google Dynamic World and are not field ground truth.
    Ground sensor data in the local application is simulated.
"""

# ============================================================================
# SETUP - Colab cell 1
# ============================================================================

# !pip install -q earthengine-api geemap

# import ee
# ee.Authenticate()
# ee.Initialize(project='YOUR-GEE-PROJECT-ID')

import ee
import json
import os
import requests


# ============================================================================
# OUTPUT DIRECTORY
# ============================================================================

OUT = "/content/out"
os.makedirs(OUT, exist_ok=True)


# ============================================================================
# CONFIG
# ============================================================================

AOI_BBOX = [
    77.45,
    11.15,
    77.80,
    11.45
]

AOI_NAME = "Perundurai-Erode window"

# Baseline epoch
T1_START = "2019-01-01"
T1_END = "2019-04-30"

# Current epoch
T2_START = "2026-01-01"
T2_END = "2026-04-30"

T1_LABEL = "2019 Jan-Apr"
T2_LABEL = "2026 Jan-Apr"


# Independent second season.
# Used only to test whether a detected change persists.
T2_CHECK_START = "2025-07-01"
T2_CHECK_END = "2025-10-31"


# Random Forest / sampling
SAMPLES_PER_CLASS = 400
RF_TREES = 100


# Minimum mapping unit:
# 10 connected 10m pixels = 1000 m2 = 0.1 ha
MIN_MAPPING_UNIT_PIXELS = 10


# PNG thumbnail size
THUMB_PIXELS = 3072


# ============================================================================
# CLASS DEFINITIONS
# ============================================================================

CLASSES = {
    0: ("Water", "#1f6feb"),
    1: ("Forest/Trees", "#1a7f37"),
    2: ("Cropland", "#a4d65e"),
    3: ("Built-up", "#d1242f"),
    4: ("Barren", "#bf8700"),
    5: ("Shrub/Grass", "#7d8590"),
}

PALETTE = [
    CLASSES[i][1]
    for i in range(6)
]


# ============================================================================
# AOI
# ============================================================================

aoi = ee.Geometry.Rectangle(AOI_BBOX)


# ============================================================================
# 1. CLOUD-FREE SENTINEL-2 COMPOSITE
# ============================================================================

def mask_s2(img):
    """
    Remove cloud, cloud shadow, cirrus and snow
    using the Sentinel-2 Scene Classification Layer.
    """

    scl = img.select("SCL")

    bad = (
        scl.eq(3)       # cloud shadow
        .Or(scl.eq(8))  # cloud, medium probability
        .Or(scl.eq(9))  # cloud, high probability
        .Or(scl.eq(10)) # thin cirrus
        .Or(scl.eq(11)) # snow
    )

    return (
        img
        .updateMask(bad.Not())
        .divide(10000)
        .copyProperties(
            img,
            ["system:time_start"]
        )
    )


S2_BANDS = [
    "B2",
    "B3",
    "B4",
    "B8",
    "B11",
    "B12"
]


def s2_composite(start, end):

    col = (
        ee.ImageCollection(
            "COPERNICUS/S2_SR_HARMONIZED"
        )
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(
            ee.Filter.lt(
                "CLOUDY_PIXEL_PERCENTAGE",
                60
            )
        )
        .map(mask_s2)
    )

    n = col.size()

    comp = (
        col
        .select(S2_BANDS)
        .median()
        .clip(aoi)
    )

    # Number of clear observations per pixel
    clear = (
        col
        .select("B8")
        .count()
        .rename("clear_obs")
        .clip(aoi)
    )

    return (
        comp.addBands(clear),
        n
    )


# ============================================================================
# 1B. SPECTRAL INDICES
# ============================================================================

def add_indices(img):

    b = lambda x: img.select(x)

    ndvi = (
        img
        .normalizedDifference(
            ["B8", "B4"]
        )
        .rename("NDVI")
    )

    mndwi = (
        img
        .normalizedDifference(
            ["B3", "B11"]
        )
        .rename("MNDWI")
    )

    ndbi = (
        img
        .normalizedDifference(
            ["B11", "B8"]
        )
        .rename("NDBI")
    )

    ndmi = (
        img
        .normalizedDifference(
            ["B8", "B11"]
        )
        .rename("NDMI")
    )

    bsi = (
        b("B11")
        .add(b("B4"))
        .subtract(
            b("B8").add(b("B2"))
        )
        .divide(
            b("B11")
            .add(b("B4"))
            .add(b("B8"))
            .add(b("B2"))
        )
        .rename("BSI")
    )

    return img.addBands([
        ndvi,
        mndwi,
        ndbi,
        ndmi,
        bsi
    ])


# ============================================================================
# 2. SENTINEL-1 RADAR COMPOSITE
# ============================================================================

def s1_composite(start, end):

    col = (
        ee.ImageCollection(
            "COPERNICUS/S1_GRD"
        )
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(
            ee.Filter.eq(
                "instrumentMode",
                "IW"
            )
        )
        .filter(
            ee.Filter.eq(
                "orbitProperties_pass",
                "DESCENDING"
            )
        )
        .filter(
            ee.Filter.listContains(
                "transmitterReceiverPolarisation",
                "VV"
            )
        )
        .filter(
            ee.Filter.listContains(
                "transmitterReceiverPolarisation",
                "VH"
            )
        )
    )

    return (
        col
        .select(["VV", "VH"])
        .median()
        .focalMedian(
            30,
            "circle",
            "meters"
        )
        .clip(aoi)
    )


# ============================================================================
# 3. TERRAIN
# ============================================================================

dem = (
    ee.Image(
        "USGS/SRTMGL1_003"
    )
    .clip(aoi)
)

terrain = (
    dem
    .rename("elevation")
    .addBands(
        ee.Terrain
        .slope(dem)
        .rename("slope")
    )
)


# ============================================================================
# 4. FEATURE STACK
# ============================================================================

def build_stack(start, end):

    opt, n_scenes = s2_composite(
        start,
        end
    )

    opt = add_indices(opt)

    sar = s1_composite(
        start,
        end
    )

    stack = (
        opt
        .addBands(sar)
        .addBands(terrain)
    )

    return stack, n_scenes


stack_t1, n1 = build_stack(
    T1_START,
    T1_END
)

stack_t2, n2 = build_stack(
    T2_START,
    T2_END
)

stack_check, n_check = build_stack(
    T2_CHECK_START,
    T2_CHECK_END
)


PREDICTORS = (
    S2_BANDS
    + [
        "NDVI",
        "MNDWI",
        "NDBI",
        "NDMI",
        "BSI",
        "VV",
        "VH",
        "elevation",
        "slope"
    ]
)


print(
    "Scenes used:",
    {
        "t1": n1.getInfo(),
        "t2": n2.getInfo(),
        "check": n_check.getInfo()
    }
)


# ============================================================================
# 5. WEAK LABELS FROM GOOGLE DYNAMIC WORLD
# ============================================================================
#
# IMPORTANT:
# These labels are model-generated.
# They are NOT field ground truth.
#
# Dynamic World classes:
#
# 0 water
# 1 trees
# 2 grass
# 3 flooded vegetation
# 4 crops
# 5 shrub/scrub
# 6 built
# 7 bare
#
# Mapping to our six classes:
#
# DW -> TN-LCO
#
# water             -> Water
# trees             -> Forest/Trees
# grass             -> Shrub/Grass
# flooded vegetation-> Cropland
# crops             -> Cropland
# shrub/scrub       -> Shrub/Grass
# built             -> Built-up
# bare              -> Barren
# ============================================================================

DW_FROM = [
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7
]

DW_TO = [
    0,
    1,
    5,
    2,
    2,
    5,
    3,
    4
]


def dw_label(start, end):

    return (
        ee.ImageCollection(
            "GOOGLE/DYNAMICWORLD/V1"
        )
        .filterBounds(aoi)
        .filterDate(start, end)
        .select("label")
        .mode()
        .remap(
            DW_FROM,
            DW_TO
        )
        .rename("class")
        .clip(aoi)
    )


label_t1 = dw_label(
    T1_START,
    T1_END
)

label_t2 = dw_label(
    T2_START,
    T2_END
)


# ============================================================================
# 6. TRAIN AND VALIDATE RANDOM FOREST
# ============================================================================

def train_and_classify(
    stack,
    label,
    seed
):

    sample = (
        stack
        .select(PREDICTORS)
        .addBands(label)
        .stratifiedSample(
            numPoints=SAMPLES_PER_CLASS,
            classBand="class",
            region=aoi,
            scale=10,
            seed=seed,
            geometries=False,
            tileScale=4
        )
        .randomColumn(
            "r",
            seed
        )
    )

    train = sample.filter(
        ee.Filter.lt(
            "r",
            0.7
        )
    )

    test = sample.filter(
        ee.Filter.gte(
            "r",
            0.7
        )
    )

    clf = (
        ee.Classifier
        .smileRandomForest(
            RF_TREES
        )
        .train(
            features=train,
            classProperty="class",
            inputProperties=PREDICTORS
        )
    )

    cm = (
        test
        .classify(clf)
        .errorMatrix(
            "class",
            "classification"
        )
    )

    classified = (
        stack
        .select(PREDICTORS)
        .classify(clf)
        .rename("class")
        .clip(aoi)
    )

    return classified, cm


cls_t1, cm_t1 = train_and_classify(
    stack_t1,
    label_t1,
    42
)

cls_t2, cm_t2 = train_and_classify(
    stack_t2,
    label_t2,
    43
)

cls_check, cm_check = train_and_classify(
    stack_check,
    dw_label(
        T2_CHECK_START,
        T2_CHECK_END
    ),
    44
)


# ============================================================================
# 7. CHANGE DETECTION
# ============================================================================
#
# Post-classification comparison:
#
# 2019 classification
#        vs
# 2026 classification
#
# ============================================================================

changed_raw = cls_t1.neq(
    cls_t2
)


# ============================================================================
# FILTER A - MINIMUM MAPPING UNIT
# ============================================================================
#
# Require at least 10 connected 10m pixels.
#
# 10 pixels x 100m2 = 1000m2 = 0.1 ha
# ============================================================================

big_enough = (
    changed_raw
    .connectedPixelCount(
        50,
        True
    )
    .gte(
        MIN_MAPPING_UNIT_PIXELS
    )
)


# ============================================================================
# FILTER B - SPECTRAL CORROBORATION
# ============================================================================
#
# For changes INTO built-up:
#
# NDBI delta > 0.05
# AND
# VV delta > 0.5 dB
#
# Other transitions are not subjected to this built-up-specific test.
# ============================================================================

ndbi_delta = (
    stack_t2
    .select("NDBI")
    .subtract(
        stack_t1.select("NDBI")
    )
)

vv_delta = (
    stack_t2
    .select("VV")
    .subtract(
        stack_t1.select("VV")
    )
)

to_built = cls_t2.eq(3)

spectral_ok = (
    to_built.Not()
    .Or(
        ndbi_delta
        .gt(0.05)
        .And(
            vv_delta.gt(0.5)
        )
    )
)


# ============================================================================
# FILTER C - CROSS-SEASON PERSISTENCE
# ============================================================================
#
# A genuine land-cover change should remain present
# in a different season.
#
# This is intended to suppress crop-cycle / seasonal false alarms.
# ============================================================================

persistent = (
    cls_check.eq(
        cls_t2
    )
)


# ============================================================================
# FILTER D - DATA QUALITY
# ============================================================================
#
# Both epochs must have at least five clear Sentinel-2 observations.
# ============================================================================

quality_ok = (
    stack_t1
    .select("clear_obs")
    .gte(5)
    .And(
        stack_t2
        .select("clear_obs")
        .gte(5)
    )
)


# ============================================================================
# FINAL CHANGE MASK
# ============================================================================

changed_final = (
    changed_raw
    .And(big_enough)
    .And(spectral_ok)
    .And(persistent)
    .And(quality_ok)
    .selfMask()
)


# ============================================================================
# TRANSITION ENCODING
# ============================================================================
#
# transition = FROM * 10 + TO
#
# Examples:
#
# 10 = Forest/Trees -> Water
# 12 = Forest/Trees -> Cropland
# 13 = Forest/Trees -> Built-up
# 20 = Cropland -> Water
# 23 = Cropland -> Built-up
# 34 = Built-up -> Barren
# 52 = Shrub/Grass -> Cropland
#
# ============================================================================

transition = (
    cls_t1
    .multiply(10)
    .add(cls_t2)
    .updateMask(changed_final)
    .rename("transition")
)


# ============================================================================
# 8. VECTORISE CHANGE POLYGONS
# ============================================================================
#
# IMPORTANT:
# The original 10m vectorisation could become too large for Earth Engine
# interactive/export limits.
#
# Working version:
#
#   scale = 20m
#   minimum area = 0.1 ha
#   maximum = 300 features
#   geometry simplified by 50m
#
# ============================================================================

print(
    "Vectorising change polygons..."
)

VECTOR_SCALE = 20


vectors = (
    transition
    .addBands(
        ndbi_delta.rename("ndbi_d")
    )
    .addBands(
        vv_delta.rename("vv_d")
    )
    .reduceToVectors(
        geometry=aoi,
        scale=VECTOR_SCALE,
        geometryType="polygon",
        labelProperty="transition",
        reducer=(
            ee.Reducer
            .mean()
            .forEach(
                ["ndbi_d", "vv_d"]
            )
        ),
        bestEffort=True,
        maxPixels=1e8,
        tileScale=8
    )
)

print(
    "Vectorisation graph created."
)


# ============================================================================
# AREA CALCULATION
# ============================================================================

def area_ha(f):

    return f.set(
        "area_ha",
        f.geometry()
        .area(10)
        .divide(10000)
    )


vectors = (
    vectors
    .map(area_ha)
    .filter(
        ee.Filter.gte(
            "area_ha",
            0.1
        )
    )
    .sort(
        "area_ha",
        False
    )
    .limit(300)
)

print(
    "Applied 0.1 ha minimum and 300-feature limit."
)


# ============================================================================
# SIMPLIFY GEOMETRIES
# ============================================================================
#
# IMPORTANT:
# Earth Engine Python Geometry.simplify()
# does NOT accept maxVertices.
#
# Therefore only tolerance is supplied here.
# ============================================================================

def simplify_feature(f):

    geom = (
        f.geometry()
        .simplify(50)
    )

    return f.setGeometry(
        geom
    )


vectors_export = (
    vectors
    .map(
        simplify_feature
    )
)

print(
    "Simplified export geometries."
)


# ============================================================================
# HEADLINE CHANGE PIXEL COUNTS
# ============================================================================

def pixel_count(img):

    result = (
        img
        .selfMask()
        .reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=aoi,
            scale=10,
            maxPixels=1e10,
            bestEffort=True
        )
        .values()
        .get(0)
    )

    return ee.Number(
        result
    )


raw_px = pixel_count(
    changed_raw.And(
        quality_ok
    )
)

kept_px = pixel_count(
    changed_final
)


# ============================================================================
# CLASS AREAS
# ============================================================================

def class_areas(cls):

    hist = (
        cls
        .reduceRegion(
            ee.Reducer.frequencyHistogram(),
            aoi,
            10,
            maxPixels=1e10,
            bestEffort=True
        )
        .get("class")
    )

    return ee.Dictionary(
        hist
    )


# ============================================================================
# STATS OBJECT
# ============================================================================

stats = ee.Dictionary({

    "aoi_name": AOI_NAME,

    "aoi_bbox": AOI_BBOX,

    "t1_label": T1_LABEL,

    "t2_label": T2_LABEL,

    "t1_start": T1_START,

    "t1_end": T1_END,

    "t2_start": T2_START,

    "t2_end": T2_END,

    "scenes_t1": n1,

    "scenes_t2": n2,

    "areas_t1_pixels": class_areas(
        cls_t1
    ),

    "areas_t2_pixels": class_areas(
        cls_t2
    ),

    "agreement_t1": ee.Dictionary({

        "overall": cm_t1.accuracy(),

        "kappa": cm_t1.kappa(),

        "matrix": cm_t1.array(),

        "producers":
            cm_t1.producersAccuracy(),

        "consumers":
            cm_t1.consumersAccuracy()
    }),

    "agreement_t2": ee.Dictionary({

        "overall": cm_t2.accuracy(),

        "kappa": cm_t2.kappa(),

        "matrix": cm_t2.array()
    }),

    "change_pixels_raw": raw_px,

    "change_pixels_kept": kept_px,

    "classes": {
        str(k): CLASSES[k][0]
        for k in CLASSES
    }
})


# ============================================================================
# 9. HONEST VALIDATION SAMPLE
# ============================================================================
#
# These are 60 points total:
# 10 points per class.
#
# Open validation_points.geojson in Google Earth Pro or QGIS
# and label each point BY EYE using a high-resolution basemap.
#
# That is the meaningful independent visual validation step.
# ============================================================================

val_points = (
    cls_t2
    .stratifiedSample(
        numPoints=10,
        classBand="class",
        region=aoi,
        scale=10,
        seed=7,
        geometries=True,
        tileScale=4
    )
)


# ============================================================================
# 10. WRITE LOCAL PNG OUTPUTS
# ============================================================================
#
# Direct image export previously exceeded the Earth Engine memory limit.
#
# getThumbURL() is used instead for lightweight PNG generation.
# ============================================================================

def save_png(
    img,
    name
):

    url = (
        img
        .visualize(
            min=0,
            max=5,
            palette=PALETTE
        )
        .getThumbURL({
            "region": aoi,
            "dimensions": THUMB_PIXELS,
            "format": "png",
            "crs": "EPSG:3857"
        })
    )

    r = requests.get(
        url,
        timeout=300
    )

    r.raise_for_status()

    path = f"{OUT}/{name}"

    with open(
        path,
        "wb"
    ) as f:

        f.write(
            r.content
        )

    print(
        "wrote",
        name,
        len(r.content) // 1024,
        "KB"
    )


# ============================================================================
# JSON HELPER
# ============================================================================

def save_json(
    obj,
    name
):

    path = f"{OUT}/{name}"

    with open(
        path,
        "w"
    ) as f:

        json.dump(
            obj,
            f,
            indent=2,
            default=str
        )

    print(
        "wrote",
        name
    )


# ============================================================================
# SAVE LULC PNGs
# ============================================================================

save_png(
    cls_t1,
    "lulc_t1.png"
)

save_png(
    cls_t2,
    "lulc_t2.png"
)


# ============================================================================
# SAVE OVERLAY METADATA
# ============================================================================

save_json(
    {
        "bbox": AOI_BBOX,

        "corners_lonlat": [
            [
                AOI_BBOX[0],
                AOI_BBOX[3]
            ],
            [
                AOI_BBOX[2],
                AOI_BBOX[3]
            ],
            [
                AOI_BBOX[2],
                AOI_BBOX[1]
            ],
            [
                AOI_BBOX[0],
                AOI_BBOX[1]
            ]
        ],

        "crs": "EPSG:3857",

        "t1_label": T1_LABEL,

        "t2_label": T2_LABEL,

        "classes": {
            str(k): {
                "name": v[0],
                "color": v[1]
            }
            for k, v in CLASSES.items()
        }
    },
    "overlay_bounds.json"
)


# ============================================================================
# IMPORTANT: CHANGE POLYGON EXPORT
# ============================================================================
#
# DO NOT use vectors.getInfo() here.
#
# The working approach is to export the FeatureCollection through
# Google Drive.
#
# CRITICAL:
# ".geo" MUST be included in selectors.
#
# Without ".geo", the resulting GeoJSON contains:
#
#     "geometry": null
#
# which caused the SQLite loader to skip every feature.
# ============================================================================

task = ee.batch.Export.table.toDrive(
    collection=vectors_export,

    description="TN_LCO_changes_v5",

    folder="TN_LCO",

    fileNamePrefix="changes",

    fileFormat="GeoJSON",

    selectors=[
        ".geo",
        "transition",
        "ndbi_d",
        "vv_d",
        "area_ha"
    ]
)

task.start()

print(
    "Started Google Drive export:"
)

print(
    "TN_LCO_changes_v5"
)

print(
    "Open the Earth Engine Tasks tab "
    "and wait until the export is COMPLETED."
)


# ============================================================================
# SAVE STATS
# ============================================================================
#
# stats.getInfo() is intentionally used here.
# Unlike the huge vector FeatureCollection, this dictionary is small enough.
# ============================================================================

save_json(
    stats.getInfo(),
    "stats.json"
)


# ============================================================================
# SAVE VALIDATION POINTS
# ============================================================================

save_json(
    val_points.getInfo(),
    "validation_points.geojson"
)


# ============================================================================
# DONE
# ============================================================================

print(
    "\nDONE."
)

print(
    "Download these files from /content/out:"
)

print(
    "  lulc_t1.png"
)

print(
    "  lulc_t2.png"
)

print(
    "  overlay_bounds.json"
)

print(
    "  stats.json"
)

print(
    "  validation_points.geojson"
)

print(
    "\nIMPORTANT:"
)

print(
    "changes.geojson is exported to Google Drive "
    "as a separate Earth Engine task."
)

print(
    "After the Drive export completes, download changes.geojson "
    "and place it in your local tn-lco/data/ folder."
)

print(
    "\nThen run locally:"
)

print(
    "python scripts/load_results.py"
)