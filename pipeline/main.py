"""Entry point: python -m pipeline.main [--dry-run] [--max-queries N]"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import config
from .config import RunSettings, load_config, parse_settings
from .contacts import NON_CONTACT_LINK_DOMAINS, extract_emails, extract_phones_near_keywords, find_links
from .fetcher import PoliteFetcher, is_blocked
from .logging_setup import setup_logging
from .metrics import VideoStats, video_stats
from .models import Candidate, Lead, QueryLogEntry, Rejection, RunStats
from .queries import build_queries, select_queries
from .sheets import SheetStore, SheetsUnavailable
from .validate import VALID, EmailValidator
from .youtube import YouTubeClient, YouTubeError, YouTubeFatal, channel_url, video_url

log = logging.getLogger("pipeline")

EXIT_OK = 0
EXIT_SHEETS_UNAVAILABLE = 1

MAX_EMAILS_TO_VALIDATE = 3


# ---------------------------------------------------------------------------
# Pure decision helpers (unit tested)
# ---------------------------------------------------------------------------


def skip_reason(channel_id: str, lead_statuses: dict[str, str], rejected: set[str]) -> str | None:
    """Why a discovered channel must not be processed again (None = process it)."""
    status = lead_statuses.get(channel_id)
    if status == "do_not_contact":
        return "do_not_contact"
    if status is not None:
        return "already a lead"
    if channel_id in rejected:
        return "previously rejected"
    return None


def subscriber_reason(subscribers: int | None, settings: RunSettings) -> str | None:
    if subscribers is None:
        return "subscriber count hidden"
    if subscribers < settings.min_subscribers:
        return f"subscribers {subscribers} < {settings.min_subscribers}"
    if subscribers > settings.max_subscribers:
        return f"subscribers {subscribers} > {settings.max_subscribers}"
    return None


def video_reason(stats: VideoStats, settings: RunSettings) -> str | None:
    """Are the recent videos good enough? (active, well viewed, engaging)"""
    if stats.sample_size == 0:
        return "no public videos"
    if stats.days_since_upload is None or stats.days_since_upload > settings.max_days_since_upload:
        return f"inactive: last upload {stats.last_upload or 'unknown'}"
    if stats.median_views < settings.min_median_views:
        return f"median views {stats.median_views} < {settings.min_median_views}"
    if stats.engagement_rate is not None and stats.engagement_rate < settings.min_engagement_rate:
        return f"engagement_rate {stats.engagement_rate:.2f} < {settings.min_engagement_rate}"
    return None


def mask_email(email: str) -> str:
    """'riya.fit@gmail.com' -> 'r***@gmail.com' (for logs only)."""
    if not email or "@" not in email:
        return "-"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    """'+919876543210' -> '+91******3210' (for logs only)."""
    if not phone:
        return "-"
    return phone[:3] + "*" * max(0, len(phone) - 7) + phone[-4:]


def collect_candidates(results_by_query) -> dict[str, Candidate]:
    """Unique channels from (PlannedQuery, [video search result]) pairs, first niche wins."""
    candidates: dict[str, Candidate] = {}
    for planned, results in results_by_query:
        for result in results:
            cid = result["channel_id"]
            if cid not in candidates:
                candidates[cid] = Candidate(channel_id=cid, niche=planned.niche,
                                            found_via=video_url(result["video_id"]))
    return candidates


def _host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def own_links(description: str) -> list[str]:
    """Links in the channel description that may be the creator's own site / link page."""
    out = []
    for url in find_links(description):
        host = _host(url)
        if not host or is_blocked(url):
            continue
        if any(host == d or host.endswith("." + d) for d in NON_CONTACT_LINK_DOMAINS):
            continue
        out.append(url)
    return out


