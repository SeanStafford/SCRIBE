#!/usr/bin/env python3
"""
Command-line interface for the SCRIBE AWS pipeline.

Interact with the S3-backed Textract pipeline: upload documents, check
processing status, view extraction results, and query CloudWatch logs.

Commands:
    upload   - Upload a document to S3 (triggers automated pipeline)
    invoke   - Manually invoke Lambda on an already-uploaded document
    list     - List documents in the bucket by prefix
    results  - View extraction results for a document
    status   - Check Step Functions execution status
    logs     - View recent CloudWatch log events
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
import typer
from dotenv import load_dotenv

load_dotenv()

BUCKET = "scribe-idp-demo"
REGION = "us-east-1"
STATE_MACHINE_ARN = (
    "arn:aws:states:us-east-1:948532067960:stateMachine:scribe-document-pipeline"
)
LAMBDA_FUNCTION = "scribe-extract"
LOG_GROUP = f"/aws/lambda/{LAMBDA_FUNCTION}"

s3 = boto3.client("s3", region_name=REGION)
sfn = boto3.client("stepfunctions", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)

app = typer.Typer(
    help="SCRIBE AWS pipeline — upload, process, and inspect OCR results",
    invoke_without_command=True,
)


@app.callback()
def main(ctx: typer.Context):
    """Show help by default when no command is provided."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@app.command("upload")
