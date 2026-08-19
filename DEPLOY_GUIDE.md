# Getting this online with zero installs — step by step

You will not touch a terminal or write any code. This uses two free
services: **GitHub** (just stores your files) and **Streamlit Community
Cloud** (runs the app and gives you a web address). Total time: ~15 minutes,
one-time.

---

## Step 1 — Unzip the project

Double-click `sourcing_dashboard.zip` to extract it. You should end up with
a folder containing files like `app.py`, `roi.py`, `README.md`, etc. Leave
this folder where it is — you'll upload these individual files in Step 3.

## Step 2 — Create a free GitHub account

1. Go to [github.com](https://github.com) and click **Sign up**.
2. Use your email, pick a username and password. Free plan is all you need.
3. Verify your email when it asks.

## Step 3 — Upload the project files to a new repository

1. Once logged in, click the **+** icon top-right → **New repository**.
2. Name it something like `sourcing-dashboard`. You can leave it **Public**
   (Streamlit Community Cloud's free tier only works with public code
   repos — but don't worry, your API keys never go in this repo, see Step 5
   — and you'll separately lock down *who can open the running app* in
   Step 6).
3. Click **Create repository**.
4. On the next page, click **uploading an existing file**.
5. Drag every file from the unzipped folder (`app.py`, `roi.py`,
   `sheet_parser.py`, `keepa_client.py`, `ebay_client.py`,
   `walmart_client.py`, `demo_data.py`, `requirements.txt`, `README.md`,
   and the `sample_data` folder with `sample_distributor_sheet.xlsx`
   inside it) into the upload box.
   - Do **not** upload `.env` or `.env.example` — you won't need them for
     the hosted version; keys go in through Streamlit's own Secrets screen
     instead (Step 5).
6. Scroll down, click **Commit changes**.

## Step 4 — Create a free Streamlit Community Cloud account

1. Go to [share.streamlit.io](https://share.streamlit.io).
2. Click **Continue with GitHub** and log in with the account from Step 2.
3. Approve Streamlit's request to access your GitHub repos.

## Step 5 — Deploy the app

1. Click **Create app** → **Deploy a public app from GitHub**.
2. Pick the `sourcing-dashboard` repository you created.
3. Branch: `main`. Main file path: `app.py`.
4. Click **Advanced settings** before deploying, and paste this into the
   **Secrets** box (fill in the values you actually have — leave any you
   don't have as empty quotes, the app runs fine without them, just in demo
   mode for whichever pieces are missing):

   ```toml
   KEEPA_API_KEY = "your-keepa-key-here"
   EBAY_APP_ID = ""
   EBAY_CERT_ID = ""
   WALMART_CLIENT_ID = ""
   WALMART_CLIENT_SECRET = ""
   ```

5. Click **Save**, then **Deploy**. It'll take a minute or two to build.

## Step 6 — Lock the app down to just you

By default anyone with the link could open your running app (and type in
whatever keys they want in the sidebar — not great). Restrict it:

1. From your app's page on share.streamlit.io, open its settings (⋮ menu →
   **Settings** → **Sharing**).
2. Under viewer access, switch it to restrict by email, and add your own
   email (gdecllc10@gmail.com) to the allow-list.
3. Now only someone signed in as you can open the app.

## Step 7 — Use it

Streamlit gives you a URL like `https://sourcing-dashboard-yourname.streamlit.app`.
Bookmark it. From now on, that's your dashboard — open it in any browser,
upload a distributor sheet, get ranked ROI. No installs, ever.

## Updating it later

If I (or you) change the code, the update goes to the GitHub repo (drag in
the new file the same way as Step 3, or ask me for the updated files and
repeat the upload), and the live app on Streamlit Cloud picks it up
automatically within a minute or two — no redeploying needed.
