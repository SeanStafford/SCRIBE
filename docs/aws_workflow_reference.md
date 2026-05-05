# AWS Workflow Reference

How the SCRIBE AWS pipeline processes documents, from upload to results.

## Pipeline

```
  Upload             S3 raw/            EventBridge          Step Functions         Lambda               Textract
    │                   │                    │                     │                   │                    │
    └──► store file ───►│                    │                     │                   │                    │
                        └──► Object Created ►│                     │                   │                    │
                                             └──► start workflow ─►│                   │                    │
                                                                   └──► invoke ───────►│                    │
                                                                                       ├──► validate       │
                                                                                       ├──► call API ─────►│
                                                                                       │◄── lines + conf ──┘
                                                                                       ├──► confidence gate
                                                                                       └──► write results to S3
```

## Services

| Service | Name | Role |
|---------|------|------|
| S3 | `scribe-idp-demo` | Document storage — `raw/`, `working/`, `output/`, `review/` prefixes |
| EventBridge | `scribe-raw-upload` | Listens for uploads to `raw/`, triggers the pipeline |
| Step Functions | `scribe-document-pipeline` | Runs the workflow with retry (2x) and error handling |
| Lambda | `scribe-extract` | Validates input, calls Textract, scores confidence, routes output |
| Textract | `DetectDocumentText` | Extracts text lines with bounding boxes and confidence scores |

## Confidence Routing

Same thresholds as the local pipeline:

| Mean Confidence | Routing | Output Location |
|----------------|---------|-----------------|
| >= 90% | ACCEPT | `output/{doc_id}/result.json` |
| < 90% | REVIEW | `review/{doc_id}/result.json` |

## Artifacts

Each processed document produces:

| File | Location | Content |
|------|----------|---------|
| `textract_response.json` | `working/{doc_id}/` | Raw Textract API response — full audit trail |
| `extracted_fields.json` | `working/{doc_id}/` | Parsed lines with confidence and bounding boxes |
| `result.json` | `output/` or `review/` | Final result with text, confidence summary, routing |

## CLI

All pipeline operations are available via `scripts/aws_pipeline.py`:

```
upload    Upload a document to S3 (triggers pipeline automatically)
invoke    Manually invoke Lambda on an uploaded document
list      List documents in S3 by prefix
results   View extraction results for a document
status    Check Step Functions execution history
logs      View structured log events
search    Search for documents by name
```
