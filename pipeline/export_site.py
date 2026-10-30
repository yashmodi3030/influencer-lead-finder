"""Build the static dashboard with the leads embedded as an encrypted file.

    python -m pipeline.export_site --out _site

Copies web/ to --out and writes data.enc.json: the Leads and latest RunLog row,
encrypted with AES-256-GCM using a key derived (PBKDF2-SHA256) from
DASHBOARD_USERNAME + DASHBOARD_PASSWORD. The browser decrypts it locally after the
user types the same credentials, so no login API is needed and the published file is
useless without them. The credentials themselves are never written anywhere.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import load_config
from .logging_setup import setup_logging
from .sheets import SheetStore, SheetsUnavailable

log = logging.getLogger("pipeline.export_site")

PBKDF2_ITERATIONS = 600_000
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DATA_FILE = "data.enc.json"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def derive_key(username: str, password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    # Must match web/app.js: UTF-8 of "<trimmed username>\n<password>".
    material = f"{username.strip()}\n{password}".encode("utf-8")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(material)


def encrypt_payload(payload: dict, username: str, password: str,
                    iterations: int = PBKDF2_ITERATIONS) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    key = derive_key(username, password, salt, iterations)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)  # ciphertext || 16-byte tag
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": _b64(salt),
            "iv": _b64(iv), "ct": _b64(ciphertext)}


def decrypt_payload(blob: dict, username: str, password: str) -> dict:
    """Inverse of encrypt_payload; raises ValueError on wrong credentials."""
    salt = base64.b64decode(blob["salt"])
    key = derive_key(username, password, salt, int(blob["iter"]))
    try:
        plaintext = AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["ct"]), None)
    except InvalidTag as exc:
        raise ValueError("wrong username or password") from exc
    return json.loads(plaintext)


RECENT_SEARCHES = 20


def build_payload(store: SheetStore, now: datetime | None = None, sheet_id: str = "",
                  repo: str = "", dispatch_token: str = "") -> dict:
    """Everything the dashboard shows. It is encrypted before it is published.

    ``dispatch_token`` (optional) lets the dashboard start a search via the GitHub API;
    it should be a fine-grained token limited to Actions on this one repository.
    """
    store.attach()
    leads = [r for r in store.read_table("Leads") if (r.get("channel_id") or "").strip()]
    runs = store.read_table("RunLog")
    searches = [r for r in store.read_table("Searches") if (r.get("query") or "").strip()]
    return {
        "generated_at": (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat(),
        "leads": leads,
        "last_run": runs[-1] if runs else None,
        "searches": searches[-RECENT_SEARCHES:][::-1],
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit" if sheet_id else "",
        "repo": repo,
        "dispatch_token": dispatch_token,
    }


def write_site(out_dir: Path, blob: dict) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(WEB_DIR, out_dir)
    (out_dir / DATA_FILE).write_text(json.dumps(blob), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.export_site", description=__doc__)
    parser.add_argument("--out", default="_site", help="output folder (default: _site)")
    args = parser.parse_args(argv)
    setup_logging()

    cfg = load_config()  # also loads a local .env into os.environ
    username = (os.environ.get("DASHBOARD_USERNAME") or "").strip()
    password = os.environ.get("DASHBOARD_PASSWORD") or ""
    if not username or not password:
        log.error("DASHBOARD_USERNAME and DASHBOARD_PASSWORD must be set (GitHub secrets or .env).")
        return 1
    if not cfg.has_sheets:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON and SHEET_ID are required to read the leads.")
        return 1
    try:
        payload = build_payload(SheetStore.connect(cfg.google_service_account_json, cfg.sheet_id),
                                sheet_id=cfg.sheet_id, repo=(os.environ.get("GITHUB_REPOSITORY") or "").strip(),
                                dispatch_token=(os.environ.get("DISPATCH_TOKEN") or "").strip())
    except SheetsUnavailable as exc:
        log.error("Google Sheets unreachable: %s", exc)
        return 1

    write_site(Path(args.out), encrypt_payload(payload, username, password))
    log.info("Wrote %s with %d encrypted leads", Path(args.out) / DATA_FILE, len(payload["leads"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
