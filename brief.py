#!/usr/bin/env python3
"""Write the daily brief and knowledge card with `claude -p`. Runs on the Mac only.

Reads profile.md (private, gitignored) + data.json, asks Claude for a JSON brief
(headline, chart spec, 3 points, bilingual knowledge card, 3 reads), checks that
nothing from profile.md leaked into the output, writes brief.json,
then commits and pushes it. brief.json is public: the prompt forbids restating
personal context and the leak check enforces it mechanically.

    python3 brief.py                # generate, commit, push
    python3 brief.py --no-push      # generate + commit only
    python3 brief.py --no-commit    # generate only
    python3 brief.py --dry-run      # print the prompt, call nothing
    python3 brief.py --force        # regenerate the knowledge card even if today's exists
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "profile.md"
DATA = ROOT / "data.json"
OUT = ROOT / "brief.json"
STATE = ROOT / ".brief_state.json"          # gitignored: past knowledge topics
FETCH_MAX_AGE = timedelta(hours=3)
HEADLINES_PER_FEED = 6
KNOWLEDGE_HISTORY = 90

SCHEMA = """{
  "date": "YYYY-MM-DD",
  "headline": "the single most important story today, <= 70 chars, plain and concrete",
  "headline_url": "url of the headline article, copied exactly from the headlines above",
  "lead": "one sentence, <= 28 words: what it means for markets",
  "chart": {
    "series": ["one or two symbols from the `chartable` list; two series are drawn rebased to % change"],
    "title": "<= 40 chars, what the chart shows",
    "note": "<= 60 chars, the insight to read off the chart",
    "mark": {"t": "ISO-8601 UTC time of the key event within the last 7 days, or null", "label": "<= 18 chars"}
  },
  "points": [
    {"title": "<= 7 words, the takeaway", "detail": "one sentence, <= 30 words, with the key number from the data", "url": "url of the article this point is based on, copied exactly from the headlines above"}
  ],
  "knowledge": {
    "title_en": "short concept title in English",
    "title_ja": "same concept in Japanese",
    "body_en": "2 short paragraphs separated by a blank line, <= 110 words total, plain text",
    "body_ja": "same content in natural Japanese, <= 260 characters, plain text",
    "why_en": "one sentence tying it to today's market",
    "why_ja": "same in Japanese",
    "chart": {"series": ["..."], "title": "...", "note": "...", "mark": null},
    "facts": [{"label": "<= 20 chars", "value": "<= 12 chars, e.g. 1.25%"}],
    "sources": [{"title": "...", "url": "..."}]
  },
  "reading": [{"title": "...", "url": "...", "source": "feed name", "why": "<= 12 words"}]
}"""



def die(msg: str, code: int = 1):
    print(f"brief.py: {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_data() -> dict:
    stale = True
    if DATA.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(DATA.stat().st_mtime, timezone.utc)
        stale = age > FETCH_MAX_AGE
    if stale:
        print("data.json missing or stale, running fetch.py ...")
        r = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "fetch.py")], cwd=ROOT)
        if r.returncode != 0 or not DATA.exists():
            die("fetch.py failed")
    return json.loads(DATA.read_text(encoding="utf-8"))


def compact(data: dict) -> dict:
    """Trim data.json to what the model needs (and what fits comfortably in a prompt)."""
    def px(rows, crypto=False):
        out = []
        for r in rows:
            if crypto:
                out.append({"symbol": r["symbol"], "usd": r["price"].get("usd"),
                            "chg_24h": r.get("change_24h_pct", {}).get("usd"), "chg_7d": r.get("change_7d_pct")})
            else:
                out.append({"symbol": r["symbol"], "name": r["name"], "price": r["price"],
                            "chg_1d": r.get("change_pct"), "chg_7d": r.get("change_7d_pct"), "as_of": r.get("as_of")})
        return out
    p = data.get("prices", {})
    news = []
    for f in data.get("news", []):
        items = [{"title": i["title"], "url": i["link"], "published": i.get("published")}
                 for i in f.get("items", [])[:HEADLINES_PER_FEED] if i.get("title")]
        if items:
            news.append({"feed": f["feed"], "tabs": f.get("tabs", []), "items": items})
    chartable = []
    for group, rows in (("crypto", p.get("crypto", [])), ("indices", p.get("indices", [])),
                        ("stocks", p.get("stocks", [])), ("macro", p.get("macro", []))):
        for r in rows:
            h = [v for _, v in r.get("history", []) if v is not None]
            if len(h) > 2:
                chartable.append({"symbol": r["symbol"], "name": r["name"], "group": group, "unit": r.get("unit", ""),
                                  "last": h[-1], "7d_low": min(h), "7d_high": max(h), "chg_7d": r.get("change_7d_pct"),
                                  "from": datetime.fromtimestamp(r["history"][0][0], timezone.utc).strftime("%Y-%m-%dT%H:%MZ")})
    return {
        "generated_at": data.get("generated_at"),
        "crypto": px(p.get("crypto", []), crypto=True),
        "indices": px(p.get("indices", [])),
        "stocks": px(p.get("stocks", [])),
        "macro": px(p.get("macro", [])),
        "chartable": chartable,
        "news": news,
    }


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"knowledge_history": []}


def build_prompt(profile: str, data: dict, history: list, today: str, now_local: str) -> str:
    past = "\n".join(f"- {h['date']}: {h['title']}" for h in history[-KNOWLEDGE_HISTORY:]) or "- (none yet)"
    return f"""You write a daily market brief for one reader's private dashboard. Today is {today} ({now_local}).

