from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd

# =========================
# Configuration
# =========================
DEFAULT_INPUT_CSV = Path(r"D:\IT\CDE\datasets\Superstore.csv")
OUTPUT_DIRECTORY = Path(r"D:\IT\CDE\datasets")
DEFAULT_CHUNK_SIZE = 100_000

DATE_COLUMN_CANDIDATES = [
    "date",
    "order_date",
    "orderdate",
    "transaction_date",
    "transactiondate",
    "purchase_date",
    "purchasedate",
    "invoice_date",
    "invoicedate",
    "sale_date",
    "saledate",
    "created_date",
    "createddate",
    "created_at",
    "createdat",
    "timestamp",
    "order_timestamp",
    "datetime",
]

COMMON_ENCODINGS = [
    "utf-8",
    "utf-8-sig",
    "cp1252",
    "latin1",
    "iso-8859-1",
]


# =========================
# Utility Functions
# =========================
def normalize_column_name(column_name: str) -> str:
    return "".join(ch.lower() for ch in str(column_name) if ch.isalnum())


def detect_input_file(cli_arg: Optional[str]) -> Path:
    if cli_arg:
        return Path(cli_arg).expanduser()
    return DEFAULT_INPUT_CSV


def detect_working_encoding(input_csv: Path, chunk_size: int = 5000) -> str:
    """
    Validate encoding by actually reading a real chunk, not just the header.
    """
    last_error = None

    for encoding in COMMON_ENCODINGS:
        try:
            reader = pd.read_csv(
                input_csv,
                encoding=encoding,
                encoding_errors="strict",
                chunksize=chunk_size,
                low_memory=False,
            )
            next(reader)
            return encoding
        except StopIteration:
            return encoding
        except Exception as exc:
            last_error = exc

    raise ValueError(
        f"Could not read CSV with tested encodings: {COMMON_ENCODINGS}. "
        f"Last error: {last_error}"
    )


def read_csv_header(input_csv: Path, encoding: str) -> List[str]:
    try:
        header_df = pd.read_csv(
            input_csv,
            nrows=0,
            encoding=encoding,
            encoding_errors="replace",
        )
        return header_df.columns.tolist()
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {input_csv}")
    except pd.errors.EmptyDataError:
        raise ValueError(f"Input CSV is empty: {input_csv}")
    except Exception as exc:
        raise RuntimeError(f"Failed to read CSV header from '{input_csv}': {exc}") from exc


def detect_date_column(columns: List[str]) -> str:
    normalized_map = {normalize_column_name(col): col for col in columns}

    for candidate in DATE_COLUMN_CANDIDATES:
        normalized_candidate = normalize_column_name(candidate)
        if normalized_candidate in normalized_map:
            return normalized_map[normalized_candidate]

    fallback_keywords = ("date", "time", "created", "timestamp")
    for original_col in columns:
        normalized_col = normalize_column_name(original_col)
        if any(keyword in normalized_col for keyword in fallback_keywords):
            return original_col

    raise ValueError(
        "No valid date column found. Expected a column like: "
        "date, order_date, transaction_date, created_at, timestamp, etc."
    )


def ensure_output_directory(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)


def remove_old_output_files(output_dir: Path) -> None:
    """
    Delete previously generated orders_YYYY_MM_DD.csv files
    to avoid duplicate appends on rerun.
    """
    for file_path in output_dir.glob("orders_????_??_??.csv"):
        try:
            file_path.unlink()
        except Exception as exc:
            raise PermissionError(f"Unable to delete old output file: {file_path}. {exc}") from exc


def output_file_path_for_date(output_dir: Path, date_value: pd.Timestamp) -> Path:
    filename = f"orders_{date_value.strftime('%Y_%m_%d')}.csv"
    return output_dir / filename


def append_group_to_csv(group_df: pd.DataFrame, output_path: Path) -> None:
    write_header = not output_path.exists()
    group_df.to_csv(
        output_path,
        mode="a",
        index=False,
        header=write_header,
        encoding="utf-8",
    )


