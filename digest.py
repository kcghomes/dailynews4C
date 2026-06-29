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

try:
    from config import SIGNOFF
except ImportError:
    SIGNOFF = ""

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
def build_feed_list(query_suffix=""):
    # query_suffix lets the weekly job append Google News' "when:7d"
    # operator to pull a whole week per topic.
    feeds = []
    for label, query in TOPICS:
        url = GNEWS.format(
            q=urllib.parse.quote(query + query_suffix),
            hl=SETTINGS["gnews_hl"], gl=SETTINGS["gnews_gl"],
            ceid=SETTINGS["gnews_ceid"],
        )
        feeds.append((label, url, True))     # True = is a topic search
    # Direct feeds can't be date-filtered via query; include them only for
    # the daily run (the recency window handles freshness there).
    if not query_suffix:
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


def fetch_recent(lookback_hours=None, max_per_topic=None,
                 max_total=None, query_suffix=""):
    lookback_hours = lookback_hours or SETTINGS["lookback_hours"]
    max_per_topic  = max_per_topic  or SETTINGS["max_per_topic"]
    max_total      = max_total      or SETTINGS["max_total"]

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    seen_titles = set()
    grouped = {}            # label -> list of items

    for label, url, is_topic in build_feed_list(query_suffix):
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
            if len(kept) >= max_per_topic:
                break

        if kept:
            grouped.setdefault(label, []).extend(kept)
        print(f"  • {label}: {len(kept)} item(s)")

    # enforce global cap
    total = sum(len(v) for v in grouped.values())
    if total > max_total:
        trimmed = {}
        for label, items in grouped.items():
            take = max(1, round(len(items) / total * max_total))
            trimmed[label] = items[:take]
        grouped = trimmed

    return grouped


# ----------------------------------------------------------------------
# 2. ANALYSE (Claude)
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """You write a short, friendly daily update about the Singapore \
property market for ordinary readers — people thinking about buying, selling, or \
renting a home, who are NOT finance experts. A property agent will forward this \
update directly to clients, so it must be clear, warm, and easy to share.

How to write:
- Plain, everyday English. Short sentences. No jargon.
- If you must mention a technical term (e.g. SORA, ABSD, the Fed), explain it in \
a few simple words the first time, like you're talking to a friend.
- Always connect news back to what it means for a normal person: home prices, \
loan/mortgage repayments, rents, or whether it's a good time to act.
- Be warm and helpful, never alarmist. Calm, balanced tone.
- Be accurate. Only use what's in the headlines. If something is uncertain, say \
"early signs suggest" rather than stating it as fact. Don't invent numbers.
- Prefer trustworthy news sources. If the day is quiet, just say so briefly."""

USER_TEMPLATE = """Here are today's news headlines, grouped by topic. Date: {date}.

{articles}

Write a short, friendly daily update for ordinary readers. Use ONLY <b>bold</b> \
tags for headers (no markdown, no other HTML — it goes to Telegram). Use simple \
language someone can forward to clients as-is.

<b>🏠 Singapore Property — Daily Update</b>
<i>{date}</i>

<b>In short</b>
- 1-2 sentences in plain English: the main thing to know today.

<b>What's happening</b>
- 3-4 short, simple bullets on the most relevant news. Explain any technical \
term in plain words. No brackets or source codes — just clear sentences.

<b>What this means for you</b>
- 2-3 bullets translating the news into everyday impact: home prices, monthly \
loan repayments, rents, or timing for buyers/sellers.

<b>The bottom line</b>
- 1-2 friendly sentences wrapping up, plus one thing worth keeping an eye on.

Keep it under ~350 words and genuinely easy to understand. If there's little \
news, keep it short and reassuring rather than padding it out."""


def format_articles(grouped):
    lines = []
    for label, items in grouped.items():
        lines.append(f"\n## {label}")
        for it in items:
            lines.append(f"- {it['title']} ({it['source']}, {it['when']})")
            if it["summary"]:
                lines.append(f"    {it['summary']}")
    return "\n".join(lines)


def call_claude(system_prompt, user_msg, max_tokens=2500):
    """Single Claude API call. Reused by both the daily and weekly jobs."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": SETTINGS["model"],
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", [])).strip()


def analyse(grouped):
    if not grouped:
        return ("<b>🗞 SG Real-Estate Daily</b>\n\nNo articles passed the "
                "freshness filter in the last "
                f"{SETTINGS['lookback_hours']}h. Either a quiet news day or a "
                "feed issue — run <code>--check</code> to verify the feeds.")

    today = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))).strftime("%a %d %b %Y")   # SGT
    user_msg = USER_TEMPLATE.format(date=today, articles=format_articles(grouped))
    return call_claude(SYSTEM_PROMPT, user_msg)


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
    if SIGNOFF.strip():
        text = f"{text}\n\n{SIGNOFF}"
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
