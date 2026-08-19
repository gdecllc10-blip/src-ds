"""
Reads a distributor Excel/CSV sheet and figures out which columns hold the
UPC, wholesale cost, and (optionally) description/title/quantity, without
requiring the user to reformat their file.

Detection strategy:
1. Try common header-name matches (case-insensitive, punctuation-insensitive).
2. Fall back to content sniffing: a column full of 8/12/13/14-digit numeric
   strings is almost certainly the UPC/EAN column; a numeric column with a
   '$' formatted header or currency-looking values is probably cost.
3. Anything it can't confidently detect is left for the user to pick from a
   dropdown in the UI - we never silently guess wrong on cost, since that
   feeds directly into the ROI math.
"""
import re
import pandas as pd

UPC_HEADER_HINTS = [
    "upc", "upc code", "upc#", "ean", "gtin", "asin", "barcode", "isbn",
]
COST_HEADER_HINTS = [
    "cost", "wholesale", "wholesale cost", "your cost", "unit cost",
    "price", "buy price", "case cost", "net cost", "dist cost",
]
DESC_HEADER_HINTS = [
    "description", "title", "item", "product", "item description",
    "product name", "name",
]
QTY_HEADER_HINTS = [
    "qty", "quantity", "case pack", "pack", "case qty", "units",
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _best_header_match(columns, hints):
    norm_cols = {c: _norm(c) for c in columns}
    hint_norms = [_norm(h) for h in hints]
    # exact normalized match first
    for c, nc in norm_cols.items():
        if nc in hint_norms:
            return c
    # substring match second
    for c, nc in norm_cols.items():
        if any(h in nc for h in hint_norms):
            return c
    return None


def _looks_like_upc_series(series: pd.Series) -> bool:
    sample = series.dropna().astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    sample = sample[sample != ""]
    if len(sample) == 0:
        return False
    sample = sample.head(50)
    digit_like = sample.str.match(r"^\d{8,14}$")
    return digit_like.mean() > 0.7


def _looks_like_cost_series(series: pd.Series) -> bool:
    cleaned = pd.to_numeric(
        series.astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce"
    )
    valid = cleaned.dropna()
    if len(valid) < max(3, 0.5 * len(series)):
        return False
    # wholesale costs are usually modest positive numbers, not huge IDs
    return (valid > 0).mean() > 0.9 and valid.median() < 100000


def load_sheet(file) -> pd.DataFrame:
    """Load an uploaded xlsx/xls/csv file into a DataFrame, header row auto-detected."""
    name = getattr(file, "name", "")
    if name.lower().endswith(".csv"):
        raw = pd.read_csv(file, header=None, dtype=str)
    else:
        raw = pd.read_excel(file, header=None, dtype=str)

    # Some distributor sheets have a title/logo row or two before the real header.
    # Scan the first 10 rows for the one that looks most like a header (mostly
    # non-numeric, non-null text cells).
    header_row_idx = 0
    best_score = -1
    for i in range(min(10, len(raw))):
        row = raw.iloc[i]
        non_null = row.notna().sum()
        text_like = row.dropna().astype(str).apply(lambda x: not re.match(r"^-?\d+(\.\d+)?$", x.strip())).sum()
        score = non_null + text_like
        if non_null >= 2 and score > best_score:
            best_score = score
            header_row_idx = i

    header = raw.iloc[header_row_idx].fillna("").astype(str).tolist()
    df = raw.iloc[header_row_idx + 1:].copy()
    df.columns = [h.strip() if h.strip() else f"col_{i}" for i, h in enumerate(header)]
    df = df.dropna(how="all")
    df = df.reset_index(drop=True)
    return df


def detect_columns(df: pd.DataFrame) -> dict:
    """Return best-guess column names for upc/cost/description/qty, or None if unsure."""
    cols = list(df.columns)
    result = {
        "upc": _best_header_match(cols, UPC_HEADER_HINTS),
        "cost": _best_header_match(cols, COST_HEADER_HINTS),
        "description": _best_header_match(cols, DESC_HEADER_HINTS),
        "qty": _best_header_match(cols, QTY_HEADER_HINTS),
    }

    # content-sniffing fallback for upc/cost if header match failed
    if result["upc"] is None:
        for c in cols:
            if _looks_like_upc_series(df[c]):
                result["upc"] = c
                break

    if result["cost"] is None:
        for c in cols:
            if c == result["upc"]:
                continue
            if _looks_like_cost_series(df[c]):
                result["cost"] = c
                break

    return result


def clean_upc(value) -> str:
    """Normalize a UPC/EAN cell to a bare digit string."""
    if value is None:
        return ""
    s = str(value).strip()
    s = re.sub(r"\.0$", "", s)
    s = re.sub(r"[^\d]", "", s)
    return s


def clean_cost(value):
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None
