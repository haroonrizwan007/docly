"""
csv_handler.py
================
CSV validation + duplicate detection for lead import — Phase 1.

Rules (per Master Specification):
- Required columns: business_name, website
- Optional columns: category, address, city, country, phone, owner_name,
  email, linkedin
- Missing optional columns must NOT crash the app.
- Duplicate detection is based on normalized website / email / business_name,
  both within the uploaded file and against leads already stored for the
  target campaign.
"""

import pandas as pd

import config
import database


class CsvValidationError(Exception):
    pass


def load_csv(file_obj) -> pd.DataFrame:
    """Read an uploaded CSV into a DataFrame. Raises CsvValidationError on
    unreadable files."""
    try:
        df = pd.read_csv(file_obj, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise CsvValidationError(f"Could not read CSV file: {exc}") from exc

    # Normalize column names (strip whitespace, lowercase) so headers like
    # " Business Name " or "Website" still match.
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def validate_columns(df: pd.DataFrame) -> list:
    """Return a list of human-readable warnings. Raises CsvValidationError
    if a required column is missing."""
    warnings = []

    missing_required = [c for c in config.CSV_REQUIRED_COLUMNS if c not in df.columns]
    if missing_required:
        raise CsvValidationError(
            f"Missing required column(s): {', '.join(missing_required)}. "
            f"Required columns are: {', '.join(config.CSV_REQUIRED_COLUMNS)}."
        )

    missing_optional = [c for c in config.CSV_OPTIONAL_COLUMNS if c not in df.columns]
    if missing_optional:
        warnings.append(
            f"Optional column(s) not found and will be left blank: "
            f"{', '.join(missing_optional)}."
        )

    unknown_cols = [c for c in df.columns if c not in config.CSV_ALL_COLUMNS]
    if unknown_cols:
        warnings.append(
            f"Unrecognized column(s) will be ignored: {', '.join(unknown_cols)}."
        )

    return warnings


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure every expected column exists (fill blanks for missing optional
    ones), and drop rows missing a required field."""
    out = df.copy()

    for col in config.CSV_ALL_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    out = out[list(config.CSV_ALL_COLUMNS)]

    for col in config.CSV_ALL_COLUMNS:
        out[col] = out[col].fillna("").astype(str).str.strip()

    # Drop rows with an empty required field (can't import a lead with no
    # business_name or website).
    before = len(out)
    out = out[(out["business_name"] != "") & (out["website"] != "")]
    dropped = before - len(out)

    return out, dropped


def detect_duplicates(df: pd.DataFrame, campaign_id: int):
    """
    Split rows into (unique_rows, duplicate_rows) using normalized
    website / email / business_name, checked both:
      - against leads already stored for this campaign, and
      - against earlier rows within the same file.
    Returns (unique_df, duplicate_df).
    """
    existing_websites, existing_emails, existing_names = database.get_existing_keys(
        campaign_id
    )

    seen_websites = set(existing_websites)
    seen_emails = set(existing_emails)
    seen_names = set(existing_names)

    keep_mask = []
    for _, row in df.iterrows():
        nw = database.normalize_website(row["website"])
        ne = database.normalize_email(row["email"])
        nn = database.normalize_business_name(row["business_name"])

        is_dup = (
            (nw and nw in seen_websites)
            or (ne and ne in seen_emails)
            or (nn and nn in seen_names)
        )

        if is_dup:
            keep_mask.append(False)
        else:
            keep_mask.append(True)
            if nw:
                seen_websites.add(nw)
            if ne:
                seen_emails.add(ne)
            if nn:
                seen_names.add(nn)

    df = df.reset_index(drop=True)
    mask = pd.Series(keep_mask)
    unique_df = df[mask].reset_index(drop=True)
    duplicate_df = df[~mask].reset_index(drop=True)

    return unique_df, duplicate_df
