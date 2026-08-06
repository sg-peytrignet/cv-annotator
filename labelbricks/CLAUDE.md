# LabelBricks

Lightweight image labeling web app built on Databricks Apps with Unity Catalog Volumes for data storage. Human-in-the-loop image annotation with AI-assisted labeling capabilities.

## Key Directives
1. DO NOT READ .env
2. No `.env` file is required — do not add `load_dotenv()` or `python-dotenv`

## Execution and Decisions
- When making architectural or design decisions consult with me
- Use subagents when tackling complex tasks

## Available Skills
The following project skills are available in `.claude/skills/`:

## Available Agents
The following subagents are available in `.claude/agents/`:

## Available Plugins
Check for plugins here: `.claude/settings.json`

## Tech Stack

- **Backend**: Python / Flask (served via Gunicorn)
- **Frontend**: Vanilla JS with Fabric.js (canvas-based annotation)
- **Platform**: Databricks Apps (serverless containerized deployment)
- **Storage**: Unity Catalog Volumes only — images plus one annotation JSON sidecar per image
  (`.labelbricks/annotations/<file>.json`). No database.
- **AI**: Databricks Foundation Model APIs via a thin REST client — databricks-sdk for auth + `requests` (Claude Sonnet vision model for AI-assisted labeling). `databricks-openai` was intentionally removed (see Lessons Learned).
- **Auth**: Databricks OAuth 2.0 / SSO (via app service principal + user authorization)
- **Config**: repo-root `databricks.yml` manifest (Databricks Asset Bundles — bundle `cv_annotator`, app source path `./labelbricks`) + `app.yaml` runtime config
- **Dev tooling**: `uv` for dependency management and virtual environment

## Project Structure

```
labelbricks/
├── CLAUDE.md                     # You are here
├── app.py                        # Flask application - main entry point
├── app.yaml                      # Databricks App runtime config (gunicorn command + env)
├── pyproject.toml                # uv/pip project config with dependencies
├── requirements.in               # Direct deps (edit this, then regenerate the pin file with uv)
├── requirements.txt              # Pinned lockfile output (what DABs/the app installs)
├── template.env.txt              # Reference doc — no .env file needed
├── libraries/
│   └── ai_client.py              # FMAPI vision client — image → bounding box suggestions
├── templates/
│   ├── index.html                # Main annotation UI (three-panel Fabric.js canvas)
│   └── set_volume.html           # Styled landing page
├── static/
│   ├── style.css                 # Databricks-aligned design system (CSS custom properties)
│   ├── js/
│   │   ├── app.js                # Main entry point — LabelBricksApp orchestrator
│   │   ├── api-client.js         # Centralized fetch wrapper for backend APIs
│   │   ├── canvas-manager.js     # Fabric.js canvas lifecycle + image loading
│   │   ├── tool-manager.js       # Tool state machine + keyboard shortcuts
│   │   ├── annotation-store.js   # In-memory annotation model + JSON serialization
│   │   ├── label-manager.js      # Label class input + color palette + recent chips
│   │   ├── label-popup.js        # Floating popup for post-draw labeling
│   │   ├── sidebar.js            # Image queue + lazy thumbnails + status badges
│   │   ├── volume-browser.js     # Cascading catalog→schema→volume→directory modal
│   │   ├── undo-manager.js       # Snapshot-based undo/redo (Ctrl+Z/Y)
│   │   ├── ai-suggest.js         # AI suggestion lifecycle — render, accept/edit/reject, threshold
│   │   └── tools/
│   │       ├── select.js         # Select/move tool
│   │       ├── rectangle.js      # Rectangle draw tool
│   │       ├── circle.js         # Ellipse draw tool
│   │       ├── polygon.js        # Click-to-add-vertices polygon tool
│   │       └── freehand.js       # Freehand drawing tool
│   ├── images/                   # App logos and assets
│   └── test/imgs/                # Sample images for testing
```

Note: the DAB manifest (`databricks.yml`) and the COCO-converter unit tests live at the **repo root**
(`../databricks.yml`, `../pipeline/tests/`), not in this directory.

## Key Commands

