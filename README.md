# CV Annotator on Databricks

An end-to-end, **governed** computer-vision annotation workflow that deploys into a Databricks
workspace with **two commands** (`bundle deploy` then `bundle run labelbricks`): ingest images/video →
govern in Unity Catalog → annotate (**LabelBricks** app + AI-assisted labeling) → export COCO → train
a detector → register to UC → deploy to a Model Serving endpoint.

The whole thing is one Databricks Asset Bundle. All customer-specific values live in **one file**
(`customer.env`) — you do not edit `databricks.yml` or `app.yaml`.

Annotations are stored as **JSON sidecars on the images Volume**
(`.labelbricks/annotations/*.json`) — there is no database to provision.

Follow steps 0–5 below in order for a fresh workspace.

---

## Prerequisites

**On your machine:** the **Databricks CLI v0.299+** (installed in Step 0).

**In the target workspace** — the bundle cannot turn these on:

- [ ] **Unity Catalog**, with a **catalog that already exists** (it needs a storage location; the
      bundle creates the schema + volumes, but not the catalog).
- [ ] **Databricks Apps** enabled.
- [ ] **Foundation Model APIs** — pay-per-token `databricks-claude-sonnet-4-5` (powers AI Suggest and
      ingest tagging).
- [ ] An **all-purpose cluster** to point jobs at (avoids ~10-min job-cluster cold starts).
- [ ] A **SQL warehouse** (for the dashboard).
- [ ] Permission to create schemas/volumes in that catalog, and to create apps and jobs.

---

## Step 0 — Install and authenticate the Databricks CLI

You need the Databricks CLI to deploy the application https://docs.databricks.com/aws/en/dev-tools/cli/install

Check the version, then log in to the workspace you want to deploy into:

```bash
databricks --version                      # must be >= 0.299.0

databricks auth login --host https://<your-workspace-host>
```

This opens a browser for SSO, then prompts you for a **profile name** (it suggests one based on the
host — press Enter to accept, or type your own, e.g. `cv-demo`). Remember whatever name you choose:
every command below takes `--profile <your-profile>`, shown as `<PROFILE>` throughout. The profile is
saved to `~/.databrickscfg`.

Verify it worked — this must print your email:

```bash
databricks current-user me --profile <PROFILE>
```

If you'd rather not repeat `--profile` on every command, export it once for the session and drop the
flag (this is what `customer.env` does for you in Step 1):

```bash
export DATABRICKS_CONFIG_PROFILE=<PROFILE>
```

> **The OAuth token expires.** When commands start failing with *"refresh token is invalid"*, just
> re-run the `databricks auth login` above. And if you skip the profile entirely, you'll see a
> "cannot configure default credentials" error — that just means the CLI doesn't know which
> workspace to use.
>
> If the workspace has an **IP access list**, the machine you deploy from must be on it, or every
> CLI call fails with an IP ACL error.

---

## Step 1 — Configure

```bash
cp customer.env.example customer.env
```
Open the customer.env file and input your values

Only these four values are required

| Value | How to get it |
|---|---|
| `DATABRICKS_CONFIG_PROFILE` | The profile name you chose in Step 0 (`<PROFILE>`). |
| `BUNDLE_VAR_catalog` | An existing UC catalog — `databricks catalogs list --profile <PROFILE>` |
| `BUNDLE_VAR_existing_cluster_id` | `databricks clusters list --profile <PROFILE>` |
| `BUNDLE_VAR_warehouse_id` | `databricks warehouses list --profile <PROFILE>` (SQL Warehouses > your warehouse > Connection details) |

## Step 2 — Deploy

```bash
set -a; source customer.env; set +a     # BUNDLE_VAR_* are read natively by the CLI
databricks bundle validate              # catches config errors before touching the workspace
databricks bundle deploy
databricks bundle run labelbricks       # REQUIRED: starts the app and activates its code
```

