# Databricks notebook source
# MAGIC %md
# MAGIC # Reset annotation state — start from scratch
# MAGIC
# MAGIC Wipes annotation **data** while leaving the UC namespace and the app in place:
# MAGIC - Deletes the annotation JSON sidecars + composites under `.labelbricks/` on the images
# MAGIC   Volume (including anything written by `scripts/seed_annotations.py`)
# MAGIC - Clears the `exports` Volume (COCO export datasets)
# MAGIC - Optionally deletes the registered UC model, and/or wipes `images` + `landing` +
# MAGIC   `image_catalog` for a full dataset swap
# MAGIC
# MAGIC After this every image shows as unlabeled/pending again. Run before a demo with:
# MAGIC `databricks bundle run cv_clear`.

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Catalog")
dbutils.widgets.text("schema", "", "Schema")
dbutils.widgets.text("images_volume", "images", "Images volume")
dbutils.widgets.text("landing_volume", "landing", "Landing volume")
dbutils.widgets.text("exports_volume", "exports", "Exports volume")
dbutils.widgets.dropdown("clear_images", "false", ["true", "false"],
                         "Also wipe images/landing + image_catalog")
dbutils.widgets.dropdown("clear_model", "false", ["true", "false"], "Also delete registered model")
dbutils.widgets.text("model_name", "object_detector", "UC model name (if clear_model)")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
IMAGES_VOL = dbutils.widgets.get("images_volume")
LANDING_VOL = dbutils.widgets.get("landing_volume")
EXPORTS_VOL = dbutils.widgets.get("exports_volume")
CLEAR_IMAGES = dbutils.widgets.get("clear_images") == "true"
CLEAR_MODEL = dbutils.widgets.get("clear_model") == "true"
MODEL_NAME = dbutils.widgets.get("model_name")

# This notebook DELETES data — never let it run against a guessed namespace.
assert CATALOG and SCHEMA, "catalog and schema are required"

# COMMAND ----------

# ---- 1. Delete the annotation JSON sidecars + composites ----
base = f"/Volumes/{CATALOG}/{SCHEMA}/{IMAGES_VOL}/.labelbricks"
for sub in ["annotations", "composites"]:
    path = f"{base}/{sub}"
    try:
        n = len(dbutils.fs.ls(path))
        dbutils.fs.rm(path, recurse=True)
        print(f"Deleted {n} file(s) under {path}")
    except Exception as e:
        print(f"{path}: nothing to delete ({str(e)[:60]})")

# COMMAND ----------

# ---- 2. Clear the exports volume (COCO export datasets) ----
exports_path = f"/Volumes/{CATALOG}/{SCHEMA}/{EXPORTS_VOL}"
try:
    entries = dbutils.fs.ls(exports_path)
    for e in entries:
        dbutils.fs.rm(e.path, recurse=True)
    print(f"Cleared {len(entries)} item(s) from {exports_path}")
except Exception as e:
    print(f"{exports_path}: nothing to delete ({str(e)[:60]})")

# COMMAND ----------

# ---- 3. (Optional) Delete the registered UC model so train comes back as a fresh v1 ----
if CLEAR_MODEL:
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    uc_model = f"{CATALOG}.{SCHEMA}.{MODEL_NAME}"
    try:
        w.registered_models.delete(full_name=uc_model)
        print(f"Deleted registered model {uc_model} (all versions)")
    except Exception as e:
        print(f"{uc_model}: not deleted ({str(e)[:80]})")
else:
    print("clear_model=false → leaving the registered model in place.")

# COMMAND ----------

# ---- 4. (Optional) Wipe images + landing volumes + image_catalog (full dataset swap) ----
# image_catalog is DERIVED — clearing it is safe; the ingest job rebuilds it from the files
# in `landing` on the next run. After this, upload the new dataset to `landing` and run cv_ingest.
if CLEAR_IMAGES:
    for vol in [IMAGES_VOL, LANDING_VOL]:
        vp = f"/Volumes/{CATALOG}/{SCHEMA}/{vol}"
        try:
            entries = dbutils.fs.ls(vp)
            for e in entries:
                dbutils.fs.rm(e.path, recurse=True)
            print(f"Wiped {len(entries)} item(s) from {vp}")
        except Exception as e:
            print(f"{vp}: nothing to delete ({str(e)[:60]})")
    try:
        spark.sql(f"DELETE FROM {CATALOG}.{SCHEMA}.image_catalog")
        print("Cleared image_catalog rows (ingest will rebuild on next run).")
    except Exception as e:
        print(f"image_catalog: not cleared ({str(e)[:80]})")
else:
    print("clear_images=false → keeping images/landing + image_catalog.")

# COMMAND ----------

print("\nReset complete. Annotate again in the app, or upload a new dataset to `landing` "
      "and run cv_ingest to repopulate image_catalog.")
