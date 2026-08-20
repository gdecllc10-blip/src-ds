"""
Synthetic Amazon/eBay data generator so the dashboard is fully clickable and
demoable before you've plugged in real API keys. Deterministic per-UPC (hash
seeded) so re-running gives consistent results, not random noise each time.
"""
import hashlib
import random

CATEGORIES = ["Home & Kitchen", "Toys & Games", "Grocery", "Pet Supplies", "Beauty", "Tools & Home Improvement"]


def _seeded_random(upc: str) -> random.Random:
    seed = int(hashlib.md5(upc.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


def fake_keepa_product(upc: str, cost: float) -> dict | None:
    rnd = _seeded_random(upc)

    # ~10% of demo products simulate "not sold on Amazon at all" - Keepa
    # returns no match for the UPC. Proves the app doesn't block eBay/Walmart
    # results just because Amazon has nothing.
    if rnd.random() < 0.10:
        return None

    # bias sell price around cost * a markup factor so results feel realistic
    markup = rnd.uniform(1.4, 3.2)
    price = round((cost or rnd.uniform(5, 40)) * markup, 2)

    # ~10% simulate a matched product with no rank data available (Keepa
    # sometimes just doesn't have current rank history) - this should NOT
    # disqualify the product, only an explicit rank over the cutoff should.
    has_rank = rnd.random() >= 0.10
    if not has_rank:
        sales_rank = None
    else:
        is_low_demand = rnd.random() < 0.2
        sales_rank = rnd.randint(500_001, 900_000) if is_low_demand else rnd.randint(500, 300000)

    is_hazmat = rnd.random() < 0.08
    return {
        "asin": f"B0{rnd.randint(10**7, 10**8 - 1)}",
        "title": f"Demo Product {upc[-4:]}",
        "current_price": price,
        "sales_rank": sales_rank,
        "category": rnd.choice(CATEGORIES),
        "fba_pick_pack_fee": round(rnd.uniform(3.5, 8.5), 2),
        "referral_fee_pct": rnd.choice([0.08, 0.15, 0.15, 0.15, 0.17]),
        "offer_count_new": rnd.randint(1, 25),
        "amazon_is_seller": rnd.random() < 0.3,
        "hazmat": is_hazmat,
        "hazmat_detail": rnd.choice(["AEROSOLS", "FLAMMABLE LIQUID", "LITHIUM BATTERY"]) if is_hazmat else None,
    }


def fake_ebay_comp(upc: str, cost: float) -> dict:
    rnd = _seeded_random(upc[::-1])
    markup = rnd.uniform(1.2, 2.6)
    median = round((cost or rnd.uniform(5, 40)) * markup, 2)
    return {
        "ebay_active_median_price": median,
        "ebay_active_low_price": round(median * 0.85, 2),
        "ebay_active_listings": rnd.randint(1, 40),
    }


def fake_walmart_match(upc: str, cost: float) -> dict | None:
    rnd = _seeded_random(upc + "wmt")
    if rnd.random() < 0.15:
        return None  # simulate "not carried on Walmart" for some products
    markup = rnd.uniform(1.3, 2.8)
    price = round((cost or rnd.uniform(5, 40)) * markup, 2)
    return {
        "item_id": str(rnd.randint(10**8, 10**9 - 1)),
        "title": f"Demo Walmart Match {upc[-4:]}",
        "brand": rnd.choice(["Generic", "HomeBasics", "ProLine", "Everyday"]),
        "price": price,
    }