`deploy` creates, in one shot: the UC **schema**, the **`landing` / `images` / `exports` volumes**,
the **app** (with a `WRITE_VOLUME` grant on `images`), the three **jobs**, and the **dashboard**. It
is idempotent — re-run it after any code change.

> **Both commands are needed.** `bundle deploy` creates the app resource but leaves it **stopped with
> no active deployment** — verified on CLI v0.299. `bundle run labelbricks` starts the compute and
> activates the source, and it is what you re-run after any app code change too. `labelbricks` is the
> bundle *resource key*, not the app name, so it does not change when you set `BUNDLE_VAR_app_name`.
> Expect roughly 3–5 minutes on first start (compute provisioning + `pip install`).

Get the app URL (also printed at the end of `bundle run`):

```bash
databricks apps get "$BUNDLE_VAR_app_name" -o json | grep '"url"'
```
---

## Step 3 — Get images in (ingest)

There are **two volumes with different jobs**, and this is the part most easily got wrong:

| Volume | Role |
|---|---|
| `landing` | **You upload here.** Raw images + videos, untouched. |
| `images` | **The app reads here.** Curated output of the ingest job, plus annotation sidecars. |


**3a. Upload to `landing`** — via the Catalog Explorer UI (*Catalog → your catalog → schema →
`landing` → Upload*), or the CLI:

```bash
databricks fs cp ./my-images \
  "dbfs:/Volumes/$BUNDLE_VAR_catalog/$BUNDLE_VAR_schema/landing/" \
  --recursive --profile "$DATABRICKS_CONFIG_PROFILE"
```

**3b. Run the ingest job:**
You can run the ingest job either from the UI (Jobs and pipeline > cv-ingest > run) or via this command:

```bash
databricks bundle run cv_ingest
```

It copies images into `images`, extracts video frames (one row per frame), **AI-tags** every image
via FMAPI Claude (caption, object tags, person count, face flag, quality), and registers everything
in the `image_catalog` Delta table. It is idempotent — files already ingested are skipped, so you can
re-run it after each upload..


Confirm it worked:
```sql
SELECT filename, tags, person_count, contains_faces, quality
FROM <catalog>.<schema>.image_catalog
```

## Step 4 — Annotate

Open the app URL → **Browse** → pick `<catalog>.<schema>.`**`images`** → click an image → draw a box,
label it, and try **AI Suggest**. Hit **Save**: annotations are written as a JSON sidecar on the
volume. Reload the page — the sidebar badge should still show your review status.

## Step 5 — Train and serve the model (optional)

Label at least **2** images, then:

```bash
databricks bundle run cv_train_pipeline
```

Three chained tasks: **export** annotations → COCO, **train** an SSDLite detector and register it to
Unity Catalog, then **deploy** that version to a Mosaic AI **Model Serving endpoint** (aliased
`@champion`) and sanity-check it with a live call. The notebook prints the invocations URL:

```
https://<workspace>/serving-endpoints/<model>-endpoint/invocations
```

> The endpoint is created with **`scale_to_zero_enabled=true`**, so it costs nothing while idle but
> takes a few minutes to cold-start on the first request. Before a live demo, re-run the `deploy`
> task with `scale_to_zero: "false"` to keep it warm.

---

## What gets deployed

| Component | What it is |
|-----------|-----------|
| **UC namespace** | Schema + `landing` / `images` / `exports` volumes, as bundle resources. |
| **LabelBricks app** | Flask + Fabric.js annotation UI on Databricks Apps. Draw boxes/polygons/etc., **AI Suggest** proposes labels via Foundation Model API (Claude). |
| **Ingest job** (`cv_ingest`) | Copies uploads + extracts video frames into the `images` volume, **AI-tags** each image (caption, object tags, person count, face flag, quality), and registers rows in an `image_catalog` Delta table. |
| **ML pipeline job** (`cv_train_pipeline`) | Export annotations → COCO → train an SSDLite detector → register to UC → deploy to a Model Serving endpoint. |
| **Reset job** (`cv_clear`) | Wipes annotations + exports so every image shows as unlabeled again. |
| **Dataset-health dashboard** | Lakeview dashboard over `image_catalog` (needs `BUNDLE_VAR_warehouse_id`). |

