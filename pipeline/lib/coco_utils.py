"""
Pure helpers to convert LabelBricks annotations into COCO detection format.

The crux: LabelBricks stores annotation coordinates in *scaled display space*
(canvas pixels = image pixels x display_scale, where display_scale fits the image
to the browser canvas). To recover true image-pixel boxes we divide by display_scale.
The natural image width/height are stored alongside (see the LabelBricks coordinate
patch). These functions have no Spark/Databricks dependency so they are unit-testable.
"""
from __future__ import annotations

from typing import Optional


def _scale(display_scale: Optional[float]) -> float:
    """display_scale may be None/0 for very old saves — fall back to 1.0."""
    if not display_scale or display_scale <= 0:
        return 1.0
    return float(display_scale)


def annotation_to_bbox(
    ann_type: str, coordinates: dict, display_scale: Optional[float]
) -> Optional[list[float]]:
    """Return a COCO bbox [x, y, w, h] in **image pixels**, or None if unsupported.

    rectangle: {left, top, width, height}
    circle:    {cx, cy, rx, ry}            -> enclosing box
    polygon:   {points: [{x, y}, ...]}     -> min/max enclosing box
    freehand:  unsupported for detection   -> None
    """
    s = _scale(display_scale)

    if ann_type == "rectangle":
        return [
            coordinates["left"] / s,
            coordinates["top"] / s,
            coordinates["width"] / s,
            coordinates["height"] / s,
        ]

    if ann_type == "circle":
        cx, cy = coordinates["cx"], coordinates["cy"]
        rx, ry = coordinates["rx"], coordinates["ry"]
        return [(cx - rx) / s, (cy - ry) / s, (2 * rx) / s, (2 * ry) / s]

    if ann_type == "polygon":
        pts = coordinates.get("points") or []
        if not pts:
            return None
        xs = [p["x"] / s for p in pts]
        ys = [p["y"] / s for p in pts]
        return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]

    return None  # freehand or unknown


def polygon_segmentation(coordinates: dict, display_scale: Optional[float]) -> Optional[list[list[float]]]:
    """COCO segmentation [[x1,y1,x2,y2,...]] in image pixels for polygon annotations."""
    s = _scale(display_scale)
    pts = coordinates.get("points") or []
    if len(pts) < 3:
        return None
    flat: list[float] = []
    for p in pts:
        flat.extend([p["x"] / s, p["y"] / s])
    return [flat]


def build_coco(samples: list[dict], description: str = "LabelBricks export") -> dict:
    """Assemble a COCO dict from per-image sample records.

    Each sample: {
        "filename": str, "width": int, "height": int,
        "annotations": [{"type", "labelClass", "coordinates"} ...]
    }
    Categories are derived from the distinct labelClass values (sorted).
    Freehand and zero-size boxes are dropped (counted in the returned _dropped).
    """
    classes = sorted({
        a.get("labelClass", "unlabeled")
        for s in samples for a in s.get("annotations", [])
        if a.get("type") in ("rectangle", "circle", "polygon")
    })
    cat_id = {c: i + 1 for i, c in enumerate(classes)}  # COCO category ids are 1-based

    coco = {
        "info": {"description": description, "version": "1.0"},
        "images": [],
        "annotations": [],
        "categories": [{"id": cat_id[c], "name": c} for c in classes],
    }

    img_id = 0
    ann_id = 0
    dropped = 0
    for s in samples:
        img_id += 1
        coco["images"].append({
            "id": img_id,
            "file_name": s["filename"],
            "width": s.get("width"),
            "height": s.get("height"),
        })
        for a in s.get("annotations", []):
            bbox = annotation_to_bbox(a.get("type"), a.get("coordinates", {}), s.get("display_scale"))
            if bbox is None or bbox[2] <= 0 or bbox[3] <= 0:
                dropped += 1
                continue
            ann_id += 1
            entry = {
                "id": ann_id,
                "image_id": img_id,
                "category_id": cat_id[a.get("labelClass", "unlabeled")],
                "bbox": [round(v, 2) for v in bbox],
                "area": round(bbox[2] * bbox[3], 2),
                "iscrowd": 0,
            }
            if a.get("type") == "polygon":
                seg = polygon_segmentation(a.get("coordinates", {}), s.get("display_scale"))
                if seg:
                    entry["segmentation"] = [[round(v, 2) for v in seg[0]]]
            coco["annotations"].append(entry)

    coco["info"]["_dropped_annotations"] = dropped
    return coco
