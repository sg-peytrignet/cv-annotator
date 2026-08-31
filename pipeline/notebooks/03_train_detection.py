# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Train a (dummy) detection model + register to Unity Catalog
# MAGIC
# MAGIC Demo steps 7–8. Loads the COCO export, fine-tunes a small **torchvision** detector
# MAGIC (`fasterrcnn_resnet50_fpn`), tracks everything in **MLflow**, and registers the model to the
# MAGIC **Unity Catalog model registry** (`<catalog>.<schema>.<model>`) to show governed model lineage.
# MAGIC
# MAGIC It's a *dummy* model — overfitting on a handful of labeled images is fine; the point is the
# MAGIC governed train → track → register pipeline, not accuracy.
# MAGIC
# MAGIC **Compute:** run on a **Databricks ML Runtime** cluster (torch/torchvision/mlflow preinstalled).
# MAGIC CPU is fine for the tiny dataset; a single small GPU just makes it faster.

# COMMAND ----------

# Portable installs so this runs on a standard runtime too (no ML runtime required).
# torch/torchvision/mlflow may be preinstalled on ML runtimes; pip is a no-op there.
# numpy<2 keeps the runtime's pandas intact.
# MAGIC %pip install --quiet torch torchvision mlflow "numpy<2" pyopenssl
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "", "Catalog")
dbutils.widgets.text("schema", "", "Schema")
dbutils.widgets.text("exports_volume", "exports", "Exports volume")
dbutils.widgets.text("export_dir", "", "COCO export dir (blank = latest)")
dbutils.widgets.text("model_name", "object_detector", "UC model name")
dbutils.widgets.text("epochs", "8", "Epochs")
dbutils.widgets.dropdown("pretrained_backbone", "true", ["true", "false"],
                         "Start from ImageNet-pretrained backbone (needs internet egress)")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
EXPORTS_VOL = dbutils.widgets.get("exports_volume")
EXPORT_DIR = dbutils.widgets.get("export_dir").strip()
MODEL_NAME = dbutils.widgets.get("model_name")
EPOCHS = int(dbutils.widgets.get("epochs"))
PRETRAINED = dbutils.widgets.get("pretrained_backbone") == "true"

# Fail fast rather than building a "/Volumes///..." path from blank widgets.
assert CATALOG and SCHEMA, "catalog and schema are required"

EXPORTS_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{EXPORTS_VOL}"
UC_MODEL = f"{CATALOG}.{SCHEMA}.{MODEL_NAME}"

# Resolve the export dir: explicit widget, else upstream task value, else latest coco_* dir.
if not EXPORT_DIR:
    try:
        EXPORT_DIR = dbutils.jobs.taskValues.get(taskKey="export", key="export_dir")
    except Exception:
        dirs = sorted([f.path for f in dbutils.fs.ls(EXPORTS_PATH) if f.name.startswith("coco_")])
        assert dirs, f"No coco_* exports found in {EXPORTS_PATH}. Run 02_export_coco first."
        EXPORT_DIR = dirs[-1].replace("dbfs:", "")
EXPORT_DIR = EXPORT_DIR.replace("dbfs:", "")
print(f"Training from: {EXPORT_DIR}")
print(f"UC model     : {UC_MODEL}")

# COMMAND ----------

import json, os
import torch
import torchvision
from torch.utils.data import Dataset, DataLoader
from PIL import Image
import torchvision.transforms.functional as F

coco = json.load(open(f"{EXPORT_DIR}/annotations.json"))
img_dir = f"{EXPORT_DIR}/images"
# category_id (1..N) -> contiguous model label (1..N); 0 reserved for background
cats = sorted(coco["categories"], key=lambda c: c["id"])
catid_to_label = {c["id"]: i + 1 for i, c in enumerate(cats)}
num_classes = len(cats) + 1  # + background
print(f"{len(coco['images'])} images, {len(coco['annotations'])} boxes, {len(cats)} classes")

anns_by_img = {}
for a in coco["annotations"]:
    anns_by_img.setdefault(a["image_id"], []).append(a)


class CocoDet(Dataset):
    def __init__(self, coco, img_dir):
        self.images = [im for im in coco["images"] if anns_by_img.get(im["id"])]
        self.img_dir = img_dir

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        im = self.images[idx]
        img = Image.open(os.path.join(self.img_dir, im["file_name"])).convert("RGB")
        boxes, labels = [], []
        for a in anns_by_img.get(im["id"], []):
            x, y, w, h = a["bbox"]
            if w <= 0 or h <= 0:
                continue
            boxes.append([x, y, x + w, y + h])  # COCO xywh -> xyxy
            labels.append(catid_to_label[a["category_id"]])
        target = {
            "boxes": torch.as_tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            "labels": torch.as_tensor(labels, dtype=torch.int64),
        }
        return F.to_tensor(img), target


ds = CocoDet(coco, img_dir)

# BatchNorm needs >1 sample per batch, so:
#  - require >=2 labeled images (a single image -> batch of 1 -> crash; and drop_last alone
#    would silently drop the only batch and "train" nothing), with a clear message; and
#  - drop_last=True so an odd count (e.g. 3, 5) doesn't leave a trailing size-1 batch.
# With >=2 images this always yields at least one full batch of 2.
BATCH_SIZE = 2
if len(ds) < 2:
    raise RuntimeError(
        f"Need at least 2 labeled images to train (found {len(ds)}). "
        "BatchNorm requires batches larger than 1 — annotate at least 2 images and re-run."
    )
