#!/usr/bin/env python3
"""Fetch prices, 7-day history, ticker tapes and RSS headlines for the dashboard.

Reads watchlist.yaml, writes data.json. Public endpoints only, no API keys,
no AI. Runs in GitHub Actions every 2 hours and locally with `python3 fetch.py`.

Failures in one source are recorded under `errors` and do not stop the run.
Exits non-zero only if nothing at all could be fetched.
"""

import calendar
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml

warnings.filterwarnings("ignore")  # urllib3/LibreSSL noise on macOS

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

ROOT = Path(__file__).resolve().parent
WATCHLIST = ROOT / "watchlist.yaml"
OUTPUT = ROOT / "data.json"

# SEC and some others reject requests without a descriptive User-Agent.
USER_AGENT = "market-terminal/0.1 (personal static dashboard; python-requests)"
TIMEOUT = 20

COINGECKO = "https://api.coingecko.com/api/v3"
CG_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso(ts) -> str:
    """epoch seconds or pandas Timestamp -> ISO 8601 UTC."""
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return datetime.fromtimestamp(float(ts), timezone.utc).replace(microsecond=0).isoformat()


def _round(x, nd: int = 2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return round(x, nd)


def _sig(x):
    """Round a price sensibly: 4 sig decimals below 10, 2 below 1000, else integer-ish."""
    if x is None:
        return None
    ax = abs(x)
    return _round(x, 6 if ax < 0.1 else 4 if ax < 10 else 2 if ax < 1000 else 1)


def downsample(points: list, max_points: int) -> list:
    """Keep at most max_points, always including the last one."""
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def load_watchlist() -> dict:
    with WATCHLIST.open(encoding="utf-8") as f:
        wl = yaml.safe_load(f) or {}
    for key in ("crypto", "indices", "stocks", "macro", "rss"):
        wl[key] = wl.get(key) or []
    wl["tape"] = wl.get("tape") or {}
    wl["tape"]["stocks"] = wl["tape"].get("stocks") or []
    wl["tape"]["crypto"] = wl["tape"].get("crypto") or []
    wl["settings"] = wl.get("settings") or {}
    return wl


# ---------------------------------------------------------------- crypto ----

def cg_get(path: str, params: dict, errors: list, label: str):
    try:
        r = requests.get(f"{COINGECKO}{path}", params=params, timeout=TIMEOUT, headers=CG_HEADERS)
        if r.status_code == 429:
            time.sleep(15)
            r = requests.get(f"{COINGECKO}{path}", params=params, timeout=TIMEOUT, headers=CG_HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        errors.append({"source": f"coingecko:{label}", "error": f"{type(exc).__name__}: {exc}"})
        return None


def fetch_crypto(entries: list, tape_entries: list, fiat: list, days: int, max_points: int, errors: list):
    """Returns (main_rows, tape_rows). One simple/price call covers both lists."""
    all_entries = entries + tape_entries
    if not all_entries:
        return [], []
    ids = ",".join(dict.fromkeys(e["coingecko_id"] for e in all_entries))
    data = cg_get("/simple/price", {
        "ids": ids,
        "vs_currencies": ",".join(fiat),
        "include_24hr_change": "true",
        "include_market_cap": "true",
        "include_last_updated_at": "true",
    }, errors, "simple_price") or {}

    def row_for(e, full: bool):
        d = data.get(e["coingecko_id"])
        if not d:
            errors.append({"source": "coingecko", "error": f"no data for {e['coingecko_id']}"})
            return None
        row = {
            "symbol": e["symbol"],
            "name": e.get("name", e["symbol"]),
            "source": "coingecko",
            "price": {c: _sig(d.get(c)) for c in fiat},
            "change_24h_pct": {c: _round(d.get(f"{c}_24h_change")) for c in fiat},
        }
        if full:
            row["market_cap"] = {c: _round(d.get(f"{c}_market_cap"), 0) for c in fiat}
        if d.get("last_updated_at"):
            row["as_of"] = iso(d["last_updated_at"])
        return row

    # 7-day history comes from Yahoo (BTC-USD etc.): CoinGecko's keyless tier
    # rate-limits market_chart hard enough to be unusable from CI.
    main = [r for r in (row_for(e, full=True) for e in entries) if r]
    ytick = {e["symbol"]: e.get("yahoo_ticker") or f"{e['symbol']}-USD" for e in entries}
    hourly = yf_download([ytick[r["symbol"]] for r in main], f"{days}d", "1h", errors, "crypto:hourly")
    for row in main:
        c = closes_of(hourly.get(ytick[row["symbol"]]))
        pts = [[int(ts.timestamp()), _sig(float(v))] for ts, v in c.items()]
        row["history"] = downsample(pts, max_points)
        row["history_currency"] = "usd"
        row["history_interval"] = "1h"
        if len(pts) > 1 and pts[0][1]:
            row["change_7d_pct"] = _round((pts[-1][1] / pts[0][1] - 1) * 100)

    tape = [r for r in (row_for(e, full=False) for e in tape_entries) if r]
    return main, tape


# --------------------------------------------------------------- yfinance ----

def yf_download(tickers: list, period: str, interval: str, errors: list, label: str) -> dict:
    """Batch download -> {ticker: DataFrame with Close column}."""
    if not tickers:
        return {}
    try:
        df = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception as exc:
        errors.append({"source": f"yfinance:{label}", "error": f"{type(exc).__name__}: {exc}"})
        return {}
    if df is None or df.empty:
        errors.append({"source": f"yfinance:{label}", "error": "empty download"})
        return {}
    out = {}
    if isinstance(df.columns, pd.MultiIndex):
        for t in tickers:
            if t in df.columns.get_level_values(0):
                out[t] = df[t]
    else:
        out[tickers[0]] = df
    return out


def closes_of(frame) -> "pd.Series":
    """Close prices with a UTC tz-aware index (Yahoo mixes naive daily and aware intraday)."""
    if frame is None or "Close" not in frame:
        return pd.Series(dtype=float)
    c = frame["Close"].dropna()
    if c.empty:
        return c
    idx = pd.DatetimeIndex(c.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    c.index = idx
    return c


def fetch_yahoo(entries: list, kind: str, days: int, max_points: int, errors: list) -> list:
    if not entries:
        return []
    tickers = [e["ticker"] for e in entries]
    daily = yf_download(tickers, "10d", "1d", errors, f"{kind}:daily")
    hourly = yf_download(tickers, f"{days}d", "1h", errors, f"{kind}:hourly")

    out = []
    for e in entries:
        t = e["ticker"]
        d_closes = closes_of(daily.get(t))
        h_closes = closes_of(hourly.get(t))
        if d_closes.empty and h_closes.empty:
            errors.append({"source": f"yfinance:{t}", "error": "no price data"})
            continue
        # Latest price: prefer the freshest hourly bar, fall back to daily close.
        if not h_closes.empty and (d_closes.empty or h_closes.index[-1] >= d_closes.index[-1]):
            last, last_ts = float(h_closes.iloc[-1]), h_closes.index[-1]
        else:
            last, last_ts = float(d_closes.iloc[-1]), d_closes.index[-1]
        # Previous close: last daily close strictly before the latest bar's date.
        prev = None
        if not d_closes.empty:
            before = d_closes[d_closes.index.normalize() < pd.Timestamp(last_ts).normalize()]
            if not before.empty:
                prev = float(before.iloc[-1])
            elif len(d_closes) > 1:
                prev = float(d_closes.iloc[-2])

        series = h_closes if not h_closes.empty else d_closes
        pts = [[int(ts.timestamp()), _sig(float(v))] for ts, v in series.items()]
        row = {
            "symbol": e["symbol"],
            "name": e.get("name", e["symbol"]),
            "ticker": t,
            "source": "yfinance",
            "kind": kind,
            "price": _sig(last),
            "prev_close": _sig(prev),
            "change_pct": _round((last / prev - 1) * 100) if prev else None,
            "as_of": iso(last_ts),
            "history": downsample(pts, max_points),
            "history_interval": "1h" if not h_closes.empty else "1d",
        }
        if len(pts) > 1 and pts[0][1]:
            row["change_7d_pct"] = _round((pts[-1][1] / pts[0][1] - 1) * 100)
        if e.get("note"):
            row["note"] = e["note"]
        if e.get("unit"):
            row["unit"] = e["unit"]
        out.append(row)
    return out


def fetch_tape_stocks(entries: list, errors: list) -> list:
    if not entries:
        return []
    tickers = [e["ticker"] for e in entries]
    daily = yf_download(tickers, "5d", "1d", errors, "tape:daily")
    out = []
    for e in entries:
        c = closes_of(daily.get(e["ticker"]))
        if c.empty:
            errors.append({"source": f"yfinance:{e['ticker']}", "error": "no tape data"})
            continue
        last = float(c.iloc[-1])
        prev = float(c.iloc[-2]) if len(c) > 1 else None
        out.append({
            "symbol": str(e["symbol"]),
            "name": e.get("name", e["symbol"]),
            "ticker": e["ticker"],
            "price": _sig(last),
            "change_pct": _round((last / prev - 1) * 100) if prev else None,
            "as_of": iso(c.index[-1]),
        })
    return out


# -------------------------------------------------------------------- rss ----

def fetch_rss(feeds: list, limit: int, errors: list) -> list:
    out = []
    for f in feeds:
        name, url = f["name"], f["url"]
        try:
            r = requests.get(
                url, timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT,
                         "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"},
            )
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
        except Exception as exc:
            errors.append({"source": f"rss:{name}", "error": f"{type(exc).__name__}: {exc}"})
            continue
        if parsed.bozo and not parsed.entries:
            errors.append({"source": f"rss:{name}", "error": f"parse error: {parsed.bozo_exception}"})
            continue

        items = []
        for entry in parsed.entries[:limit]:
            title = (entry.get("title") or "").strip()
            if "news.google.com" in url and " - " in title:  # Google News appends " - Publisher"
                title = title.rsplit(" - ", 1)[0].strip()
            items.append({"title": title, "link": entry.get("link"), "published": _entry_time(entry)})
        out.append({
            "feed": name,
            "url": url,
            "tabs": [str(x) for x in (f.get("tabs") or [])],
            "fetched_at": utc_now_iso(),
            "items": items,
        })
        if not items:
            errors.append({"source": f"rss:{name}", "error": "feed returned zero entries"})
    return out


def _entry_time(entry):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            try:
                return iso(calendar.timegm(t))
            except (OverflowError, ValueError):
                pass
    return entry.get("published") or entry.get("updated") or None


# ------------------------------------------------------------------- main ----

def main() -> int:
    started = time.time()
    wl = load_watchlist()
    st = wl["settings"]
    limit = int(st.get("items_per_feed", 15))
    fiat = [str(c).lower() for c in st.get("fiat", ["usd", "jpy"])]
    days = int(st.get("history_days", 7))
    max_points = int(st.get("history_points", 90))
    errors: list = []

    crypto, tape_crypto = fetch_crypto(wl["crypto"], wl["tape"]["crypto"], fiat, days, max_points, errors)
    indices = fetch_yahoo(wl["indices"], "index", days, max_points, errors)
    stocks = fetch_yahoo(wl["stocks"], "stock", days, max_points, errors)
    macro = fetch_yahoo(wl["macro"], "macro", days, max_points, errors)
    tape_stocks = fetch_tape_stocks(wl["tape"]["stocks"], errors)
    news = fetch_rss(wl["rss"], limit, errors)

    data = {
        "generated_at": utc_now_iso(),
        "prices": {"crypto": crypto, "indices": indices, "stocks": stocks, "macro": macro},
        "tape": {"crypto": tape_crypto, "stocks": tape_stocks},
        "news": news,
        "errors": errors,
        "meta": {
            "watchlist": WATCHLIST.name,
            "items_per_feed": limit,
            "fiat": fiat,
            "history_days": days,
            "duration_sec": round(time.time() - started, 1),
        },
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    n_prices = len(crypto) + len(indices) + len(stocks) + len(macro)
    n_items = sum(len(f["items"]) for f in news)
    print(f"wrote {OUTPUT.name} ({OUTPUT.stat().st_size // 1024} KB): {n_prices} prices, "
          f"{len(tape_crypto)}+{len(tape_stocks)} tape, {len(news)} feeds, {n_items} headlines, "
          f"{len(errors)} errors in {data['meta']['duration_sec']}s")
    for err in errors:
        print(f"  ! {err['source']}: {err['error']}", file=sys.stderr)

    if n_prices == 0 and n_items == 0:
        print("nothing fetched; failing the run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
