"""
Thin wrapper around the Keepa Product API for Amazon lookups by UPC/EAN.

Docs: https://keepa.com/#!discuss/t/product-object/116
      https://keepa.com/#!discuss/t/request-products/109

IMPORTANT - why this queries one UPC per request instead of batching 100
comma-separated codes into one call (an earlier version of this file did
batch, and it had a real bug because of it - see below):

Keepa's product endpoint lets you pass up to 100 codes in one `code=`
parameter, but the response doesn't clearly tag which returned product
came from which of your input codes - you're expected to reverse-match
each product's own `upcList`/`eanList` field back against your input list.
The previous version of this file did that matching, but fell back to
"attach this product to the ENTIRE batch" whenever the reverse-match came
up empty:

    matched_upcs = [u for u in batch if u in code] or batch   # BUG

That fallback is what caused the real-world symptom of one random product
(e.g. a knitting stitch-holder set) getting attached to dozens of
completely unrelated UPCs in the same 100-code batch - whenever Keepa
didn't return a usable upcList/eanList for a product (which turned out to
be common enough to matter), every UPC in that batch silently got the
wrong product's price/rank/ROI.

Querying one UPC per request instead sidesteps the whole problem: every
product Keepa returns in that response is unambiguously for that one code,
no reverse-matching needed. Token cost is identical either way (1 token per
code, whether batched or not) - the only trade-off is more HTTP requests,
which we parallelize with a small thread pool to keep it fast.

Other things this module relies on (verify against Keepa's docs if their
API changes - third-party API surfaces do drift over time):
- `csv` arrays on the product object are Keepa's compressed price/rank
  history; index 18 is "Buy Box" price history (cents) on current Keepa
  schema, and index 3 is Sales Rank history. We read the *last* value in
  each series as "current".
- `fbaFees.pickAndPackFee` + a size-tier-based fulfillment estimate approximates
  the FBA fee. Keepa doesn't always return a fully computed all-in FBA fee,
  so we treat this as an estimate and let the user override with a flat
  per-item fee assumption in the UI.
- Passing `stats=1` (which we do) makes Keepa also return a `stats` object
  with `avg30`/`avg90`/`avg180` arrays - averages over those trailing
  windows, indexed the same way as `csv` (so `stats.avg90[18]` is the
  90-day average Buy Box price). We use this to flag when today's price is
  unusually high vs its own recent history.
"""
from __future__ import annotations
import time
import concurrent.futures
import requests

KEEPA_BASE = "https://api.keepa.com"
AMAZON_DOMAIN_US = 1  # Keepa domain id for amazon.com
MAX_WORKERS = 8  # parallel requests - keeps things fast without hammering the API
MAX_RETRIES_PER_UPC = 3


class KeepaError(Exception):
    pass


def _cents_to_dollars(v):
    if v is None or v < 0:
        return None
    return round(v / 100.0, 2)


def _last_valid(csv_series):
    """Keepa csv arrays are [time, value, time, value, ...]; grab the last valid value."""
    if not csv_series:
        return None
    for i in range(len(csv_series) - 1, 0, -2):
        val = csv_series[i]
        if val is not None and val >= 0:
            return val
    return None


