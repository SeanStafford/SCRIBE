# SCRIBE

**S**tructured **C**apture and **R**ecognition of **I**llegible **B**ook **E**xcerpts

OCR extraction and document processing toolkit for messy, real-world scanned documents.

## Setup

```bash
make venv
source .venv/bin/activate
make install-dev
cp .env.example .env
```

Requires Tesseract: `sudo apt-get install tesseract-ocr tesseract-ocr-eng`

## Local Pipeline

Config-driven OCR pipeline with adaptive preprocessing. Analyzes each image (contrast, resolution, skew), applies only the transforms that help, runs Tesseract, and routes output by confidence.

```bash
python -m scribe.pipeline            # Standard run
python -m scribe.pipeline --demo     # Compare raw vs smart vs full preprocessing
```

Output goes to `outs/pipeline_latest/` (symlinked to most recent run). See `docs/pipeline_output_reference.md` for the full output format.

### Module structure

| Module | Role |
|---|---|
| `scribe/pipeline.py` | Orchestration — config, flow, output, logging |
| `scribe/preprocessing.py` | Image analysis + adaptive preprocessing |
| `scribe/ocr.py` | Tesseract wrapper (extensible to other engines) |
| `scribe/postprocessing.py` | OCR error correction + field extraction |
| `scribe/logging.py` | Structured JSONL event logging |

### Configuration

- `configs/pipeline.yaml` — confidence thresholds, preprocessing params, OCR engine settings
- `data/images.yaml` — image manifest (canonical names, metadata, preprocessing overrides)

## AWS Pipeline

Event-driven pipeline on AWS: S3 upload → EventBridge → Step Functions → Lambda (Textract) → results back to S3, with structured CloudWatch logging using the same JSON schema as the local pipeline.

```bash
make install-aws
python3 scripts/aws_pipeline.py upload data/images/grocery_contract.png
python3 scripts/aws_pipeline.py results grocery_contract
```

See `docs/aws_workflow_reference.md` for more details.

## Local vs AWS Pipeline

Both pipelines share the same logical steps and logging schema but use different tools for each.

| Aspect | Local | AWS |
|--------|-------|-----|
| OCR engine | Tesseract | Textract |
| Preprocessing | Adaptive (CLAHE, deskew, upscale) | Not needed for Textract |
| Storage | `outs/pipeline_latest/` | S3 prefixes (`raw/`, `working/`, `output/`, `review/`) |
| Logging | JSONL file (`pipeline_events.jsonl`) | CloudWatch Logs (analagous to JSONL schema) |
| Orchestration | `pipeline.py` sequential loop | EventBridge → Step Functions → Lambda |
| Trigger | Manual (`python -m scribe.pipeline`) | Triggered automatically on S3 upload |

