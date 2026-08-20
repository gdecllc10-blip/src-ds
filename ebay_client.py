"""
eBay comp pricing via the official eBay Browse API (active listings, US only).

IMPORTANT - about "sold" comps:
eBay's *actual* sold/completed-item data lives behind the Marketplace
Insights API, which is limited-release - as of this build, independent/small
developers are generally not being approved for it (eBay's own developer
forum has multiple threads from developers stuck on a waitlist with no
approval path). The older Finding API's findCompletedItems endpoint that
used to work around this has also become unreliable (heavy rate-limiting
even on first calls) as eBay deprecates it in favor of Browse/Insights.

So this module intentionally does NOT claim to return sold comps. It uses
the Browse API (freely available to any developer account) restricted to
US-located, active listings, and the ROI math in roi.py applies a haircut
to the active asking price to approximate what it might actually sell for,
since active "ask" prices run higher than realized sale prices. That haircut
is a rough estimate, not real sold data - see README for how to apply for
real Marketplace Insights access if you want to try, or use a paid
third-party sold-comps provider instead.

Docs: https://developer.ebay.com/api-docs/buy/browse/overview.html
"""
from __future__ import annotations
import base64
import time
import requests

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

_token_cache = {"token": None, "expires_at": 0}


class EbayError(Exception):
    pass


def _get_token(app_id: str, cert_id: str) -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["token"]

    creds = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise EbayError(f"eBay OAuth error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 7200)
    return _token_cache["token"]


def get_comp_price(app_id: str, cert_id: str, upc: str) -> dict | None:
    """
    Search active, US-located eBay listings by UPC/GTIN and return a simple
    price comp. NOT sold data - see module docstring.
    """
    if not app_id or not cert_id:
        return None
    token = _get_token(app_id, cert_id)
    resp = requests.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
        params={
            "gtin": upc,
            "limit": 20,
            # restrict to items physically located in the US, and to
            # active/fixed-price + auction listings currently live
            "filter": "itemLocationCountry:US",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get("itemSummaries") or []
    prices = []
    for it in items:
        price = it.get("price", {}).get("value")
        if price:
            prices.append(float(price))
    if not prices:
        return None
    prices.sort()
    median = prices[len(prices) // 2]
    return {
        "ebay_active_median_price": round(median, 2),
        "ebay_active_low_price": round(min(prices), 2),
        "ebay_active_listings": len(prices),
    }
