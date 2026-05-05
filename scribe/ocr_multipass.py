"""Multi-pass OCR — try multiple Tesseract PSM modes, pick the best result.

PSM modes produce qualitatively different segmentations:
  PSM 3  = fully automatic (handles most documents)
  PSM 4  = single column of variable-size text
  PSM 6  = single uniform block (best for clean single-column scans)
  PSM 11 = sparse text (find text anywhere)

Running multiple and picking the highest confidence is cheap insurance
against the wrong segmentation silently degrading results.
"""

import numpy as np

from scribe.ocr import run_tesseract

DEFAULT_PSM_MODES = [3, 6]


def run_tesseract_multipass(
    img_gray: np.ndarray,
    psm_modes: list[int] | None = None,
) -> dict:
    """Run Tesseract with multiple PSM modes, return the best result.

    Returns the same dict format as run_tesseract, plus:
        psm_used: which PSM mode won
        psm_results: summary of all passes {psm: {avg_confidence, word_count}}
    """
    if psm_modes is None:
        psm_modes = DEFAULT_PSM_MODES

    results = {}
    for psm in psm_modes:
        results[psm] = run_tesseract(img_gray, psm=psm)

    best_psm = max(results, key=lambda k: results[k]["avg_confidence"])
    best = results[best_psm]

    best["psm_used"] = best_psm
    best["psm_results"] = {
        psm: {"avg_confidence": r["avg_confidence"], "word_count": r["word_count"]}
        for psm, r in results.items()
    }

    return best
