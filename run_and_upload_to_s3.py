from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# =========================
# Configuration
# =========================
EXISTING_SCRIPT_PATH = Path(
    r"D:\IT\CDE\split_orders_by_date.py"
)
OUTPUT_DIRECTORY = Path(
    r"D:\IT\CDE\datasets"
)

S3_BUCKET_NAME = "smit-store-analysis"
S3_PREFIX = "orders/"
AWS_REGION = "us-east-1"   


def run_existing_script() -> None:
    if not EXISTING_SCRIPT_PATH.exists():
        raise FileNotFoundError(f"Existing script not found: {EXISTING_SCRIPT_PATH}")

    result = subprocess.run(
        [sys.executable, str(EXISTING_SCRIPT_PATH)],
        capture_output=True,
        text=True
    )

    print(result.stdout)

    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("Existing script failed, so upload to S3 was not started.")


def build_snapshot_s3_key(file_path: Path, prefix: str) -> str:
    # expected local filename format: orders_YYYY_MM_DD.csv
    name = file_path.stem

    if not name.startswith("orders_"):
        raise ValueError(f"Unexpected output filename format: {file_path.name}")

    date_part = name.replace("orders_", "", 1)
    snapshot_filename = f"snapshot_{date_part}.csv"

    return f"{prefix}{snapshot_filename}" if prefix else snapshot_filename


def upload_daily_files_to_s3(output_dir: Path, bucket_name: str, prefix: str = "") -> None:
    if not output_dir.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    s3_client = boto3.client("s3", region_name=AWS_REGION)

    daily_files = sorted(output_dir.glob("orders_????_??_??.csv"))
    if not daily_files:
        raise FileNotFoundError("No daily output files found to upload.")

    uploaded_count = 0

    for file_path in daily_files:
        s3_key = build_snapshot_s3_key(file_path, prefix)

        try:
            s3_client.upload_file(str(file_path), bucket_name, s3_key)
            print(f"Uploaded: {file_path.name} -> s3://{bucket_name}/{s3_key}")
            uploaded_count += 1
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Failed to upload '{file_path.name}' to S3: {exc}") from exc

    print("-" * 60)
    print(f"Upload completed successfully. Total files uploaded: {uploaded_count}")


def main() -> None:
    try:
        run_existing_script()
        upload_daily_files_to_s3(
            output_dir=OUTPUT_DIRECTORY,
            bucket_name=S3_BUCKET_NAME,
            prefix=S3_PREFIX,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()