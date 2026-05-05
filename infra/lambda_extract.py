"""
SCRIBE Lambda — Textract extraction triggered by S3 upload.

Receives an S3 event (via Step Functions), calls Textract, writes results
back to S3 with structured logging matching the local PipelineLogger schema.
"""

import json
import logging
import time
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
textract = boto3.client("textract")
bedrock = boto3.client("bedrock-runtime")

BUCKET = "scribe-idp-demo"
CONFIDENCE_THRESHOLD = 90.0
BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def log_event(document_id: str, step: str, status: str, duration_ms: int = None, **details):
    """Structured log matching PipelineLogger schema — CloudWatch picks this up."""
    record = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "document_id": document_id,
        "step": step,
        "status": status,
    }
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    if details:
        record["details"] = details
    logger.info(json.dumps(record))


def handler(event, context):
    """Lambda entry point. Expects S3 bucket/key in the event."""
    # Extract S3 info — supports both EventBridge and direct invocation
    if "detail" in event:
        # EventBridge S3 event format
        bucket = event["detail"]["bucket"]["name"]
        key = event["detail"]["object"]["key"]
    else:
        # Direct invocation or Step Functions
        bucket = event.get("bucket", BUCKET)
        key = event["key"]

    # Optional flags
    enable_postprocess = event.get("postprocess", False)

    # Derive document ID from key: raw/2026/05/05/test-001/original.png -> test-001
    parts = key.split("/")
    doc_id = parts[-2] if len(parts) >= 2 else key

    # --- validate ---
    t0 = time.monotonic()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        file_size = head["ContentLength"]
        content_type = head.get("ContentType", "unknown")
    except Exception as exc:
        log_event(doc_id, "validate", "ERROR", error_message=str(exc))
        raise

    elapsed = int((time.monotonic() - t0) * 1000)
    log_event(doc_id, "validate", "SUCCESS", duration_ms=elapsed,
              file_size=file_size, content_type=content_type)

    # --- extract (Textract) ---
    t0 = time.monotonic()
    try:
        response = textract.detect_document_text(
            Document={"S3Object": {"Bucket": bucket, "Name": key}}
        )
    except textract.exceptions.DocumentTooLargeException:
        elapsed = int((time.monotonic() - t0) * 1000)
        log_event(doc_id, "extract", "ERROR", duration_ms=elapsed,
                  error_message="Document exceeds 10MB sync limit",
                  file_size=file_size)
        return {"document_id": doc_id, "status": "ERROR", "error": "DocumentTooLarge"}
    except textract.exceptions.UnsupportedDocumentException:
        elapsed = int((time.monotonic() - t0) * 1000)
        log_event(doc_id, "extract", "ERROR", duration_ms=elapsed,
                  error_message="Unsupported document format")
        return {"document_id": doc_id, "status": "ERROR", "error": "UnsupportedFormat"}
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log_event(doc_id, "extract", "ERROR", duration_ms=elapsed,
                  error_message=str(exc), error_type=type(exc).__name__)
        raise

    # Parse Textract response
    lines = [b for b in response["Blocks"] if b["BlockType"] == "LINE"]
    confs = [b["Confidence"] for b in lines]
    conf_min = min(confs) if confs else 0.0
    conf_mean = sum(confs) / len(confs) if confs else 0.0
    conf_max = max(confs) if confs else 0.0
    full_text = "\n".join(b["Text"] for b in lines)

    elapsed = int((time.monotonic() - t0) * 1000)
    log_event(doc_id, "extract", "SUCCESS", duration_ms=elapsed,
              engine="textract", lines=len(lines),
              confidence_min=round(conf_min, 1),
              confidence_mean=round(conf_mean, 1),
              confidence_max=round(conf_max, 1))

    # Write raw Textract JSON to working/
    s3.put_object(
        Bucket=bucket,
        Key=f"working/{doc_id}/textract_response.json",
        Body=json.dumps(response, indent=2, default=str),
        ContentType="application/json",
    )

    # Build extracted fields
    extracted = {
        "document_id": doc_id,
        "source_key": key,
        "engine": "textract",
        "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        "text": full_text,
        "line_count": len(lines),
        "confidence": {
            "min": round(conf_min, 1),
            "mean": round(conf_mean, 1),
            "max": round(conf_max, 1),
        },
        "lines": [
            {
                "text": b["Text"],
                "confidence": round(b["Confidence"], 1),
                "bbox": b.get("Geometry", {}).get("BoundingBox", {}),
            }
            for b in lines
        ],
    }

    s3.put_object(
        Bucket=bucket,
        Key=f"working/{doc_id}/extracted_fields.json",
        Body=json.dumps(extracted, indent=2),
        ContentType="application/json",
    )

    # --- confidence_gate ---
    low_conf_lines = [b for b in lines if b["Confidence"] < CONFIDENCE_THRESHOLD]
    if conf_mean >= CONFIDENCE_THRESHOLD:
        routing = "ACCEPT"
    elif conf_mean >= 60.0:
        routing = "REVIEW"
    else:
        routing = "ESCALATE"

    log_event(doc_id, "confidence_gate", "SUCCESS",
              confidence_mean=round(conf_mean, 1),
              routing=routing,
              low_confidence_lines=len(low_conf_lines))

    # --- post-process with Bedrock (opt-in, REVIEW/ESCALATE only) ---
    corrected_text = None
    if enable_postprocess and routing != "ACCEPT":
        t0 = time.monotonic()
        try:
            low_lines = [
                f"[{b['Confidence']:.0f}%] {b['Text']}"
                for b in lines if b["Confidence"] < CONFIDENCE_THRESHOLD
            ]
            prompt = (
                "You are an OCR post-correction assistant. Below is text extracted from a "
                "scanned historical document. Lines prefixed with confidence scores are the "
                "ones the OCR engine was least certain about.\n\n"
                "Low-confidence lines:\n" + "\n".join(low_lines) + "\n\n"
                "Full extracted text:\n" + full_text + "\n\n"
                "Please return the corrected full text. Fix OCR errors (misread characters, "
                "broken words, nonsense substitutions) while preserving the original meaning "
                "and structure. Only fix clear OCR errors — do not modernize spelling, "
                "grammar, or punctuation. Return ONLY the corrected text, no commentary."
            )

            bedrock_response = bedrock.invoke_model(
                modelId=BEDROCK_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            bedrock_body = json.loads(bedrock_response["body"].read())
            corrected_text = bedrock_body["content"][0]["text"]

            elapsed = int((time.monotonic() - t0) * 1000)
            log_event(doc_id, "postprocess", "SUCCESS", duration_ms=elapsed,
                      engine="bedrock", model=BEDROCK_MODEL_ID,
                      input_tokens=bedrock_body.get("usage", {}).get("input_tokens"),
                      output_tokens=bedrock_body.get("usage", {}).get("output_tokens"))

        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            log_event(doc_id, "postprocess", "ERROR", duration_ms=elapsed,
                      error_message=str(exc), error_type=type(exc).__name__)
            # Non-fatal — continue with uncorrected text

    # Write to output/ or review/ based on routing
    if routing == "ACCEPT":
        dest_prefix = "output"
    else:
        dest_prefix = "review"

    result = {
        "document_id": doc_id,
        "source_key": key,
        "routing": routing,
        "confidence": extracted["confidence"],
        "line_count": len(lines),
        "text": corrected_text if corrected_text else full_text,
    }
    if corrected_text:
        result["original_text"] = full_text
        result["postprocessing"] = {"engine": "bedrock", "model": BEDROCK_MODEL_ID}

    s3.put_object(
        Bucket=bucket,
        Key=f"{dest_prefix}/{doc_id}/result.json",
        Body=json.dumps(result, indent=2),
        ContentType="application/json",
    )

    log_event(doc_id, "persist", "SUCCESS",
              destination=f"s3://{bucket}/{dest_prefix}/{doc_id}/result.json",
              routing=routing)

    return {
        "document_id": doc_id,
        "status": "COMPLETE",
        "routing": routing,
        "confidence_mean": round(conf_mean, 1),
        "lines": len(lines),
    }
