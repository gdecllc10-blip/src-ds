"""
Walmart catalog matching for approved Walmart Marketplace sellers, via the
official Item Search API.

Docs: https://developer.walmart.com/us-marketplace/docs/item-search-for-the-walmart-catalog
      https://developer.walmart.com/us-marketplace/reference/getsearchresult

IMPORTANT - what this can and can't do:
- This endpoint lets an approved seller search the Walmart catalog by
  UPC/GTIN to see if an item already exists on Walmart.com (returns
  itemId/title/brand and, for some items, a price snapshot). It's meant
  for the "does this already exist so I can add an offer to it" workflow,
  not as a dedicated competitive-pricing feed - so treat any price it
  returns as a rough signal, not a guaranteed-current buy box price.
- For a reliable, current Buy Box / competitor price on an item you don't
  sell yet, Walmart's *Pricing Insights* API only covers items you already
  have an active offer on. The common workaround sellers use is: match the
  item here, create a $0-inventory (inactive) offer against that itemId via
  the Item Setup API, then pull Pricing Insights for it. That's a
  multi-step, semi-manual process this tool doesn't attempt to automate,
  since creating offers is a meaningful account action that shouldn't
  happen silently in a sourcing scan.
- We deliberately do NOT scrape walmart.com for live prices - that's
  against Walmart's Terms of Service and outside the scope of the official
  Marketplace API this module uses.
"""
from __future__ import annotations
import base64
import time
import uuid
import requests

TOKEN_URL = "https://marketplace.walmartapis.com/v3/token"
SEARCH_URL = "https://marketplace.walmartapis.com/v3/items/walmart/search"

_token_cache = {"token": None, "expires_at": 0}


class WalmartError(Exception):
    pass


def _get_token(client_id: str, client_secret: str) -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["token"]

    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "WM_SVC.NAME": "Walmart Marketplace",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
        },
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise WalmartError(f"Walmart OAuth error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 900)
    return _token_cache["token"]


def match_item_by_upc(client_id: str, client_secret: str, upc: str) -> dict | None:
    """
    Returns catalog match info for a UPC if Walmart already carries it:
    {itemId, title, brand, price (may be None - not guaranteed)}.
    Returns None if no match or on error (caller should treat as "unknown,
    check manually" rather than "definitely not on Walmart").
    """
    if not client_id or not client_secret:
        return None
    try:
        token = _get_token(client_id, client_secret)
    except WalmartError:
        return None

    resp = requests.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "WM_SVC.NAME": "Walmart Marketplace",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
        },
        params={"upc": upc},
        timeout=30,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    items = data.get("ItemResponse") or data.get("items") or []
    if not items:
        return None
    item = items[0]
    price = None
    price_block = item.get("price")
    if isinstance(price_block, dict):
        price = price_block.get("amount")
    elif isinstance(price_block, (int, float)):
        price = price_block

    return {
        "item_id": item.get("itemId"),
        "title": item.get("title") or item.get("productName"),
        "brand": item.get("brand"),
        "price": float(price) if price is not None else None,
    }