---

## The notebooks

All live in `pipeline/notebooks/`. They are plain Databricks notebooks — the jobs run them in order,
but each one is independently runnable with widgets if you want to step through it in the UI.

| Notebook | Job / task | What it does |
|---|---|---|
| **`01_ingest_pipeline.py`** | `cv_ingest` | Reads new files from `landing`. Validates images with Pillow and copies them to `images`; extracts video frames with OpenCV at a fixed interval (one `image_catalog` row per frame). **AI-tags** every image via FMAPI Claude — caption, object tags, person count, face flag, quality — in parallel (`tag_parallelism`, ~5× faster than serial). Writes it all to the `image_catalog` Delta table. **Idempotent**: skips source files already in the catalog, so re-running after each upload is safe. |
| **`02_export_coco.py`** | `cv_train_pipeline` → `export` | Reads every `.labelbricks/annotations/*.json` sidecar and builds a **COCO detection dataset** (`annotations.json` + `images/` + `metadata.json`) under `exports/coco_<timestamp>/`. Converts coordinates out of scaled display space by dividing by `display_scale`. Copies the referenced images alongside so the dataset is self-contained. Fails loudly if nothing is labeled yet. |
| **`03_train_detection.py`** | `cv_train_pipeline` → `train` | Fine-tunes a torchvision **SSDLite MobileNetV3** detector on the COCO export, tracking params/loss in **MLflow**, then registers the model to the **Unity Catalog** registry as `<catalog>.<schema>.<model_name>`. Also logs a `label_map.json` artifact and hands the new version number to the next task. Deliberately a *dummy* model — the point is the governed train → track → register path, not accuracy. |
| **`04_deploy_serving.py`** | `cv_train_pipeline` → `deploy` | Sets a **`@champion` alias** on the freshly registered version, then creates (or updates in place) a **Mosaic AI Model Serving** endpoint for it and waits for readiness. Prints the REST invocations URL and runs a sanity inference. Idempotent — re-running rolls the endpoint onto the newest version. |
| **`05_visualize_coco.py`** | — (run manually) | QA helper: renders a grid of exported images with their bounding boxes drawn on, plus per-class box counts. Use it to eyeball whether the exported COCO coordinates actually line up with the images — the fastest way to catch a coordinate-scaling problem. |
| **`clear_annotations.py`** | `cv_clear` | Reset for demos: deletes the annotation sidecars + composites, clears the `exports` volume, and optionally (`clear_model=true` / `clear_images=true`) deletes the registered model and wipes `images`/`landing` + `image_catalog`. |

`pipeline/lib/coco_utils.py` holds the unit-tested coordinate/COCO conversion logic that
`02_export_coco.py` mirrors; `pipeline/tests/` covers it.

---


---

## Where annotations live

The app writes one JSON sidecar per annotated image:

```
/Volumes/<catalog>/<schema>/images/
  .labelbricks/annotations/<image>.jpg.json    # boxes, labels, status, notes, image dims
  .labelbricks/composites/<image>.jpg.png      # flattened preview
```

---

## Repository layout

```
customer.env.example      Per-customer config template  ->  copy to customer.env (gitignored)
databricks.yml            The Asset Bundle (UC namespace + app + jobs + dashboard)

labelbricks/              Annotation app (Flask + Fabric.js)
  app.py                    Flask entry point
  app.yaml                  App runtime config (gunicorn command + env)
  libraries/ai_client.py    FMAPI vision client (AI Suggest)

pipeline/
  notebooks/              01 ingest · 02 export COCO · 03 train · 04 deploy serving · 05 visualize
                          clear_annotations (reset)
  resources/              DABs job definitions (ingest, train, clear, dashboard)
  dashboards/             Lakeview dashboard as code
  lib/coco_utils.py + tests/   COCO converter + unit tests

scripts/
  seed_demo_data.py       Upload a local folder of images/video into the landing volume
  seed_annotations.py     Pre-bake annotation sidecars (to train without labeling by hand)

docs/
  demo_runbook.md         Demo-day script (live vs pre-baked steps)
  architecture.drawio     Architecture diagram
```

