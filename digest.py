#!/usr/bin/env python3
"""
Daily Singapore real-estate news digest.

Pipeline:
  1. Pull recent articles from Google News searches + direct RSS feeds.
  2. Send the headlines/snippets to Claude for a summary + impact analysis
     focused on the Singapore property market.
  3. Deliver the result to Telegram (and print it to the log).

Run modes:
  python digest.py            # full run (fetch -> analyse -> send)
  python digest.py --check    # just test every feed and report counts
  python digest.py --dry-run  # fetch + analyse, print result, DON'T send
"""

import os
import re
import sys
import html
import json
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
import feedparser

from config import TOPICS, DIRECT_FEEDS, SETTINGS

# ----------------------------------------------------------------------
# Optional: load a local .env file when running on your own machine.
# (On GitHub Actions the secrets are already injected as env vars.)
# ----------------------------------------------------------------------
def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

GNEWS = ("https://news.google.com/rss/search?q={q}"
         "&hl={hl}&gl={gl}&ceid={ceid}")


# ----------------------------------------------------------------------
# 1. FETCH
# ----------------------------------------------------------------------
def build_feed_list():
    feeds = []
    for label, query in TOPICS:
        url = GNEWS.format(
            q=urllib.parse.quote(query),
            hl=SETTINGS["gnews_hl"], gl=SETTINGS["gnews_gl"],
            ceid=SETTINGS["gnews_ceid"],
        )
        feeds.append((label, url, True))     # True = is a topic search
    for name, url in DIRECT_FEEDS:
        feeds.append((name, url, False))
    return feeds


def _entry_datetime(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")          # strip html tags
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _source_of(entry, fallback):
    src = entry.get("source")
    if isinstance(src, dict) and src.get("title"):
        return src["title"]
    # Google News often appends " - Publisher" to the title
    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return fallback


def fetch_recent():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SETTINGS["lookback_hours"])
    seen_titles = set()
    grouped = {}            # label -> list of items

    for label, url, is_topic in build_feed_list():
        try:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
        except Exception as e:
            print(f"  ! {label}: fetch error: {e}", file=sys.stderr)
            continue

        kept = []
        for e in parsed.entries:
            dt = _entry_datetime(e)
            if dt and dt < cutoff:
                continue
            title = _clean(e.get("title", ""))
            if not title:
                continue
            # dedupe on a normalised title key
            key = re.sub(r"[^a-z0-9]+", "", title.lower())[:80]
            if key in seen_titles:
                continue
            seen_titles.add(key)

            kept.append({
                "title": title,
                "source": _source_of(e, label),
                "when": dt.strftime("%d %b %H:%M UTC") if dt else "recent",
                "summary": _clean(e.get("summary", ""))[:400],
                "link": e.get("link", ""),
            })
            if len(kept) >= SETTINGS["max_per_topic"]:
                break

        if kept:
            grouped.setdefault(label, []).extend(kept)
        print(f"  • {label}: {len(kept)} item(s)")

    # enforce global cap
    total = sum(len(v) for v in grouped.values())
    if total > SETTINGS["max_total"]:
        budget = SETTINGS["max_total"]
        trimmed = {}
        for label, items in grouped.items():
            take = max(1, round(len(items) / total * budget))
            trimmed[label] = items[:take]
        grouped = trimmed

    return grouped


# ----------------------------------------------------------------------
# 2. ANALYSE (Claude)
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """You are a sharp, sceptical research analyst writing a daily \
briefing for a Singapore real-estate professional. Your job is to turn raw \
headlines into a tight, useful read on what matters for the Singapore property \
market — covering both direct drivers (HDB, private/condo, cooling measures, \
SORA/mortgage rates, en-bloc, supply) and indirect ones (US Fed rates, China \
property, global inflation, SGD, regional capital flows).

Rules:
- Prioritise and clearly attribute reputable outlets (Reuters, Bloomberg, CNA, \
The Business Times, The Straits Times, EdgeProp, official central banks). If an \
item looks thin or from an unreliable source, say so or drop it.
- Be concrete. No filler, no hedging fluff. If a day is quiet, say it's quiet.
- Distinguish fact (what was reported) from your inference (what it may mean).
- You are reading headlines and snippets, not full articles — don't overstate \
certainty."""

USER_TEMPLATE = """Here are today's articles, grouped by topic. Date: {date}.

{articles}

Write the briefing in this structure, using ONLY <b>bold</b> tags for headers \
(no markdown, no other HTML — it will be sent to Telegram):

<b>🗞 SG Real-Estate Daily — {date}</b>

<b>Top 3 things that matter</b>
- 3 bullets, the most consequential items for SG property, each with a one-line \
"why it matters".

<b>Direct impact (SG property)</b>
- short bullets on HDB / private / policy / rates news, with source in (brackets).

<b>Indirect / global watch</b>
- short bullets on Fed, China, macro — and the transmission path to SG property.

<b>Bottom line</b>
- 2-3 sentences: net read for the SG market today, and one thing to watch next.

Keep the whole thing under ~450 words. If there's genuinely little news, keep it \
brief rather than padding."""


def format_articles(grouped):
    lines = []
    for label, items in grouped.items():
        lines.append(f"\n## {label}")
        for it in items:
            lines.append(f"- {it['title']} ({it['source']}, {it['when']})")
            if it["summary"]:
                lines.append(f"    {it['summary']}")
    return "\n".join(lines)


def analyse(grouped):
    if not grouped:
        return ("<b>🗞 SG Real-Estate Daily</b>\n\nNo articles passed the "
                "freshness filter in the last "
                f"{SETTINGS['lookback_hours']}h. Either a quiet news day or a "
                "feed issue — run <code>--check</code> to verify the feeds.")

    today = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))).strftime("%a %d %b %Y")   # SGT
    user_msg = USER_TEMPLATE.format(date=today, articles=format_articles(grouped))

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": SETTINGS["model"],
            "max_tokens": 2500,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", [])).strip()


# ----------------------------------------------------------------------
# 3. DELIVER (Telegram)
# ----------------------------------------------------------------------
def _chunks(text, limit=3900):
    """Split on blank lines so messages stay under Telegram's 4096 limit."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit:
            if cur:
                parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for part in _chunks(text):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=30)
        if not r.ok:
            # retry once as plain text in case of an HTML parse error
            payload.pop("parse_mode")
            payload["text"] = re.sub(r"<[^>]+>", "", part)
            r = requests.post(url, json=payload, timeout=30)
            r.raise_for_status()
        time.sleep(0.5)


# ----------------------------------------------------------------------
# MODES
# ----------------------------------------------------------------------
def check_feeds():
    print("Checking feeds...\n")
    for label, url, _ in build_feed_list():
        try:
            p = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
            n = len(p.entries)
            flag = "ok " if n else "EMPTY"
            print(f"[{flag}] {label:<24} {n:>3} items   {url[:60]}")
        except Exception as e:
            print(f"[ERR] {label:<24} {e}")


def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print("Missing env vars: " + ", ".join(missing), file=sys.stderr)
        sys.exit(1)


def main():
    args = sys.argv[1:]

    if "--check" in args:
        check_feeds()
        return

    print("Fetching feeds...")
    grouped = fetch_recent()

    print("\nAnalysing with Claude...")
    require_env("ANTHROPIC_API_KEY")
    digest = analyse(grouped)

    print("\n" + "=" * 60 + "\n" + digest + "\n" + "=" * 60 + "\n")

    if "--dry-run" in args:
        print("(dry run — not sending)")
        return

    require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    print("Sending to Telegram...")
    send_telegram(digest)
    print("Done.")


if __name__ == "__main__":
    main()
