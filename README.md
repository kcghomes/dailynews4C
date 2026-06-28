# SG Real-Estate Daily Digest

A small pipeline that, once a day:

1. pulls recent news from credible sources (Google News searches + direct RSS),
2. asks Claude to summarise it and analyse the impact on the **Singapore real-estate market** (direct + indirect), and
3. sends the briefing to your **Telegram**.

It runs free on **GitHub Actions** — no server to manage. The only cost is the Claude API call (a few cents a day).

---

## What you'll need
- A GitHub account (free).
- A Telegram account.
- An Anthropic API key — from https://console.anthropic.com (see https://docs.claude.com for reference).

---

## Setup (about 15 minutes)

### 1. Make a Telegram bot
1. In Telegram, message **@BotFather**.
2. Send `/newbot`, follow the prompts. It gives you a **bot token** like `123456:ABC...`. Save it.
3. Open a chat with your new bot and send it any message (e.g. "hi"). This is required so it can message you back.
4. Get your **chat id**: open this URL in a browser (paste your token in):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Look for `"chat":{"id":123456789` — that number is your **chat id**.

### 2. Put the code on GitHub
1. Create a new repository (private is fine).
2. Upload all the files in this folder (keep the `.github/workflows/` folder structure intact).

### 3. Add your secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add three:
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 4. Test it
Go to the **Actions** tab → "Daily SG Real-Estate Digest" → **Run workflow**. Within a minute you should get a message in Telegram. The run log also prints the full digest, so you can debug there if needed.

That's it. After this it runs automatically every day.

---

## Customising

Everything you'd normally change lives in **`config.py`**:
- **Topics** — add/remove what gets tracked (each becomes a Google News search).
- **Direct feeds** — add a publisher's own RSS feed for primary sources.
- **Model** — `claude-haiku-4-5-20251001` (cheapest) / `claude-sonnet-4-6` (default) / `claude-opus-4-8` (deepest).
- **Lookback window**, **article caps**.

Change the **delivery time** in `.github/workflows/daily-digest.yml`. Cron is in **UTC**; Singapore is UTC+8. The default `30 23 * * *` = **07:30 SGT**.

---

## Running locally (optional)
```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your real keys
python digest.py --check    # test which feeds return items
python digest.py --dry-run  # full run but DON'T send to Telegram
python digest.py            # full run + send
```

---

## Good to know
- **Sources:** the backbone is Google News searches, which pull from many outlets at once, so you don't have to maintain fragile per-publisher URLs. The analysis prompt tells Claude to prioritise and attribute reputable outlets and to be sceptical of thin sources.
- **Headlines, not full articles:** to stay robust and avoid paywalls/scraping issues, it reads headlines + snippets, not full article text. That's plenty for a daily thematic read. (Full-text fetching is a possible upgrade.)
- **GitHub Actions quirks:** scheduled runs can be delayed a few minutes at peak times, and Actions auto-disables scheduled workflows after ~60 days of no repo activity — just re-enable if that happens.
- **Feeds drift:** if a direct feed goes quiet, run `python digest.py --check` to see counts and prune dead ones.
