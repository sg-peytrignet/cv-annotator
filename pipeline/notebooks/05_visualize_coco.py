# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Visualize the COCO export
# MAGIC
# MAGIC Demo aid: loads the latest `coco_<timestamp>` export and draws the bounding boxes + class labels
# MAGIC on the images, plus a per-class summary. Use this live to show *what was labeled and exported*.
# MAGIC
# MAGIC No installs needed — uses Pillow + matplotlib (preinstalled on Databricks runtimes).

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Catalog")
dbutils.widgets.text("schema", "", "Schema")
dbutils.widgets.text("exports_volume", "exports", "Exports volume")
dbutils.widgets.text("coco_dir", "", "COCO dir (blank = latest)")
dbutils.widgets.text("max_images", "9", "Max images to show")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
EXPORTS_VOL = dbutils.widgets.get("exports_volume")
COCO_DIR = dbutils.widgets.get("coco_dir").strip()

# Fail fast rather than building a "/Volumes///..." path from blank widgets.
assert CATALOG and SCHEMA, "catalog and schema are required"
MAX_IMAGES = int(dbutils.widgets.get("max_images"))

EXPORTS_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{EXPORTS_VOL}"

# Resolve to the latest coco_* export if not specified
if not COCO_DIR:
    dirs = sorted([f.path.replace("dbfs:", "") for f in dbutils.fs.ls(EXPORTS_PATH) if f.name.startswith("coco_")])
    assert dirs, f"No coco_* exports in {EXPORTS_PATH}. Run 02_export_coco first."
    COCO_DIR = dirs[-1]
COCO_DIR = COCO_DIR.rstrip("/")
print(f"Visualizing: {COCO_DIR}")

# COMMAND ----------

import json

coco = json.load(open(f"{COCO_DIR}/annotations.json"))
cats = {c["id"]: c["name"] for c in coco["categories"]}
anns_by_img = {}
for a in coco["annotations"]:
    anns_by_img.setdefault(a["image_id"], []).append(a)

print(f"{len(coco['images'])} images · {len(coco['annotations'])} boxes · {len(cats)} classes: {list(cats.values())}")

# COMMAND ----------

# MAGIC %md ## Per-class box counts

# COMMAND ----------

from collections import Counter

counts = Counter(cats[a["category_id"]] for a in coco["annotations"])
rows = [{"class": k, "boxes": v} for k, v in counts.most_common()]
display(spark.createDataFrame(rows)) if rows else print("No annotations yet.")

# COMMAND ----------

# MAGIC %md ## Images with bounding boxes overlaid

# COMMAND ----------

import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib import cm
from PIL import Image

# stable color per class
cat_ids = sorted(cats)
palette = {cid: cm.tab10(i % 10) for i, cid in enumerate(cat_ids)}

imgs = [im for im in coco["images"] if anns_by_img.get(im["id"])][:MAX_IMAGES]
if not imgs:
    print("No labeled images to show.")
else:
    n = len(imgs)
    cols = min(3, n)
    rows_n = math.ceil(n / cols)
    fig, axes = plt.subplots(rows_n, cols, figsize=(5 * cols, 4 * rows_n))
    axes = [axes] if n == 1 else axes.ravel()

    for ax, im in zip(axes, imgs):
        img = Image.open(f"{COCO_DIR}/images/{im['file_name']}").convert("RGB")
        ax.imshow(img)
        ax.set_title(im["file_name"], fontsize=9, color="black")
        ax.axis("off")
        for a in anns_by_img[im["id"]]:
            x, y, w, h = a["bbox"]
            color = palette[a["category_id"]]
            ax.add_patch(patches.Rectangle((x, y), w, h, fill=False, edgecolor=color, linewidth=2))
            ax.text(x, max(0, y - 4), cats[a["category_id"]], color="white", fontsize=8,
                    bbox=dict(facecolor=color, edgecolor="none", pad=1))
    for ax in axes[len(imgs):]:
        ax.axis("off")
    plt.tight_layout()
    display(fig)
