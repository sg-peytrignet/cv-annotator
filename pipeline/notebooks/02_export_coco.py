# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Export labels → COCO
# MAGIC
# MAGIC Demo step 7 (glue). Reads LabelBricks annotations and writes a **COCO detection** dataset
# MAGIC (`annotations.json` + `images/` + `metadata.json`) to the **exports** Volume, ready for training.
# MAGIC
# MAGIC **Source**: the `.labelbricks/annotations/*.json` sidecars the app writes onto the images
# MAGIC Volume. This is the app's storage format — one JSON per annotated image.
# MAGIC
# MAGIC Coordinate conversion mirrors `pipeline/lib/coco_utils.py` (unit-tested): annotation coords are
# MAGIC in scaled display space, so we divide by `display_scale` to recover true image pixels.
# MAGIC **Keep the two in sync** — `pipeline/tests/test_coco_utils.py` guards the library copy.

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Catalog")
dbutils.widgets.text("schema", "", "Schema")
dbutils.widgets.text("images_volume", "images", "Curated images volume")
dbutils.widgets.text("exports_volume", "exports", "Exports volume")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
IMAGES_VOL = dbutils.widgets.get("images_volume")
EXPORTS_VOL = dbutils.widgets.get("exports_volume")

# Fail fast rather than building a "/Volumes///..." path from blank widgets.
assert CATALOG and SCHEMA, "catalog and schema are required"

IMAGES_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{IMAGES_VOL}"
EXPORTS_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{EXPORTS_VOL}"

# COMMAND ----------

# ---- Conversion helpers (mirror of pipeline/lib/coco_utils.py, unit-tested) ----
from typing import Optional


def _scale(display_scale: Optional[float]) -> float:
    return float(display_scale) if display_scale and display_scale > 0 else 1.0


def annotation_to_bbox(ann_type, coordinates, display_scale):
    s = _scale(display_scale)
    if ann_type == "rectangle":
        return [coordinates["left"] / s, coordinates["top"] / s,
                coordinates["width"] / s, coordinates["height"] / s]
    if ann_type == "circle":
        cx, cy, rx, ry = coordinates["cx"], coordinates["cy"], coordinates["rx"], coordinates["ry"]
        return [(cx - rx) / s, (cy - ry) / s, (2 * rx) / s, (2 * ry) / s]
    if ann_type == "polygon":
        pts = coordinates.get("points") or []
        if not pts:
            return None
        xs = [p["x"] / s for p in pts]; ys = [p["y"] / s for p in pts]
        return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
    return None


def polygon_segmentation(coordinates, display_scale):
    """COCO segmentation [[x1,y1,x2,y2,...]] in image pixels for polygon annotations."""
    s = _scale(display_scale)
    pts = coordinates.get("points") or []
    if len(pts) < 3:
        return None
    flat = []
    for p in pts:
        flat.extend([p["x"] / s, p["y"] / s])
    return [flat]


def build_coco(samples, description="LabelBricks export"):
    classes = sorted({a.get("labelClass", "unlabeled") for s in samples
                      for a in s.get("annotations", []) if a.get("type") in ("rectangle", "circle", "polygon")})
    cat_id = {c: i + 1 for i, c in enumerate(classes)}
    coco = {"info": {"description": description, "version": "1.0"}, "images": [],
            "annotations": [], "categories": [{"id": cat_id[c], "name": c} for c in classes]}
    img_id = ann_id = dropped = 0
    for s in samples:
        img_id += 1
        coco["images"].append({"id": img_id, "file_name": s["filename"],
                               "width": s.get("width"), "height": s.get("height")})
        for a in s.get("annotations", []):
            bbox = annotation_to_bbox(a.get("type"), a.get("coordinates", {}), s.get("display_scale"))
            if bbox is None or bbox[2] <= 0 or bbox[3] <= 0:
                dropped += 1; continue
            ann_id += 1
            entry = {"id": ann_id, "image_id": img_id,
                "category_id": cat_id[a.get("labelClass", "unlabeled")],
                "bbox": [round(v, 2) for v in bbox], "area": round(bbox[2] * bbox[3], 2), "iscrowd": 0}
            if a.get("type") == "polygon":
                seg = polygon_segmentation(a.get("coordinates", {}), s.get("display_scale"))
                if seg:
                    entry["segmentation"] = [[round(v, 2) for v in seg[0]]]
            coco["annotations"].append(entry)
    coco["info"]["_dropped_annotations"] = dropped
    return coco

# COMMAND ----------

# ---- Load samples from the annotation JSON sidecars ----
import json, os


def load_from_json():
    """Read every .labelbricks/annotations/*.json sidecar on the images Volume."""
    samples = []
    ann_dir = f"{IMAGES_PATH}/.labelbricks/annotations"
    try:
        files = dbutils.fs.ls(ann_dir)
    except Exception:
        print(f"No annotation directory yet at {ann_dir}.")
        return samples
    for f in files:
        if not f.name.endswith(".json"):
            continue
        with open(f.path.replace("dbfs:", ""), "r") as fh:
            d = json.load(fh)
        if not d.get("annotations"):
            continue
        samples.append({
            "filename": d["filename"], "width": d.get("image_width"),
            "height": d.get("image_height"), "display_scale": d.get("display_scale"),
            "annotations": d["annotations"],
            "file_path": f"{IMAGES_PATH}/{d['filename']}",
        })
    return samples


samples = load_from_json()
print(f"Loaded {len(samples)} labeled image(s).")

assert samples, "No labeled images found. Annotate some images in LabelBricks first."

# COMMAND ----------

# ---- Build COCO + write dataset to the exports Volume ----
import shutil
from datetime import datetime, timezone

ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
out_dir = f"{EXPORTS_PATH}/coco_{ts}"
img_out = f"{out_dir}/images"
dbutils.fs.mkdirs(img_out)

coco = build_coco(samples, description=f"CV annotator export {ts}")

# Copy referenced images next to the COCO json so the dataset is self-contained.
copied = 0
for s in samples:
    src = s.get("file_path") or f"{IMAGES_PATH}/{s['filename']}"
    try:
        shutil.copyfile(src, f"{img_out}/{s['filename']}")
        copied += 1
    except Exception as e:
        print(f"  ! could not copy {s['filename']}: {e}")

with open(f"{out_dir}/annotations.json", "w") as fh:
    json.dump(coco, fh, indent=2)

metadata = {
    "exported_at": ts, "catalog": CATALOG, "schema": SCHEMA,
    "num_images": len(coco["images"]), "num_annotations": len(coco["annotations"]),
    "dropped_annotations": coco["info"]["_dropped_annotations"],
    "categories": [c["name"] for c in coco["categories"]], "images_copied": copied,
}
with open(f"{out_dir}/metadata.json", "w") as fh:
    json.dump(metadata, fh, indent=2)

print(json.dumps(metadata, indent=2))
print(f"\nCOCO dataset written to: {out_dir}")

# Hand the export path to a downstream task (e.g. the training notebook in a job).
dbutils.jobs.taskValues.set(key="export_dir", value=out_dir) if hasattr(dbutils, "jobs") else None
dbutils.notebook.exit(out_dir)
