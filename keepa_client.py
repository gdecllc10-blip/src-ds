"""
Thin wrapper around the Keepa Product API for bulk Amazon lookups by UPC/EAN.

Docs: https://keepa.com/#!discuss/t/product-object/116
      https://keepa.com/#!discuss/t/request-products/109

Key facts this module relies on (verify against Keepa's docs if their API
changes - third-party API surfaces do drift over time):
- The `code` param on the product endpoint accepts UPC/EAN/ISBN-13, comma
  separated, up to 100 per request, and costs 1 token per code plus extra
  tokens if you request live `offers`/`buybox` data.
- One UPC can map to more than one ASIN (bundles, variations, different
  marketplaces bundled under one barcode) - we keep all matches and let the
  ROI step pick the best (or show all).
- `csv` arrays on the product object are Keepa's compressed price/rank
  history; index 18 is "Buy Box" price history (cents) on current Keepa
  schema, and index 3 is Sales Rank history. We read the *last* value in
  each series as "current".
- `fbaFees.pickAndPackFee` + a size-tier-based fulfillment estimate approximates
  the FBA fee. Keepa doesn't always return a fully computed all-in FBA fee,
  so we treat this as an estimate and let the user override with a flat
  per-item fee assumption in the UI.
"""
from __future__ import annotations
import time
import requests

KEEPA_BASE = "https://api.keepa.com"
BATCH_SIZE = 100
AMAZON_DOMAIN_US = 1  # Keepa domain id for amazon.com


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


def fetch_products_by_upc(api_key: str, upcs: list[str], domain: int = AMAZON_DOMAIN_US) -> dict:
    """
    Look up a list of UPCs against Keepa in batches of 100.
    Returns {upc: [product_dict, ...]} - list because one UPC can map to
    multiple ASINs.
    """
    results: dict[str, list] = {u: [] for u in upcs}
    if not api_key:
        raise KeepaError("No Keepa API key configured")

    for i in range(0, len(upcs), BATCH_SIZE):
        batch = upcs[i:i + BATCH_SIZE]
        params = {
            "key": api_key,
            "domain": domain,
            "code": ",".join(batch),
            "stats": 1,
        }
        resp = requests.get(f"{KEEPA_BASE}/product", params=params, timeout=60)
        if resp.status_code != 200:
            raise KeepaError(f"Keepa API error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        products = data.get("products") or []
        for p in products:
            code = p.get("upcList") or p.get("eanList") or []
            matched_upcs = [u for u in batch if u in code] or batch  # best effort
            parsed = _parse_product(p)
            for u in matched_upcs:
                results.setdefault(u, []).append(parsed)

        # be polite to the rate limiter between batches
        if i + BATCH_SIZE < len(upcs):
            time.sleep(1.0)

    return results


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

    fba_fees = p.get("fbaFees") or {}
    pick_pack = _cents_to_dollars(fba_fees.get("pickAndPackFee"))
    referral_pct = None
    ref_fee_pct = p.get("referralFeePercentage")
    if ref_fee_pct:
        referral_pct = ref_fee_pct / 100.0

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
    }
