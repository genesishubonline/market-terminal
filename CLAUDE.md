# market-terminal — Bell & Block

Personal market dashboard, named Bell & Block (opening bell + blockchain block).
Tagline: Markets · Crypto · News. A static site served from GitHub Pages out of this
public repo. The URL is unlisted, but the repo is public: treat every committed
byte as world-readable.

## Architecture

Two independent pipelines feed one static site.

### 1. Data refresh (GitHub Actions, every 2 hours)

- Repo: https://github.com/genesishubonline/market-terminal (public).
  Site: https://genesishubonline.github.io/market-terminal/ (Pages, source =
  GitHub Actions). Commit author is the repo-local noreply identity
  `genesishubonline <genesishubonline@users.noreply.github.com>`; never commit
  with a real name or address.
- Workflow `.github/workflows/pages.yml` ("Build and deploy") runs `fetch.py`
  on a schedule (every 2 hours at :17, `17 */2 * * *` UTC; on-the-hour crons
  were delayed by up to 7h on GitHub's shared scheduler), on push to `main`, and by
  `workflow_dispatch`. First run from a GitHub runner: 14 prices, 150
  headlines, 0 errors, 7s.
- `fetch.py` pulls prices and RSS headlines from public endpoints. **No AI, no
  API keys.** Only sources that work anonymously.
- Output is written into the build directory and deployed straight to GitHub
  Pages with the Pages deploy action. **Generated data is never committed.**
  The workflow must not `git commit` or `git push`.
- The deploy also ships the current `brief.json` from the repo (see below), so
  the site always shows the latest committed brief alongside fresh data.

### 2. Daily brief (local Mac only)

- `brief.py` runs on my Mac, not in CI.
- It reads `profile.md` (my personal context: holdings, interests, watchlist,
  preferences) and market data, then calls `claude -p` to write a daily brief
  and reading recommendations.
- Output is `brief.json`, which `brief.py` commits and pushes to `main`.
- `claude -p` uses the local Claude Code login. No API key is involved.
- Automated on the Mac by launchd: `~/Library/LaunchAgents/com.bellblock.brief.plist`
  (outside the repo, holds the user's paths) runs `scripts/run_brief.sh` daily
  at 07:00 Mac local time; if the Mac was asleep it runs at next wake. The
  wrapper pulls, runs `brief.py` (which commits + pushes with the user's own
  git credentials), and logs to `~/Library/Logs/bell-block-brief.log`.
  Manage with `launchctl kickstart gui/501/com.bellblock.brief` (run now),
  `launchctl bootout gui/501/com.bellblock.brief` (disable). Only runs while
  the Mac is on; that is the accepted trade-off for keeping AI out of CI.

### Site

- Plain static HTML/CSS/JS. The page loads the generated data files and
  `brief.json` at runtime. No build framework, no server.

## Hard rules

1. **No API keys anywhere.** Not in code, not in workflow files, not in GitHub
   secrets, not in `.env`. If a data source needs a key, don't use it.
2. **`profile.md` never leaves this Mac.** It is gitignored and must stay that
   way. Never `git add -f` it, never copy its contents into another tracked
   file, never echo it into `brief.json` or logs. `brief.py` may read it but
   must not write it or any verbatim excerpt anywhere tracked.
3. **The repo contains nothing personal.** No names, holdings, account
   details, positions, portfolio sizes, or anything that identifies me. The
   greeting name is stored in the browser's localStorage only (seeded once via
   `?name=...`, or by clicking the greeting), never in the repo. The same goes
   for the Tasks card: tasks live in localStorage (`bb.tasks`) on each device
   and are never written to any file. Do not add a sync that goes through the
   repo or the Pages deploy.
   `brief.json` is committed and public, so `brief.py`'s prompt must instruct
   Claude to write the brief in general market terms and not restate personal
   context from `profile.md`.
4. **Generated data is not committed.** Prices and headlines exist only in the
   Pages deployment. Only `brief.json` is committed as generated output.
5. **CI has no AI.** `fetch.py` and the workflow never call Claude or any LLM.
   AI generation happens only in `brief.py` on the Mac.

## Files

| Path | Tracked | Purpose |
|---|---|---|
| `watchlist.yaml` | yes | Tickers, CoinGecko ids, macro series, ticker-tape lists, RSS feeds + tabs. Edit this to change what is tracked. |
| `fetch.py` | yes | Read `watchlist.yaml`, fetch prices (CoinGecko, yfinance) + RSS, write `data.json`. Runs in CI. |
| `requirements.txt` | yes | Python deps for `fetch.py`. |
| `make_icons.py`, `icons/` | yes | Pixel-robot home-screen icons (PNG 180/192/512 + SVG), generated without deps. Rerun after changing the mascot. |
| `manifest.webmanifest` | yes | Web app manifest so the site installs to a phone home screen with the mascot icon. |
| `brief.py` | yes | Build prompt from `profile.md` + `data.json`, run `claude -p`, validate, leak-check, write and push `brief.json`. Mac only. |
| `scripts/run_brief.sh` | yes | launchd wrapper: pull, run `brief.py`, log. Generic paths; the plist that schedules it lives outside the repo. |
| `.brief_state.json` | **no** | Past knowledge-card titles so topics don't repeat. Gitignored. |
| `brief.json` | yes | Daily brief + reading recommendations. Public. |
| `profile.md` | **no** | Personal context. Gitignored. Mac only. |
| `index.html` | yes | Single-file dashboard (inline CSS/JS, no deps). Loads `data.json` and, if present, `brief.json`. |
| `.github/workflows/*.yml` | yes | 2-hourly fetch + Pages deploy. No commits, no secrets. |
| `data.json` | **no** | Produced by `fetch.py` at build time. Gitignored. |

## brief.py

- Requires `profile.md` (fails loudly if missing or near-empty) and a fresh
  `data.json` (runs `fetch.py` if older than 3h).
- Calls `claude -p --output-format json --tools ""` with the prompt on stdin,
  unsetting `CLAUDECODE` so it also works from inside a Claude Code session.
  Model uses only the data in the prompt, no tools.
- Validates the JSON: `reading` and `knowledge.sources` URLs must be copied
  from the fetched headlines or they are dropped. Then a **leak check**: if any
  substantive line of `profile.md` appears in the output, it refuses to write.
- The knowledge card refreshes once per calendar day; rerunning the same day
  keeps the existing card unless `--force`. Past titles are kept in
  `.brief_state.json` and fed back as "do not repeat".
- Flags: `--no-commit`, `--no-push` (push is skipped anyway when no remote),
  `--dry-run` prints the prompt, `--model`, `--timeout`.
- Cost is roughly $0.5 per run on the default model.

## brief.json shape

`index.html` renders these fields, all optional; the brief section stays hidden
if the file is missing or empty. `brief.py` produces this shape and enforces the
counts: exactly 3 points, exactly 3 reading links.

```json
{
  "date": "2026-09-20",
  "generated_at": "2026-09-20T22:00:00+00:00",
  "headline": "the one story of the day",
  "headline_url": "article url", "headline_source": "feed name",
  "lead": "one sentence on what it means",
  "chart": {"series": ["USDJPY"], "title": "...", "note": "...", "mark": {"t": "ISO time", "label": "..."}},
  "points": [{"title": "<= 7 words", "detail": "one sentence with a number", "url": "article url", "source": "feed name"}],
  "knowledge": {
    "title_en": "...", "body_en": "2 short paragraphs", "why_en": "...",
    "chart": {"series": ["USDJPY", "US10Y"], "title": "...", "note": "..."},
    "facts": [{"label": "BOJ policy rate", "value": "1.25%"}],
    "sources": [{"title": "...", "url": "..."}]
  },
  "reading": [{"title": "...", "url": "...", "source": "feed", "why": "..."}]
}
```

Every `url` (headline, points, reading, knowledge sources) must be copied
from the fetched headlines; `brief.py` drops any that isn't and fills `source`
from the item's publisher (aggregators) or feed name. The headline and the
three points must come from four different outlets, and the reading list from
three more; the prompt enforces it and `brief.py` warns on a repeat. The page renders the headline as a link and a small
"SOURCE ↗" tag after the headline and each point.

Legacy fields (`points` as strings, `summary`, `knowledge.title/body`) still render.

### Language rule (enforced in the prompt)

An item about the Japanese market (BOJ, yen, Nikkei/TOPIX, Japanese companies,
FSA, MOF) is written entirely in Japanese: headline, lead, point title and
detail, chart title and note. Everything else is English, including the
knowledge card (English only; the `_en` field names are historical). Dates are
en-US.

### Charts instead of pictures

Illustrations are charts drawn by the page from the 7-day history already in
`data.json`. Claude picks a `chart` spec: 1 or 2 symbols from the `chartable`
list (crypto, indices, stocks, macro), a title, a one-line note, and optionally
a `mark` (event time + label, drawn as a dashed marker). Two series are rebased
to % change on one axis (never dual axes). `brief.py` drops any symbol not in
the data. `facts` on the Learn card are 2-4 label/value chips. Charts render at
their real pixel width (ResizeObserver) so text stays legible.

The `macro:` list in `watchlist.yaml` (US 10Y, VIX, DXY, gold, WTI) exists
mainly to make these charts meaningful; it is fetched like indices into
`prices.macro` but not shown as tiles.

## Price data sources

- Spot crypto price, 24h change, market cap: one CoinGecko `simple/price` call
  covering the main coins and the crypto tape. CoinGecko's keyless tier
  rate-limits `market_chart` hard, so **7-day history for crypto comes from
  Yahoo** (`BTC-USD` etc.) in the same batched yfinance download as the indices.
- Indices, stocks, stock tape: yfinance batch downloads. Daily 10d for
  close/prev close, hourly 7d for the sparkline. Yahoo mixes tz-naive daily and
  tz-aware intraday indexes; `closes_of()` normalizes everything to UTC.
- TOPIX uses the 1306.T ETF as a proxy (no Yahoo index ticker). KOSPI is `^KS11`.
- History is downsampled to `settings.history_points` per series to keep
  `data.json` reasonable (~150 KB with 39 feeds × 12 items).

## Page layout (`index.html`)

- Full-width. Row 1: Crypto panel (left) and Indices & FX panel (right), side by
  side at >= 1200px, stacked below. Each tile has a 7-day sparkline colored by
  7d direction, with hover/touch readout. Each panel ends in a CSS-animated
  ticker tape fed by `tape.crypto` / `tape.stocks` (pauses on hover, static and
  scrollable under `prefers-reduced-motion`).
- Row 2: all five headline tabs rendered at once as columns. Each list is a
  fixed 372px scroll box showing the top 5, so "View more" sits at the same
  height in every column and the box closes right under it. The top 5 allow at most 2 items per
  source; undated feeds (Nikkei Asia) are treated as (i+1)*3h old.
- Design direction: Bloomberg-terminal inspired, not a copy. True black
  background, amber (#ff9e1b) for section labels, symbols and single-series
  chart lines, blue (#4ea8ff) for the second series and links, green/red only
  for changes. Monospace uppercase for labels and numbers, system sans for
  prose (Japanese needs it). Thin #262626 borders, square corners, dense tiles.
- Header: brand centered (big mono "BELL & BLOCK" + one bright tagline),
  status right, 2px amber rule underneath.
- Above the prices: a time-of-day greeting (+ browser-stored name) with the
  date in English, a daily quote (fixed list of ~36 ancient East/West figures in
  `index.html`, picked by day of year, no AI), then the Today card (headline,
  lead, 3 numbered points, 3 reads on the left; on the right a chart that
  fills the column height (min 210px, larger text via the `big` option) with a
  one-column stats strip computed client-side: last, 7d change, 7d high/low
  with times, and the marked event with change since), the Learn card (chart, facts,
  bilingual concept with EN/JA toggle, default EN) from `brief.json`, and the
  Desk card: two stacked city blocks (New York ET, Tokyo JST), each with the
  live clock beside the city name, two icon tiles NOW | TMRW side by side from Open-Meteo
  (keyless, fetched by the browser, refreshed every 10 min, °C with °F for
  New York, condition and rain probability), and one short amber notice box
  (umbrella when rain is likely/possible in the next 12h, else "dry"; heat
  >= 30/33°C, cold <= 5/0°C). Then a ruled-off Tasks list that takes the
  remaining height and scrolls inside it (flex-basis 0, min 160px), so adding
  tasks never grows the row (add / check / remove / clear done,
  localStorage only). The brief section shows even when `brief.json` is
  missing so the Desk card is always available.
  At >= 1600px the three sit in one row; 900-1599px Desk spans a second row;
  below 900px (phones) the three cards become a horizontal swipe row
  (scroll-snap, 86vw each, next card peeks in) so Today and Learn sit side by
  side instead of stacking.
  The three cards share one height (grid stretch): Learn is normally the
  tallest, Today's chart grows to fill up to a 380px plot cap, Desk's task list
  takes the rest and scrolls.
- Exactly one pixel-robot mascot, inline SVG in a 66px lane left of the
  greeting text so it never overlaps text. Moves in discrete pixel steps
  (`steps(1,end)`): bob, hop, shuffle 30px and back, eye glance, antenna
  twitch, on a 12s loop; off under `prefers-reduced-motion`. No other mascots.
- The quote is one line, truncated with an ellipsis.
- Section titles are 15px on a tinted bar; hue per section via `data-hue`.

## News sources (45 feeds, `watchlist.yaml` → `rss:`)

Scope: finance, economy, politics, crypto. Nothing lifestyle or general-interest.
Japan tab = Japanese and Japan-focused outlets (日経 市場/経済/政治, NHK 経済/政治,
Nikkei Asia, Japan Times business+politics, 日銀, FSA, a 日本株・為替 aggregator).
Every other tab uses Western media only (Reuters, FT, Bloomberg, NYT, Barron's,
BBC, The Economist, MarketWatch, Politico; CoinDesk, The Block, Cointelegraph,
Decrypt, Bitcoin Magazine, CryptoSlate; TechCrunch, Ars, MIT TR, Wired, The
Information; SEC, Fed, CFTC, ECB). Per tab: Japan 10, US 14, Crypto 7, Tech/AI
9, Regulation 9 (some feeds sit in two tabs). Fetched in parallel (~4s).

Per-item routing: a feed may declare `also: {tab: regex}`; items whose title
matches get `extra_tabs`. CoinPost (Japanese crypto outlet) lives in Crypto and
its Japan-domestic stories (金融庁, 税制, 国内 ...) also appear in Japan.

Google News search feeds aggregate publishers; `fetch.py` stores the entry's
`source.title` as `publisher` and the page shows that instead of the feed name.
The page caps each tab at 20 items (`MAX_PER_TAB`), newest first with at most
2 per outlet in the top 5; "View more" reveals the remaining 15.

Probed and rejected (2026-09-21): WSJ feeds (frozen on year-old items), CNBC,
The Verge, IMF, 経産省 (403 to bots), Yahoo Finance and VentureBeat (429),
Treasury, Japan Times business feed, 財務省, BIS press, Semafor (404), Blockworks
and DL News (stale dates), ダイヤモンド and MAS (empty), FCA (undated junk
titles), Hacker News (off-topic), Politico picks and BoE (403). Removed by
scope: 東洋経済, ITmedia ビジネス/AI+ (lifestyle/Japanese tech), Economic Times
(non-Western). The 日本株・為替 aggregator query
excludes prtimes.jp and atpress.ne.jp press releases. Re-probe before re-adding.

nikkei.com has no public RSS. "日経" is a Google News RSS search scoped to
nikkei.com with a 市場 query. NHK 経済 and 日銀 新着 are native feeds. Reuters
日本語 via Google News returned stale, off-topic items and was dropped.
X/Twitter needs a paid API key, and Nitter mirrors are dead, so there is no
keyless way to pull posts; not included. Bloomberg Crypto's RSS is live but has
published nothing since Bloomberg folded the vertical; removed.

## Headline tabs

`index.html` has five fixed tabs: japan, us, crypto, tech, regulation. Each RSS
feed in `watchlist.yaml` declares `tabs: [...]` and may appear in several.
`fetch.py` passes the list through into `data.json`. To change what a tab
shows, edit the watchlist, not the HTML.

## Working in this repo

- Before committing, check `git status` and `git diff --cached` for anything
  personal or any credential. When in doubt, don't commit.
- `.gitignore` must always include `profile.md`, generated data outputs, and
  `.env*`. Do not remove these entries.
- Keep `fetch.py` dependency-light and runnable with plain `python3` on a
  GitHub-hosted runner.
- `brief.py` should fail loudly if `profile.md` is missing rather than
  silently producing a generic brief.
- Don't add GitHub secrets, tokens, or third-party services to the workflow.
- Preview locally with `python3 fetch.py && python3 -m http.server 8765` and
  open http://localhost:8765/. Opening `index.html` directly via `file://`
  shows an empty page because browsers block `fetch()` there.
