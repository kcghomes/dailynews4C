# =============================================================
#  CONFIG  —  edit this file to change what gets tracked
# =============================================================
#
#  Two kinds of sources:
#
#  1) TOPICS  -> turned into Google News RSS searches.
#     This is the reliable backbone. Google News pulls from many
#     credible outlets at once, so you don't have to hunt down a
#     working RSS URL for every publisher. Just describe the topic.
#
#  2) DIRECT_FEEDS -> a specific publisher's own RSS feed.
#     Use for primary sources (e.g. central banks). Publisher feed
#     URLs change often, so run  `python digest.py --check`  to see
#     which ones are actually returning items, and prune the dead.
#
# -------------------------------------------------------------

# (label shown in digest, search query)
TOPICS = [
    ("SG Property Market",     "Singapore property market"),
    ("SG Private / Condo",     "Singapore private property condo new launch"),
    ("SG HDB Resale",          "Singapore HDB resale flat prices"),
    ("SG Policy / Cooling",    "Singapore property cooling measures ABSD stamp duty"),
    ("SG Rates / Mortgage",    "Singapore mortgage SORA interest rate"),
    ("MAS / SG Economy",       "MAS Singapore monetary policy economy GDP"),
    ("US Fed / Rates",         "US Federal Reserve interest rate decision"),
    ("China Property",         "China property developers real estate"),
    ("Global Macro",           "global inflation interest rates economy outlook"),
]

# (publisher name, RSS url)  — verify with --check before trusting
DIRECT_FEEDS = [
    ("US Federal Reserve (press releases)",
     "https://www.federalreserve.gov/feeds/press_all.xml"),
    # Add your own here, e.g. a property blog's WordPress feed:
    # ("Stacked Homes", "https://stackedhomes.com/feed/"),
]

SETTINGS = {
    # Only consider articles published within this many hours of run time.
    # 30 gives a little overlap so nothing slips through the cracks.
    "lookback_hours": 30,

    # Max articles to keep PER topic (newest first) before sending to Claude.
    "max_per_topic": 6,

    # Hard cap on total articles sent to Claude (cost / focus control).
    "max_total": 45,

    # Which Claude model to use.
    #   claude-haiku-4-5-20251001  -> cheapest, fast, lighter analysis
    #   claude-sonnet-4-6          -> balanced (recommended)
    #   claude-opus-4-8            -> deepest analysis, pricier
    "model": "claude-sonnet-4-6",

    # Region tuning for Google News (Singapore English).
    "gnews_hl": "en-SG",
    "gnews_gl": "SG",
    "gnews_ceid": "SG:en",
}


# -------------------------------------------------------------
#  SIGN-OFF
# -------------------------------------------------------------
#  Appended to the bottom of every message, so each digest is
#  ready to forward to clients. Edit the details below.
#  Leave SIGNOFF = "" to turn it off.
#
#  You can use simple formatting:
#    <b>bold</b>   <i>italic</i>   and line breaks with \n
# -------------------------------------------------------------
SIGNOFF = (
    "—\n"
    "<b>Your Name</b>\n"
    "Your Agency · CEA Reg. No. R0000000X\n"
    "📱 +65 0000 0000   ✉️ you@email.com\n"
    "<i>Shared as general information, not financial or property advice.</i>"
)

