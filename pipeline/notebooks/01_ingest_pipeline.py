# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Ingest pipeline — images + video frame extraction
# MAGIC
# MAGIC Demo step 3: "automated pipeline for new image/video".
# MAGIC
# MAGIC Reads new files from the **landing** UC Volume, and:
# MAGIC - **Images** → validated with Pillow, copied to the **images** (curated) Volume, one row in `image_catalog`.
# MAGIC - **Videos** → frames extracted with OpenCV at a fixed interval, written to the **images** Volume,
# MAGIC   one `image_catalog` row per frame.
# MAGIC - **AI enrichment** → every curated image/frame is tagged by a vision model (FMAPI Claude):
# MAGIC   caption, object tags, person count, face flag, quality. Written into `image_catalog`, which
# MAGIC   makes the dataset searchable (e.g. `array_contains(tags, 'keyboard')`) and feeds the
# MAGIC   dataset-health dashboard. `contains_faces` is recorded as an informational flag.
# MAGIC
# MAGIC The `image_catalog` Delta table is the governance/lineage artifact for demo step 4. The notebook is
# MAGIC **idempotent** — it skips source files already recorded in the catalog, so it can run on a
# MAGIC file-arrival trigger (see `pipeline/resources/ingest_job.yml`).

# COMMAND ----------

# MAGIC %pip install --quiet "opencv-python-headless<4.10" "numpy<2"
# MAGIC %restart_python
# NOTE: pin numpy<2 — newer opencv pulls numpy 2.x which breaks the runtime's
# preinstalled pandas and prevents the kernel from restarting. Pillow is preinstalled.

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Catalog")
dbutils.widgets.text("schema", "", "Schema")
dbutils.widgets.text("landing_volume", "landing", "Landing volume (raw uploads)")
dbutils.widgets.text("images_volume", "images", "Curated images volume")
dbutils.widgets.text("tagging_endpoint", "databricks-claude-sonnet-4-5", "FMAPI vision endpoint for tagging")
dbutils.widgets.text("tag_parallelism", "6", "Concurrent tagging requests")
dbutils.widgets.text("frame_interval_sec", "2.0", "Video: seconds between extracted frames")
dbutils.widgets.text("max_frames_per_video", "40", "Video: max frames per video")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
LANDING_VOL = dbutils.widgets.get("landing_volume")
IMAGES_VOL = dbutils.widgets.get("images_volume")
TAGGING_ENDPOINT = dbutils.widgets.get("tagging_endpoint")
FRAME_INTERVAL_SEC = float(dbutils.widgets.get("frame_interval_sec"))
MAX_FRAMES = int(dbutils.widgets.get("max_frames_per_video"))

# Fail fast rather than building a "/Volumes///..." path from blank widgets.
assert CATALOG and SCHEMA, "catalog and schema are required"

LANDING_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{LANDING_VOL}"
IMAGES_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{IMAGES_VOL}"
CATALOG_TABLE = f"{CATALOG}.{SCHEMA}.image_catalog"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

print(f"Landing : {LANDING_PATH}")
print(f"Images  : {IMAGES_PATH}")
print(f"Catalog : {CATALOG_TABLE}")

# COMMAND ----------

import os
import io
import shutil
from datetime import datetime, timezone

from PIL import Image
import cv2
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, TimestampType,
    ArrayType, BooleanType,
)

# Explicit schema — image rows have frame_index=None, so type inference fails.
CATALOG_SCHEMA = StructType([
    StructField("file_path", StringType()),
    StructField("filename", StringType()),
    StructField("source_path", StringType()),
    StructField("media_type", StringType()),
    StructField("frame_index", IntegerType()),
    StructField("width", IntegerType()),
    StructField("height", IntegerType()),
    StructField("ingested_at", TimestampType()),
    StructField("status", StringType()),
    # ---- AI enrichment (vision model at ingest) ----
    StructField("caption", StringType()),
    StructField("tags", ArrayType(StringType())),
    StructField("person_count", IntegerType()),
    StructField("contains_faces", BooleanType()),   # NULL = tagging failed / unknown
    StructField("quality", StringType()),
])

