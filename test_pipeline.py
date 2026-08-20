"""
Standalone smoke test for the core logic (sheet parsing -> demo data -> ROI),
independent of Streamlit, so it can run anywhere Python + pandas is available.
Run: python3 test_pipeline.py
"""
import pandas as pd
from sheet_parser import load_sheet, detect_columns, clean_upc, clean_cost
from roi import FeeAssumptions, amazon_roi, amazon_disqualify_reason, ebay_roi, walmart_roi, best_channel
import demo_data


def main():
    with open("sample_data/sample_distributor_sheet.xlsx", "rb") as f:
        df = load_sheet(f)

    detected = detect_columns(df)
    print("Detected columns:", detected)
    assert detected["upc"] is not None, "UPC column not detected"
    assert detected["cost"] is not None, "Cost column not detected"

    work = pd.DataFrame()
    work["upc"] = df[detected["upc"]].apply(clean_upc)
    work["cost"] = df[detected["cost"]].apply(clean_cost)
    work["description"] = df[detected["description"]] if detected["description"] else ""
    work = work[(work["upc"] != "") & work["cost"].notna()].reset_index(drop=True)

    assert len(work) == 15, f"Expected 15 valid rows, got {len(work)}"
    assert all(len(u) >= 8 for u in work["upc"]), "Some UPCs look malformed after cleaning"

    fees = FeeAssumptions()  # rank cap 500k, hazmat exclusion on, eBay haircut 15% - all defaults
    results = []
    disqualified_count = 0
    no_match_count = 0
    no_rank_count = 0
    for _, r in work.iterrows():
        kp = demo_data.fake_keepa_product(r["upc"], r["cost"])  # None simulates "no Amazon match"
        eb = demo_data.fake_ebay_comp(r["upc"], r["cost"])
        wm = demo_data.fake_walmart_match(r["upc"], r["cost"])
        az_roi = amazon_roi(r["cost"], kp, fees)
        reason = amazon_disqualify_reason(kp, fees)
        if reason:
            disqualified_count += 1
            assert az_roi is None, "amazon_roi should return None when disqualified"
        if kp is None:
            no_match_count += 1
            assert reason is None, "No Amazon match should not itself count as a disqualify reason"
        elif kp["sales_rank"] is None:
            no_rank_count += 1
            assert reason != "No sales rank data" if reason else True, \
                "Missing rank data alone should not disqualify - only a rank that exceeds the cutoff should"
        eb_roi = ebay_roi(r["cost"], eb, fees)
        wm_roi = walmart_roi(r["cost"], wm, fees)
        channel, roi_pct = best_channel(az_roi, eb_roi, wm_roi)

        if kp is None:
            # the critical case the user flagged: no Amazon listing at all should
            # still let eBay/Walmart results come through, not zero everything out
            assert eb_roi is not None, "eBay ROI should still compute when there's no Amazon match"

        results.append({
            "upc": r["upc"], "desc": r["description"], "cost": r["cost"],
            "rank": kp["sales_rank"] if kp else "no match",
            "hazmat": kp["hazmat"] if kp else None,
            "disqualified": reason,
            "amazon_roi": az_roi["roi_pct"] if az_roi else None,
            "ebay_roi": eb_roi["roi_pct"] if eb_roi else None,
            "walmart_roi": wm_roi["roi_pct"] if wm_roi else None,
            "best_channel": channel, "best_roi": roi_pct,
        })

    out = pd.DataFrame(results).sort_values("best_roi", ascending=False)
    print(out.to_string(index=False))

    meets_30 = (out["best_roi"] >= 30).sum()
    print(f"\n{meets_30} of {len(out)} demo products clear a 30% ROI threshold.")
    print(f"{disqualified_count} of {len(out)} disqualified on the Amazon channel (hazmat / rank over cutoff).")
    print(f"{no_match_count} had no Amazon match at all; {no_rank_count} matched but had no rank data - "
          f"neither case disqualifies, confirming eBay/Walmart still surface for them.")

    # sanity: every disqualified row has amazon_roi blank, and eBay ROI now reflects
    # the haircut'd estimated-sold price rather than the raw active price
    assert out.loc[out["disqualified"].notna(), "amazon_roi"].isna().all(), \
        "A disqualified row still has an Amazon ROI value"
    assert out["ebay_roi"].notna().all(), "eBay ROI missing for some rows"
    assert disqualified_count > 0, "Expected at least one demo product to hit a qualifier (rank/hazmat) - check demo_data odds"
    # The 15-row sample sheet is deterministic (hash-seeded per UPC), so whether
    # any row happens to land on "no Amazon match" this run is luck of the draw -
    # verify the "no match" path separately over a larger synthetic UPC set instead.
    synthetic_upcs = [str(100000000000 + i) for i in range(300)]
    synthetic_matches = [demo_data.fake_keepa_product(u, 10.0) for u in synthetic_upcs]
    synthetic_no_match = sum(1 for m in synthetic_matches if m is None)
    print(f"(separately, over 300 synthetic UPCs: {synthetic_no_match} had no Amazon match)")
    assert synthetic_no_match > 0, "fake_keepa_product never returns None across 300 tries - no-match path is untested"
    for m in synthetic_matches:
        assert amazon_disqualify_reason(m, fees) != "No sales rank data", \
            "Missing-rank case should never disqualify by that reason anymore"
    print("\nAll smoke-test assertions passed.")


if __name__ == "__main__":
    main()
