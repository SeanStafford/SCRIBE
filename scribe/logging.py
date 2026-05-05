"""
Structured pipeline event logging for SCRIBE.

JSON Lines format — one event per line, matching the structure that would go to
CloudWatch Logs in an AWS deployment. Locally, writes to a JSONL file in the
pipeline output directory.

Usage:
    from scribe.logging import PipelineLogger

    log = PipelineLogger(out_dir)
    log.event("grocery_contract", "preprocess", "SUCCESS", duration_ms=120,
              details={"scale": 4, "clahe_clip": 2.0})
"""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class PipelineLogger:
    """Append-only JSONL logger for pipeline events."""

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.out_dir / "pipeline_events.jsonl"

    def event(
        self,
        document_id: str,
        step: str,
        status: str,
        duration_ms: int | None = None,
        details: dict | None = None,
    ) -> dict:
        """Log a single pipeline event.

        Args:
            document_id: Canonical image name (e.g., "grocery_contract")
            step: Pipeline step — validate, preprocess, extract, postprocess, confidence_gate
            status: SUCCESS, ERROR, or SKIPPED
            duration_ms: Wall-clock time for this step
            details: Step-specific data (engine, confidence, error_message, etc.)
        """
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

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        return record
