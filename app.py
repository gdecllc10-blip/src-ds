import os
import io
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from sheet_parser import load_sheet, detect_columns, clean_upc, clean_cost
from roi import FeeAssumptions, amazon_roi, amazon_disqualify_reason, ebay_roi, walmart_roi, best_channel
import demo_data
import keepa_client
import ebay_client
import walmart_client

load_dotenv()


def get_default(key: str) -> str:
    """Check Streamlit Cloud secrets first (when hosted), then local .env / env vars."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, "")


st.set_page_config(page_title="Sourcing ROI Dashboard", layout="wide")

st.title("Sourcing ROI Dashboard")
st.caption(
    "Upload a distributor sheet, get every UPC checked against Amazon (and optionally eBay) "
    "pricing automatically, and see ranked ROI - no more copy/pasting UPCs one at a time."
)

# ---------------------------------------------------------------------------
# Sidebar: API keys + fee assumptions
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("API keys")
    keepa_key = st.text_input(
        "Keepa API key", value=get_default("KEEPA_API_KEY"), type="password",
        help="Get one at keepa.com. Leave blank to use DEMO MODE with synthetic data.",
    )
    ebay_app_id = st.text_input("eBay App ID (optional)", value=get_default("EBAY_APP_ID"), type="password")
    ebay_cert_id = st.text_input("eBay Cert ID (optional)", value=get_default("EBAY_CERT_ID"), type="password")
    walmart_client_id = st.text_input(
        "Walmart Client ID (optional)", value=get_default("WALMART_CLIENT_ID"), type="password",
        help="Requires an approved Walmart Marketplace seller account. See README for how to generate API keys.",
    )
    walmart_client_secret = st.text_input(
        "Walmart Client Secret (optional)", value=get_default("WALMART_CLIENT_SECRET"), type="password",
    )

    demo_mode = st.checkbox("Force demo mode (synthetic data)", value=not bool(keepa_key))

    st.divider()
    st.header("ROI threshold")
    min_roi = st.slider("Minimum ROI % to flag as a buy", min_value=10, max_value=100, value=30, step=5)
    st.caption("You mentioned 30-50% depending on the deal - default is set to 30%, the floor.")

    st.divider()
    st.header("Fee assumptions")
    referral_fallback = st.number_input("Amazon referral fee fallback %", value=15.0, step=0.5) / 100.0
    fulfillment_fallback = st.number_input("Amazon fulfillment fee fallback ($)", value=5.50, step=0.25)
    ebay_fee_pct = st.number_input("eBay final value fee %", value=13.25, step=0.25) / 100.0
    ebay_shipping = st.number_input("eBay shipping cost estimate ($)", value=6.00, step=0.5)
    walmart_referral_fallback = st.number_input(
        "Walmart referral fee fallback %", value=12.0, step=0.5,
        help="Walmart referral fees vary widely by category (roughly 6-20%) - set this closer to your typical category if you know it.",
    ) / 100.0
    walmart_fulfillment_fallback = st.number_input("Walmart fulfillment fee fallback ($)", value=5.00, step=0.25)
    ebay_haircut = st.number_input(
        "eBay active-to-sold price haircut %", value=15.0, step=1.0,
        help="eBay's real sold-price API (Marketplace Insights) is locked to approved partners - "
             "not generally available to independent developers. This discounts the active asking "
             "price to roughly approximate what it'd realistically sell for. See README.",
    ) / 100.0

    st.divider()
    st.header("Amazon qualifiers")
    use_rank_cap = st.checkbox("Require Amazon sales rank under a cutoff", value=True)
    max_sales_rank = st.number_input(
        "Max Amazon sales rank to qualify", value=500_000, step=10_000, min_value=1,
        disabled=not use_rank_cap,
        help="Only applies when Keepa actually reports a rank over this cutoff. No Amazon match, or a "
             "match with no rank data, is treated as unknown - NOT disqualified - so it can still "
             "surface via eBay/Walmart.",
    )
    exclude_hazmat = st.checkbox(
        "Exclude Amazon hazmat / dangerous goods items", value=True,
        help="Uses Keepa's hazardousMaterials flag (aerosols, flammables, lithium batteries, etc).",
    )
    spike_threshold = st.number_input(
        "Flag price if it's this much above its 90-day average (%)", value=25.0, step=5.0,
        help="Informational only - doesn't disqualify anything. Flags products where today's Amazon "
             "price looks like a temporary spike rather than the normal going rate, since ROI calculated "
             "against a spike price often disappears once the price reverts.",
    )
    min_rating_flag = st.number_input(
        "Flag rating below (stars)", value=3.5, step=0.1, min_value=1.0, max_value=5.0,
        help="Informational only - doesn't disqualify anything. Flags products with a low star rating, "
             "or with no rating data at all (too new/low-volume to have one), so you notice before buying.",
    )
    min_reviews_flag = st.number_input(
        "Flag review count below", value=10, step=5, min_value=0,
        help="A high star rating built on very few reviews isn't a reliable signal - flags products "
             "under this review count alongside the rating flag.",
    )

    fees = FeeAssumptions(
        amazon_referral_fallback_pct=referral_fallback,
        amazon_fulfillment_fallback=fulfillment_fallback,
        ebay_fee_pct=ebay_fee_pct,
        ebay_shipping_estimate=ebay_shipping,
        ebay_active_to_sold_haircut_pct=ebay_haircut,
        walmart_referral_fallback_pct=walmart_referral_fallback,
        walmart_fulfillment_fallback=walmart_fulfillment_fallback,
        amazon_max_sales_rank=int(max_sales_rank) if use_rank_cap else None,
        amazon_exclude_hazmat=exclude_hazmat,
    )

    check_ebay = st.checkbox("Also check eBay comps (active US listings, not sold)", value=False)
    check_walmart = st.checkbox(
        "Also check Walmart catalog match (approved sellers)", value=False,
        help="Uses Walmart's official Item Search API to see if the item exists on Walmart.com. "
             "Price, when returned, is a rough signal, not a guaranteed current price - see README.",
    )

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
uploaded = st.file_uploader("Distributor sheet (.xlsx, .xls, .csv)", type=["xlsx", "xls", "csv"])

use_sample = False
if uploaded is None:
    use_sample = st.button("Try it with a sample distributor sheet instead")

if uploaded is None and not use_sample:
    st.info("Upload a distributor sheet above, or click the sample-sheet button to see it in action.")
    st.stop()

if use_sample:
    uploaded = open("sample_data/sample_distributor_sheet.xlsx", "rb")
    uploaded.name = "sample_distributor_sheet.xlsx"

df = load_sheet(uploaded)
detected = detect_columns(df)

st.subheader("1. Confirm columns")
cols = list(df.columns)


def _idx(col_name):
    return cols.index(col_name) + 1 if col_name in cols else 0  # +1 for the blank option


options = ["(none)"] + cols
c1, c2, c3, c4 = st.columns(4)
with c1:
    upc_col = st.selectbox("UPC / EAN column", options, index=_idx(detected["upc"]))
with c2:
    cost_col = st.selectbox("Wholesale cost column", options, index=_idx(detected["cost"]))
with c3:
    desc_col = st.selectbox("Description column (optional)", options, index=_idx(detected["description"]))
with c4:
    qty_col = st.selectbox("Case qty column (optional)", options, index=_idx(detected["qty"]))

if upc_col == "(none)" or cost_col == "(none)":
    st.warning("Select at least a UPC column and a cost column to continue.")
    st.stop()

work = pd.DataFrame()
work["upc"] = df[upc_col].apply(clean_upc)
work["cost"] = df[cost_col].apply(clean_cost)
work["description"] = df[desc_col] if desc_col != "(none)" else ""
work["case_qty"] = df[qty_col] if qty_col != "(none)" else ""
work = work[(work["upc"] != "") & work["cost"].notna()].drop_duplicates(subset="upc").reset_index(drop=True)

st.caption(f"{len(work)} valid rows with a UPC and cost detected.")

st.subheader("2. Run sourcing analysis")
run = st.button(f"Analyze {len(work)} products", type="primary")

if not run:
    st.dataframe(work, use_container_width=True)
    st.stop()

# ---------------------------------------------------------------------------
# Fetch data (Keepa / eBay / demo) and compute ROI
# ---------------------------------------------------------------------------
progress = st.progress(0.0, text="Fetching Amazon data...")

upcs = work["upc"].tolist()
keepa_results = {}

if demo_mode or not keepa_key:
    for u in upcs:
        row = work.loc[work["upc"] == u].iloc[0]
        kp = demo_data.fake_keepa_product(u, row["cost"])
        keepa_results[u] = [kp] if kp else []  # empty list = "no Amazon match", same shape Keepa returns
else:
    try:
        keepa_results, keepa_errors = keepa_client.fetch_products_by_upc(keepa_key, upcs)
    except keepa_client.KeepaError as e:
        st.error(f"Keepa lookup failed: {e}")
        st.stop()
    if keepa_errors:
        with st.expander(f"⚠️ {len(keepa_errors)} UPC(s) failed to look up after retries - click to see which"):
            st.write(
                "These are shown as 'no Amazon match' below, but that's not confirmed - "
                "the lookup itself failed (rate limiting or a transient error), not that "
                "Amazon doesn't carry them. Worth re-running if this list is large."
            )
            st.table(pd.DataFrame(
                [{"UPC": u, "Error": msg} for u, msg in keepa_errors.items()]
            ))

progress.progress(0.6, text="Fetching eBay comps..." if check_ebay else "Computing ROI...")

ebay_results = {}
if check_ebay:
    if demo_mode or not (ebay_app_id and ebay_cert_id):
        for u in upcs:
            row = work.loc[work["upc"] == u].iloc[0]
            ebay_results[u] = demo_data.fake_ebay_comp(u, row["cost"])
    else:
        for u in upcs:
            try:
                ebay_results[u] = ebay_client.get_comp_price(ebay_app_id, ebay_cert_id, u)
            except ebay_client.EbayError:
                ebay_results[u] = None

walmart_results = {}
if check_walmart:
    progress.progress(0.75, text="Checking Walmart catalog matches...")
    if demo_mode or not (walmart_client_id and walmart_client_secret):
        for u in upcs:
            row = work.loc[work["upc"] == u].iloc[0]
            walmart_results[u] = demo_data.fake_walmart_match(u, row["cost"])
    else:
        for u in upcs:
            walmart_results[u] = walmart_client.match_item_by_upc(walmart_client_id, walmart_client_secret, u)

progress.progress(0.9, text="Computing ROI...")

rows = []
for _, r in work.iterrows():
    u, cost = r["upc"], r["cost"]
    kp_list = keepa_results.get(u) or []
    kp = kp_list[0] if kp_list else None
    az = amazon_roi(cost, kp, fees)
    az_disqualified = amazon_disqualify_reason(kp, fees)
    eb = ebay_roi(cost, ebay_results.get(u), fees) if check_ebay else None
    wm = walmart_roi(cost, walmart_results.get(u), fees) if check_walmart else None
    channel, channel_roi = best_channel(az, eb, wm)

    offer_count = kp.get("offer_count_new") if kp else None
    amazon_sells_it = kp.get("amazon_is_seller") if kp else None
    price_vs_avg = kp.get("price_vs_avg90_pct") if kp else None
    is_price_spike = (price_vs_avg is not None and price_vs_avg >= spike_threshold)
    rating = kp.get("rating") if kp else None
    review_count = kp.get("review_count") if kp else None
    rating_concern = kp is not None and (
        rating is None or rating < min_rating_flag or (review_count or 0) < min_reviews_flag
    )

    rows.append({
        "UPC": u,
        "Cost": cost,
        "Price Spike?": ("Yes" if is_price_spike else ("No" if price_vs_avg is not None else None)),
        "Price vs 90-Day Avg %": price_vs_avg,
        "Amazon Rating": rating,
        "Amazon Reviews": review_count,
        "Rating Concern?": ("Yes" if rating_concern else ("No" if kp is not None else None)),
        "Description": r["description"],
        "Case Qty": r["case_qty"],
        "Amazon ASIN": kp.get("asin") if kp else None,
        "Amazon Title": kp.get("title") if kp else None,
        "Amazon Price": az["sell_price"] if az else None,
        "Amazon Sales Rank": kp.get("sales_rank") if kp else None,
        "Amazon Offers (New)": offer_count,
        "Amazon Sells This?": ("Yes" if amazon_sells_it else ("No" if amazon_sells_it is not None else None)),
        "Amazon ROI %": az["roi_pct"] if az else None,
        "Amazon Net Profit": az["net_profit"] if az else None,
        "Amazon Disqualified": az_disqualified,
        "eBay Active Price": eb["active_price"] if eb else None,
        "eBay Est. Sold Price": eb["sell_price"] if eb else None,
        "eBay ROI %": eb["roi_pct"] if eb else None,
        "Walmart Match": bool(walmart_results.get(u)) if check_walmart else None,
        "Walmart Price": wm["sell_price"] if wm else None,
        "Walmart ROI %": wm["roi_pct"] if wm else None,
        "Best Channel": channel,
        "Best ROI %": channel_roi,
        "Meets Threshold": (channel_roi is not None and channel_roi >= min_roi),
    })

progress.progress(1.0, text="Done")
progress.empty()

results = pd.DataFrame(rows)
results = results.sort_values("Best ROI %", ascending=False, na_position="last")

# Sanity check: the same Amazon ASIN showing up against many different UPCs
# in one run is a red flag, not a coincidence - it's the exact signature of
# a UPC-to-product mismatch bug (see keepa_client.py docstring for the one
# this app used to have). Surface it loudly rather than let bad matches
# blend into the results quietly.
asin_counts = results.loc[results["Amazon ASIN"].notna(), "Amazon ASIN"].value_counts()
suspicious_asins = asin_counts[asin_counts > 3]
if not suspicious_asins.empty:
    st.error(
        f"⚠️ {len(suspicious_asins)} Amazon product(s) are showing up against more than 3 different "
        f"UPCs in this run - that usually means a mismatch, not a real coincidence. Do not trust "
        f"the ROI numbers for these rows until you spot-check them on Amazon directly:"
    )
    for asin, count in suspicious_asins.items():
        sample_title = results.loc[results["Amazon ASIN"] == asin, "Amazon Title"].iloc[0]
        st.write(f"- **{asin}** (\"{sample_title}\") matched to {count} different UPCs")

st.subheader("3. Results")
n_buys = int(results["Meets Threshold"].sum())
st.success(f"{n_buys} of {len(results)} products meet your {min_roi}%+ ROI threshold.")

only_buys = st.checkbox(f"Show only products meeting the {min_roi}% threshold", value=True)
display_df = results[results["Meets Threshold"]] if only_buys else results

def _highlight_flags(row):
    styles = [""] * len(row)
    if row.get("Amazon Sells This?") == "Yes":
        styles[list(row.index).index("Amazon Sells This?")] = "background-color: #ffe9e0"
    if row.get("Price Spike?") == "Yes":
        styles[list(row.index).index("Price Spike?")] = "background-color: #fff3cd"
    if row.get("Rating Concern?") == "Yes":
        styles[list(row.index).index("Rating Concern?")] = "background-color: #fff3cd"
    return styles


st.dataframe(
    display_df.style.format({
        "Cost": "${:.2f}", "Amazon Price": "${:.2f}", "Amazon Net Profit": "${:.2f}",
        "eBay Active Price": "${:.2f}", "eBay Est. Sold Price": "${:.2f}", "Walmart Price": "${:.2f}",
        "Amazon ROI %": "{:.1f}%", "eBay ROI %": "{:.1f}%", "Walmart ROI %": "{:.1f}%", "Best ROI %": "{:.1f}%",
        "Price vs 90-Day Avg %": "{:+.1f}%",
        "Amazon Rating": "{:.1f} ★", "Amazon Reviews": "{:,.0f}",
    }, na_rep="-").apply(_highlight_flags, axis=1),
    use_container_width=True,
    height=500,
)

n_disqualified = int(results["Amazon Disqualified"].notna().sum())
if n_disqualified:
    st.caption(
        f"{n_disqualified} product(s) were excluded from the Amazon channel by your qualifiers "
        f"(sales rank cutoff and/or hazmat) - see the 'Amazon Disqualified' column for why. "
        f"They can still qualify via eBay/Walmart if those are checked."
    )

n_amazon_sells = int((results["Amazon Sells This?"] == "Yes").sum())
if n_amazon_sells:
    st.caption(
        f"{n_amazon_sells} product(s) have Amazon itself as a seller (highlighted) - competing "
        f"directly against Amazon on the Buy Box is usually a tough spot even when the ROI math looks fine."
    )

n_spikes = int((results["Price Spike?"] == "Yes").sum())
if n_spikes:
    st.caption(
        f"{n_spikes} product(s) are priced well above their 90-day average (highlighted) - worth "
        f"double-checking the price history on Amazon/Keepa before buying, since ROI calculated "
        f"against a temporary spike often disappears once the price normalizes."
    )

n_rating_concerns = int((results["Rating Concern?"] == "Yes").sum())
if n_rating_concerns:
    st.caption(
        f"{n_rating_concerns} product(s) have a low rating, too few reviews to trust the rating, or no "
        f"rating data at all (highlighted) - a great ROI on a poorly-rated listing often means high "
        f"returns/refunds eating the margin, so worth a manual look before buying."
    )

if check_ebay:
    st.caption(
        "eBay 'Est. Sold Price' applies your active-to-sold haircut % to the active asking price - "
        "it is an estimate, not real sold data (see README for why)."
    )

if check_walmart:
    st.caption(
        "Walmart price is a rough catalog-search signal (see README) - verify on Walmart.com or via "
        "Pricing Insights after listing before treating it as final."
    )

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    results.to_excel(writer, index=False, sheet_name="ROI Results")
st.download_button(
    "Download full results as Excel",
    data=buf.getvalue(),
    file_name="sourcing_roi_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

if demo_mode or not keepa_key:
    st.warning(
        "You're viewing DEMO MODE data (synthetic, not real Amazon prices). "
        "Add a Keepa API key in the sidebar to run this against real marketplace data."
    )