def _fetch_one(session: requests.Session, api_key: str, upc: str, domain: int) -> list:
    """Fetch and parse the product(s) matching exactly one UPC. Raises KeepaError on
    a hard failure (bad key, etc). Retries transient/rate-limit errors a few times."""
    params = {"key": api_key, "domain": domain, "code": upc, "stats": 1}
    last_error = None

    for attempt in range(MAX_RETRIES_PER_UPC):
        resp = session.get(f"{KEEPA_BASE}/product", params=params, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            products = data.get("products") or []
            # Every product in THIS response is for THIS one queried UPC -
            # no reverse-matching needed, unlike the old batched approach.
            return [_parse_product(p) for p in products]

        if resp.status_code in (401, 403):
            # Auth problem - retrying won't help and every other UPC will
            # fail the same way, so abort the whole run immediately.
            raise KeepaError(f"Keepa auth error {resp.status_code} - check your API key. {resp.text[:300]}")

        if resp.status_code == 429:
            # Out of tokens right now - back off and retry, tokens regenerate
            # continuously on Keepa's side.
            last_error = f"429 rate limited (attempt {attempt + 1})"
            time.sleep(2.0 * (attempt + 1))
            continue

        # Other transient error - short retry
        last_error = f"{resp.status_code}: {resp.text[:200]}"
        time.sleep(1.0)

    raise KeepaError(f"Keepa lookup failed for UPC {upc} after {MAX_RETRIES_PER_UPC} attempts: {last_error}")


def fetch_products_by_upc(api_key: str, upcs: list[str], domain: int = AMAZON_DOMAIN_US) -> tuple[dict, dict]:
    """
    Look up each UPC against Keepa individually (see module docstring for why).
    Returns (results, errors):
      - results: {upc: [product_dict, ...]} - list because one UPC can map to
        multiple ASINs (bundles/variations). Empty list = no Amazon match.
      - errors: {upc: error message} for any UPC that failed after retries -
        these are NOT silently dropped, so the caller can show the user
        exactly which products it couldn't check rather than pretending
        they simply have no data.
    """
    if not api_key:
        raise KeepaError("No Keepa API key configured")

    results: dict[str, list] = {}
    errors: dict[str, str] = {}
    session = requests.Session()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_upc = {
            executor.submit(_fetch_one, session, api_key, u, domain): u for u in upcs
        }
        for future in concurrent.futures.as_completed(future_to_upc):
            upc = future_to_upc[future]
            try:
                results[upc] = future.result()
            except KeepaError as e:
                if "auth error" in str(e):
                    # cancel remaining work and surface immediately - a bad key
                    # will fail every single request, no point burning them all
                    for f in future_to_upc:
                        f.cancel()
                    raise
                errors[upc] = str(e)
                results[upc] = []

    return results, errors


def _parse_product(p: dict) -> dict:
    csv = p.get("csv") or []
    buybox_series = csv[18] if len(csv) > 18 else None
    amazon_series = csv[0] if len(csv) > 0 else None
    rank_series = csv[3] if len(csv) > 3 else None
    new_series = csv[1] if len(csv) > 1 else None

    current_price = (
        _cents_to_dollars(_last_valid(buybox_series))
        or _cents_to_dollars(_last_valid(new_series))
        or _cents_to_dollars(_last_valid(amazon_series))
    )
    sales_rank = _last_valid(rank_series)

    # 90-day average price, preferring the same series current_price came
    # from (Buy Box), falling back to New if Buy Box has no average on file.
    stats = p.get("stats") or {}
    avg90 = stats.get("avg90") or []

    def _avg_at(series, idx):
        if len(series) > idx and series[idx] is not None and series[idx] >= 0:
            return _cents_to_dollars(series[idx])
        return None

    price_avg_90 = _avg_at(avg90, 18) or _avg_at(avg90, 1) or _avg_at(avg90, 0)

    fba_fees = p.get("fbaFees") or {}
    pick_pack = _cents_to_dollars(fba_fees.get("pickAndPackFee"))
    referral_pct = None
    ref_fee_pct = p.get("referralFeePercentage")
    if ref_fee_pct:
        referral_pct = ref_fee_pct / 100.0

    # Keepa reports hazmat/dangerous-goods classification as a
    # "hazardousMaterials" array (e.g. [{"aspect": "proper_shipping_name",
    # "value": "AEROSOLS"}]) when Amazon has flagged the product.
    hazmat_list = p.get("hazardousMaterials") or []
    hazmat_summary = ", ".join(
        str(h.get("value")) for h in hazmat_list if isinstance(h, dict) and h.get("value")
    ) or None

    price_vs_avg90_pct = None
    if current_price is not None and price_avg_90 not in (None, 0):
        price_vs_avg90_pct = round((current_price - price_avg_90) / price_avg_90 * 100, 1)

    return {
        "asin": p.get("asin"),
        "title": p.get("title"),
        "current_price": current_price,
        "sales_rank": sales_rank,
        "category": (p.get("categoryTree") or [{}])[-1].get("name") if p.get("categoryTree") else None,
        "fba_pick_pack_fee": pick_pack,
        "referral_fee_pct": referral_pct,
        "offer_count_new": p.get("offerCountNew"),
        "amazon_is_seller": (p.get("availabilityAmazon", -1) not in (-1, None)),
        "hazmat": bool(hazmat_list),
        "hazmat_detail": hazmat_summary,
        "price_avg_90": price_avg_90,
        "price_vs_avg90_pct": price_vs_avg90_pct,
    }
