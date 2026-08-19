"""
Optional eBay comp pricing via the official eBay Browse API (active listings).

eBay's Browse API is free to use on a developer account (OAuth client
credentials flow, no user login needed for search). It only returns *active*
listing prices, not sold/completed comps - eBay locked sold-item history
behind the more restrictive Marketplace Insights API (limited-release,
requires an approved use case). Active-listing price is still a reasonable
proxy for "what could I list this for," just be aware it's not a sold comp.

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
    """Search active eBay listings by UPC/GTIN and return a simple price comp."""
    if not app_id or not cert_id:
        return None
    token = _get_token(app_id, cert_id)
    resp = requests.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
        params={"gtin": upc, "limit": 20},
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
        "ebay_median_price": round(median, 2),
        "ebay_low_price": round(min(prices), 2),
        "ebay_active_listings": len(prices),
    }
