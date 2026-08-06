#!/usr/bin/env python3
"""
Seed the demo's *landing* UC Volume with sample images (and optionally a short video).

Uploads run through the Databricks SDK files API, so this works from a laptop with a
CLI profile — no FUSE mount needed. Afterwards run `databricks bundle run cv_ingest`
to process what landed.

Usage:
    python scripts/seed_demo_data.py --profile <profile> \\
        --catalog <catalog> --schema <schema> --landing-volume landing \\
        --local-dir ~/my_samples

Uploading through the Catalog Explorer UI does the same thing; this script is just the
scriptable equivalent for a folder of files.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from databricks.sdk import WorkspaceClient

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


def upload(w: WorkspaceClient, local: Path, dst: str) -> None:
    with open(local, "rb") as fh:
        w.files.upload(dst, fh, overwrite=True)
    print(f"  ↑ {local.name}  →  {dst}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT"))
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--landing-volume", default="landing")
    ap.add_argument("--local-dir", required=True, help="Folder of images/video to upload")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    base = f"/Volumes/{args.catalog}/{args.schema}/{args.landing_volume}"
    print(f"Seeding {base} (profile={args.profile})")

    n_img = n_vid = 0

    for p in sorted(Path(args.local_dir).expanduser().iterdir()):
        ext = p.suffix.lower()
        if ext in IMAGE_EXTS:
            upload(w, p, f"{base}/{p.name}"); n_img += 1
        elif ext in VIDEO_EXTS:
            upload(w, p, f"{base}/{p.name}"); n_vid += 1

    if not n_img and not n_vid:
        ap.error(f"No images or videos found in {args.local_dir}")

    print(f"\nDone: {n_img} image(s), {n_vid} video(s) uploaded to {base}")
    print("Now process them with:  databricks bundle run cv_ingest")


if __name__ == "__main__":
    main()
