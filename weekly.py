#!/usr/bin/env python3
"""
Weekly Singapore real-estate market report.

Pulls the past 7 days of news, asks Claude for a structured report, then
delivers BOTH:
  1. a short, plain-English summary as a Telegram message, and
  2. a polished branded PDF, attached to Telegram.

Tone: accessible enough to forward to clients, with enough substance for
your team. Reuses fetch/Claude/Telegram helpers from digest.py.

Run modes:
  python weekly.py            # full run + send (message + PDF)
  python weekly.py --dry-run  # build everything, save PDF locally, DON'T send
"""

import os
import re
import sys
import json
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import requests

import digest   # reuse fetch_recent, format_articles, call_claude, send_telegram

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem,
)


# =============================================================
#  BRAND  —  edit your details here (used in the PDF)
# =============================================================
BRAND = {
    "name": "KCG Homes",
    "report_title": "Singapore Property — Weekly Market Report",
    "logo": "kcg-logo.png",            # white logo file in the repo (leave "" for text)
    "website": "kcghomes.sg",
    "website_url": "https://kcghomes.sg",
    "instagram": "@kcghomes.sg",
    "instagram_url": "https://instagram.com/kcghomes.sg",
    "disclaimer": ("Shared as general information about the Singapore property "
                   "market, drawn from public news. It is not financial, legal, "
                   "or property advice. Please verify key figures before acting."),
    # Colours (hex). Tweak to match your branding.
    "navy":  "#16263F",
    "gold":  "#B08D43",
    "ink":   "#222222",
    "muted": "#6B7280",
    "line":  "#E3E6EB",
}


# =============================================================
#  1. ASK CLAUDE FOR A STRUCTURED REPORT (returns JSON)
# =============================================================
REPORT_SYSTEM = """You are writing a WEEKLY market report on Singapore property. \
The audience is a mix: clients (ordinary people thinking about buying, selling, \
or renting) and the agent's own team. So: write clearly and explain any jargon \
in plain words, but include real substance and a sensible read of what the week \
meant.

You are given a week's worth of news headlines. Find the throughline — what \
actually mattered for the Singapore property market this week — across both \
direct drivers (HDB, private/condo, cooling measures, mortgage/SORA rates, \
en-bloc, supply) and indirect ones (US Fed, China property, global inflation, \
the Singapore dollar, regional money flows).

Rules:
- Be accurate. Only use what's in the headlines. Don't invent numbers or names.
- Prefer trustworthy sources; ignore thin or low-quality items.
- Separate what was reported from your own read (use "this suggests / likely").
- Keep it calm and balanced, never alarmist.
- You are reading headlines and snippets, not full articles — stay measured."""

REPORT_USER = """Here are the past week's news headlines, grouped by topic. \
Week ending: {date}.

{articles}

Return ONLY a JSON object (no markdown, no backticks, no text before or after) \
with exactly this shape:

{{
  "headline": "one punchy sentence capturing the week's main story",
  "overview": "2-3 plain-English sentences setting the scene",
  "sections": [
    {{"title": "What moved in the Singapore market", "points": ["...", "..."]}},
    {{"title": "The global backdrop", "points": ["...", "..."]}},
    {{"title": "Trends worth watching", "points": ["...", "..."]}}
  ],
  "what_it_means": ["plain bullet on impact for buyers/sellers/owners", "..."],
  "week_ahead": ["thing to watch next week", "..."]
}}

Guidance: 2-4 points per section, each a clear full sentence. Explain any \
technical term simply. If the week was quiet, say so honestly and keep it short. \
Do not include source codes or brackets — just clear sentences."""


