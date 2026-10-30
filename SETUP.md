# Setup guide (no prior experience needed)

Budget about 30 minutes. The pipeline uses these secrets:

| Secret | Where it comes from |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Step 2 – service-account key file contents |
| `SHEET_ID` | Step 2 – from the Sheet URL |
| `YOUTUBE_API_KEY` | Step 4 – recommended (without it the service account is tried) |
| `TAVILY_API_KEY` / `SERPAPI_KEY` | Step 4 – optional, for finding Instagram profiles |
| `DISPATCH_TOKEN` | Step 5 – optional, lets the dashboard start searches directly |
| `DASHBOARD_USERNAME` | Step 3 – already set for you |
| `DASHBOARD_PASSWORD` | Step 3 – already set for you |

Keep them in a password manager while you work. Never paste them into
`web/config.js` or any committed file.

> Menus change over time. If a button has moved, look for the same words nearby.

---

## 1. Google Cloud project + Sheets API

1. Open <https://console.cloud.google.com/> and sign in with the Google account that will own the Sheet.
2. Top bar → click the **project picker** (next to "Google Cloud") → **New project**.
3. Project name: `influencer-lead-finder` → **Create**. Wait for the notification, then **Select project**.
4. Left menu (☰) → **APIs & Services** → **Library**.
5. Search **Google Sheets API** → click it → **Enable**.

## 2. Service account, key, and the Sheet

