#!/usr/bin/env python3
"""
Seed LabelBricks-format annotation JSON onto the images Volume so the export→train→edge
pipeline has labeled data WITHOUT manual clicking (for demo pre-bake / dry-run only).

Writes one box per image ("person") aligned to the synthetic figure, in the exact JSON
shape the app's /api/save produces, to {images}/.labelbricks/annotations/{file}.json.
The export notebook reads exactly these files. display_scale=1.0 (full-size).

Real annotations come from the app UI; this is only to pre-bake a model without labeling by hand.

Usage:
  python scripts/seed_annotations.py --profile <profile> --catalog <catalog> \
      --schema <schema> --images-volume images --limit 12
"""
from __future__ import annotations

import argparse
import io
import json
import os
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="Databricks CLI profile")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--images-volume", default="images")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    base = f"/Volumes/{args.catalog}/{args.schema}/{args.images_volume}"
    ann_dir = f"{base}/.labelbricks/annotations"

    files = [
        e.path for e in w.files.list_directory_contents(base)
        if os.path.splitext(e.path)[1].lower() in IMAGE_EXTS
    ][: args.limit]

    n = 0
    for path in files:
        fname = path.rsplit("/", 1)[-1]
        # true dims
        raw = w.files.download(path).contents.read()
        wpx, hpx = Image.open(io.BytesIO(raw)).size
        # "person" box aligned to the synthetic figure (30-70% w, 25-85% h)
        box = {
            "left": round(0.30 * wpx), "top": round(0.25 * hpx),
            "width": round(0.40 * wpx), "height": round(0.60 * hpx),
        }
        ann = {
            "filename": fname,
            "volume_path": base,
            "reviewer": "seed@demo",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "reviewed",
            "notes": "seeded for pre-bake",
            "image_width": wpx, "image_height": hpx, "display_scale": 1.0,
            "annotations": [{
                "annotationId": f"seed-{n}", "type": "rectangle", "labelClass": "person",
                "coordinates": box, "confidence": None, "createdBy": "human", "color": "#FF3621",
            }],
        }
        w.files.upload(f"{ann_dir}/{fname}.json",
                       io.BytesIO(json.dumps(ann, indent=2).encode()), overwrite=True)
        print(f"  ✓ {fname}  ({wpx}x{hpx})  box={box}")
        n += 1

    print(f"\nSeeded {n} annotation file(s) to {ann_dir}")


if __name__ == "__main__":
    main()
