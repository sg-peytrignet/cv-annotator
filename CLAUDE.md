# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

End-to-end computer-vision demo on Databricks: ingest images/video → govern in Unity Catalog →
annotate (LabelBricks app + AI Suggest) → export COCO → train a detector → register to UC → deploy to
a Model Serving endpoint. One DABs bundle (`databricks.yml`) deploys the UC namespace, the app, all
jobs, and the dashboard.

Key documents — read before making changes:
- `README.md` — install/config contract and the customer-facing story.
- `docs/demo_runbook.md` — setup steps and the demo-day script (live vs pre-baked steps).
- `labelbricks/CLAUDE.md` — detailed guidance for the annotation app subtree (Flask/Fabric.js
  patterns, JSON storage, lessons learned). Defer to it for any work inside `labelbricks/`.

## Commands

```bash
# Install / redeploy everything (UC namespace + app + jobs + dashboard)
set -a; source customer.env; set +a      # BUNDLE_VAR_* are read natively by the CLI
databricks bundle validate
databricks bundle deploy

# REQUIRED after deploy: `bundle deploy` leaves the app STOPPED with no active deployment.
# This starts it and activates the code. Also the way to pick up any app code change.
databricks bundle run labelbricks

# Jobs
databricks bundle run cv_ingest          # landing -> images + image_catalog (AI-tagged)
databricks bundle run cv_train_pipeline  # export COCO -> train -> register -> serving endpoint
databricks bundle run cv_clear           # reset annotation state before a demo

# Tests (pure-Python, no Databricks connection needed).
# pytest is not in a project venv — this invocation is the working one:
uv run --with pytest python -m pytest pipeline/tests/ -v
uv run --with pytest python -m pytest pipeline/tests/test_coco_utils.py -k rectangle

# LabelBricks local dev (see labelbricks/CLAUDE.md)
cd labelbricks && uv sync && uv run python app.py
```

Target workspace = the `DATABRICKS_CONFIG_PROFILE` in `customer.env` (no hardcoded profile).
CLI gotchas: `databricks jobs run-now` takes a positional job id (not `--job-id`). OAuth login
expires — re-auth with `databricks auth login --host <your-workspace-host>`. If the workspace has an IP
access list and the egress IP rotates off it, all CLI calls fail with an IP ACL error — wait or
allowlist.

## Architecture

Data flow across the components:

1. **Ingest** — files land in the `landing` UC Volume; the `cv_ingest` job
   (`pipeline/resources/ingest_job.yml`) runs `01_ingest_pipeline.py`, which copies images and
   extracts video frames into the `images` Volume, AI-tags each one via FMAPI (caption, tags,
   person_count, contains_faces, quality), and registers rows in the `image_catalog` Delta table.
   Run it on demand — there is deliberately no file-arrival trigger (it needs an S3
   `GetBucketNotification` grant that demo workspaces usually lack).
2. **Annotate** — the `labelbricks/` Flask app reads the `images` Volume and writes annotations as
   **JSON sidecars** under `{images}/.labelbricks/annotations/`. AI Suggest calls FMAPI Claude via a
   thin REST client.
3. **Export → train → serve** — the `cv_train_pipeline` job chains `02_export_coco.py` (annotation
   JSON → COCO), `03_train_detection.py` (SSDLite detector, MLflow run, UC model registration), and
   `04_deploy_serving.py` (alias the version, create/update a Model Serving endpoint).

### Storage: JSON on Volume, no database

Annotations live in one JSON file per annotated image. There is **no Postgres/Lakebase** — it was
removed deliberately, because provisioning it (SDK-only, plus an app-SP Postgres role grant) was the
single biggest source of install complexity and the JSON path was already complete.

Consequences to keep in mind when changing the app:
- **Review status** is a field inside each sidecar. `POST /api/image-statuses` reconstructs the
  sidebar badges by listing `.labelbricks/annotations/` and reading the matching sidecars in
  parallel. Do not add a shared index file — the app runs 4 gunicorn workers and would race on it.
- **Label autocomplete across users is gone.** Recently-used labels are `localStorage` only.

### The coordinate crux (most important cross-cutting invariant)

LabelBricks stores annotation coordinates in **scaled display space** (canvas pixels = image pixels ×
`display_scale`), and persists `imageWidth/imageHeight/displayScale` alongside. The COCO export
divides by `display_scale` to recover true image pixels. `pipeline/lib/coco_utils.py` is the
unit-tested converter; notebook `02_export_coco.py` mirrors it. Any change to how the app saves
coordinates must keep these in sync, and `pipeline/tests/test_coco_utils.py` must pass.

### Deployment shape

- Everything deploys through the **top-level** `databricks.yml` (bundle `cv_annotator`): the UC
  schema and volumes, the app, the jobs, and the dashboard. Variables (catalog, schema, volumes,
  cluster id, app name) live there; job YAMLs in `pipeline/resources/` reference them.
- **The single target has no `mode:` key on purpose.** `mode: development` prefixes resource names
  with `dev_<user>_`, which would create the customer's UC schema as `dev_someone_<schema>` now that
  the schema is a bundle resource.
- Volumes carry `lifecycle: prevent_destroy` — they hold customer images/exports. Never document or
  run `bundle destroy` against a customer deployment.
- All jobs run on an **existing all-purpose cluster** (`var.existing_cluster_id`) to avoid ~10-min
  job-cluster cold starts. The cluster is a standard (non-ML) runtime — notebooks `%pip install`
  torch/opencv themselves.
- The Lakeview dashboard JSON is deployed as an **artifact**, so bundle variables are *not*
  interpolated into its queries — they hardcode a catalog/schema. Find-and-replace when retargeting.

### Hard-won constraints (do not regress)

- Pin `numpy<2` in notebooks — newer opencv/onnx pull numpy 2 and break the runtime's pandas.
- Train with `weights=None` (random init) — pretrained-weight downloads hang on the egress-restricted
  cluster (caused a 40-min stall once).
- Training requires ≥2 labeled images and uses `drop_last=True` — SSDLite BatchNorm crashes on size-1
  batches.
- Do not reintroduce `databricks-openai` in the app — its transitive `databricks-vectorsearch` dep
  broke on an unpinned upgrade. AI Suggest uses databricks-sdk auth + raw `requests`
  (`libraries/ai_client.py`).
- Dependencies are lockfile-managed: edit `labelbricks/requirements.in`, regenerate the pinned
  `requirements.txt` with `uv pip compile requirements.in -o requirements.txt --python-version 3.11`.
- Do not reintroduce a database for annotations without a strong reason — the install simplicity is
  the point of the current design.