**Create the service account (the pipeline's robot identity)**

1. ☰ → **IAM & Admin** → **Service Accounts** → **+ Create service account**.
2. Name: `lead-bot` → **Create and continue** → skip roles (**Continue**) → **Done**.
3. Click the new account → copy its **email** (looks like `lead-bot@influencer-lead-finder.iam.gserviceaccount.com`).
4. **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create**. A `.json` file downloads.
   - If you see *"Service account key creation is disabled"*, your organisation blocks keys
     (policy `iam.disableServiceAccountKeyCreation`). Use a personal Gmail account/project instead.
5. Treat this file like a password. Do **not** put it inside the repo folder.

**Create the Sheet**

6. Open <https://sheets.new>. Rename it (top-left) e.g. `Influencer Leads`.
7. Click **Share** → paste the service-account email → role **Editor** → untick *Notify people* → **Share**.
8. Copy the **SHEET_ID** from the address bar:
   `https://docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit` – the part between `/d/` and `/edit`.
9. You don't need to create any tabs: the first pipeline run creates `Leads`, `Settings`,
   `Searches`, `Rejected`, `QueryLog` and `RunLog` with headers, and seeds Settings.

**Turning the key file into the secret:** `GOOGLE_SERVICE_ACCOUNT_JSON` is the *entire
contents* of the JSON file. Open it in Notepad, **Ctrl+A**, **Ctrl+C**. For GitHub you can
paste it as-is (multi-line is fine). For a local `.env`, put it on one line wrapped in
single quotes: `GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'`.

## 3. Dashboard login (username + password)

The website has **no login API and no Google sign-in**. Instead, every deploy encrypts the
leads into `data.enc.json` (AES-256-GCM, key derived with PBKDF2-SHA256, 600,000 rounds) using
the username and password stored in two GitHub secrets. In the browser you type the same
username and password and the data is decrypted locally; wrong credentials can't decrypt it.
The password is never stored in the code or on the site.

1. The secrets `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` have already been added to the repo.
2. **To change the password** (recommended – use a long, unique passphrase, because anyone can
   download the encrypted file and try guesses offline): repo → **Settings** → **Secrets and
   variables** → **Actions** → `DASHBOARD_PASSWORD` → **Update** → then **Actions** →
   **Deploy website to GitHub Pages** → **Run workflow**. The old password stops working
   as soon as the new deploy finishes.
3. Share the username/password only with people who should see all leads. To revoke
   someone's access, change the password (step 2).
4. The dashboard is **read-only**: change `status` / `notes` directly in the Google Sheet. The site
   picks up changes after the next pipeline run, or immediately if you run the deploy workflow.
5. Anyone you want to edit the Sheet directly: share the Sheet with their Google account (Editor).

## 4. YouTube Data API (free, ~3 minutes)

Use the **same Google Cloud project** as step 1.

1. ☰ → **APIs & Services** → **Library** → search **YouTube Data API v3** → click it → **Enable**.
2. ☰ → **APIs & Services** → **Credentials** → **+ Create credentials** → **API key**. Copy the
   key (starts with `AIza…`) → this is **YOUTUBE_API_KEY**.
3. Recommended: click the new key → **API restrictions** → **Restrict key** → tick only
   **YouTube Data API v3** → **Save**. Leave *Application restrictions* on **None** (GitHub's
   servers have changing IPs).
4. Add it as the GitHub secret `YOUTUBE_API_KEY` (step 5).

No billing account is needed. The free quota is 10,000 units per day and resets at midnight
Pacific time; the pipeline uses about 1,500–2,500 per run and stops cleanly before the limit.

If `YOUTUBE_API_KEY` is not set, the pipeline tries the service account from step 2 instead
(the API must still be enabled, step 4.1). If the RunLog then says YouTube refused the
credentials, create the API key.

**Web search for Instagram (optional)**

5. <https://app.tavily.com/> → sign up → copy the API key (`tvly-…`) → **TAVILY_API_KEY**
   (free: ~1,000 searches/month).
6. Optional fallback: <https://serpapi.com/manage-api-key> → **SERPAPI_KEY** (free: ~250/month).

These are used only to find a creator's Instagram profile when their YouTube descriptions don't
link it, and to read the public bio from the search result. At most
`instagram_lookups_per_run` (default 15) searches per run, and only for leads still missing an
email or phone. Without the keys, Instagram handles still come from the creators' own text.

## 5. GitHub repository, secrets and Pages

The repository is already created for you as **private**. Choose how to host the website:

| Option | Cost | What to do |
|---|---|---|
| **A. Make the repo public** (recommended for free) | Free | Repo **Settings** → **General** → bottom *Danger Zone* → **Change visibility** → **Public**. Safe: no secrets or lead data are in the code; the published data file is encrypted. |
| B. Keep it private + GitHub Pro | Paid | Upgrade at <https://github.com/settings/billing>; Pages then works on private repos. |
| C. Keep it private, host elsewhere | Free | Netlify: import the repo, build command `pip install -r requirements.txt && python -m pipeline.export_site --out _site`, publish directory `_site`, and add `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD` as Netlify environment variables. Add a Netlify build hook to rebuild daily. |

Note: private repos get 2,000 free Actions minutes/month; one daily run uses ~2–4 minutes, so that's fine.

**Secrets**

1. Repo → **Settings** → **Secrets and variables** → **Actions** → **Secrets** tab → **New repository secret**.
2. Already added for you: `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `DASHBOARD_USERNAME`,
   `DASHBOARD_PASSWORD`, `YOUTUBE_API_KEY`, `TAVILY_API_KEY`, `SERPAPI_KEY`. To replace one,
   click it → **Update secret** (names must match exactly).

**Optional: start searches straight from the dashboard (`DISPATCH_TOKEN`)**

Without this, the dashboard's *New search* box copies your query and links you to the
Searches tab or the Run workflow page. With it, the search starts with one click.

1. <https://github.com/settings/personal-access-tokens/new> (fine-grained token).
2. Name `lead-finder-search`, expiration e.g. 1 year, **Repository access** → *Only select
   repositories* → this repo.
3. **Permissions** → Repository permissions → **Actions: Read and write**. Nothing else.
4. **Generate token**, copy it, add it as the secret **`DISPATCH_TOKEN`**, then run
   **Actions → Deploy website to GitHub Pages → Run workflow**.

The token is stored only inside the encrypted data file, so only people who know the dashboard
password can use it, and it can do nothing except start this repository's workflows.

**Enable Pages (options A/B)**

3. Repo → **Settings** → **Pages** → **Build and deployment** → **Source** = **GitHub Actions**.
4. **Settings** → **Secrets and variables** → **Actions** → **Variables** tab → **New repository variable**:
   name `PAGES_ENABLED`, value `true`. (The deploy workflow is skipped until this exists, so
   a private repo without Pages doesn't show failing runs.)
5. **Actions** → **Deploy website to GitHub Pages** → **Run workflow**. The site URL is shown on
   the run and under Settings → Pages: `https://<username>.github.io/<repo-name>/`.
   After that, the site redeploys automatically after every successful pipeline run.

## 6. First run

1. Repo → **Actions** → **Daily lead pipeline** → **Run workflow** → tick **Dry run** first → **Run workflow**.
2. Open the run → job **run** → step **Run pipeline**. A dry run prints the leads it *would*
   write. Fix anything flagged, then run again without Dry run.
3. Check the Sheet: tabs were created; `RunLog` has a row; new rows in `Leads`/`Rejected`.
4. The real run triggers the website deploy. Open the site and sign in with the dashboard
   username and password.
5. From now on it runs every day at 09:30 IST. Pause it any time with Settings `active` = `FALSE`.

Note: GitHub disables scheduled workflows in repos with no activity for 60 days – it emails
you first; click **Enable workflow** on the Actions tab to resume.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pipeline exits 1: *"Spreadsheet … not found, or not shared with …"* / Sheets **403** | Sheet not shared with the service account, or wrong SHEET_ID | Share the Sheet with the `client_email` from the JSON as **Editor**; recheck the ID between `/d/` and `/edit`. |
| *"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON"* | Partial copy | Paste the **whole** file, including `{` and `}`. |
| Sheets **403** *"API has not been used in project … or it is disabled"* | Sheets API not enabled | Step 1.4–1.5 in the same project as the service account. |
| Website: *"Invalid username or password."* | Typo, or the password secret was changed | Usernames/passwords are case-sensitive. If you changed `DASHBOARD_PASSWORD`, make sure the deploy workflow ran afterwards. |
| Website: *"No data has been published yet"* | Site deployed without data | Run **Deploy website to GitHub Pages** (it reads the Sheet). Check that step's log for errors. |
| Deploy fails: *"DASHBOARD_USERNAME and DASHBOARD_PASSWORD must be set"* | Secret missing/renamed | Re-add both secrets (step 3). |
| Website shows old data | Deploy runs after the pipeline | Run the deploy workflow manually, or wait for the next daily run. |
| RunLog: *"YouTube Data API v3 is not enabled"* | API not enabled in the project | Step 4.1, in the same project as the key / service account. |
| RunLog: *"YOUTUBE_API_KEY is invalid"* / *"restricted"* | Wrong key or restrictions | Copy the key again; API restriction must allow YouTube Data API v3, application restriction **None**. |
| RunLog: *"YouTube refused the credentials"* | Service account not accepted | Create `YOUTUBE_API_KEY` (step 4). |
| RunLog: *"YouTube quota exhausted"* / *"budget reached"* | 10,000 daily units used | Nothing to do – it continues tomorrow. Lower `queries_per_run` if it happens daily. |
| Few leads, many *"median views … <"* / *"subscribers … <"* rejections | Thresholds too strict for the niche | Lower `min_median_views` / `min_subscribers` in Settings. |
| Leads without email | Creator only shows it behind YouTube's "View email address" button | Expected: that button needs a CAPTCHA and is not automated. Use the channel link to look manually. |
| RunLog: *"no fresh queries"* | All query variants used in last 7 days | Add more niches in Settings. |
| Searches tab row stays empty | The 30-minute check hasn't run yet, or `status` isn't blank/`pending` | Wait up to 30 min, or run the workflow manually. GitHub may delay scheduled runs at busy times. |
| Dashboard: *"GitHub refused the request (HTTP 401/403/404)"* | `DISPATCH_TOKEN` expired, wrong repo, or missing *Actions: write* | Create a new token (step 5), update the secret, redeploy. |
| Lead has an Instagram handle but no followers/bio | The profile wasn't in the search results, or the lookup budget was used up | Raise `instagram_lookups_per_run`, or check the profile link manually. |
| Pages workflow skipped | `PAGES_ENABLED` variable missing | Step 5.4. |
| Pages deploy fails *"Get Pages site failed"* | Pages not enabled, or private repo on free plan | Step 5 options A/B/C. |
