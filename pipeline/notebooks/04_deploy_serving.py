# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Deploy the registered model to a Model Serving endpoint
# MAGIC
# MAGIC Final step of the loop: the model registered in Unity Catalog by `03_train_detection` is
# MAGIC promoted with an alias and served behind a **Mosaic AI Model Serving** endpoint, so it can be
# MAGIC called over REST from any application.
# MAGIC
# MAGIC What this notebook does:
# MAGIC 1. Resolves the model version to deploy (explicit widget, upstream task value, or latest).
# MAGIC 2. Sets a **`@champion` alias** on that version — apps target the alias, not a version number,
# MAGIC    so future retrains roll out without changing any client code.
# MAGIC 3. Creates the serving endpoint, or updates it in place if it already exists (idempotent).
# MAGIC 4. Waits for it to come up and runs a **sanity inference** against the live endpoint.
# MAGIC
# MAGIC **`scale_to_zero_enabled=true`** by default: the endpoint costs nothing while idle, at the price
# MAGIC of a cold start (a few minutes) on the first request after a quiet period. For a live demo, set
# MAGIC `scale_to_zero` to `false` beforehand to keep it warm.
# MAGIC
# MAGIC **Compute:** any cluster with the Databricks SDK — no torch needed here; the model runs inside
# MAGIC the serving container, not on this cluster.

# COMMAND ----------

# MAGIC %pip install --quiet --upgrade "databricks-sdk>=0.81.0" mlflow
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Catalog")
dbutils.widgets.text("schema", "", "Schema")
dbutils.widgets.text("model_name", "object_detector", "UC model name")
dbutils.widgets.text("model_version", "", "Model version (blank = latest)")
dbutils.widgets.text("endpoint_name", "", "Serving endpoint name (blank = <model>-endpoint)")
dbutils.widgets.dropdown("workload_size", "Small", ["Small", "Medium", "Large"], "Workload size")
dbutils.widgets.dropdown("scale_to_zero", "true", ["true", "false"], "Scale to zero when idle")
dbutils.widgets.text("alias", "champion", "UC model alias to set")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
MODEL_NAME = dbutils.widgets.get("model_name")
MODEL_VERSION = dbutils.widgets.get("model_version").strip()
WORKLOAD_SIZE = dbutils.widgets.get("workload_size")
SCALE_TO_ZERO = dbutils.widgets.get("scale_to_zero") == "true"
ALIAS = dbutils.widgets.get("alias").strip()

assert CATALOG and SCHEMA, "catalog and schema are required"

UC_MODEL = f"{CATALOG}.{SCHEMA}.{MODEL_NAME}"

# Endpoint names allow letters, numbers and hyphens only.
ENDPOINT = dbutils.widgets.get("endpoint_name").strip() or f"{MODEL_NAME.replace('_', '-')}-endpoint"

print(f"UC model : {UC_MODEL}")
print(f"Endpoint : {ENDPOINT}")

# COMMAND ----------

# ---- Resolve which version to serve ----
import mlflow
from mlflow.tracking import MlflowClient

mlflow.set_registry_uri("databricks-uc")
client = MlflowClient(registry_uri="databricks-uc")

if not MODEL_VERSION:
    # Prefer the version the upstream train task just produced.
    try:
        MODEL_VERSION = str(dbutils.jobs.taskValues.get(taskKey="train", key="model_version"))
    except Exception:
        MODEL_VERSION = ""

if not MODEL_VERSION:
    versions = client.search_model_versions(f"name='{UC_MODEL}'")
    assert versions, f"No versions found for {UC_MODEL}. Run 03_train_detection first."
    MODEL_VERSION = max(versions, key=lambda v: int(v.version)).version

print(f"Serving {UC_MODEL} version {MODEL_VERSION}")

# COMMAND ----------

# ---- Alias the version so clients can target a stable name ----
# Apps call @champion; a later retrain just moves the alias, no client change needed.
if ALIAS:
    client.set_registered_model_alias(name=UC_MODEL, alias=ALIAS, version=MODEL_VERSION)
    print(f"Alias @{ALIAS} -> version {MODEL_VERSION}")