```bash
# Local development (uv)
uv sync                                    # Install dependencies
uv run python app.py                       # Flask dev server on :5000

# If the DEFAULT CLI profile is not the target workspace:
DATABRICKS_CONFIG_PROFILE=<profile> uv run python app.py

# Databricks CLI (bundle commands run from the REPO ROOT, not this directory)
databricks auth login --host <your-workspace-host>   # OAuth U2M; prompts for a profile name
cd .. && databricks bundle validate
cd .. && databricks bundle deploy

# REQUIRED after every deploy (by resource key, not app name): deploy leaves the app
# stopped with no active deployment, and this is also how app code changes go live.
cd .. && databricks bundle run labelbricks

# Testing (COCO converter unit tests live at the repo root)
uv run --with pytest python -m pytest ../pipeline/tests/ -v

# Dependency changes: edit requirements.in, then regenerate the pinned requirements.txt
uv pip compile requirements.in -o requirements.txt --python-version 3.11
```

## Architecture Decisions

- **Refer to documentation and Cookbook for modern patterns**: Look at the current documentation [link](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-development) and our Cookbook [link](https://apps-cookbook.dev/docs/intro) to understand modern patterns for Databricks Apps development
- **Flask over Streamlit/Gradio**: We need precise canvas control (Fabric.js) for bounding boxes, polygons, and freehand drawing that Streamlit widgets cannot provide. Flask gives full HTML/JS control.
- **UC Volumes for images**: Images are governed assets in Unity Catalog Volumes. Volumes provide Unity Catalog lineage, access control, and auditability. Never use DBFS or local-only storage.
- **AI suggestions are non-authoritative**: The AI model proposes labels/bounding boxes, but the human reviewer always has final say. AI predictions are rendered as dashed overlays that the user accepts, modifies, or dismisses.
- **No .env file dependency**: Auth uses Databricks CLI profile (local) or SP OAuth (deployed). User identity from `w.current_user.me()` (local) or `X-Forwarded-*` headers (deployed). Volume path selected from UI. No `python-dotenv` needed.
- **User-selected volume**: the user picks catalog/schema/volume in the UI; the frontend passes
  `volumePath` on every call (Flask `session` is a fallback only). Different users can work on
  different volumes.
- **JSON sidecars, no database**: annotations persist as one JSON file per image on the Volume.
  Lakebase/Postgres was removed deliberately — provisioning it (SDK-only, plus an app-SP Postgres
  role grant the app can't do itself) was the biggest source of install complexity, and the JSON
  path was already complete. Do not reintroduce a database without a strong reason.
- **DABs for deployment**: the repo-root `databricks.yml` declares the app resource with UC Volume `uc_securable`. Use `databricks bundle deploy` instead of `databricks sync --watch`.

## Testing Deployments

1. Make sure to test UI/UX changes locally when possible so that feedback can be given to guide the overall experience
2. When testing the deployment in Databricks, use the FE VM tools (i.e. `/databricks-fe-vm-workspace-deployment`) to provision this app to a workspace for testing

## How it got here (brief)

The app was modernized in stages: OAuth auth + DABs manifest + zero `.env` dependency; a UI overhaul
(Databricks theme, three-panel layout, volume browser, 5 annotation tools, ES6 modules); AI-assisted
labeling via FMAPI vision; then a Lakebase Postgres storage layer for annotation metadata.

**That Lakebase layer has since been removed** in favour of JSON-on-Volume, to make the install a
single `databricks.yml` + `databricks bundle deploy` with nothing to provision. What was lost with it:
cross-user label autocomplete (now `localStorage` recent-chips only) and the audit log. What was
kept: cross-session review status, which lives in each JSON sidecar.

## Code Style

- Python: Follow PEP 8. Use type hints on all function signatures. Prefer `logging` over `print()`.
- JavaScript: ES6+ modules. No build step — loaded via `<script type="module">`. 16 files in `static/js/`.
- HTML/CSS: Minimal templating with Jinja2. Dark theme. Mobile-responsive where practical.
- Error handling: Always wrap Databricks SDK calls in try/except. Log the error and return a user-friendly message. Never expose stack traces to the frontend.
- Environment detection: Use `IS_DEPLOYED = os.getenv("DATABRICKS_APP_NAME") is not None`. Do not check for .env file existence.

## Current Patterns

- `WorkspaceClient()` is initialized once globally — auto-detects CLI profile (local) or SP OAuth (deployed).
- `get_user_info()` returns user identity from `X-Forwarded-*` headers (deployed) or `w.current_user.me()` (local, cached after first call).
- **Volume path is tracked on the frontend** (`LabelBricksApp.volumePath`) and passed in every API call. Flask session is a fallback only — do not rely on it for deployed apps.
- `_ensure_volume_dirs(volume_path)` pre-creates `.labelbricks/annotations/` and `.labelbricks/composites/` directories before first upload. Uses in-memory `_dirs_created` set.
- `app.py` calls `w.files.*` directly everywhere — there is no VolumeClient wrapper any more.
- Frontend is 16 ES6 modules loaded via `<script type="module">` from `static/js/app.js` entry point. No build step.
- Fabric.js v4.6.0 canvas managed by `CanvasManager`. Background image set via `setBackgroundImage` (non-selectable).
- Tool state machine in `ToolManager` — 5 tools (select, rectangle, circle, polygon, freehand) with keyboard shortcuts 1-5.
- `LabelPopup` shows after drawing or selecting annotations for post-draw labeling.
- Save has retry logic (3 attempts, 1s delay) and a saving overlay modal to prevent multi-clicks.
- **AI suggestions are NOT in `AnnotationStore` until accepted.** They live as Fabric objects with `excludeFromExport = true` (invisible to undo snapshots) and are tracked in `AISuggestManager._suggestions`. On accept, they convert to regular annotations with `createdBy: 'ai-accepted'`.
- **FMAPI calls go through a thin REST client** in `libraries/ai_client.py` — `WorkspaceClient()` supplies auth, `requests` POSTs the OpenAI-compatible chat payload to the serving-endpoint invocations URL. No `databricks-openai` dependency.
- **AI bounding boxes use percentage coordinates (0-100).** Frontend translates to canvas pixels: `canvas_x = (pct / 100) * naturalWidth * canvasManager.getScale()`.
- **Large images auto-compressed** before FMAPI calls. `_compress_image()` in `ai_client.py` uses Pillow to progressively resize/compress images >2.5MB to stay under the 4MB FMAPI request limit.
- **One JSON sidecar per image is the only store.** `POST /api/save` writes
  `{volume}/.labelbricks/annotations/{filename}.json` containing filename, reviewer, timestamp,
  status, notes, `image_width`/`image_height`/`display_scale`, and the shape list; a failed upload is
  a 500 (there is no second store to fall back on). `GET /api/annotations` reads that file back.
- **`POST /api/image-statuses` reconstructs the sidebar badges** from the sidecars: one
  `list_directory_contents` on `.labelbricks/annotations` bounds the work by *annotated* count, then
  the matching sidecars are read via `ThreadPoolExecutor` (8 workers) and only `status` is taken.
  A missing directory (fresh volume) returns `{}` — never an error.
- **Sidecars are named by basename** (`{filename}.json`) but the frontend keys statuses by **full
  volume path**. `/api/image-statuses` therefore maps basename → the full path from the posted
  `filePaths`. Return basename keys and every badge silently stays "pending".
- **Never add a shared index file for statuses.** The app runs 4 gunicorn workers; a
  read-modify-write index would race. Derive state from the directory listing instead.
- **Label suggestions are `localStorage` only** (`labelbricks-label-classes` recent chips). There is
  no server-side autocomplete endpoint — the trade-off accepted when Lakebase was removed.

## Lessons Learned

- **`databricks bundle schema` is the source of truth** for DABs YAML fields. The `uc_securable` permission values are `READ_VOLUME` / `WRITE_VOLUME` (not `READ_WRITE`).
- **`WorkspaceClient()` credential resolution**: Direct params > env vars (`DATABRICKS_HOST`, `DATABRICKS_TOKEN`) > CLI profile. If DEFAULT profile is broken, set `DATABRICKS_CONFIG_PROFILE` in the shell.
- **Don't add `.env` loading for local dev** — it creates a false dependency. CLI profile + SDK `current_user.me()` + UI-selected volume covers all needs.
- **`uv run --env-file .env`** is available if env vars are ever needed, but the app itself should not depend on `.env`.
- **Flask session is unreliable in deployed Databricks Apps.** The `app.secret_key = os.urandom()` changes on restart, and Gunicorn workers may not share session state. Always pass critical context (like `volumePath`) from the frontend in request bodies/query params, with session as fallback only.
- **Pre-create Volume directories before upload.** `w.files.upload()` does not auto-create parent directories. Use `w.files.create_directory()` wrapped in try/except (idempotent — succeeds if already exists).
- **Draw-then-label is the natural annotation UX.** Users draw a shape first, then want to label it. A floating label popup near the annotation (with recent label chips) is the right pattern. Do not require label selection before drawing.
- **App deploy IS a two-step — verified on CLI v0.299 against a live workspace (Aug 2026).**
  `databricks bundle deploy` syncs the source and creates the app resource, but leaves it
  `compute_status: STOPPED`, `app_status: UNAVAILABLE`, and **`active_deployment: null`** — the app
  is not merely serving stale code, it is not serving at all. `databricks bundle run labelbricks`
  then starts the compute and creates the deployment (observed: `SUCCEEDED`, with
  `source_code_path` = `/Workspace/Users/<me>/.bundle/cv_annotator/default/files/labelbricks`,
  ~3–5 min including `pip install`). It needs no hand-built `/Workspace/Users/.../.bundle/...` path,
  which is what made the old `databricks apps deploy --source-code-path ...` invocation so
  error-prone. Always document both commands; never present `bundle run` as conditional.
- **FMAPI has a ~4MB request body limit.** Base64-encoding inflates image size by ~33%, so a 3MB image becomes ~4MB in the request. Always compress images >2.5MB raw bytes before sending.
- **FMAPI pay-per-token endpoints are workspace-shared.** `databricks-claude-sonnet-4-5` does not need a `serving_endpoint` resource in `databricks.yml`. The app SP has access by default. Only add a resource declaration if you hit permission errors.
- **Do not reintroduce `databricks-openai`.** It transitively pulls in `databricks-vectorsearch`, whose unpinned 0.74 upgrade removed `VectorSearchIndex` and broke AI Suggest in production. The thin REST client (`WorkspaceClient()` auth + `requests`) needs no extra auth setup and has no fragile transitive deps.
- **Use `excludeFromExport = true` on temporary Fabric.js objects** (like AI suggestion overlays) to keep them out of `canvas.toJSON()` and the undo snapshot stack. This is cleaner than filtering them out in the undo manager.
- **Vision models return bboxes reliably with explicit format instructions.** Include a concrete JSON example in the prompt and request "no markdown, no explanation." Temperature 0.1 improves consistency. Still handle markdown code fences in parsing as a fallback.
- **Percentage-based coordinates are resolution-independent.** When the AI returns bboxes as 0-100% of image dimensions, the frontend handles all display scaling via `canvasManager.getScale()` and natural image dimensions. This decouples model output from canvas/display size.
- **Why Lakebase was removed.** It is not a DABs resource (`postgres_projects` etc. exist in the
  schema but the app-facing setup isn't declarative), so it needed SDK provisioning *plus* a Postgres
  role the app SP cannot grant itself — the project owner had to run
  `databricks_create_role(...)` + GRANTs out of band. That chain forced an installer script, which
  forced a local Python/`psycopg` prerequisite, which forced a Dockerfile for Windows users. Since
  the JSON-on-Volume path was already complete, dropping Postgres removed all four. If a future
  requirement genuinely needs SQL-queryable labels, prefer reading the exported COCO/JSON into Delta
  over reintroducing an operational database into the install path.
- **`resources.schemas` and `resources.volumes` are supported** (CLI v0.299) — the UC namespace is
  bundle config, not shell commands. Give volumes `lifecycle: prevent_destroy` since they hold
  customer data, and do **not** use `mode: development` on a target that owns a UC schema (it
  prefixes the schema name with `dev_<user>_`). Verified live: with no `mode:` key the schema and
  volumes are created with exactly the configured names, unprefixed.
- **Reference bundle-owned resources by resource path, not by rebuilding their name from variables.**
  The app's volume grant was `securable_full_name: ${var.catalog}.${var.schema}.${var.images_volume}`,
  which resolves to a *plain string*. Terraform therefore saw no dependency between the app and the
  volume, created them concurrently, and the deploy failed with
  `failed to create app: Volume '<catalog>.<schema>.images' does not exist`. Using
  `${resources.volumes.images.id}` (the id *is* `catalog.schema.name`) emits a real
  `${databricks_volume.images.id}` reference and forces volume-before-app ordering. Inspect
  `.databricks/bundle/<target>/terraform/bundle.tf.json` to tell the two apart — but note
  **`bundle validate` does not regenerate that file**; run `databricks bundle plan` to refresh it.
