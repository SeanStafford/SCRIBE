"""Image analysis and preprocessing for SCRIBE.

Handles auto-detection of image characteristics and adaptive preprocessing.
"""

import cv2
import numpy as np


def analyze_image(gray: np.ndarray, config: dict) -> dict:
    """Analyze image and decide which preprocessing steps to apply.

    Returns a recipe dict with decisions and the signals that drove them.
    """
    h, w = gray.shape[:2]
    prep_cfg = config.get("preprocessing", {})

    # --- Resolution ---
    target = prep_cfg.get("target_short_side", 2000)
    short_side = min(h, w)
    if short_side >= target:
        scale = 1
    else:
        # Scale up to reach target, capped at 4x
        scale = min(4, max(1, round(target / short_side)))

    # --- Contrast ---
    contrast_std = float(np.std(gray))
    contrast_threshold = prep_cfg.get("contrast_std_threshold", 55)
    apply_clahe = contrast_std < contrast_threshold

    # --- Skew ---
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, 100, minLineLength=min(w, h) // 4, maxLineGap=10
    )
    skew_angle = 0.0
    if lines is not None:
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(angle) < 15:
                angles.append(angle)
        if angles:
            skew_angle = float(np.median(angles))

    return {
        "scale": scale,
        "apply_clahe": apply_clahe,
        "clahe_clip": prep_cfg.get("clahe_clip", 2.0),
        "skew_angle": round(skew_angle, 3),
        "signals": {
            "short_side": short_side,
            "target_short_side": target,
            "contrast_std": round(contrast_std, 1),
            "contrast_threshold": contrast_threshold,
        },
    }


def preprocess(gray: np.ndarray, recipe: dict) -> np.ndarray:
    """Apply preprocessing based on the recipe from analyze_image().

    Always runs deskew (idempotent — no-op if angle is near zero).
    Conditionally applies CLAHE and upscale based on the recipe.
    """
    result = gray.copy()

    # Deskew — always run, idempotent
    angle = recipe["skew_angle"]
    if abs(angle) > 0.3:
        h, w = result.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        result = cv2.warpAffine(
            result, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )

    # CLAHE — conditional
    if recipe["apply_clahe"]:
        clahe = cv2.createCLAHE(clipLimit=recipe["clahe_clip"], tileGridSize=(8, 8))
        result = clahe.apply(result)

    # Upscale — conditional
    scale = recipe["scale"]
    if scale > 1:
        result = cv2.resize(result, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    return result
