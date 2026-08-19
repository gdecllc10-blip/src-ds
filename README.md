# Sourcing ROI Dashboard

Upload a distributor Excel sheet, and every UPC on it gets checked against
Amazon (and optionally eBay) pricing automatically — no more pasting UPCs
into SellerAmp/WAMP one at a time. Results come back as a ranked table with
ROI %, sorted best-to-worst, filterable to whatever ROI floor you set (30–50%
per your usual range).

## Why this instead of SellerAmp/WAMP automation

Neither SellerAmp SAS nor WAMP expose a bulk-upload or public API for
pushing in a list of UPCs — they're built around a Chrome extension /
mobile-scan workflow, one product at a time. So this tool pulls Amazon data
directly from the **Keepa API**, which does support bulk lookup (up to 100
UPCs per request), and optionally eBay data from eBay's official **Browse
API**. It replaces the tools' manual-entry step rather than automating
clicks inside them.

## What it does

1. **Upload** your distributor `.xlsx`/`.xls`/`.csv` file.
2. It **auto-detects** which columns are UPC and wholesale cost (you can
   override in dropdowns if it guesses wrong — your sheets vary distributor
   to distributor, so always double-check this step).
3. It **batches all UPCs to Keepa** (100 per request) and pulls current
   price, sales rank, and an FBA fee estimate.
4. It **computes ROI** per product: `(sell price − cost − fees) / cost`,
   using Amazon's real referral fee % from Keepa when available, or your
   fallback % otherwise.
5. Optionally does the same against eBay active-listing comps.
6. Shows a **sortable, filterable results table** — flip on "only show
   products meeting my ROI threshold" and you've got your buy list. Export
   to Excel with one click.

## Setup

**Not a developer / don't want to install anything?** Follow
[`DEPLOY_GUIDE.md`](./DEPLOY_GUIDE.md) instead — it walks through getting
this hosted online for free (Streamlit Community Cloud), so you just visit
a web address, no terminal or installs. The instructions below are for
running it locally on your own machine instead, if you'd rather do that.

```bash
cd sourcing_dashboard
pip install -r requirements.txt
cp .env.example .env      # then paste your API keys into .env
streamlit run app.py
```

This opens the dashboard in your browser at `http://localhost:8501`.

### Try it without any API keys first

Leave the Keepa key blank (or check "Force demo mode") and click **"Try it
with a sample distributor sheet instead."** You'll see the full workflow —
column detection, ROI calculation, filtering, export — running against
synthetic-but-realistic data, so you can confirm the tool does what you want
before paying for anything.

### Getting a Keepa API key (does the real Amazon lookups)

1. Sign up at [keepa.com](https://keepa.com) and subscribe to an API plan
   (Starter tier is ~€49/mo as of this writing — check current pricing,
   it changes). Cost scales with token usage, roughly 1 token per UPC
   checked, so a 50-product distributor sheet costs about 50 tokens.
2. Grab your API key from your Keepa account settings.
3. Paste it into `.env` as `KEEPA_API_KEY`, or paste it directly into the
   sidebar in the app (the sidebar field overrides `.env`).

### Getting eBay API credentials (optional)

1. Register a developer account at
   [developer.ebay.com](https://developer.ebay.com/my/keys) (free).
2. Create a "production" keyset — you need the **App ID (Client ID)** and
   **Cert ID (Client Secret)**.
3. Paste both into `.env` or the sidebar, and check "Also check eBay comps"
   in the app.
4. Note: eBay's Browse API returns **active listing prices**, not sold
   comps. Sold/completed data lives behind eBay's Marketplace Insights API,
   which requires an approved limited-release application — active price is
   used here as the closest available proxy.

### Walmart (approved sellers)

If you're an approved Walmart Marketplace seller, check "Also check Walmart
catalog match" in the sidebar and the app will look up each UPC against
Walmart's official **Item Search API**. Generate credentials from Seller
Center: **Settings → API Credentials** at seller.walmart.com, and paste the
Client ID/Secret into `.env` or the sidebar.

**Be aware of what this actually gives you, though — it's more limited than
the Amazon/eBay integrations:**

- Item Search is built for the "does Walmart already carry this, so I can
  add my offer to it" workflow, not as a dedicated pricing feed. It reliably
  tells you whether the UPC exists in Walmart's catalog and gives you the
  title/brand/itemId. Some items return a price alongside that, but it's
  not guaranteed to be present or fully current — treat any Walmart ROI
  number this tool shows you as a rough first pass, not something to buy
  against directly.
- For a real, current Buy Box / competitor price on an item you don't sell
  yet, Walmart's **Pricing Insights API** only works for items you already
  have an active offer on. The standard workaround experienced Walmart
  resellers use: match the item here first, then create a $0-inventory
  (inactive) offer against that itemId via the Item Setup API, then pull
  Pricing Insights for real Buy Box/competitor data. That's a meaningful
  account action (it creates a live offer, even if hidden), so this tool
  deliberately doesn't do it automatically — it's a manual next step for
  your shortlisted candidates, not something to run against your whole
  distributor sheet.
- This tool does **not** scrape walmart.com for prices. That's outside
  Walmart's Terms of Service and outside the official Marketplace API this
  module uses — worth knowing since some third-party "Walmart sourcing"
  tools do this quietly.

Practical workflow: run the sheet through Amazon/eBay first, let Walmart
catalog-match narrow down which products are even sold there, then
hand-verify Buy Box price on Walmart.com (or via the offer+Pricing Insights
path) for your top ROI candidates before committing to a purchase order.

## Tuning the fee assumptions

The sidebar lets you adjust:
- **Amazon referral fee fallback %** — used only when Keepa doesn't report
  a category-specific referral rate for a product.
- **Amazon fulfillment fee fallback ($)** — used when Keepa doesn't return
  an FBA pick & pack estimate.
- **eBay final value fee %** and **flat shipping estimate**.

These are estimates. Before committing real money to a purchase order,
double-check the actual numbers for your top candidates the way you
normally would.

## Files

- `app.py` — the Streamlit dashboard (main entry point).
- `sheet_parser.py` — reads distributor sheets, detects UPC/cost columns.
- `keepa_client.py` — bulk Amazon lookups via the Keepa API.
- `ebay_client.py` — active-listing comp pricing via eBay's Browse API.
- `walmart_client.py` — catalog matching via Walmart's Item Search API (approved sellers only).
- `roi.py` — ROI math per marketplace.
- `demo_data.py` — synthetic data generator for demo mode.
- `test_pipeline.py` — a quick non-UI smoke test (`python3 test_pipeline.py`)
  that verifies sheet parsing → ROI math end-to-end.
- `sample_data/sample_distributor_sheet.xlsx` — sample file for demo mode.

## Known limitations / things to verify before relying on this for real POs

- Keepa's FBA fee estimate is an approximation, not the exact size-tier fee
  Amazon will charge — for close-call products, verify with Amazon's
  Revenue Calculator before buying.
- One UPC can map to multiple ASINs on Amazon (bundles, variations); this
  tool currently uses the first match Keepa returns. If a product looks
  off, it's worth checking Keepa/Amazon directly for that UPC.
- eBay comps are active listing prices, not confirmed sold prices — treat
  as directional, not exact.
- Column auto-detection is a best guess — always confirm the UPC/cost
  columns are right before running an analysis, since a wrong cost column
  would silently produce wrong ROI numbers.