def find_contacts(channel: dict, videos: list[dict], fetcher, validator) -> dict[str, str]:
    """Email/phone the creator published: channel description, video descriptions, own site."""
    ch_url = channel_url(channel["channel_id"], channel.get("handle", ""))
    texts = [(channel.get("description", ""), "channel description", ch_url)]
    texts += [(v.get("description", ""), "video description", video_url(v["video_id"])) for v in videos]

    emails: list[tuple[str, str, str]] = []  # (email, source, url)
    phones: list[tuple[str, str, str]] = []
    # Numbers without a +country prefix are read in the channel's country.
    region = (channel.get("country") or config.DEFAULT_PHONE_REGION).upper()

    def add(bucket, value: str, source: str, url: str) -> None:
        if all(value != item[0] for item in bucket):
            bucket.append((value, source, url))

    for text, source, url in texts:
        for address in extract_emails(text):
            add(emails, address, source, url)
        for number in extract_phones_near_keywords(text, region=region):
            add(phones, number, source, url)

    links = own_links(channel.get("description", ""))
    website = links[0] if links else ""
    if website and fetcher is not None and (not emails or not phones):
        try:
            pages = fetcher.fetch_site(website)
        except Exception as exc:  # never let a website break the run
            log.debug("Website fetch failed for %s: %s", website, exc)
            pages = []
        for page in pages:
            for address in page.mailto_emails + extract_emails(page.text):
                for clean in extract_emails(address):
                    add(emails, clean, "website", page.url)
            for number in page.link_phones + extract_phones_near_keywords(page.text, region=region):
                add(phones, number, "website", page.url)

    result = {"website": website, "email": "", "email_status": "", "phone": "",
              "contact_source": "", "source_url": ""}
    chosen_email = None
    if emails:
        statuses = []
        for address, source, url in emails[:MAX_EMAILS_TO_VALIDATE]:
            status = validator.status(address) if validator else ""
            statuses.append((address, source, url, status))
        chosen_email = next((s for s in statuses if s[3] == VALID), statuses[0])
        result["email"], result["email_status"] = chosen_email[0], chosen_email[3]
    chosen_phone = phones[0] if phones else None
    if chosen_phone:
        result["phone"] = chosen_phone[0]

    sources, urls = [], []
    for item in (chosen_email, chosen_phone):
        if item:
            if item[1] not in sources:
                sources.append(item[1])
            if item[2] and item[2] not in urls:
                urls.append(item[2])
    result["contact_source"] = ", ".join(sources)
    result["source_url"] = " | ".join(urls)
    return result


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m pipeline.main", description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="read the sheet but write nothing; print what would be written")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="override Settings queries_per_run for this run")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING ...")
    return parser.parse_args(argv)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