# COMMAND ----------

# ---- Create or update the serving endpoint (idempotent) ----
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import (
    EndpointCoreConfigInput,
    ServedEntityInput,
)

w = WorkspaceClient()

served = ServedEntityInput(
    name=f"{MODEL_NAME}-{MODEL_VERSION}",
    entity_name=UC_MODEL,
    entity_version=MODEL_VERSION,
    workload_size=WORKLOAD_SIZE,
    scale_to_zero_enabled=SCALE_TO_ZERO,
)

existing = None
try:
    existing = w.serving_endpoints.get(name=ENDPOINT)
except Exception:
    pass  # Not found -> create below.

if existing is None:
    print(f"Creating endpoint {ENDPOINT} (this takes a few minutes)...")
    w.serving_endpoints.create_and_wait(
        name=ENDPOINT,
        config=EndpointCoreConfigInput(name=ENDPOINT, served_entities=[served]),
    )
    print("Endpoint created.")
else:
    print(f"Endpoint {ENDPOINT} exists — updating it to version {MODEL_VERSION}...")
    w.serving_endpoints.update_config_and_wait(name=ENDPOINT, served_entities=[served])
    print("Endpoint updated.")

# COMMAND ----------

# ---- Report state + the REST URL ----
ep = w.serving_endpoints.get(name=ENDPOINT)
host = w.config.host.rstrip("/")
invoke_url = f"{host}/serving-endpoints/{ENDPOINT}/invocations"

print(f"state        : {ep.state.ready if ep.state else 'unknown'}")
print(f"invocations  : {invoke_url}")
print(f"UI           : {host}/ml/endpoints/{ENDPOINT}")

# COMMAND ----------

# MAGIC %md ## Sanity inference against the live endpoint
# MAGIC
# MAGIC The endpoint takes base64-encoded image bytes in an `image_b64` column, so a small generated
# MAGIC PNG is enough to prove it is wired up and returns predictions. A cold start can make this first
# MAGIC call slow — that is expected with `scale_to_zero_enabled`.

# COMMAND ----------

import base64, io
import numpy as np
from PIL import Image

try:
    buf = io.BytesIO()
    Image.fromarray((np.random.rand(64, 64, 3) * 255).astype("uint8")).save(buf, format="PNG")
    resp = w.serving_endpoints.query(
        name=ENDPOINT,
        dataframe_records=[{"image_b64": base64.b64encode(buf.getvalue()).decode()}],
    )
    preds = resp.predictions
    print("Inference OK. Raw response (truncated):")
    print(str(preds)[:600])
except Exception as e:
    # Don't fail the job: the endpoint may still be warming after a cold start.
    # The endpoint itself is already deployed at this point.
    print(f"Sanity inference did not return cleanly: {type(e).__name__}: {str(e)[:300]}")
    print("The endpoint is deployed — check the Serving UI and retry the query if it was cold.")

# COMMAND ----------

# MAGIC %md ## How an application calls this
# MAGIC
# MAGIC ```bash
# MAGIC curl -X POST \
# MAGIC   -H "Authorization: Bearer $DATABRICKS_TOKEN" \
# MAGIC   -H "Content-Type: application/json" \
# MAGIC   -d "{\"dataframe_records\": [{\"image_b64\": \"$(base64 -i photo.jpg | tr -d '\n')\"}]}" \
# MAGIC   https://<workspace>/serving-endpoints/<endpoint>/invocations
# MAGIC ```
# MAGIC
# MAGIC Talk track: the model is **governed in Unity Catalog**, **versioned**, aliased for safe
# MAGIC promotion, and now **reachable over REST** — the same lineage carries from the annotated image
# MAGIC all the way to the served prediction.

# COMMAND ----------

import json

dbutils.notebook.exit(json.dumps({
    "uc_model": UC_MODEL,
    "model_version": str(MODEL_VERSION),
    "alias": ALIAS,
    "endpoint": ENDPOINT,
    "invocations_url": invoke_url,
}))
