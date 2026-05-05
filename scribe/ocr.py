"""OCR engine wrappers for SCRIBE.

Currently supports Tesseract. Designed so additional engines (e.g. PaddleOCR, EasyOCR,
Textract) can be added as separate functions with the same return format.
"""

import numpy as np
import pytesseract


def run_tesseract(img_gray: np.ndarray, psm: int = 3) -> dict:
    """Run Tesseract and return structured results.

    Returns dict with:
        text: full extracted text
        words: list of {text, conf, bbox} per word
        avg_confidence: mean word confidence
        word_count: total words detected
        low_confidence_count: words below 60% confidence
    """
    config = f"--oem 1 --psm {psm}"

    text = pytesseract.image_to_string(img_gray, config=config)

    data = pytesseract.image_to_data(img_gray, config=config, output_type=pytesseract.Output.DICT)

    words = []
    for i in range(len(data["text"])):
        conf = int(data["conf"][i])
        word = data["text"][i].strip()
        if conf > 0 and word:
            words.append(
                {
                    "text": word,
                    "conf": conf,
                    "bbox": {
                        "x": data["left"][i],
                        "y": data["top"][i],
                        "w": data["width"][i],
                        "h": data["height"][i],
                    },
                }
            )

    confs = [w["conf"] for w in words]
    avg_conf = sum(confs) / len(confs) if confs else 0.0

    return {
        "text": text,
        "words": words,
        "avg_confidence": round(avg_conf, 1),
        "word_count": len(words),
        "low_confidence_count": sum(1 for c in confs if c < 60),
    }
