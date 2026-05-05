"""OCR result visualization for SCRIBE.

Renders extracted words onto a blank canvas at their detected positions,
colored by confidence. Works with any word list that has {text, conf, bbox}
entries — agnostic to which engine or pipeline produced them.
"""

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _conf_to_color(conf: float) -> tuple:
    """Map confidence (0-100) to RGB color. Green=high, yellow=mid, red=low."""
    if conf >= 80:
        # Green
        return (0, 140, 0)
    elif conf >= 50:
        # Interpolate green → orange
        t = (conf - 50) / 30
        r = int(200 * (1 - t))
        g = int(140 * t + 100 * (1 - t))
        return (r, g, 0)
    else:
        # Interpolate orange → red
        t = conf / 50
        return (200, int(60 * t), 0)


def render_ocr_overlay(
    words: list[dict],
    canvas_size: tuple[int, int],
    font_size: int = 12,
    background: str = "white",
) -> "Image.Image":
    """Render OCR words on a blank canvas at their detected positions.

    Args:
        words: List of {text, conf, bbox: {x, y, w, h}} dicts.
        canvas_size: (width, height) in pixels — should match the image the
                     bounding boxes were computed on.
        font_size: Approximate font size for rendering.
        background: Canvas background color.

    Returns:
        PIL Image with words rendered at their positions, colored by confidence.
    """
    if not _HAS_PIL:
        raise ImportError("Pillow is required for visualization")

    img = Image.new("RGB", canvas_size, background)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()

    for word in words:
        x = word["bbox"]["x"]
        y = word["bbox"]["y"]
        color = _conf_to_color(word["conf"])
        draw.text((x, y), word["text"], fill=color, font=font)

    return img


def render_side_by_side(
    original: np.ndarray,
    words: list[dict],
    font_size: int = 12,
) -> "Image.Image":
    """Render original image next to OCR text overlay, side by side.

    Args:
        original: Grayscale or BGR numpy array (the image OCR was run on).
        words: List of {text, conf, bbox} dicts from that image.
        font_size: Font size for the text overlay.

    Returns:
        PIL Image with original on the left, OCR overlay on the right.
    """
    if not _HAS_PIL:
        raise ImportError("Pillow is required for visualization")

    # Convert numpy to PIL
    if len(original.shape) == 2:
        orig_pil = Image.fromarray(original, mode="L").convert("RGB")
    else:
        orig_pil = Image.fromarray(original[:, :, ::-1])  # BGR to RGB

    h, w = original.shape[:2]
    overlay = render_ocr_overlay(words, (w, h), font_size=font_size)

    combined = Image.new("RGB", (w * 2 + 20, h), "white")
    combined.paste(orig_pil, (0, 0))
    combined.paste(overlay, (w + 20, 0))

    return combined
