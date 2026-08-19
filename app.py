import os
import io
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from sheet_parser import load_sheet, detect_columns, clean_upc, clean_cost
from roi import FeeAssumptions, amazon_roi, ebay_roi, walmart_roi, best_channel
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

    fees = FeeAssumptions(
        amazon_referral_fallback_pct=referral_fallback,
        amazon_fulfillment_fallback=fulfillment_fallback,
        ebay_fee_pct=ebay_fee_pct,
        ebay_shipping_estimate=ebay_shipping,
        walmart_referral_fallback_pct=walmart_referral_fallback,
        walmart_fulfillment_fallback=walmart_fulfillment_fallback,
    )

    check_ebay = st.checkbox("Also check eBay comps", value=False)
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
        keepa_results[u] = [demo_data.fake_keepa_product(u, row["cost"])]
else:
    try:
        keepa_results = keepa_client.fetch_products_by_upc(keepa_key, upcs)
    except keepa_client.KeepaError as e:
        st.error(f"Keepa lookup failed: {e}")
        st.stop()

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
    eb = ebay_roi(cost, ebay_results.get(u), fees) if check_ebay else None
    wm = walmart_roi(cost, walmart_results.get(u), fees) if check_walmart else None
    channel, channel_roi = best_channel(az, eb, wm)

    rows.append({
        "UPC": u,
        "Description": r["description"],
        "Cost": cost,
        "Case Qty": r["case_qty"],
        "Amazon Title": kp.get("title") if kp else None,
        "Amazon Price": az["sell_price"] if az else None,
        "Amazon Sales Rank": kp.get("sales_rank") if kp else None,
        "Amazon ROI %": az["roi_pct"] if az else None,
        "Amazon Net Profit": az["net_profit"] if az else None,
        "eBay Price": eb["sell_price"] if eb else None,
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

st.subheader("3. Results")
n_buys = int(results["Meets Threshold"].sum())
st.success(f"{n_buys} of {len(results)} products meet your {min_roi}%+ ROI threshold.")

only_buys = st.checkbox(f"Show only products meeting the {min_roi}% threshold", value=True)
display_df = results[results["Meets Threshold"]] if only_buys else results

st.dataframe(
    display_df.style.format({
        "Cost": "${:.2f}", "Amazon Price": "${:.2f}", "Amazon Net Profit": "${:.2f}",
        "eBay Price": "${:.2f}", "Walmart Price": "${:.2f}",
        "Amazon ROI %": "{:.1f}%", "eBay ROI %": "{:.1f}%", "Walmart ROI %": "{:.1f}%", "Best ROI %": "{:.1f}%",
    }, na_rep="-"),
    use_container_width=True,
    height=500,
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