dl = DataLoader(ds, batch_size=BATCH_SIZE, shuffle=True, drop_last=True,
                collate_fn=lambda b: tuple(zip(*b)))

# COMMAND ----------

import mlflow

mlflow.set_registry_uri("databricks-uc")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# weights=None always: the COCO-pretrained detector head is built for 91 classes and would
# have to be replaced anyway. weights_backbone is the useful half — ImageNet features give
# the model something real to start from instead of noise.
# Set pretrained_backbone=false on clusters with restricted internet egress: the weights are
# downloaded from download.pytorch.org on first use, and a blocked cluster hangs instead.
model = torchvision.models.detection.ssdlite320_mobilenet_v3_large(
    weights=None,
    weights_backbone="DEFAULT" if PRETRAINED else None,
    num_classes=num_classes,
)
model.to(device)

optimizer = torch.optim.SGD(
    [p for p in model.parameters() if p.requires_grad], lr=0.005, momentum=0.9, weight_decay=5e-4
)

with mlflow.start_run(run_name="office-detector-dummy") as run:
    mlflow.log_params({
        "base_model": "ssdlite320_mobilenet_v3_large", "epochs": EPOCHS,
        "pretrained_backbone": PRETRAINED,
        "num_classes": num_classes, "num_images": len(ds), "device": device.type,
        "classes": ",".join(c["name"] for c in cats), "export_dir": EXPORT_DIR,
    })
    model.train()
    for epoch in range(EPOCHS):
        epoch_loss = 0.0
        for imgs, targets in dl:
            imgs = [i.to(device) for i in imgs]
            targets = [{k: v.to(device) for k, v in t.items()} for t in targets]
            loss_dict = model(imgs, targets)
            loss = sum(loss_dict.values())
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            epoch_loss += float(loss)
        mlflow.log_metric("train_loss", epoch_loss, step=epoch)
        print(f"epoch {epoch+1}/{EPOCHS}  loss={epoch_loss:.3f}")

    # Log + register to Unity Catalog as a PyFunc.
    # NOT mlflow.pytorch: that flavor rejects dict input and expects a single output
    # tensor, but a detector takes a LIST of CHW tensors and returns a LIST of dicts.
    # This wrapper does both conversions, so the endpoint takes base64-encoded images.
    from mlflow.models.signature import ModelSignature
    from mlflow.types.schema import Array, ColSpec, DataType, Schema

    label_map = {str(catid_to_label[c["id"]]): c["name"] for c in cats}
    model.eval()

    class Detector(mlflow.pyfunc.PythonModel):
        """{"image_b64": <base64 image>} in -> {"boxes", "scores", "labels"} out.

        Boxes are in the original image's pixel space; labels are class names.
        """

        def __init__(self, model, label_map):
            self.model, self.label_map = model, label_map

        def predict(self, context, model_input, params=None):
            import base64, io, torch
            import torchvision.transforms.functional as TF
            from PIL import Image

            imgs = [TF.to_tensor(Image.open(io.BytesIO(base64.b64decode(b))).convert("RGB"))
                    for b in model_input["image_b64"].tolist()]
            with torch.no_grad():
                outputs = self.model(imgs)

            results = []
            for o in outputs:
                keep = o["scores"] > 0.05  # random-init weights score low; stay permissive
                results.append({
                    "boxes": o["boxes"][keep].tolist(),
                    "scores": o["scores"][keep].tolist(),
                    "labels": [self.label_map.get(str(int(i)), "unknown")
                               for i in o["labels"][keep].tolist()],
                })
            return results

    # UC rejects a signature with only inputs, so both halves are declared. The output
    # is one row per image sent, with variable-length lists inside — hence Array types.
    signature = ModelSignature(
        inputs=Schema([ColSpec(DataType.string, "image_b64")]),
        outputs=Schema([
            ColSpec(Array(Array(DataType.double)), "boxes"),
            ColSpec(Array(DataType.double), "scores"),
            ColSpec(Array(DataType.string), "labels"),
        ]),
    )

    mlflow.pyfunc.log_model(
        artifact_path="model",
        python_model=Detector(model, label_map),
        registered_model_name=UC_MODEL,
        signature=signature,
        pip_requirements=["torch", "torchvision", "pillow", "pandas"],
    )
    # Persist label mapping as a run artifact too, for anyone reading the run.
    mlflow.log_dict(label_map, "label_map.json")
    run_id = run.info.run_id

# Resolve the version just registered so the downstream serving task deploys exactly this one
# (rather than racing on "latest" if someone else registers concurrently).
from mlflow.tracking import MlflowClient

_client = MlflowClient(registry_uri="databricks-uc")
_versions = [v for v in _client.search_model_versions(f"name='{UC_MODEL}'") if v.run_id == run_id]
model_version = _versions[0].version if _versions else max(
    (v.version for v in _client.search_model_versions(f"name='{UC_MODEL}'")), key=int
)

# Hand the version to the next task (see 04_deploy_serving).
try:
    dbutils.jobs.taskValues.set(key="model_version", value=str(model_version))
except Exception:
    pass  # Interactive run outside a job — the downstream notebook falls back to "latest".

print(f"\nRegistered {UC_MODEL} version {model_version} (run {run_id}). "
      f"Labels: {[c['name'] for c in cats]}")
dbutils.notebook.exit(json.dumps(
    {"uc_model": UC_MODEL, "run_id": run_id, "model_version": str(model_version)}))