def upload_command(
    file_path: str = typer.Argument(..., help="Local path to image or PDF"),
    doc_id: Optional[str] = typer.Option(
        None, "--id", "-i", help="Document ID (default: derived from filename)"
    ),
):
    """
    Upload a document to S3, triggering the automated pipeline.

    The file is placed under the raw/ prefix. EventBridge detects the upload
    and starts the Step Functions pipeline automatically.

    Examples:\n

        $ aws_pipeline.py upload data/images/grocery_contract.png

        $ aws_pipeline.py upload scan.pdf --id my-receipt
    """
    path = Path(file_path)
    if not path.exists():
        typer.secho(f"File not found: {file_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if doc_id is None:
        doc_id = path.stem

    now = datetime.now(tz=timezone.utc)
    key = f"raw/{now:%Y}/{now:%m}/{now:%d}/{doc_id}/original{path.suffix}"

    typer.echo(f"Uploading {path.name} → s3://{BUCKET}/{key}")
    s3.upload_file(str(path), BUCKET, key)
    typer.secho(f"Uploaded. Pipeline should trigger automatically.", fg=typer.colors.GREEN)
    typer.echo(f"  Document ID: {doc_id}")
    typer.echo(f"  S3 key:      {key}")
    typer.echo(f"\nCheck status with: aws_pipeline.py status")


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------


@app.command("invoke")
def invoke_command(
    doc_id: str = typer.Argument(..., help="Document ID (e.g., test-001 or grocery_contract)"),
    key: Optional[str] = typer.Option(
        None, "--key", "-k", help="Full S3 key (overrides doc_id lookup)"
    ),
    postprocess: bool = typer.Option(
        False, "--postprocess", "-p", help="Enable Bedrock post-correction (beta)"
    ),
):
    """
    Manually invoke the Lambda function on an uploaded document.

    Bypasses EventBridge/Step Functions — calls Lambda directly. Useful for
    re-processing or testing.

    The doc_id is matched as a substring against S3 keys in raw/. Use --key
    to specify an exact S3 path instead (doc_id is ignored when --key is set).

    Examples:\n

        $ aws_pipeline.py invoke test-001

        $ aws_pipeline.py invoke test-002 --postprocess   # With Bedrock correction

        $ aws_pipeline.py invoke _ --key raw/2026/05/05/test-001/original.png
    """
    if key is None:
        # Find the document in S3
        key = _find_document_key(doc_id)
        if key is None:
            typer.secho(f"No document found matching '{doc_id}'", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    typer.echo(f"Invoking Lambda on {key}{'  [postprocess=ON]' if postprocess else ''}...")
    response = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION,
        Payload=json.dumps({"bucket": BUCKET, "key": key, "postprocess": postprocess}),
    )
    result = json.loads(response["Payload"].read())

    if result.get("status") == "COMPLETE":
        typer.secho(f"Extraction complete", fg=typer.colors.GREEN)
        typer.echo(f"  Document:   {result['document_id']}")
        typer.echo(f"  Lines:      {result['lines']}")
        typer.echo(f"  Confidence: {result['confidence_mean']:.1f}%")
        typer.echo(f"  Routing:    {result['routing']}")
    elif result.get("status") == "ERROR":
        typer.secho(f"Extraction failed: {result.get('error')}", fg=typer.colors.RED)
    else:
        typer.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command("list")
def list_command(
    prefix: str = typer.Option("raw/", "--prefix", "-p", help="S3 prefix to list"),
    show_size: bool = typer.Option(False, "--size", "-s", help="Show file sizes"),
):
    """
    List documents in the S3 bucket.

    Examples:\n

        $ aws_pipeline.py list                     # List raw/ uploads

        $ aws_pipeline.py list -p output/          # List completed results

        $ aws_pipeline.py list -p working/ --size  # List working artifacts with sizes

        $ aws_pipeline.py list -p review/          # List documents needing review
    """
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    objects = response.get("Contents", [])

    if not objects:
        typer.echo(f"No objects under s3://{BUCKET}/{prefix}")
        return

    typer.secho(f"\ns3://{BUCKET}/{prefix}", fg=typer.colors.BLUE, bold=True)
    for obj in objects:
        if show_size:
            size_kb = obj["Size"] / 1024
            if size_kb > 1024:
                size_str = f"{size_kb / 1024:.1f} MB"
            else:
                size_str = f"{size_kb:.0f} KB"
            typer.echo(f"  {obj['Key']:70s}  {size_str:>10s}")
        else:
            typer.echo(f"  {obj['Key']}")

    typer.echo(f"\nTotal: {len(objects)} objects")


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@app.command("results")
def results_command(
    doc_id: str = typer.Argument(..., help="Document ID (e.g., test-001)"),
    full: bool = typer.Option(False, "--full", "-f", help="Show full extracted text"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Show raw Textract JSON"),
):
    """
    View extraction results for a document.

    Checks output/ first, then review/, then working/ for results.

    Examples:\n

        $ aws_pipeline.py results test-001          # Summary view

        $ aws_pipeline.py results test-001 --full   # Include extracted text

        $ aws_pipeline.py results test-001 --raw    # Raw Textract response
    """
    # Try output/ then review/
    result_data = None
    for prefix in ["output", "review"]:
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=f"{prefix}/{doc_id}/result.json")
            result_data = json.loads(obj["Body"].read())
            source = prefix
            break
        except s3.exceptions.NoSuchKey:
            continue

    if result_data is None:
        typer.secho(f"No results found for '{doc_id}'", fg=typer.colors.RED, err=True)
        typer.echo("  Check available documents with: aws_pipeline.py list -p output/")
        raise typer.Exit(code=1)

    # Display results
    routing = result_data.get("routing", "unknown")
    conf = result_data.get("confidence", {})
    routing_color = typer.colors.GREEN if routing == "ACCEPT" else typer.colors.YELLOW

    typer.secho(f"\n{doc_id}", fg=typer.colors.BLUE, bold=True)
    typer.echo(f"  Source:     {source}/")
    typer.secho(f"  Routing:    {routing}", fg=routing_color)
    typer.echo(f"  Lines:      {result_data.get('line_count', '?')}")
    typer.echo(f"  Confidence: min={conf.get('min', '?')}%  "
               f"mean={conf.get('mean', '?')}%  max={conf.get('max', '?')}%")

    if full:
        typer.echo(f"\n{'─' * 70}")
        typer.echo(result_data.get("text", "(no text)"))
        typer.echo(f"{'─' * 70}")

    if raw:
        try:
            obj = s3.get_object(
                Bucket=BUCKET, Key=f"working/{doc_id}/textract_response.json"
            )
            typer.echo(f"\n{'─' * 70}")
            typer.echo(obj["Body"].read().decode())
        except Exception:
            typer.secho("Raw Textract response not found in working/", fg=typer.colors.YELLOW)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status_command(
    limit: int = typer.Option(5, "--limit", "-n", help="Number of recent executions to show"),
):
    """
    Check recent Step Functions pipeline executions.

    Examples:\n

        $ aws_pipeline.py status          # Last 5 executions

        $ aws_pipeline.py status -n 20    # Last 20 executions
    """
    response = sfn.list_executions(
        stateMachineArn=STATE_MACHINE_ARN,
        maxResults=limit,
    )
    executions = response.get("executions", [])

    if not executions:
        typer.echo("No pipeline executions found.")
        return

    typer.secho(f"\nRecent pipeline executions", fg=typer.colors.BLUE, bold=True)

    for ex in executions:
        status = ex["status"]
        start = ex["startDate"]
        start_str = start.strftime("%Y-%m-%d %H:%M:%S")

        if status == "SUCCEEDED":
            color = typer.colors.GREEN
        elif status == "FAILED":
            color = typer.colors.RED
        elif status == "RUNNING":
            color = typer.colors.YELLOW
        else:
            color = typer.colors.WHITE

        duration = ""
        if "stopDate" in ex:
            dur_sec = (ex["stopDate"] - start).total_seconds()
            duration = f"  ({dur_sec:.1f}s)"

        typer.echo(f"  {start_str}  ", nl=False)
        typer.secho(f"{status:12s}", fg=color, nl=False)
        typer.echo(duration)

    typer.echo(f"\nTotal: {len(executions)}")


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@app.command("logs")
def logs_command(
    minutes: int = typer.Option(30, "--minutes", "-m", help="Look back this many minutes"),
    doc_id: Optional[str] = typer.Option(
        None, "--doc", "-d", help="Filter to a specific document ID"
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max log events to return"),
):
    """
    View recent CloudWatch log events from the Lambda function.

    Shows structured pipeline events (validate, extract, confidence_gate, persist).

    Examples:\n

        $ aws_pipeline.py logs                     # Last 30 minutes

        $ aws_pipeline.py logs -m 60               # Last hour

        $ aws_pipeline.py logs --doc test-001      # Filter to one document

        $ aws_pipeline.py logs -n 10               # Only 10 most recent
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (minutes * 60 * 1000)

    try:
        response = logs.filter_log_events(
            logGroupName=LOG_GROUP,
            startTime=start_ms,
            endTime=now_ms,
            limit=limit,
        )
    except logs.exceptions.ResourceNotFoundException:
        typer.secho(f"Log group {LOG_GROUP} not found (Lambda may not have run yet)",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    events = response.get("events", [])

    if not events:
        typer.echo(f"No log events in the last {minutes} minutes.")
        return

    typer.secho(f"\nLambda logs (last {minutes}m)", fg=typer.colors.BLUE, bold=True)

    for event in events:
        msg = event["message"].strip()

        # Try to parse as our structured JSON
        # Lambda prefixes logs with: [INFO]\ttimestamp\trequest_id\t{json}
        if "\t" in msg:
            parts = msg.split("\t")
            msg = parts[-1].strip()
        try:
            data = json.loads(msg)
            if "document_id" not in data:
                continue
            if doc_id and data.get("document_id") != doc_id:
                continue

            ts = data.get("timestamp", "")[:19]
            did = data.get("document_id", "?")
            step = data.get("step", "?")
            status = data.get("status", "?")
            dur = data.get("duration_ms")
            details = data.get("details", {})

            color = typer.colors.GREEN if status == "SUCCESS" else typer.colors.RED
            dur_str = f"  {dur}ms" if dur else ""

            typer.echo(f"  {ts}  {did:20s}  {step:18s}  ", nl=False)
            typer.secho(f"{status:7s}", fg=color, nl=False)
            typer.echo(dur_str, nl=False)

            # Show key details inline
            extras = []
            if "confidence_mean" in details:
                extras.append(f"conf={details['confidence_mean']}%")
            if "routing" in details:
                extras.append(f"route={details['routing']}")
            if "lines" in details:
                extras.append(f"lines={details['lines']}")
            if "error_message" in details:
                extras.append(f"err={details['error_message'][:50]}")
            if extras:
                typer.echo(f"  [{', '.join(extras)}]", nl=False)

            typer.echo()

        except (json.JSONDecodeError, KeyError):
            # Not our structured log — skip Lambda platform noise
            continue

    typer.echo()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command("stats")
def stats_command(
    hours: int = typer.Option(24, "--hours", "-h", help="Look back this many hours"),
):
    """
    Show pipeline performance statistics from CloudWatch logs.

    Aggregates latency, cost, confidence, and routing from recent executions.

    Examples:\n

        $ aws_pipeline.py stats              # Last 24 hours

        $ aws_pipeline.py stats --hours 1    # Last hour
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (hours * 3600 * 1000)

    # Collect structured log events
    try:
        response = logs.filter_log_events(
            logGroupName=LOG_GROUP,
            startTime=start_ms,
            endTime=now_ms,
            limit=500,
        )
    except logs.exceptions.ResourceNotFoundException:
        typer.secho(f"Log group {LOG_GROUP} not found", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    # Parse structured events
    events_by_doc = {}
    for event in response.get("events", []):
        msg = event["message"].strip()
        if "\t" in msg:
            msg = msg.split("\t")[-1].strip()
        try:
            data = json.loads(msg)
            if "document_id" not in data:
                continue
            doc_id = data["document_id"]
            if doc_id not in events_by_doc:
                events_by_doc[doc_id] = []
            events_by_doc[doc_id].append(data)
        except (json.JSONDecodeError, KeyError):
            continue

    # Collect Lambda REPORT lines for cost/memory
    reports = []
    for event in response.get("events", []):
        msg = event["message"].strip()
        if msg.startswith("REPORT"):
            report = {}
            for part in msg.split("\t"):
                if "Duration" in part and "Billed" in part:
                    report["billed_ms"] = float(part.split(":")[1].strip().split()[0])
                elif "Max Memory Used" in part:
                    report["memory_mb"] = int(part.split(":")[1].strip().split()[0])
                elif "Init Duration" in part:
                    report["init_ms"] = float(part.split(":")[1].strip().split()[0])
            if report:
                reports.append(report)

    if not events_by_doc:
        typer.echo(f"No pipeline events in the last {hours} hours.")
        return

    # --- Documents ---
    doc_count = len(events_by_doc)
    routing_counts = {"ACCEPT": 0, "REVIEW": 0, "ESCALATE": 0, "ERROR": 0}
    confidences = []
    for doc_id, events in events_by_doc.items():
        for ev in events:
            if ev.get("step") == "confidence_gate":
                route = ev.get("details", {}).get("routing", "?")
                routing_counts[route] = routing_counts.get(route, 0) + 1
                conf = ev.get("details", {}).get("confidence_mean")
                if conf:
                    confidences.append(conf)
            if ev.get("status") == "ERROR" and ev.get("step") == "extract":
                routing_counts["ERROR"] += 1

    typer.secho(f"\nPipeline Statistics (last {hours}h)", fg=typer.colors.BLUE, bold=True)
    typer.echo("=" * 50)

    typer.echo(f"\nDocuments processed: {doc_count}")
    for route, count in routing_counts.items():
        if count > 0:
            color = typer.colors.GREEN if route == "ACCEPT" else (
                typer.colors.RED if route == "ERROR" else typer.colors.YELLOW
            )
            typer.echo(f"  ", nl=False)
            typer.secho(f"{route:12s}", fg=color, nl=False)
            typer.echo(f" {count}")

    # --- Latency ---
    step_durations = {}
    for doc_id, events in events_by_doc.items():
        for ev in events:
            step = ev.get("step", "?")
            dur = ev.get("duration_ms")
            if dur:
                if step not in step_durations:
                    step_durations[step] = []
                step_durations[step].append(dur)

    if step_durations:
        typer.echo(f"\nLatency (ms)")
        typer.echo(f"  {'Step':20s}  {'avg':>8s}  {'min':>8s}  {'max':>8s}  {'count':>6s}")
        typer.echo(f"  {'─' * 56}")
        for step in ["validate", "extract", "confidence_gate", "persist"]:
            if step in step_durations:
                durs = step_durations[step]
                avg = sum(durs) / len(durs)
                typer.echo(
                    f"  {step:20s}  {avg:8.0f}  {min(durs):8.0f}  {max(durs):8.0f}  {len(durs):6d}"
                )

    # --- Confidence ---
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        typer.echo(f"\nConfidence")
        typer.echo(f"  Mean:  {avg_conf:.1f}%")
        typer.echo(f"  Min:   {min(confidences):.1f}%")
        typer.echo(f"  Max:   {max(confidences):.1f}%")

    # --- Cost ---
    textract_cost_per_page = 0.0015  # DetectDocumentText
    lambda_cost_per_gb_sec = 0.0000166667
    textract_pages = doc_count  # 1 page per doc (sync API)

    typer.echo(f"\nEstimated Cost")
    textract_total = textract_pages * textract_cost_per_page
    typer.echo(f"  Textract:  {textract_pages} pages x ${textract_cost_per_page}/page"
               f"  = ${textract_total:.4f}")

    if reports:
        total_billed_ms = sum(r.get("billed_ms", 0) for r in reports)
        avg_memory_mb = sum(r.get("memory_mb", 0) for r in reports) / len(reports)
        gb_seconds = (total_billed_ms / 1000) * (avg_memory_mb / 1024)
        lambda_total = gb_seconds * lambda_cost_per_gb_sec
        typer.echo(f"  Lambda:    {total_billed_ms / 1000:.1f}s billed, "
                   f"{avg_memory_mb:.0f} MB avg  = ${lambda_total:.4f}")
        typer.echo(f"  Total:     ${textract_total + lambda_total:.4f}")

        typer.echo(f"\nLambda Runtime")
        typer.echo(f"  Invocations:     {len(reports)}")
        typer.echo(f"  Avg billed:      {total_billed_ms / len(reports):.0f} ms")
        typer.echo(f"  Avg memory used: {avg_memory_mb:.0f} MB / 256 MB")
        cold_starts = sum(1 for r in reports if "init_ms" in r)
        if cold_starts:
            avg_init = sum(r["init_ms"] for r in reports if "init_ms" in r) / cold_starts
            typer.echo(f"  Cold starts:     {cold_starts} (avg init: {avg_init:.0f} ms)")

    typer.echo()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command("search")
def search_command(
    query: str = typer.Argument(..., help="Substring to match against document IDs"),
):
    """
    Search for documents in S3 by name.

    Matches the query as a substring against all document keys in the raw/ prefix.

    Examples:\n

        $ aws_pipeline.py search test         # Find all docs with 'test' in the key

        $ aws_pipeline.py search grocery      # Find the grocery contract

        $ aws_pipeline.py search 2026/05/05   # Find all docs from a specific date
    """
    matches = _find_document_keys(query)

    if not matches:
        typer.echo(f"No documents matching '{query}'")
        return

    typer.secho(f"\nDocuments matching '{query}':", fg=typer.colors.BLUE, bold=True)
    for key in matches:
        parts = key.split("/")
        doc_id = parts[-2] if len(parts) >= 2 else key
        typer.echo(f"  {doc_id:30s}  {key}")

    typer.echo(f"\nTotal: {len(matches)}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _find_document_keys(doc_id: str) -> list[str]:
    """Find all original documents in raw/ matching doc_id as a substring."""
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/")
    matches = []
    for obj in response.get("Contents", []):
        key = obj["Key"]
        if doc_id in key and "original" in key:
            matches.append(key)
    return matches


def _find_document_key(doc_id: str) -> Optional[str]:
    """Find exactly one document matching doc_id. Returns None if zero, exits if ambiguous."""
    matches = _find_document_keys(doc_id)
    if len(matches) == 0:
        return None
    if len(matches) == 1:
        return matches[0]
    typer.secho(f"Ambiguous: '{doc_id}' matches {len(matches)} documents:", fg=typer.colors.RED)
    for key in matches:
        parts = key.split("/")
        did = parts[-2] if len(parts) >= 2 else key
        typer.echo(f"  {did:30s}  {key}")
    typer.echo(f"\nBe more specific, or use: aws_pipeline.py search {doc_id}")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
