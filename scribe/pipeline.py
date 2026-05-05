"""SCRIBE OCR pipeline — config-driven orchestration.

This module handles pipeline flow, config loading, output writing, and logging.
Domain logic lives in preprocessing.py and ocr.py.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import yaml

from scribe.logging import PipelineLogger
from scribe.ocr import run_tesseract
from scribe.postprocessing import postprocess
from scribe.preprocessing import analyze_image, preprocess

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(config_path: Path) -> dict:
    """Load pipeline config YAML."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_manifest(manifest_path: Path) -> dict:
    """Load the image manifest YAML. Returns dict keyed by canonical name."""
    with open(manifest_path) as f:
        return yaml.safe_load(f)["images"]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def process_image(
    name: str,
    meta: dict,
    images_dir: Path,
    out_dir: Path,
    plog: PipelineLogger,
    config: dict,
) -> dict:
    """Run the full pipeline on a single image. Returns result dict."""
    filename = meta.get("filename", meta.get("original"))
    img_path = images_dir / filename

    # --- validate ---
    if not img_path.exists():
        logger.error("Image not found: %s", img_path)
        plog.event(
            name, "validate", "ERROR", details={"error_message": f"File not found: {img_path}"}
        )
        return {"name": name, "error": f"File not found: {img_path}"}

    logger.info("Processing %s (%s)", name, filename)
    img_bgr = cv2.imread(str(img_path))
    h, w = img_bgr.shape[:2]
    plog.event(name, "validate", "SUCCESS", details={"width": w, "height": h, "file": filename})

    # --- analyze ---
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    recipe = analyze_image(gray, config)

    img_out_dir = out_dir / name
    img_out_dir.mkdir(parents=True, exist_ok=True)

    # --- build OCR passes ---
    demo = config.get("demo", False)
    passes = {}

    # Pass 1: raw (always)
    passes["raw"] = gray

    # Pass 2: smart (auto-detected preprocessing)
    with plog.timed(name, "preprocess") as ctx:
        smart_img = preprocess(gray, recipe)
        ctx["details"] = {
            "mode": "smart",
            "scale": recipe["scale"],
            "apply_clahe": recipe["apply_clahe"],
            "clahe_clip": recipe["clahe_clip"],
            "deskew_angle": recipe["skew_angle"],
            "deskew_applied": abs(recipe["skew_angle"]) > 0.3,
            "output_shape": list(smart_img.shape),
            **recipe["signals"],
        }
    passes["smart"] = smart_img
    cv2.imwrite(str(img_out_dir / "preprocessed.png"), smart_img)

    # Pass 3: full (all preprocessing forced on) — demo mode only
    if demo:
        full_recipe = {
            "scale": recipe["scale"],
            "apply_clahe": True,
            "clahe_clip": recipe["clahe_clip"],
            "skew_angle": recipe["skew_angle"],
        }
        full_img = preprocess(gray, full_recipe)
        passes["full"] = full_img

    # --- extract all passes ---
    ocr_results = {}
    with plog.timed(name, "extract") as ctx:
        for pass_name, img in passes.items():
            ocr_results[pass_name] = run_tesseract(img)
        ctx["details"] = {
            "engine": "tesseract",
            "passes": {
                k: {"confidence": v["avg_confidence"], "words": v["word_count"]}
                for k, v in ocr_results.items()
            },
        }

    # Pick best pass
    best_pass = max(ocr_results, key=lambda k: ocr_results[k]["avg_confidence"])
    best_result = ocr_results[best_pass]
    best_conf = best_result["avg_confidence"]

    # --- confidence_gate ---
    thresholds = config.get("confidence_thresholds", {})
    accept_thresh = thresholds.get("accept", 85)
    review_thresh = thresholds.get("review", 60)

    if best_conf >= accept_thresh:
        routing = "ACCEPT"
    elif best_conf >= review_thresh:
        routing = "REVIEW"
    else:
        routing = "ESCALATE"

    plog.event(
        name,
        "confidence_gate",
        "SUCCESS",
        details={
            "confidence": best_conf,
            "best_pass": best_pass,
            "routing": routing,
            "low_confidence_words": best_result["low_confidence_count"],
        },
    )

    # --- postprocess ---
    with plog.timed(name, "postprocess") as ctx:
        post = postprocess(best_result["text"])
        ctx["details"] = {
            "corrections_applied": post["corrections_applied"],
            "fields_extracted": {k: len(v) for k, v in post["fields"].items()},
        }

    # --- build result ---
    result = {
        "name": name,
        "original_file": meta.get("original", filename),
        "image_size": {"width": w, "height": h},
        "preprocessing": {
            "scale": recipe["scale"],
            "apply_clahe": recipe["apply_clahe"],
            "deskew_angle": recipe["skew_angle"],
            "signals": recipe["signals"],
        },
        "ocr_passes": {
            k: {
                "avg_confidence": v["avg_confidence"],
                "word_count": v["word_count"],
                "low_confidence_count": v["low_confidence_count"],
            }
            for k, v in ocr_results.items()
        },
        "best": best_pass,
        "routing": routing,
        "postprocessing": {
            "corrections_applied": post["corrections_applied"],
            "fields": post["fields"],
        },
        "notes": meta.get("notes", ""),
        "difficulty": meta.get("difficulty"),
    }

    # --- write outputs ---
    for pass_name, ocr in ocr_results.items():
        (img_out_dir / f"{pass_name}.txt").write_text(ocr["text"])
    (img_out_dir / "corrected.txt").write_text(post["corrected_text"])
    if post["fields"]:
        (img_out_dir / "fields.json").write_text(
            json.dumps(post["fields"], indent=2, ensure_ascii=False)
        )
    (img_out_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    (img_out_dir / "words.json").write_text(
        json.dumps(best_result["words"], indent=2, ensure_ascii=False)
    )

    # --- log ---
    parts = [f"{k}={v['avg_confidence']:5.1f}%" for k, v in ocr_results.items()]
    logger.info("  %s: %s -> %s [%s]", name, ", ".join(parts), best_pass, routing)

    return result


def run_pipeline(
    manifest_path: Path | None = None,
    images_dir: Path | None = None,
    out_dir: Path | None = None,
    config_path: Path | None = None,
    demo: bool = False,
) -> list[dict]:
    """Run the full pipeline on all images in the manifest."""
    project_root = Path(__file__).resolve().parent.parent

    if config_path is None:
        config_path = project_root / "configs" / "pipeline.yaml"
    config = load_config(config_path)
    if demo:
        config["demo"] = True

    if manifest_path is None:
        manifest_path = project_root / "data" / "images.yaml"
    if images_dir is None:
        images_dir = project_root / "data" / "images"
    if out_dir is None:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = project_root / "outs" / f"pipeline_{timestamp}"

    out_dir.mkdir(parents=True, exist_ok=True)

    # Symlink outs/pipeline_latest -> this run
    latest_link = out_dir.parent / "pipeline_latest"
    if latest_link.is_symlink() or latest_link.exists():
        latest_link.unlink()
    latest_link.symlink_to(out_dir.name)

    plog = PipelineLogger(out_dir)

    manifest = load_manifest(manifest_path)
    results = []

    for name, meta in manifest.items():
        result = process_image(name, meta, images_dir, out_dir, plog, config)
        results.append(result)

    # Write summary
    summary = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "output_dir": str(out_dir),
        "demo_mode": config.get("demo", False),
        "results": [
            {
                "name": r["name"],
                "best": r.get("best"),
                "routing": r.get("routing"),
                "ocr_passes": r.get("ocr_passes"),
                "difficulty": r.get("difficulty"),
            }
            for r in results
            if "error" not in r
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    demo = "--demo" in sys.argv
    results = run_pipeline(demo=demo)

    print("\n" + "=" * 70)
    print(f"PIPELINE SUMMARY{' (demo mode)' if demo else ''}")
    print("=" * 70)
    for r in results:
        if "error" in r:
            print(f"  {r['name']}: ERROR — {r['error']}")
            continue
        passes = r["ocr_passes"]
        parts = [f"{k}={v['avg_confidence']:5.1f}%" for k, v in passes.items()]
        print(f"  {r['name']:25s}  {', '.join(parts):50s}  best={r['best']}  [{r['routing']}]")
