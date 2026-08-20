"""
ROI math for each marketplace. These are estimates, not guarantees - always
sanity-check a product before committing real money, especially around
Amazon category-specific referral fee rates and true FBA size-tier fees.

Amazon:
    revenue        = current sell price (Keepa buy box / lowest new)
    referral_fee    = revenue * referral_fee_pct   (Keepa-reported if available,
                                                      else the fallback % you set)
    fulfillment_fee = Keepa FBA pick&pack estimate, else your flat fallback
    net_profit      = revenue - cost - referral_fee - fulfillment_fee - misc_fee
    roi             = net_profit / cost

eBay:
    revenue        = median ACTIVE US-listing comp price, minus a haircut %
                      to approximate a realized sold price (eBay's real sold
                      data requires Marketplace Insights API access, which is
                      limited-release and generally not grantable to small
                      developers right now - see ebay_client.py). Treat this
                      as an estimate, not a real sold comp.
    final_value_fee = revenue * ebay_fee_pct + fixed_fee
    net_profit      = revenue - cost - final_value_fee - shipping_estimate
    roi             = net_profit / cost

Walmart (approved sellers only):
    revenue        = catalog match price from Walmart's Item Search API,
                      when Walmart returns one - NOT guaranteed to be
                      present or fully current, treat as directional
    referral_fee    = revenue * walmart_referral_fallback_pct (Walmart's
                      referral fee varies a lot by category - 6% to 20%+ -
                      so this fallback is a rough blended estimate; verify
                      the real category rate before buying)
    fulfillment_fee = flat fallback (WFS vs seller-fulfilled cost varies
                      enough that a single number is only a starting point)
    net_profit      = revenue - cost - referral_fee - fulfillment_fee
    roi             = net_profit / cost
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class FeeAssumptions:
    amazon_referral_fallback_pct: float = 0.15
    amazon_fulfillment_fallback: float = 5.50
    amazon_misc_fee: float = 0.0
    ebay_fee_pct: float = 0.1325
    ebay_fixed_fee: float = 0.30
    ebay_shipping_estimate: float = 6.00
    ebay_active_to_sold_haircut_pct: float = 0.15
    walmart_referral_fallback_pct: float = 0.12
    walmart_fulfillment_fallback: float = 5.00
    amazon_max_sales_rank: int | None = 500_000
    amazon_exclude_hazmat: bool = True


def amazon_disqualify_reason(keepa_data: dict | None, fees: FeeAssumptions) -> str | None:
    """
    Returns a short reason string if this product should be disqualified from
    the Amazon channel regardless of ROI (a dangerous-goods/hazmat listing,
    or a confirmed sales rank worse than your cutoff), else None.

    Important: the rank cutoff only applies when Keepa actually reports a
    rank. No Amazon match at all, or a match with no rank data, is treated
    as "unknown," not "fails the check" - it does NOT disqualify the
    product. That way a product with no Amazon listing (or no rank signal)
    still flows through normally and can surface via eBay/Walmart if it
    clears ROI there; the "Amazon Sales Rank" column will just show blank
    so you know the rank simply isn't known, not that it failed a rule.
    """
    if not keepa_data:
        return None
    if fees.amazon_exclude_hazmat and keepa_data.get("hazmat"):
        return "Hazmat / dangerous goods"
    if fees.amazon_max_sales_rank is not None:
        rank = keepa_data.get("sales_rank")
        if rank is not None and rank > fees.amazon_max_sales_rank:
            return f"Sales rank {rank:,} exceeds {fees.amazon_max_sales_rank:,} cutoff"
    return None


def amazon_roi(cost: float, keepa_data: dict | None, fees: FeeAssumptions):
    if not keepa_data or keepa_data.get("current_price") is None or not cost:
        return None
    if amazon_disqualify_reason(keepa_data, fees):
        return None
    price = keepa_data["current_price"]
    referral_pct = keepa_data.get("referral_fee_pct") or fees.amazon_referral_fallback_pct
    referral_fee = price * referral_pct
    fulfillment_fee = keepa_data.get("fba_pick_pack_fee") or fees.amazon_fulfillment_fallback
    net_profit = price - cost - referral_fee - fulfillment_fee - fees.amazon_misc_fee
    roi = net_profit / cost if cost else None
    return {
        "sell_price": round(price, 2),
        "referral_fee": round(referral_fee, 2),
        "fulfillment_fee": round(fulfillment_fee, 2),
        "net_profit": round(net_profit, 2),
        "roi_pct": round(roi * 100, 1) if roi is not None else None,
    }


def ebay_roi(cost: float, ebay_data: dict | None, fees: FeeAssumptions):
    if not ebay_data or ebay_data.get("ebay_active_median_price") is None or not cost:
        return None
    active_price = ebay_data["ebay_active_median_price"]
    estimated_sold_price = active_price * (1 - fees.ebay_active_to_sold_haircut_pct)
    fvf = estimated_sold_price * fees.ebay_fee_pct + fees.ebay_fixed_fee
    net_profit = estimated_sold_price - cost - fvf - fees.ebay_shipping_estimate
    roi = net_profit / cost if cost else None
    return {
        "active_price": round(active_price, 2),
        "sell_price": round(estimated_sold_price, 2),
        "final_value_fee": round(fvf, 2),
        "net_profit": round(net_profit, 2),
        "roi_pct": round(roi * 100, 1) if roi is not None else None,
    }


def walmart_roi(cost: float, walmart_data: dict | None, fees: FeeAssumptions):
    if not walmart_data or walmart_data.get("price") is None or not cost:
        return None
    price = walmart_data["price"]
    referral_fee = price * fees.walmart_referral_fallback_pct
    fulfillment_fee = fees.walmart_fulfillment_fallback
    net_profit = price - cost - referral_fee - fulfillment_fee
    roi = net_profit / cost if cost else None
    return {
        "sell_price": round(price, 2),
        "referral_fee": round(referral_fee, 2),
        "fulfillment_fee": round(fulfillment_fee, 2),
        "net_profit": round(net_profit, 2),
        "roi_pct": round(roi * 100, 1) if roi is not None else None,
    }


def best_channel(amazon: dict | None, ebay: dict | None, walmart: dict | None = None):
    candidates = []
    if amazon and amazon.get("roi_pct") is not None:
        candidates.append(("Amazon", amazon["roi_pct"]))
    if ebay and ebay.get("roi_pct") is not None:
        candidates.append(("eBay", ebay["roi_pct"]))
    if walmart and walmart.get("roi_pct") is not None:
        candidates.append(("Walmart", walmart["roi_pct"]))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]
