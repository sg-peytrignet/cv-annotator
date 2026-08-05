"""Unit checks for the LabelBricks->COCO coordinate conversion (the display_scale fix)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from coco_utils import annotation_to_bbox, build_coco, polygon_segmentation


def approx(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_rectangle_scaled_back_to_pixels():
    # A 4000x3000 image shown on a canvas at display_scale=0.25.
    # A box drawn at display coords (100,50,200,150) must map to 4x those pixels.
    bbox = annotation_to_bbox("rectangle", {"left": 100, "top": 50, "width": 200, "height": 150}, 0.25)
    assert approx(bbox, [400, 200, 800, 600]), bbox


def test_rectangle_upscaled_image_roundtrips():
    # Small image shown enlarged at display_scale=2.0 (e.g. a 300px image on a big canvas).
    # A box drawn at display coords (360,120,200,160) must map back to half those pixels.
    bbox = annotation_to_bbox("rectangle", {"left": 360, "top": 120, "width": 200, "height": 160}, 2.0)
    assert approx(bbox, [180, 60, 100, 80]), bbox


def test_rectangle_scale_one_is_identity():
    bbox = annotation_to_bbox("rectangle", {"left": 10, "top": 20, "width": 30, "height": 40}, 1.0)
    assert approx(bbox, [10, 20, 30, 40]), bbox


def test_missing_scale_falls_back_to_one():
    bbox = annotation_to_bbox("rectangle", {"left": 10, "top": 20, "width": 30, "height": 40}, None)
    assert approx(bbox, [10, 20, 30, 40]), bbox


def test_circle_to_enclosing_box():
    bbox = annotation_to_bbox("circle", {"cx": 100, "cy": 100, "rx": 20, "ry": 10}, 0.5)
    # (cx-rx, cy-ry, 2rx, 2ry)/0.5 = (160, 180, 80, 40)
    assert approx(bbox, [160, 180, 80, 40]), bbox


def test_polygon_bbox_and_segmentation():
    coords = {"points": [{"x": 10, "y": 10}, {"x": 30, "y": 10}, {"x": 20, "y": 40}]}
    bbox = annotation_to_bbox("polygon", coords, 0.5)
    assert approx(bbox, [20, 20, 40, 60]), bbox  # min/max *2
    seg = polygon_segmentation(coords, 0.5)
    assert seg == [[20, 20, 60, 20, 40, 80]], seg


def test_freehand_unsupported():
    assert annotation_to_bbox("freehand", {"path": "M1,2L3,4"}, 1.0) is None


def test_build_coco_end_to_end():
    samples = [{
        "filename": "img1.jpg", "width": 4000, "height": 3000, "display_scale": 0.25,
        "annotations": [
            {"type": "rectangle", "labelClass": "person",
             "coordinates": {"left": 100, "top": 50, "width": 200, "height": 150}},
            {"type": "freehand", "labelClass": "scribble", "coordinates": {"path": "M0,0"}},
        ],
    }]
    coco = build_coco(samples)
    assert len(coco["images"]) == 1
    assert len(coco["annotations"]) == 1  # freehand dropped
    assert coco["info"]["_dropped_annotations"] == 1
    assert coco["annotations"][0]["bbox"] == [400, 200, 800, 600]
    assert coco["categories"] == [{"id": 1, "name": "person"}]
    print("all coco_utils tests passed")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
    print("OK")