---

## Common operations

All of these need the config in the environment first (`set -a; source customer.env; set +a`).

```bash
# Re-deploy after a code change (app + jobs)
databricks bundle deploy

# Restart the app (picks up newly deployed code)
databricks bundle run labelbricks

# Ingest whatever is sitting in the landing volume
databricks bundle run cv_ingest

# Run the export -> train -> register -> serve pipeline
databricks bundle run cv_train_pipeline

# Reset annotation state before a demo
databricks bundle run cv_clear

# Local app dev (no deploy)
cd labelbricks && uv sync && DATABRICKS_CONFIG_PROFILE=<profile> uv run python app.py

# Unit tests (pure Python, no workspace needed)
uv run --with pytest python -m pytest pipeline/tests/ -v
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Not authenticated` / `refresh token is invalid` | Re-run `databricks auth login --host <your-workspace-host>` (OAuth expires). |
| `cannot configure default credentials` | The CLI doesn't know which workspace to use — `export DATABRICKS_CONFIG_PROFILE=<PROFILE>` or pass `--profile <PROFILE>`. |
| Resource named literally `${var.catalog}`, or "variable has not been defined" | The config isn't in the environment. Run `set -a; source customer.env; set +a` in the *same* shell, then re-deploy. |
| `unknown field: schemas` / `volumes` on validate | CLI too old. `databricks --version` must be ≥ 0.299.0. |
| `catalog ... does not exist` | The catalog must be created first (it needs a storage location); the bundle only creates the schema + volumes. |
| `PERMISSION_DENIED` creating the schema or volume | Your user needs `CREATE SCHEMA` / `CREATE VOLUME` on the catalog. |
| `Source IP ... blocked by Databricks IP ACL` | The workspace IP access list rejected your egress IP — allow-list it or wait for rotation. |
| App blank / 500 | `databricks apps logs <app-name>`; confirm the `labelbricks-volume` resource is bound. |
| App shows no images | You're likely pointed at `landing`. Pick the **`images`** volume — and run `cv_ingest` if you haven't. |
| Volume browser is empty / "Unity Catalog denied access" | The app browses UC as its **own service principal**, and the `WRITE_VOLUME` grant on `images` is normally enough — the browser was verified working with no extra grants. If your metastore is locked down more tightly, the cascading picker also needs `USE CATALOG` on the catalog and `USE SCHEMA` on the schema: get the SP id from `databricks apps get <app-name> -o json` (`service_principal_client_id`) and run `GRANT USE CATALOG ON CATALOG <catalog> TO \`<sp-id>\`;`. Only the `images` volume appears by design (least privilege). |
| Ingest job fails on `dbutils.fs` / opencv | The cluster in `BUNDLE_VAR_existing_cluster_id` must be running and UC-enabled; the notebook `%pip install`s opencv with `numpy<2` pinned. |
| App stopped / `UNAVAILABLE`, or still running old code after deploy | `databricks bundle run labelbricks` (the bundle resource key, not the app name). `bundle deploy` alone never starts the app. |
| AI Suggest fails | Confirm FMAPI `databricks-claude-sonnet-4-5` is available; large images are auto-compressed. |
| Status badges all show "pending" | Statuses come from the annotation sidecars — confirm `.labelbricks/annotations/` exists and the app SP can read the images volume. |
| Export job finds no labels | Annotate at least one image first; training additionally needs **≥2** labeled images. |
| Dashboard widgets error | Run `cv_ingest` at least once — the widgets query `image_catalog`, which the ingest job creates. |

For the hard-won constraints (numpy pin, `weights=None`, batch-size ≥2) and app-internals notes,
see **`labelbricks/CLAUDE.md`** and **`CLAUDE.md`**.

---
