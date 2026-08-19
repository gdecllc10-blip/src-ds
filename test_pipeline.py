"""
Standalone smoke test for the core logic (sheet parsing -> demo data -> ROI),
independent of Streamlit, so it can run anywhere Python + pandas is available.
Run: python3 test_pipeline.py
"""
import pandas as pd
from sheet_parser import load_sheet, detect_columns, clean_upc, clean_cost
from roi import FeeAssumptions, amazon_roi, ebay_roi, walmart_roi, best_channel
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

    fees = FeeAssumptions()
    results = []
    for _, r in work.iterrows():
        kp = demo_data.fake_keepa_product(r["upc"], r["cost"])
        eb = demo_data.fake_ebay_comp(r["upc"], r["cost"])
        wm = demo_data.fake_walmart_match(r["upc"], r["cost"])
        az_roi = amazon_roi(r["cost"], kp, fees)
        eb_roi = ebay_roi(r["cost"], eb, fees)
        wm_roi = walmart_roi(r["cost"], wm, fees)
        channel, roi_pct = best_channel(az_roi, eb_roi, wm_roi)
        results.append({
            "upc": r["upc"], "desc": r["description"], "cost": r["cost"],
            "amazon_roi": az_roi["roi_pct"], "ebay_roi": eb_roi["roi_pct"],
            "walmart_roi": wm_roi["roi_pct"] if wm_roi else None,
            "best_channel": channel, "best_roi": roi_pct,
        })

    out = pd.DataFrame(results).sort_values("best_roi", ascending=False)
    print(out.to_string(index=False))

    meets_30 = (out["best_roi"] >= 30).sum()
    print(f"\n{meets_30} of {len(out)} demo products clear a 30% ROI threshold.")

    assert out["amazon_roi"].notna().all(), "Amazon ROI missing for some rows"
    assert out["ebay_roi"].notna().all(), "eBay ROI missing for some rows"
    print("\nAll smoke-test assertions passed.")


if __name__ == "__main__":
    main()