# Reader profile (PRIVATE - use it to choose focus and language, NEVER restate it)
{profile.strip()}

# Market data (prices, changes, and the latest headlines per feed)
```json
{json.dumps(data, ensure_ascii=False, separators=(",", ":"))}
```

# Knowledge topics already covered (do not repeat these)
{past}

# Task
Return ONE JSON object and nothing else (no markdown fences, no commentary) with exactly this shape:
{SCHEMA}

# Rules
- The output is PUBLISHED on a public web page. Do not mention the reader, their holdings, job, location, employer, or anything from the profile. Write in general market terms ("BTC fell", not "your BTC position").
- Do not use any tools. Work only from the data above. If something is not in the data, do not invent it.
- LANGUAGE: an item about the Japanese market (BOJ, yen, Nikkei/TOPIX, Japanese companies, FSA, MOF) is written ENTIRELY in Japanese: headline, lead, point title, point detail, chart title and note. Everything else is written in English. Never mix languages inside one item. `knowledge` always has both versions.
- Be brief. The reader wants to digest the whole card in 30 seconds.
- `chart` (Today): pick the series from `chartable` that best SHOWS the headline (e.g. USDJPY for a yen story, US10Y for a rates story, BTC for a crypto story). Two series only when the comparison is the insight (e.g. BTC vs IXIC). `mark.t` is the time of the event if it happened within the 7-day window, else null. `note` states what the reader should see.
- `points`: EXACTLY 3, each a different theme from the headline (e.g. Japan, US/macro, crypto, regulation/AI). What moved and why it matters, with one concrete number from the data. Do not repeat the headline as a point. Each point carries the `url` of the headline it draws on, so the reader can read more.
- Every `url` field must be copied character for character from the headlines above; anything else is dropped.
- `knowledge`: teach one concept a serious market reader may not fully know (market structure, fundamentals, a regulation, a crypto mechanism, a macro relationship...). Connected to today's data when possible. Not covered before. Provide BOTH English and Japanese versions with the same content. `knowledge.chart`: a series from `chartable` that illustrates the concept, or null if none fits. `facts`: 2 to 4 key numbers that anchor the concept, taken from the data above or from stable, well-established public facts (e.g. a policy rate, a law's year); never guess. `sources` may be empty; if included, urls must come from the data above.
- `reading`: EXACTLY 3 items chosen ONLY from the headlines above, url copied exactly, different feeds, not the headline article itself if possible.
- Dates in `date` use YYYY-MM-DD and must equal {today}.
"""


def run_claude(prompt: str, model, timeout: int) -> str:
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # allow running from inside Claude Code
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env, cwd=ROOT)
    except FileNotFoundError:
        die("`claude` CLI not found on PATH")
    except subprocess.TimeoutExpired:
        die(f"claude -p timed out after {timeout}s")
    if r.returncode != 0:
        die(f"claude -p failed ({r.returncode}): {r.stderr.strip()[:500]}")
    try:
        outer = json.loads(r.stdout)
        text = outer.get("result", "")
        cost = outer.get("total_cost_usd")
        if cost is not None:
            print(f"claude -p ok: ${cost:.3f}, {outer.get('duration_api_ms', 0) / 1000:.0f}s")
    except json.JSONDecodeError:
        text = r.stdout
    return text


def parse_brief(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        die(f"no JSON object in model output:\n{text[:800]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        die(f"model output is not valid JSON ({exc}):\n{text[:800]}")


def validate_chart(c, symbols: set):
    if not isinstance(c, dict):
        return None
    series = c.get("series") or []
    if isinstance(series, str):
        series = [series]
    series = [str(x).upper() for x in series if str(x).upper() in symbols][:2]
    if not series:
        return None
    out = {"series": series, "title": str(c.get("title", "")).strip()[:60], "note": str(c.get("note", "")).strip()[:90]}
    m = c.get("mark")
    if isinstance(m, dict) and m.get("t"):
        try:
            datetime.fromisoformat(str(m["t"]).replace("Z", "+00:00"))
            out["mark"] = {"t": str(m["t"]), "label": str(m.get("label", "")).strip()[:24]}
        except ValueError:
            pass
    return out


def validate(brief: dict, data: dict, today: str) -> dict:
    allowed_urls = {i["url"] for f in data["news"] for i in f["items"]}
    url_source = {i["url"]: f["feed"] for f in data["news"] for i in f["items"]}
    symbols = {c["symbol"].upper() for c in data.get("chartable", [])}
    out = {"date": today}
    if isinstance(brief.get("headline"), str):
        out["headline"] = brief["headline"].strip()[:140]
    if brief.get("headline_url") in allowed_urls:
        out["headline_url"] = brief["headline_url"]
        out["headline_source"] = url_source[brief["headline_url"]]
    if isinstance(brief.get("lead"), str) and brief["lead"].strip():
        out["lead"] = brief["lead"].strip()[:300]
    ch = validate_chart(brief.get("chart"), symbols)
    if ch:
        out["chart"] = ch
    pts = []
    for p in brief.get("points", []):
        if isinstance(p, dict) and p.get("title"):
            row = {"title": str(p["title"]).strip(), "detail": str(p.get("detail", "")).strip()}
            if p.get("url") in allowed_urls:
                row["url"] = p["url"]
                row["source"] = url_source[p["url"]]
            pts.append(row)
        elif isinstance(p, str) and p.strip():
            pts.append({"title": p.strip(), "detail": ""})
    if len(pts) < 2:
        die(f"too few points: {pts}")
    out["points"] = pts[:3]
    k = brief.get("knowledge") or {}
    if isinstance(k, dict) and (k.get("title_en") or k.get("title")) and (k.get("body_en") or k.get("body")):
        kn = {
            "title_en": str(k.get("title_en") or k.get("title")).strip(),
            "title_ja": str(k.get("title_ja", "")).strip(),
            "body_en": str(k.get("body_en") or k.get("body")).strip(),
            "body_ja": str(k.get("body_ja", "")).strip(),
            "why_en": str(k.get("why_en") or k.get("why_it_matters", "")).strip(),
            "why_ja": str(k.get("why_ja", "")).strip(),
            "facts": [{"label": str(f["label"]).strip()[:30], "value": str(f["value"]).strip()[:20]}
                      for f in (k.get("facts") or []) if isinstance(f, dict) and f.get("label") and f.get("value")][:4],
            "sources": [x for x in k.get("sources", []) if isinstance(x, dict) and x.get("url") in allowed_urls][:2],
        }
        kch = validate_chart(k.get("chart"), symbols)
        if kch:
            kn["chart"] = kch
        out["knowledge"] = kn
    reading = []
    for r in brief.get("reading", []):
        if isinstance(r, dict) and r.get("url") in allowed_urls and r.get("title"):
            reading.append({"title": str(r["title"]).strip(), "url": r["url"],
                            "source": str(r.get("source", "")).strip(), "why": str(r.get("why", "")).strip()})
    out["reading"] = reading[:3]
    return out


def leak_check(profile: str, brief: dict):
    """Fail if any substantive profile line appears in the public output."""
    hay = json.dumps(brief, ensure_ascii=False).lower()
    for raw in profile.splitlines():
        line = raw.strip().lstrip("-*# ").strip()
        if len(line) < 12 or line.lower().startswith(("language", "brief style")):
            continue
        if line.lower() in hay:
            die(f"profile text leaked into brief.json, refusing to write: {line[:60]!r}")


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and exit")
    ap.add_argument("--force", action="store_true", help="regenerate today's knowledge card")
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    if not PROFILE.exists():
        die(f"{PROFILE.name} not found. It holds your private context and is gitignored; create it next to brief.py.")
    profile = PROFILE.read_text(encoding="utf-8")
    if len(profile.strip()) < 40:
        die(f"{PROFILE.name} is nearly empty; fill it in first.")

    data = compact(ensure_data())
    now = datetime.now().astimezone()
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    prompt = build_prompt(profile, data, state.get("knowledge_history", []), today, now.strftime("%A %H:%M %Z"))
    if args.dry_run:
        print(prompt)
        return 0

    t0 = time.time()
    brief = validate(parse_brief(run_claude(prompt, args.model, args.timeout)), data, today)

    # Knowledge refreshes once per 24h: keep today's existing card unless --force.
    if OUT.exists() and not args.force:
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            if prev.get("date") == today and prev.get("knowledge"):
                brief["knowledge"] = prev["knowledge"]
        except json.JSONDecodeError:
            pass

    leak_check(profile, brief)
    brief["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if brief.get("knowledge"):
        title = brief["knowledge"]["title_en"]
        hist = [h for h in state.get("knowledge_history", []) if h.get("title") != title]
        hist.append({"date": today, "title": title})
        state["knowledge_history"] = hist[-KNOWLEDGE_HISTORY:]
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUT.name}: {len(brief['points'])} points, knowledge='{brief.get('knowledge', {}).get('title_en', '-')}', "
          f"{len(brief['reading'])} reading, charts: today={(brief.get('chart') or {}).get('series', '-')} "
          f"learn={(brief.get('knowledge', {}).get('chart') or {}).get('series', '-')}, in {time.time() - t0:.0f}s")

    if args.no_commit:
        return 0
    git("add", OUT.name)
    if not git("diff", "--cached", "--quiet", check=False).returncode == 0:
        git("commit", "-q", "-m", f"brief: {today}")
        print(f"committed brief: {today}")
    else:
        print("brief.json unchanged, nothing to commit")
        return 0
    if args.no_push:
        return 0
    if not git("remote", check=False).stdout.strip():
        print("no git remote configured, skipping push", file=sys.stderr)
        return 0
    git("push", "-q")
    print("pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