class Pipeline:
    def __init__(self, cfg: config.Config, store: SheetStore, youtube: YouTubeClient,
                 fetcher=None, validator=None,
                 dry_run: bool = False, max_queries: int | None = None, now=_now):
        self.cfg = cfg
        self.store = store
        self.youtube = youtube
        self.fetcher = fetcher
        self.validator = validator
        self.dry_run = dry_run
        self.max_queries = max_queries
        self.now = now
        started = now()
        self.stats = RunStats(run_id=started.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
                              started_at=_iso(started))
        self.leads: list[Lead] = []
        self.rejections: list[Rejection] = []
        self.query_log: list[QueryLogEntry] = []

    # The returned code is the process exit code.
    def run(self) -> int:
        try:
            actions = self.store.bootstrap(dry_run=self.dry_run)
            for action in actions:
                log.info("Sheet setup: %s%s", action, " (dry run, skipped)" if self.dry_run else "")
            settings, warnings = parse_settings(self.store.read_settings())
        except SheetsUnavailable as exc:
            log.error("Google Sheets unreachable: %s", exc)
            return EXIT_SHEETS_UNAVAILABLE
        for w in warnings:
            self.stats.warn(w)

        if not settings.active:
            log.info("Settings active != TRUE; nothing to do.")
            self.stats.warn("inactive: Settings active != TRUE")
            return self._finish()

        try:
            self._run_inner(settings)
        except SheetsUnavailable as exc:
            log.error("Google Sheets unreachable: %s", exc)
            return EXIT_SHEETS_UNAVAILABLE
        except Exception as exc:  # unexpected bug: record it, still write what we have
            self.stats.errors += 1
            self.stats.warn(f"unexpected error: {type(exc).__name__}: {exc}")
            log.error("Unexpected error: %s: %s", type(exc).__name__, exc)
        finally:
            self.stats.youtube_units_used = self.youtube.units_used
        return self._finish()

    def _stop(self, exc: Exception) -> None:
        self.stats.warn(f"YouTube stop: {exc}")
        log.error("YouTube stop: %s", exc)

    def _run_inner(self, settings: RunSettings) -> None:
        now = self.now()
        today = now.date()
        lead_statuses = self.store.read_lead_statuses()
        rejected = self.store.read_rejected_ids()
        last_used = self.store.read_query_log()

        limit = settings.queries_per_run if self.max_queries is None else max(0, self.max_queries)
        planned = build_queries(settings.niches)
        queries = select_queries(planned, last_used, limit, today)
        if not queries:
            self.stats.warn(f"no fresh queries: all {len(planned)} were used in the last "
                            f"{config.QUERY_REUSE_DAYS} days; add more niches")
        log.info("Running %d of %d planned queries", len(queries), len(planned))

        published_after = now - timedelta(days=settings.search_window_days)
        results_by_query = []
        for planned_query in queries:
            try:
                results = self.youtube.search_videos(planned_query.query, published_after)
            except YouTubeFatal as exc:
                self._stop(exc)
                break
            except YouTubeError as exc:
                self.stats.errors += 1
                log.warning("Search failed: %s", exc)
                continue
            self.query_log.append(QueryLogEntry(query=planned_query.query, api="youtube",
                                                date=today.isoformat(), results_count=len(results)))
            results_by_query.append((planned_query, results))

        candidates = collect_candidates(results_by_query)
        self.stats.found = len(candidates)
        fresh = [c for c in candidates.values() if skip_reason(c.channel_id, lead_statuses, rejected) is None]
        log.info("Found %d channels, %d new", len(candidates), len(fresh))
        if not fresh:
            return

        try:
            channels = {c["channel_id"]: c for c in self.youtube.channels([c.channel_id for c in fresh])}
        except YouTubeFatal as exc:
            self._stop(exc)
            return
        except YouTubeError as exc:
            self.stats.errors += 1
            log.warning("Channel lookup failed: %s", exc)
            return

        # Cheapest check first: subscriber range needs no extra API calls.
        to_check = []
        for cand in fresh:
            channel = channels.get(cand.channel_id)
            if channel is None:
                continue
            reason = subscriber_reason(channel["subscribers"], settings)
            if reason:
                self._reject(channel, reason, today)
            else:
                to_check.append((cand, channel))

        for cand, channel in to_check:
            if len(self.leads) >= settings.max_new_leads_per_run:
                self.stats.warn(f"reached max_new_leads_per_run ({settings.max_new_leads_per_run})")
                break
            try:
                self._process(cand, channel, settings, now)
            except YouTubeFatal as exc:
                self._stop(exc)
                break
            except Exception as exc:  # one bad channel never stops the run
                self.stats.errors += 1
                log.warning("Channel %s failed: %s: %s", channel["channel_id"], type(exc).__name__, exc)

    def _reject(self, channel: dict, reason: str, today) -> None:
        self.rejections.append(Rejection(channel_id=channel["channel_id"], name=channel.get("title", ""),
                                         reason=reason, date=today.isoformat()))
        self.stats.rejected += 1
        log.info("Rejected %s (%s): %s", channel.get("title", ""), channel["channel_id"], reason)

    def _process(self, cand: Candidate, channel: dict, settings: RunSettings, now: datetime) -> None:
        today = now.date()
        videos = self.youtube.recent_videos(channel["uploads_playlist"])
        self.stats.checked += 1
        stats = video_stats(videos, now)
        reason = video_reason(stats, settings)
        if reason:
            self._reject(channel, reason, today)
            return

        contacts = find_contacts(channel, videos, self.fetcher, self.validator)
        lead = Lead(
            channel_id=channel["channel_id"],
            handle=channel.get("handle", ""),
            channel_url=channel_url(channel["channel_id"], channel.get("handle", "")),
            name=channel.get("title", ""),
            subscribers=channel["subscribers"],
            total_views=channel.get("total_views", 0),
            video_count=channel.get("video_count", 0),
            median_views=stats.median_views,
            avg_views=stats.avg_views,
            engagement_rate=stats.engagement_rate if stats.engagement_rate is not None else "",
            last_upload=stats.last_upload,
            country=channel.get("country", ""),
            niche=cand.niche,
            description=channel.get("description", "")[:1000],
            date_found=today.isoformat(),
            status="new",
            **contacts,
        )
        self.leads.append(lead)
        self.stats.accepted += 1
        # Logs are public on a public repo: never print the contact details themselves.
        log.info("Accepted %s (%d subs, median %d views, email=%s, phone=%s)", lead.name,
                 lead.subscribers, lead.median_views, mask_email(lead.email), mask_phone(lead.phone))

    def _finish(self) -> int:
        self.stats.finished_at = _iso(self.now())
        if self.dry_run:
            self._print_dry_run()
            return EXIT_OK
        try:
            self.store.append_leads(self.leads)
            self.store.append_rejections(self.rejections)
            self.store.append_query_log(self.query_log)
            self.store.append_run_log(self.stats)
        except Exception as exc:
            log.error("Google Sheets unreachable while writing results: %s", exc)
            return EXIT_SHEETS_UNAVAILABLE
        log.info("Run %s done: %d found, %d checked, %d accepted, %d rejected, %d errors",
                 self.stats.run_id, self.stats.found, self.stats.checked, self.stats.accepted,
                 self.stats.rejected, self.stats.errors)
        for w in self.stats.warnings:
            log.warning("Run warning: %s", w)
        return EXIT_OK

    def _print_dry_run(self) -> None:
        print("\n=== DRY RUN: nothing was written to the sheet ===")
        print(f"Leads to append ({len(self.leads)}):")
        for lead in self.leads:
            d = asdict(lead)
            d["email"], d["phone"] = mask_email(lead.email), mask_phone(lead.phone)
            print("  " + ", ".join(f"{k}={d[k]!r}" for k in
                                   ("name", "subscribers", "median_views", "engagement_rate",
                                    "email", "phone", "contact_source")))
        print(f"Rejected to append ({len(self.rejections)}):")
        for r in self.rejections:
            print(f"  {r.name} ({r.channel_id}): {r.reason}")
        print(f"QueryLog rows to append ({len(self.query_log)}):")
        for q in self.query_log:
            print(f"  [{q.api}] {q.query} -> {q.results_count} results")
        s = self.stats
        print(f"RunLog row: run_id={s.run_id} found={s.found} checked={s.checked} "
              f"accepted={s.accepted} rejected={s.rejected} errors={s.errors} "
              f"youtube_units_used={s.youtube_units_used}")
        for w in s.warnings:
            print(f"  warning: {w}")


