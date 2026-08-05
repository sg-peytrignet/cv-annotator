# CV Annotator — Runbook

This is both the **setup guide** and the **demo-day script**. The flow it implements:
ingest → automated pipeline (images + video frames) → UC governance → annotate (LabelBricks + AI
Suggest) → export COCO → train a detector → register to UC → serve it behind a REST endpoint.

Annotations are stored as **JSON sidecars on the images Volume** — there is no database to
provision. See `README.md` for the install contract.

---

## Part 1 — One-time setup (do this days before, not live)

### 1.1 Prereqs
- Databricks CLI (v0.299+) authenticated to the target workspace:
  `databricks auth login --host <your-workspace-host>` (it prompts for a profile name).
- The workspace needs **Unity Catalog** (with an existing catalog), **Databricks Apps**, **FMAPI
  Claude** (`databricks-claude-sonnet-4-5`), an **all-purpose cluster**, and a **SQL warehouse**.
- Local `uv` + Python 3.11 only if you want to run the seed scripts or unit tests.

### 1.2 Deploy everything
```bash
cp customer.env.example customer.env   # then edit: profile, catalog, schema, app name, cluster, warehouse
set -a; source customer.env; set +a
databricks bundle validate
databricks bundle deploy
```

This creates the UC schema + `landing`/`images`/`exports` volumes, the app, the three jobs, and the
dashboard. Get the app URL:

```bash
databricks apps get "$BUNDLE_VAR_app_name" -o json | grep '"url"'
```

If the app serves stale code after a redeploy, restart it: `databricks bundle run labelbricks`.

Open the app → **Browse** → pick `<catalog>.<schema>.images` → annotate a test image → **AI Suggest**
to confirm both the Volume wiring and FMAPI access.

### 1.3 Seed demo data
```bash
# Real images make the best demo:
python scripts/seed_demo_data.py --profile <PROFILE> --catalog <catalog> --schema <schema> \
    --local-dir ~/my_samples

# Or smoke-test the plumbing with synthetic placeholders:
python scripts/seed_demo_data.py --profile <PROFILE> --catalog <catalog> --schema <schema> --generate 12
```

Then process them (the job is on-demand — there is no file-arrival trigger):
```bash
databricks bundle run cv_ingest
```
Confirm `image_catalog` has the AI-enrichment columns populated:
`SELECT filename, tags, person_count, contains_faces, quality FROM <catalog>.<schema>.image_catalog`

### 1.4 Pre-bake the back half (train once so the demo is reliable)
Label ~15–30 images in the app first (training needs **≥2** minimum). To skip manual labeling:
```bash
python scripts/seed_annotations.py --profile <PROFILE> --catalog <catalog> --schema <schema> --limit 12
```

Then:
```bash
databricks bundle run cv_train_pipeline   # export -> train -> register -> serve
```
Confirm: a COCO export under `exports/coco_*`, an MLflow run, a registered model
`<catalog>.<schema>.<model_name>`, and a Model Serving endpoint `<model_name>-endpoint` that is READY.

### 1.5 Reset before the demo
```bash
databricks bundle run cv_clear    # wipes annotation sidecars + exports
```
Every image shows as unlabeled/pending again. Pass `clear_images=true` / `clear_model=true` as task
parameters for a full dataset swap.

---

## Part 2 — Demo-day script (~30-40 min)

Legend: **[LIVE]** = run in front of them · **[PRE-BAKED]** = show the result you produced in 1.4.

| # | Step | Mode | What to show / say |
|---|------|------|--------------------|
| 1 | Ecosystem overview | slide | `docs/architecture.drawio`. Frame the governed CV lifecycle end to end: ingest → govern → annotate → train → serve. |
| 2 | Ingestion | **[LIVE]** | Drop a few images + a short video into the `landing` Volume (UI upload or `seed_demo_data.py`). |
| 3 | Automated pipeline | **[LIVE]** | Run `databricks bundle run cv_ingest`. Every image is **AI-tagged at ingest** (FMAPI Claude: caption, object tags, person count, face flag, quality → `image_catalog` columns). Query it: `SELECT filename, tags, contains_faces FROM image_catalog`, then a tag search (`array_contains(tags, 'keyboard')`). Talk track: *"your dataset became searchable for free, and video became frames automatically."* |
| 4 | Governance | **[LIVE]** | In Catalog Explorer: the `images` Volume, `image_catalog` table, lineage, and access control. |
| 5 | Annotate | **[LIVE]** | LabelBricks: draw a box, then **AI Suggest** → accept a suggestion. The "wow". Powered by FMAPI Claude (GenAI vision). Keep one labeled image ready in case the model is slow. |
| 5b| Durable labels | **[LIVE]** | Show the JSON sidecar that just appeared under `images/.labelbricks/annotations/` in Catalog Explorer — labels are governed UC data, versioned with the images. Reload the page to show the **reviewed** badge persisting. |
| 6 | Export → COCO | **[PRE-BAKED]** (or live) | Open `02_export_coco` output `metadata.json`; explain training-ready COCO. |
| 7 | Train + register | **[PRE-BAKED]** | MLflow run (metrics) + the registered detector in UC; lineage from data → model. |
| 8 | Serve the model | **[PRE-BAKED]** | The Model Serving endpoint: the `@champion` alias in UC, then a live `curl` against the invocations URL returning predictions. Talk track: *"same lineage from the annotated image all the way to the served prediction — and a retrain just moves the alias, no client change."* |
| 9 | Dataset health | slide/**[LIVE]** | The Lakeview dashboard over `image_catalog` — ingest volume, AI quality flags, top tags. |
| 10| Cost wrap | slide | Serverless app + pay-per-token FMAPI + small jobs → predictable cost. |

**Reliability tips**
- Have LabelBricks already open with the images volume pre-selected.
- Keep one labeled image ready in case AI Suggest is slow.
- Run live only steps 2–5; everything after is pre-baked artifacts you walk through.

---

## Troubleshooting
- **App blank / 500** → `databricks apps logs <app-name>`; confirm the `labelbricks-volume` resource is bound.
- **App serving old code** → `databricks bundle run labelbricks`.
- **AI Suggest fails** → confirm FMAPI `databricks-claude-sonnet-4-5` access; large images auto-compress.
- **Status badges all "pending"** → statuses come from the sidecars; confirm
  `images/.labelbricks/annotations/` exists and the app SP can read the volume.
- **opencv on the job cluster** → the notebook `%pip install`s `opencv-python-headless` with `numpy<2`
  pinned; do not unpin (numpy 2 breaks the runtime's pandas).
- **COCO boxes look shifted** → the `display_scale` conversion is the usual cause; run
  `uv run --with pytest python -m pytest pipeline/tests/ -v`.
- **Export finds no labels** → annotate at least one image (training needs ≥2).
- **Dashboard widgets error** → run `cv_ingest` once; the widgets query `image_catalog`.
- **IP access list** → if the workspace has one and your egress IP rotates off it, all CLI calls are
  rejected until it rotates back or is allowlisted.
