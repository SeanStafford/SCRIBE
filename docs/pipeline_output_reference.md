# Pipeline Output Reference

How to read and use the output of `python -m scribe.pipeline`.

This documents the *local* pipeline (`scribe.pipeline`). The AWS pipeline uses similar logging and routing logic but writes output to S3.

## Output Location

Each run produces a timestamped directory under `outs/`:

```
outs/
├── pipeline_20260505_143645/   # Timestamped run
├── pipeline_20260505_150012/   # Another run
└── pipeline_latest -> pipeline_20260505_150012   # Symlink to most recent
```

Always use `outs/pipeline_latest/` to access the most recent run.

## Directory Structure

```
outs/pipeline_latest/
├── summary.json                # Pipeline-level results overview
├── pipeline_events.jsonl       # Structured event log (CloudWatch-compatible)
├── grocery_contract/           # Per-image output directories
│   ├── result.json             #   Full result with all OCR passes
│   ├── raw.txt                 #   OCR text with no preprocessing
│   ├── smart.txt               #   OCR text after auto-detected preprocessing
│   ├── corrected.txt           #   Post-processed text (OCR error fixes applied)
│   ├── fields.json             #   Extracted structured fields (dates, amounts, locations)
│   ├── preprocessed.png        #   The preprocessed image fed to Tesseract
│   └── words.json              #   Word-level detail with bounding boxes
├── wiretap_transcript/
├── mpa_newsletter/
├── vance_deed/
└── rattler_page/
```

In `--demo` mode, each image also gets `full.txt` (all preprocessing forced on) for comparison.

## Pipeline-Level Files

### `summary.json`

Quick overview of all images processed in this run.

| Field | Description |
|---|---|
| `timestamp` | UTC time the pipeline completed |
| `demo_mode` | Whether `--demo` was used |
| `results[].name` | Canonical image name |
| `results[].best` | Which OCR pass won: `"raw"`, `"smart"`, or `"full"` |
| `results[].routing` | Confidence gate decision |
| `results[].ocr_passes` | Per-pass confidence and word count |

### `pipeline_events.jsonl`

Structured event log — one JSON object per line. This is the local equivalent of what goes to CloudWatch Logs in a production AWS deployment. Queryable with `jq`:

```bash
# All events for one document
jq 'select(.document_id == "grocery_contract")' outs/pipeline_latest/pipeline_events.jsonl

# All routing decisions
jq 'select(.step == "confidence_gate")' outs/pipeline_latest/pipeline_events.jsonl

# All errors
jq 'select(.status == "ERROR")' outs/pipeline_latest/pipeline_events.jsonl
```

Each event has:

| Field | Description |
|---|---|
| `timestamp` | UTC ISO 8601 |
| `document_id` | Canonical image name |
| `step` | Pipeline stage: `validate`, `preprocess`, `extract`, `postprocess`, `confidence_gate` |
| `status` | `SUCCESS`, `ERROR`, or `SKIPPED` |
| `duration_ms` | Wall-clock time for this step (where applicable) |
| `details` | Step-specific data (see below) |

**Step-specific details:**

- **validate**: `width`, `height`, `file` — or `error_message` on failure
- **preprocess**: `scale`, `apply_clahe`, `deskew_angle`, `deskew_applied`, `contrast_std`, `output_shape`
- **extract**: `engine`, `passes` (dict of pass name → `{confidence, words}`)
- **postprocess**: `corrections_applied`, `fields_extracted` (dict of field type → count)
- **confidence_gate**: `confidence`, `best_pass`, `routing` (`ACCEPT`/`REVIEW`/`ESCALATE`), `low_confidence_words`

**Routing thresholds:**

| Routing | Confidence | Meaning |
|---|---|---|
| `ACCEPT` | >= 85% | High confidence — results usable as-is |
| `REVIEW` | 60-84% | Moderate confidence — human spot-check recommended |
| `ESCALATE` | < 60% | Low confidence — re-run with a stronger engine or send to HITL |

*See [note on configuring the local SCRIBE pipeline](#note-on-configuring-the-local-scribe-pipeline) below.*

## Per-Image Files

### `result.json`

The full result for one image. Contains all OCR passes so you can compare.

| Field | Description |
|---|---|
| `name` | Canonical name |
| `original_file` | Original filename before renaming |
| `image_size` | `{width, height}` in pixels |
| `preprocessing` | Auto-detected recipe: `scale`, `apply_clahe`, `deskew_angle`, and `signals` (the measurements that drove each decision) |
| `ocr_passes` | Dict of pass name → `{avg_confidence, word_count, low_confidence_count}` |
| `best` | Which pass had highest confidence |
| `routing` | Confidence gate decision: `ACCEPT`, `REVIEW`, or `ESCALATE` |
| `postprocessing` | `corrections_applied` count and `fields` dict (extracted dates, amounts, locations) |

### `raw.txt` / `smart.txt`

Plain text output from each OCR pass. `raw` = no preprocessing, `smart` = auto-detected preprocessing (CLAHE, deskew, upscale applied only where the image analysis found them beneficial). The `best` field in `result.json` tells you the most successful mode.

### `corrected.txt`

The best OCR pass after post-processing (common OCR error fixes like `rn`→`m`, `O`→`0` in numeric contexts). Compare against the uncorrected pass to see what changed.

### `fields.json`

Structured fields extracted via regex from the corrected text. Only present if fields were found.

```json
{
  "dates": ["4/26/50"],
  "amounts": ["$92.00", "$25.00"],
  "locations": ["North Carolina", "New Hanover"]
}
```

### `preprocessed.png`

The image after preprocessing. Useful for visually verifying that preprocessing helped — compare against the original in `data/images/`.

### `words.json`

Word-level OCR output from the best pass. Each entry:

```json
{
  "text": "County",
  "conf": 92,
  "bbox": {"x": 412, "y": 180, "w": 145, "h": 32}
}
```

Use `words.json` for downstream tasks like field extraction, confidence visualization, or targeted post-correction on low-confidence words.

---

## Note on configuring the local SCRIBE pipeline

The numeric thresholds referenced in this document (routing cutoffs at 85%/60%, contrast threshold, CLAHE clip limit, upscale target, etc.) are defaults from `configs/pipeline.yaml`. They can be tuned per-deployment or overridden per-image with `data/images.yaml` preprocessing field.