# Influencer Lead Finder

A daily, fully automated pipeline that finds **YouTube creators** in your niches whose
channels are big enough and whose recent videos perform well, extracts the business
emails/phones they **published themselves**, and writes new de-duplicated leads to a
private Google Sheet. A small static dashboard (username + password, no login API) lets you
search, filter and export the leads.

```
GitHub Actions (daily 09:30 IST)
  └─ python -m pipeline.main
       1. Settings tab (Google Sheet)                ← niches and thresholds
       2. YouTube Data API: search videos             ← most-viewed recent videos per niche query
       3. YouTube Data API: channels + last 10 videos ← subscribers, views, likes, comments, dates
       4. Filters: subscriber range, median views, engagement %, uploaded recently
       5. Contacts: channel description → video descriptions → own website (robots.txt respected)
       6. One batched append to Leads; Rejected / QueryLog / RunLog updated
Deploy workflow (after each run)
  └─ python -m pipeline.export_site  → web/ + data.enc.json (leads, AES-256-GCM encrypted)
Static site ── user types username + password ── browser decrypts data.enc.json locally
```

## Code is public-safe, data is private

Everything in this repository is safe to publish: there are **no keys, tokens or lead
data** in the code. Secrets live only in GitHub Actions secrets (or your local `.env`,
which is git-ignored). The leads live in your Google Sheet, which is private.

The website has **no login API**. At deploy time the leads are encrypted into
`data.enc.json` with AES-256-GCM, using a key derived (PBKDF2-SHA256, 600k rounds) from the
`DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` secrets. The login form simply tries to
decrypt that file in the browser: correct credentials reveal the data, wrong ones can't.
The password is not in the code or on the site, and decrypted data lives only in memory
(nothing in localStorage/cookies; auto-lock after 60 idle minutes).

Because anyone can download the encrypted file and guess passwords offline, **use a long,
unique password** (SETUP.md step 3). The dashboard is read-only; edit status/notes in the Sheet.

> The repo is created **private** here. Free GitHub Pages hosting needs a public repo
> (or GitHub Pro for private). Making the repo public is safe because of the design
> above — see SETUP.md step 5 for the options.

## Compliance

- YouTube data comes only from the official **YouTube Data API v3**. youtube.com pages are never
  fetched, no logins, no headless browsers, no CAPTCHA tricks (the hidden "View email address"
  button on a channel's About page is deliberately not used).
- Only contact details the creator **published** (channel description, their own video
  descriptions, their own website linked from the channel) are stored, with `contact_source`
  and `source_url` for traceability. Emails are never guessed; phones are only kept next to
  words like *contact/business/booking/whatsapp*.
- Website fetching honours `robots.txt`, identifies itself with a User-Agent, uses a 10 s timeout
  and goes at most one level deep on the same domain.
- `do_not_contact` leads are never re-added. Respect local anti-spam/data-protection laws
  (e.g. India's DPDP Act, CAN-SPAM, GDPR) when you reach out.

## Quick start (local)

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # fill in values (SETUP.md)
python -m pytest
python -m pipeline.main --dry-run --max-queries 2  # reads the sheet, writes nothing
python -m pipeline.main                            # real run
```

Build and serve the dashboard locally (needs `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` in `.env`):
`python -m pipeline.export_site --out _site && python -m http.server 8000 -d _site`, then open
<http://localhost:8000>.

## Configuration

Edit the **Settings** tab of the Sheet (created automatically on first run):

| key | default | meaning |
|---|---|---|
| niches | fitness | comma-separated, e.g. `fitness, yoga, cooking` |
| min_subscribers / max_subscribers | 50000 / 5000000 | channel size range |
| min_median_views | 10000 | median views of the channel's last 10 videos |
| min_engagement_rate | 1.0 | % = (likes + comments) / views over the last 10 videos (skipped if likes are hidden) |
| max_days_since_upload | 60 | channel must have uploaded within this many days |
| search_window_days | 90 | only videos published in this window are searched |
| queries_per_run | 10 | YouTube searches per run (100 quota units each) |
| max_new_leads_per_run | 50 | stop after this many accepted leads |
| active | TRUE | anything else pauses the pipeline |

Queries rotate across niche × 10 templates (`{niche}`, `{niche} tips`, `{niche} for beginners`,
…), ordered by views, and skip anything used in the last 7 days (tracked in `QueryLog`).

**Quota:** the YouTube Data API gives 10,000 free units per day. A default run uses about
1,000 for searches plus ~1 unit per channel and 2 per checked channel — typically 1,500–2,500.
The pipeline stops cleanly before the limit and records a RunLog warning.

CLI flags: `--dry-run`, `--max-queries N`, `--log-level DEBUG`.
Exit code is non-zero **only** when Google Sheets is unreachable; everything else is
recorded as errors/warnings in `RunLog`.

## Repository layout

```
pipeline/   Python pipeline (entry point: python -m pipeline.main)
tests/      pytest suite, every network call mocked
web/        static dashboard (no build step)
.github/workflows/  pipeline.yml (daily), tests.yml (push/PR), pages.yml (site deploy)
SETUP.md    step-by-step account & credential setup
```

## Email status values

`valid` (domain has MX records) · `no_mx` (domain can't receive mail) · `invalid`
(bad syntax) · `unchecked` (DNS lookup timed out). No SMTP probing is done.