def get_report(grouped, week_ending):
    user_msg = REPORT_USER.format(
        date=week_ending, articles=digest.format_articles(grouped))
    raw = digest.call_claude(REPORT_SYSTEM, user_msg, max_tokens=3500)
    # strip accidental code fences, then parse
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(),
                     flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # last resort: grab the outermost {...}
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# =============================================================
#  2. TELEGRAM SUMMARY (short message; full detail is in the PDF)
# =============================================================
def build_telegram_summary(report, week_ending):
    lines = [
        f"<b>📊 {escape(BRAND['name'])} — Weekly Market Report</b>",
        f"<i>week ending {escape(week_ending)}</i>",
        "",
        f"<b>{escape(report.get('headline',''))}</b>",
        "",
        escape(report.get("overview", "")),
    ]
    wim = report.get("what_it_means") or []
    if wim:
        lines += ["", "<b>What this means for you</b>"]
        lines += [f"• {escape(p)}" for p in wim[:3]]
    lines += ["", "📄 Full report attached below."]
    return "\n".join(lines)


# =============================================================
#  3. PDF
# =============================================================
def _styles():
    navy = colors.HexColor(BRAND["navy"])
    gold = colors.HexColor(BRAND["gold"])
    ink = colors.HexColor(BRAND["ink"])
    muted = colors.HexColor(BRAND["muted"])
    ss = getSampleStyleSheet()
    return {
        "headline": ParagraphStyle("headline", parent=ss["Normal"],
            fontName="Helvetica-Bold", fontSize=15, leading=20, textColor=navy,
            spaceBefore=4, spaceAfter=10),
        "overview": ParagraphStyle("overview", parent=ss["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=16, textColor=ink,
            spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=ss["Normal"], fontName="Helvetica-Bold",
            fontSize=11.5, leading=15, textColor=gold, spaceBefore=14,
            spaceAfter=6),
        "bullet": ParagraphStyle("bullet", parent=ss["Normal"],
            fontName="Helvetica", fontSize=10, leading=15, textColor=ink),
        "meanslead": ParagraphStyle("meanslead", parent=ss["Normal"],
            fontName="Helvetica-Bold", fontSize=11, leading=15,
            textColor=navy, spaceAfter=6),
        "disc": ParagraphStyle("disc", parent=ss["Normal"], fontName="Helvetica",
            fontSize=7.5, leading=10.5, textColor=muted),
    }


def _bullets(points, style):
    items = [ListItem(Paragraph(escape(p), style), leftIndent=6, value="•")
             for p in points if p]
    return ListFlowable(items, bulletType="bullet", bulletColor=colors.HexColor(BRAND["gold"]),
                        bulletFontSize=8, leftIndent=12, spaceBefore=2, spaceAfter=2)


def _header_footer(canvas, doc):
    canvas.saveState()
    w, h = A4
    navy = colors.HexColor(BRAND["navy"])
    gold = colors.HexColor(BRAND["gold"])
    muted = colors.HexColor(BRAND["muted"])

    # top band
    canvas.setFillColor(navy)
    canvas.rect(0, h - 26 * mm, w, 26 * mm, fill=1, stroke=0)
    canvas.setFillColor(gold)
    canvas.rect(0, h - 27.2 * mm, w, 1.2 * mm, fill=1, stroke=0)

    logo = BRAND.get("logo")
    drew_logo = False
    if logo and os.path.exists(logo):
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(logo)
            iw, ih = img.getSize()
            target_h = 17 * mm
            target_w = target_h * iw / ih
            y = h - 26 * mm + (26 * mm - target_h) / 2.0
            canvas.drawImage(img, 18 * mm, y, width=target_w, height=target_h,
                             mask="auto", preserveAspectRatio=True)
            drew_logo = True
        except Exception:
            drew_logo = False

    if not drew_logo:
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(18 * mm, h - 14 * mm, BRAND["name"])

    # right-aligned title + links
    canvas.setFillColor(colors.HexColor("#C9D2E0"))
    canvas.setFont("Helvetica", 10)
    canvas.drawRightString(w - 18 * mm, h - 13 * mm, BRAND["report_title"])
    canvas.setFont("Helvetica", 8.5)
    canvas.drawRightString(w - 18 * mm, h - 18.5 * mm,
                           f"{BRAND['website']}  ·  {BRAND['instagram']}")

    # footer
    canvas.setStrokeColor(colors.HexColor(BRAND["line"]))
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 16 * mm, w - 18 * mm, 16 * mm)
    canvas.setFillColor(muted)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(18 * mm, 11 * mm,
                      f"{BRAND['website']}   {BRAND['instagram']}")
    canvas.drawRightString(w - 18 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(report, week_ending, path):
    st = _styles()
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        topMargin=34 * mm, bottomMargin=22 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
        title=f"{BRAND['name']} Weekly Market Report",
        author=BRAND["name"],
    )
    story = []

    # date line
    story.append(Paragraph(
        f"<font color='{BRAND['muted']}'>Week ending {escape(week_ending)}</font>",
        st["bullet"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(escape(report.get("headline", "")), st["headline"]))
    if report.get("overview"):
        story.append(Paragraph(escape(report["overview"]), st["overview"]))

    for sec in report.get("sections", []):
        pts = sec.get("points") or []
        if not pts:
            continue
        story.append(Paragraph(escape(sec.get("title", "")), st["h2"]))
        story.append(_bullets(pts, st["bullet"]))

    wim = report.get("what_it_means") or []
    if wim:
        story.append(Spacer(1, 6))
        box = Table(
            [[Paragraph("What this means for you", st["meanslead"])],
             [_bullets(wim, st["bullet"])]],
            colWidths=[doc.width])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F2EA")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(BRAND["gold"])),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(box)

    ahead = report.get("week_ahead") or []
    if ahead:
        story.append(Paragraph("The week ahead", st["h2"]))
        story.append(_bullets(ahead, st["bullet"]))

    story.append(Spacer(1, 16))
    story.append(Paragraph(escape(BRAND["disclaimer"]), st["disc"]))
    story.append(Paragraph(
        f"<font color='{BRAND['muted']}'>Generated {datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%d %b %Y, %H:%M')} SGT</font>",
        st["disc"]))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return path


# =============================================================
#  4. SEND PDF TO TELEGRAM
# =============================================================
def send_document(path, caption):
    url = f"https://api.telegram.org/bot{digest.TELEGRAM_BOT_TOKEN}/sendDocument"
    data = {"chat_id": digest.TELEGRAM_CHAT_ID, "caption": caption,
            "parse_mode": "HTML"}
    if digest.TELEGRAM_TOPIC_ID:
        data["message_thread_id"] = digest.TELEGRAM_TOPIC_ID
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data=data,
            files={"document": (os.path.basename(path), f, "application/pdf")},
            timeout=120,
        )
    r.raise_for_status()


# =============================================================
#  MAIN
# =============================================================
def main():
    dry = "--dry-run" in sys.argv[1:]

    print("Fetching the past 7 days...")
    grouped = digest.fetch_recent(
        lookback_hours=24 * 8, max_per_topic=12, max_total=90,
        query_suffix=" when:7d",
    )

    week_ending = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))).strftime("%d %b %Y")   # SGT

    if not grouped:
        msg = ("<b>📊 Weekly Market Report</b>\n\nNo news came back for the past "
               "week. Run <code>python digest.py --check</code> to verify feeds.")
        print(msg)
        if not dry:
            digest.require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
            digest.send_telegram(msg)
        return

    print("Asking Claude for the report...")
    digest.require_env("ANTHROPIC_API_KEY")
    report = get_report(grouped, week_ending)

    summary = build_telegram_summary(report, week_ending)
    print("\n--- Telegram summary ---\n" + summary + "\n")

    pdf_path = f"weekly-report-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(report, week_ending, pdf_path)
    print(f"PDF written: {pdf_path}")

    if dry:
        print("(dry run — not sending; PDF saved locally)")
        return

    digest.require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    print("Sending summary to Telegram...")
    digest.send_telegram(summary)
    print("Sending PDF to Telegram...")
    send_document(
        pdf_path,
        f"📄 {BRAND['name']} — Weekly Market Report (week ending {week_ending})")
    print("Done.")


if __name__ == "__main__":
    main()
