#!/usr/bin/env python3
"""
HITL review interface for SCRIBE AWS pipeline.

Lists documents routed to review/, displays extracted text alongside
the original image, and lets you accept or correct results.

Commands:
    queue    - List documents waiting for review
    show     - Display extraction results for a document
    accept   - Accept results and move to output/
    correct  - Open corrected text in editor, save to output/
"""

import json
import subprocess
import tempfile
from pathlib import Path

import boto3
import typer
from dotenv import load_dotenv

load_dotenv()

BUCKET = "scribe-idp-demo"
REGION = "us-east-1"

s3 = boto3.client("s3", region_name=REGION)

app = typer.Typer(
    help="SCRIBE HITL review — inspect, accept, or correct low-confidence extractions",
    invoke_without_command=True,
)


@app.callback()
def main(ctx: typer.Context):
    """Show help by default when no command is provided."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command("queue")
def queue_command():
    """
    List documents waiting for human review.

    Examples:\n

        $ review.py queue
    """
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix="review/")
    objects = response.get("Contents", [])

    if not objects:
        typer.secho("Review queue is empty.", fg=typer.colors.GREEN)
        return

    # Extract unique doc IDs
    doc_ids = set()
    for obj in objects:
        parts = obj["Key"].split("/")
        if len(parts) >= 2:
            doc_ids.add(parts[1])

    typer.secho(f"\nDocuments awaiting review: {len(doc_ids)}", fg=typer.colors.YELLOW, bold=True)
    for doc_id in sorted(doc_ids):
        # Get confidence from result.json
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=f"review/{doc_id}/result.json")
            result = json.loads(obj["Body"].read())
            conf = result.get("confidence", {}).get("mean", "?")
            lines = result.get("line_count", "?")
            typer.echo(f"  {doc_id:30s}  conf={conf}%  lines={lines}")
        except Exception:
            typer.echo(f"  {doc_id}")

    typer.echo()


@app.command("show")
def show_command(
    doc_id: str = typer.Argument(..., help="Document ID to review"),
    open_image: bool = typer.Option(False, "--image", "-i", help="Open the original image"),
):
    """
    Display extraction results for a document in the review queue.

    Examples:\n

        $ review.py show test-002

        $ review.py show test-002 --image    # Also open the original image
    """
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"review/{doc_id}/result.json")
        result = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        typer.secho(f"'{doc_id}' not found in review queue", fg=typer.colors.RED, err=True)
        typer.echo("  Check queue with: review.py queue")
        raise typer.Exit(code=1)

    conf = result.get("confidence", {})
    typer.secho(f"\n{doc_id}", fg=typer.colors.BLUE, bold=True)
    typer.echo(f"  Routing:    {result.get('routing', '?')}")
    typer.echo(f"  Lines:      {result.get('line_count', '?')}")
    typer.echo(
        f"  Confidence: min={conf.get('min', '?')}%  "
        f"mean={conf.get('mean', '?')}%  max={conf.get('max', '?')}%"
    )

    typer.echo(f"\n{'─' * 70}")
    typer.echo(result.get("text", "(no text)"))
    typer.echo(f"{'─' * 70}")

    if open_image:
        _open_original(doc_id)

    typer.echo(f"\nActions:")
    typer.echo(f"  review.py accept {doc_id}       # Accept as-is")
    typer.echo(f"  review.py correct {doc_id}      # Edit text before accepting")


@app.command("accept")
def accept_command(
    doc_id: str = typer.Argument(..., help="Document ID to accept"),
):
    """
    Accept review results as-is and move to output/.

    Examples:\n

        $ review.py accept test-002
    """
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"review/{doc_id}/result.json")
        result = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        typer.secho(f"'{doc_id}' not found in review queue", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    result["routing"] = "ACCEPTED_BY_REVIEWER"
    result["review"] = {"action": "accepted", "original_routing": result.get("routing", "REVIEW")}

    s3.put_object(
        Bucket=BUCKET,
        Key=f"output/{doc_id}/result.json",
        Body=json.dumps(result, indent=2),
        ContentType="application/json",
    )

    # Delete from review/
    s3.delete_object(Bucket=BUCKET, Key=f"review/{doc_id}/result.json")

    typer.secho(f"Accepted {doc_id} — moved to output/", fg=typer.colors.GREEN)


@app.command("correct")
def correct_command(
    doc_id: str = typer.Argument(..., help="Document ID to correct"),
    editor: str = typer.Option("vim", "--editor", "-e", help="Text editor to use"),
):
    """
    Open extracted text in an editor for correction, then save to output/.

    Examples:\n

        $ review.py correct test-002

        $ review.py correct test-002 --editor vim
    """
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"review/{doc_id}/result.json")
        result = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        typer.secho(f"'{doc_id}' not found in review queue", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    original_text = result.get("text", "")

    # Write text to temp file for editing
    with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{doc_id}.txt", delete=False) as f:
        f.write(original_text)
        tmp_path = f.name

    typer.echo(f"Opening {tmp_path} in {editor}...")
    subprocess.run([editor, tmp_path])

    corrected_text = Path(tmp_path).read_text()

    if corrected_text == original_text:
        typer.secho("No changes made.", fg=typer.colors.YELLOW)
        confirm = typer.confirm("Accept without changes?")
        if not confirm:
            raise typer.Exit()

    result["text"] = corrected_text
    result["routing"] = "CORRECTED_BY_REVIEWER"
    result["review"] = {
        "action": "corrected",
        "original_routing": result.get("routing", "REVIEW"),
        "original_text": original_text,
    }

    s3.put_object(
        Bucket=BUCKET,
        Key=f"output/{doc_id}/result.json",
        Body=json.dumps(result, indent=2),
        ContentType="application/json",
    )

    s3.delete_object(Bucket=BUCKET, Key=f"review/{doc_id}/result.json")

    # Clean up
    Path(tmp_path).unlink(missing_ok=True)

    typer.secho(f"Corrected {doc_id} — moved to output/", fg=typer.colors.GREEN)


def _open_original(doc_id: str):
    """Download and open the original image."""
    keys = []
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/")
    for obj in response.get("Contents", []):
        if doc_id in obj["Key"] and "original" in obj["Key"]:
            keys.append(obj["Key"])

    if not keys:
        typer.secho(f"  Original image not found for {doc_id}", fg=typer.colors.YELLOW)
        return

    tmp_path = f"/tmp/scribe_review_{doc_id}{Path(keys[0]).suffix}"
    s3.download_file(BUCKET, keys[0], tmp_path)

    try:
        subprocess.Popen(
            ["xdg-open", tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        typer.echo(f"  Opened: {tmp_path}")
    except FileNotFoundError:
        typer.echo(f"  Downloaded to: {tmp_path}")


if __name__ == "__main__":
    app()