def get_csv_reader(input_csv: Path, encoding: str, chunk_size: int):
    try:
        return pd.read_csv(
            input_csv,
            chunksize=chunk_size,
            low_memory=False,
            encoding=encoding,
            encoding_errors="replace",
        )
    except pd.errors.EmptyDataError:
        raise ValueError(f"Input CSV is empty: {input_csv}")
    except Exception as exc:
        raise RuntimeError(f"Failed to open CSV '{input_csv}': {exc}") from exc


# =========================
# Core Processing
# =========================
def split_csv_by_date(
    input_csv: Path,
    output_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> None:
    if not input_csv.exists():
        raise FileNotFoundError(f"Input file not found: {input_csv}")

    ensure_output_directory(output_dir)
    remove_old_output_files(output_dir)

    detected_encoding = detect_working_encoding(input_csv)
    columns = read_csv_header(input_csv, detected_encoding)
    date_column = detect_date_column(columns)

    print(f"Input file: {input_csv}")
    print(f"Detected encoding: {detected_encoding}")
    print(f"Detected date column: {date_column}")
    print(f"Output directory: {output_dir}")
    print(f"Chunk size: {chunk_size}")
    print("-" * 60)

    total_rows_read = 0
    total_rows_written = 0
    total_invalid_dates = 0
    chunk_number = 0
    found_any_valid_date = False

    chunk_iterator = get_csv_reader(input_csv, detected_encoding, chunk_size)

    for chunk in chunk_iterator:
        chunk_number += 1
        rows_in_chunk = len(chunk)
        total_rows_read += rows_in_chunk

        print(f"Reading chunk {chunk_number} ({rows_in_chunk} rows)...")

        if date_column not in chunk.columns:
            raise ValueError(
                f"Date column '{date_column}' was detected from header but is missing in chunk {chunk_number}."
            )

        parsed_dates = pd.to_datetime(chunk[date_column], errors="coerce")

        invalid_dates_in_chunk = int(parsed_dates.isna().sum())
        total_invalid_dates += invalid_dates_in_chunk

        valid_mask = parsed_dates.notna()
        if not valid_mask.any():
            print(f"Chunk {chunk_number}: no valid dates found, skipping.")
            print("-" * 60)
            continue

        found_any_valid_date = True

        valid_chunk = chunk.loc[valid_mask].copy()
        valid_chunk[date_column] = parsed_dates.loc[valid_mask]
        valid_chunk["_split_date"] = valid_chunk[date_column].dt.normalize()

        for split_date, group_df in valid_chunk.groupby("_split_date", sort=True):
            print(f"Processing date: {split_date.strftime('%Y-%m-%d')}")
            output_path = output_file_path_for_date(output_dir, split_date)
            group_to_save = group_df.drop(columns=["_split_date"])
            append_group_to_csv(group_to_save, output_path)
            total_rows_written += len(group_to_save)

        print(
            f"Completed chunk {chunk_number}: "
            f"written={len(valid_chunk)}, invalid_dates={invalid_dates_in_chunk}"
        )
        print("-" * 60)

    if not found_any_valid_date:
        raise ValueError(
            "No valid date values were found in the detected date column. "
            "Please check the source file date format."
        )

    print("Processing completed successfully.")
    print(f"Total rows read: {total_rows_read}")
    print(f"Total rows written: {total_rows_written}")
    print(f"Total invalid date rows skipped: {total_invalid_dates}")


# =========================
# Main Entry Point
# =========================
def main() -> None:
    try:
        input_arg = sys.argv[1] if len(sys.argv) > 1 else None
        input_csv = detect_input_file(input_arg)
        split_csv_by_date(
            input_csv=input_csv,
            output_dir=OUTPUT_DIRECTORY,
            chunk_size=DEFAULT_CHUNK_SIZE,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    except PermissionError as exc:
        print(f"ERROR: Permission denied: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: Unexpected failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()