def build_pipeline(cfg: config.Config, args: argparse.Namespace) -> Pipeline:
    store = SheetStore.connect(cfg.google_service_account_json, cfg.sheet_id)
    youtube = YouTubeClient(api_key=cfg.youtube_api_key, service_account_json=cfg.google_service_account_json)
    return Pipeline(cfg, store, youtube, fetcher=PoliteFetcher(), validator=EmailValidator(),
                    dry_run=args.dry_run, max_queries=args.max_queries)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    cfg = load_config()

    if not cfg.has_sheets:
        log.error(
            "Cannot reach Google Sheets: GOOGLE_SERVICE_ACCOUNT_JSON and SHEET_ID are required. "
            "Locally, copy .env.example to .env and fill them in; on GitHub, add them under "
            "Settings > Secrets and variables > Actions. See SETUP.md steps 1-2."
        )
        return EXIT_SHEETS_UNAVAILABLE
    if not cfg.youtube_api_key:
        log.info("YOUTUBE_API_KEY not set; using the service account for the YouTube Data API")
    try:
        pipeline = build_pipeline(cfg, args)
    except SheetsUnavailable as exc:
        log.error("Google Sheets unreachable: %s", exc)
        return EXIT_SHEETS_UNAVAILABLE
    return pipeline.run()


if __name__ == "__main__":
    sys.exit(main())
