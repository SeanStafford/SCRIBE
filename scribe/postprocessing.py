"""Post-processing for OCR output.

Level 1: Regex-based field extraction and common OCR error correction.
Level 2 (TODO): Structural key-value extraction using anchor phrases.
Level 3 (TODO): LLM post-correction via Bedrock (image + noisy transcript).
"""

import re

# ---------------------------------------------------------------------------
# Common OCR error corrections
# ---------------------------------------------------------------------------

# Patterns where OCR confuses visually similar characters
OCR_FIXES = [
    # 'rn' misread as 'm' — only fix in known word contexts
    (r"\bsarne\b", "same"),
    (r"\bcomn\b", "corn"),
    (r"\bfrorn\b", "from"),
    (r"\bforrn\b", "form"),
    (r"\bnurnber\b", "number"),
    (r"\bgoverrnent\b", "government"),
    # 'l' / '1' / 'I' confusion in numeric contexts
    (r"(\$\d+)[lI](\d+)", r"\g<1>1\2"),
    # '0' / 'O' confusion in numeric contexts
    (r"(\d)O(\d)", r"\g<1>0\2"),
    (r"O(\d{2,})", r"0\1"),
    # Common date OCR errors
    (r"\b(\d{1,2})[,.](\d{1,2})[,.](\d{2,4})\b", r"\1/\2/\3"),
]


def fix_ocr_errors(text: str) -> str:
    """Apply common OCR error corrections."""
    for pattern, replacement in OCR_FIXES:
        text = re.sub(pattern, replacement, text)
    return text


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

DATE_PATTERNS = [
    # Written dates: March 27, 1903
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4}",
    # Numeric dates: 3/27/1903, 03-27-1903
    r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}",
    # Ordinal dates: 10th day of September
    r"\d{1,2}(?:st|nd|rd|th)\s+day\s+of\s+\w+",
]

MONEY_PATTERNS = [
    r"\$[\d,]+\.?\d*",
    r"\b\d+\s+[Dd]ollars?\b",
]

NAME_PATTERNS = [
    # "STATE OF X" / "County of X"
    r"[Ss]tate\s+of\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    r"[Cc]ounty\s+of\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
]


def extract_fields(text: str) -> dict:
    """Extract structured fields from OCR text using regex patterns.

    Returns a dict of field type -> list of matches found.
    """
    fields = {}

    dates = []
    for pattern in DATE_PATTERNS:
        dates.extend(re.findall(pattern, text))
    if dates:
        fields["dates"] = list(dict.fromkeys(dates))  # dedupe, preserve order

    amounts = []
    for pattern in MONEY_PATTERNS:
        amounts.extend(re.findall(pattern, text))
    if amounts:
        fields["amounts"] = list(dict.fromkeys(amounts))

    locations = []
    for pattern in NAME_PATTERNS:
        locations.extend(re.findall(pattern, text))
    if locations:
        fields["locations"] = list(dict.fromkeys(locations))

    return fields


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def postprocess(text: str) -> dict:
    """Run all post-processing on OCR text.

    Returns:
        corrected_text: text after OCR error fixes
        fields: extracted structured fields
        corrections_applied: count of regex substitutions made
    """
    corrected = fix_ocr_errors(text)
    corrections = sum(1 for a, b in zip(text.split(), corrected.split()) if a != b)
    fields = extract_fields(corrected)

    return {
        "corrected_text": corrected,
        "fields": fields,
        "corrections_applied": corrections,
    }
