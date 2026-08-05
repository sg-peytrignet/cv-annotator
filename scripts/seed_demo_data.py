#!/usr/bin/env python3
"""
Seed the demo's *landing* UC Volume with sample images (and optionally a short video).

Uploads run through the Databricks SDK files API, so this works from a laptop with a
CLI profile — no FUSE mount needed. Afterwards run `databricks bundle run cv_ingest`
to process what landed.

Usage:
    # Upload your own images + a clip:
    python scripts/seed_demo_data.py --profile <profile> \\
        --catalog <catalog> --schema <schema> --landing-volume landing \\
        --local-dir ~/my_samples

    # No data handy? Generate synthetic placeholders so the pipeline can be smoke-tested:
    python scripts/seed_demo_data.py --profile <profile> --catalog <catalog> \\
        --schema <schema> --generate 12

Real images make a far better demo; --generate exists only to prove the plumbing.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path

from databricks.sdk import WorkspaceClient

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


def upload(w: WorkspaceClient, local: Path, dst: str) -> None:
    with open(local, "rb") as fh:
        w.files.upload(dst, fh, overwrite=True)
    print(f"  ↑ {local.name}  →  {dst}")


def generate_synthetic(n: int) -> list[tuple[str, bytes]]:
    """Produce n simple synthetic images (varied size/shapes) for plumbing tests."""
    from PIL import Image, ImageDraw

    out = []
    palette = [(40, 44, 52), (53, 92, 125), (108, 91, 123), (192, 108, 132)]
    for i in range(n):
        w_px, h_px = (640 + (i % 3) * 160, 480 + (i % 2) * 120)  # vary dims on purpose
        img = Image.new("RGB", (w_px, h_px), palette[i % len(palette)])
        d = ImageDraw.Draw(img)
        d.rectangle([w_px * 0.3, h_px * 0.25, w_px * 0.7, h_px * 0.85], outline=(255, 255, 255), width=4)
        d.ellipse([w_px * 0.38, h_px * 0.30, w_px * 0.62, h_px * 0.52], fill=(230, 230, 230))
        d.text((10, 10), f"synthetic-{i:03d} {w_px}x{h_px}", fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        out.append((f"synthetic_{i:03d}.jpg", buf.getvalue()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT"))
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--landing-volume", default="landing")
    ap.add_argument("--local-dir", help="Folder of images/video to upload")
    ap.add_argument("--generate", type=int, default=0, help="Generate N synthetic images instead")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    base = f"/Volumes/{args.catalog}/{args.schema}/{args.landing_volume}"
    print(f"Seeding {base} (profile={args.profile})")

    n_img = n_vid = 0

    if args.local_dir:
        for p in sorted(Path(args.local_dir).expanduser().iterdir()):
            ext = p.suffix.lower()
            if ext in IMAGE_EXTS:
                upload(w, p, f"{base}/{p.name}"); n_img += 1
            elif ext in VIDEO_EXTS:
                upload(w, p, f"{base}/{p.name}"); n_vid += 1

    if args.generate:
        for name, data in generate_synthetic(args.generate):
            w.files.upload(f"{base}/{name}", io.BytesIO(data), overwrite=True)
            print(f"  ↑ (generated) {name}")
            n_img += 1

    if not args.local_dir and not args.generate:
        ap.error("Provide --local-dir and/or --generate N")

    print(f"\nDone: {n_img} image(s), {n_vid} video(s) uploaded to {base}")
    print("The cv-ingest job's file-arrival trigger will process them shortly.")


if __name__ == "__main__":
    main()