# ---- Governance table: one row per curated image (or extracted frame) ----
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG_TABLE} (
    file_path       STRING,   -- curated image path in the images Volume
    filename        STRING,
    source_path     STRING,   -- original file in the landing Volume
    media_type      STRING,   -- 'image' | 'video_frame'
    frame_index     INT,      -- NULL for images
    width           INT,
    height          INT,
    ingested_at     TIMESTAMP,
    status          STRING,   -- 'ingested'
    caption         STRING,        -- AI enrichment: one-sentence description
    tags            ARRAY<STRING>, -- AI enrichment: object/scene tags
    person_count    INT,           -- AI enrichment
    contains_faces  BOOLEAN,       -- AI enrichment; NULL = unknown
    quality         STRING         -- AI enrichment: sharp | blurry | dark
) USING DELTA
""")

# Schema evolution for tables created before AI enrichment existed (idempotent).
_existing_cols = {f.name for f in spark.table(CATALOG_TABLE).schema.fields}
for _col, _typ in [
    ("caption", "STRING"), ("tags", "ARRAY<STRING>"), ("person_count", "INT"),
    ("contains_faces", "BOOLEAN"), ("quality", "STRING"),
]:
    if _col not in _existing_cols:
        spark.sql(f"ALTER TABLE {CATALOG_TABLE} ADD COLUMN ({_col} {_typ})")
        print(f"schema: added column {_col} {_typ}")


def already_ingested(source_path: str) -> bool:
    """Idempotency: skip a source file already represented in the catalog."""
    df = spark.sql(
        f"SELECT 1 FROM {CATALOG_TABLE} WHERE source_path = '{source_path}' LIMIT 1"
    )
    return df.count() > 0


def record_rows(rows: list[dict]) -> None:
    if not rows:
        return
    spark.createDataFrame(rows, schema=CATALOG_SCHEMA).write.mode("append").saveAsTable(CATALOG_TABLE)


def list_landing() -> list:
    try:
        return dbutils.fs.ls(LANDING_PATH)
    except Exception as e:
        print(f"Landing volume not readable ({e}); nothing to ingest.")
        return []


# ---- AI enrichment: vision tagging via FMAPI (caption / tags / faces / quality) ----
import base64
import json as _json
import re as _re
import requests as _requests

_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
_WORKSPACE_URL = _ctx.apiUrl().get()
_API_TOKEN = _ctx.apiToken().get()

TAGGING_PROMPT = (
    "You are an image dataset curation assistant. Analyze this image and return ONLY a JSON "
    "object with these fields:\n"
    '- "caption": one concise sentence describing the image\n'
    '- "tags": array of up to 10 lowercase object/scene tags (e.g. "keyboard", "office", "whiteboard")\n'
    '- "person_count": integer number of people visible\n'
    '- "contains_faces": boolean — true if ANY human face is visible, even partially, small, or '
    "in the background. If you are unsure, answer true.\n"
    '- "quality": one of "sharp", "blurry", "dark"\n'
    "No markdown, no explanation, no extra text."
)

MAX_TAG_IMAGE_BYTES = 2_500_000  # FMAPI request limit headroom (base64 inflates ~33%)


def _png_b64(path: str) -> str:
    """Load an image, convert to PNG (FMAPI requirement), downscale if oversized."""
    img = Image.open(path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    for scale in [1.0, 0.75, 0.5, 0.35, 0.25]:
        resized = img if scale == 1.0 else img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS
        )
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        if buf.tell() <= MAX_TAG_IMAGE_BYTES:
            break
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def analyze_image(path: str) -> dict | None:
    """Tag one image with the FMAPI vision model. Returns the enrichment dict, or None on failure."""
    try:
        b64 = _png_b64(path)
        resp = _requests.post(
            f"{_WORKSPACE_URL}/serving-endpoints/{TAGGING_ENDPOINT}/invocations",
            headers={"Authorization": f"Bearer {_API_TOKEN}", "Content-Type": "application/json"},
            json={
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TAGGING_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }],
                "max_tokens": 512,
                "temperature": 0.0,
            },
            timeout=90,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = _re.sub(r"^```(?:json)?\s*", "", raw)
        raw = _re.sub(r"\s*```$", "", raw).strip()
        d = _json.loads(raw)
        return {
            "caption": str(d.get("caption", ""))[:500] or None,
            "tags": [str(t).lower()[:50] for t in d.get("tags", [])][:10],
            "person_count": int(d.get("person_count", 0)),
            "contains_faces": bool(d.get("contains_faces", True)),
            "quality": str(d.get("quality", "")) or None,
        }
    except Exception as e:
        print(f"tagging ✗ {os.path.basename(path)}: {str(e)[:120]}")
        return None


TAG_WORKERS = int(dbutils.widgets.get("tag_parallelism"))


def tag_parallel(paths: list[str]) -> list:
    """Run analyze_image over many files concurrently, preserving order.

    FMAPI pay-per-token endpoints handle concurrent requests; a per-image failure
    returns None (that image keeps NULL enrichment columns) without affecting the others.
    """
    if not paths:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(TAG_WORKERS, len(paths))) as ex:
        return list(ex.map(analyze_image, paths))


# COMMAND ----------

# MAGIC %md ## Process images

# COMMAND ----------

# Phase 1: collect validated, not-yet-ingested candidates.
candidates = []
for f in list_landing():
    name = f.name.rstrip("/")
    src = f.path.replace("dbfs:", "")  # /Volumes/...
    ext = os.path.splitext(name)[1].lower()
    if ext not in IMAGE_EXTS or f.isDir():
        continue
    if already_ingested(src):
        continue
    try:
        with Image.open(src) as im:
            im.verify()  # validate it's a real image
        with Image.open(src) as im:
            w, h = im.size
        candidates.append({"name": name, "src": src, "w": w, "h": h})
    except Exception as e:
        print(f"image  ✗ {name}: {e}")

# Phase 2: tag in parallel — the FMAPI calls dominate ingest time (~3-6s each, serial
# would be N×; the endpoint handles concurrent requests fine).
enrichments = tag_parallel([c["src"] for c in candidates])

# Phase 3: place + record.
image_rows = []
for c, enrich in zip(candidates, enrichments):
    name, src, w, h = c["name"], c["src"], c["w"], c["h"]
    contains_faces = enrich["contains_faces"] if enrich else None
    dst = f"{IMAGES_PATH}/{name}"
    try:
        shutil.copyfile(src, dst)
    except Exception as e:
        # Skip (no catalog row) so idempotency retries this file on the next run.
        print(f"image  ✗ {name}: cannot place in curated volume ({e}) — skipped")
        continue
    image_rows.append({
        "file_path": dst, "filename": name, "source_path": src,
        "media_type": "image", "frame_index": None, "width": w, "height": h,
        "ingested_at": datetime.now(timezone.utc), "status": "ingested",
        "caption": enrich["caption"] if enrich else None,
        "tags": enrich["tags"] if enrich else None,
        "person_count": enrich["person_count"] if enrich else None,
        "contains_faces": contains_faces,
        "quality": enrich["quality"] if enrich else None,
    })
    print(f"image  ✓ {name} ({w}x{h}) tags={enrich['tags'] if enrich else 'n/a'}")

record_rows(image_rows)
print(f"Ingested {len(image_rows)} image(s).")

# COMMAND ----------

# MAGIC %md ## Process videos → frames (OpenCV)

# COMMAND ----------

def extract_frames(src: str, stem: str) -> list[dict]:
    """Sample frames every FRAME_INTERVAL_SEC; tag in parallel; route each to its volume."""
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f"video  ✗ cannot open {src}")
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps * FRAME_INTERVAL_SEC)))

    # Pass 1: sample frames to driver-local disk (fast, sequential decode).
    sampled, frame_idx = [], 0
    while len(sampled) < MAX_FRAMES:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step == 0:
            fname = f"{stem}_frame{len(sampled):04d}.jpg"
            local_tmp = f"/tmp/{fname}"
            cv2.imwrite(local_tmp, frame)
            h, w = frame.shape[:2]
            sampled.append({"fname": fname, "local": local_tmp, "w": w, "h": h,
                            "idx": len(sampled)})
        frame_idx += 1
    cap.release()

    # Pass 2: tag all frames in parallel (the dominant cost — was ~4s × N serial).
    enrichments = tag_parallel([s["local"] for s in sampled])

    # Pass 3: place + record.
    rows = []
    for s, enrich in zip(sampled, enrichments):
        contains_faces = enrich["contains_faces"] if enrich else None
        dst = f"{IMAGES_PATH}/{s['fname']}"
        try:
            shutil.copyfile(s["local"], dst)
        except Exception as e:
            print(f"frame  ✗ {s['fname']}: cannot place in curated volume ({e}) — skipped")
            os.remove(s["local"])
            continue
        os.remove(s["local"])
        rows.append({
            "file_path": dst, "filename": s["fname"], "source_path": src,
            "media_type": "video_frame", "frame_index": s["idx"], "width": s["w"], "height": s["h"],
            "ingested_at": datetime.now(timezone.utc), "status": "ingested",
            "caption": enrich["caption"] if enrich else None,
            "tags": enrich["tags"] if enrich else None,
            "person_count": enrich["person_count"] if enrich else None,
            "contains_faces": contains_faces,
            "quality": enrich["quality"] if enrich else None,
        })
    print(f"video  ✓ {stem}: {len(rows)} frame(s)")
    return rows


frame_rows = []
for f in list_landing():
    name = f.name.rstrip("/")
    src = f.path.replace("dbfs:", "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in VIDEO_EXTS or f.isDir():
        continue
    if already_ingested(src):
        continue
    frame_rows.extend(extract_frames(src, os.path.splitext(name)[0]))

record_rows(frame_rows)
print(f"Extracted {len(frame_rows)} frame(s) from video(s).")

# COMMAND ----------

# MAGIC %md ## Summary

# COMMAND ----------

summary = spark.sql(f"""
  SELECT media_type, count(*) AS n
  FROM {CATALOG_TABLE} GROUP BY media_type ORDER BY media_type
""").collect()
for r in summary:
    print(f"{r['media_type']}: {r['n']}")

enrichment = spark.sql(f"""
  SELECT
    count(*) AS total,
    count_if(contains_faces) AS with_faces,
    count_if(contains_faces IS NULL) AS tagging_unknown
  FROM {CATALOG_TABLE}
""").collect()[0]
print(f"\nAI enrichment: {enrichment['total']} image(s) tagged "
      f"({enrichment['with_faces']} with faces, {enrichment['tagging_unknown']} untagged)")
