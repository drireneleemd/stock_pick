"""
Wall Street Favorites - personal replica
-----------------------------------------
Pulls price history, analyst targets, dividends and buyback data for a
stock universe via yfinance (free, no API key), computes six ranking
categories, and writes a static HTML dashboard to docs/index.html.

Categories:
  1. Highest Upside      - (mean analyst target - price) / price
  2. Steady Climbers     - positive 6mo return, drawdown from 52wk high <= 15%, has upside
  3. Top Conviction      - composite score (0-100) within Steady Climbers universe
  4. Dividend Growth     - 3+ consecutive years of dividend increases, sustainable payout
  5. Stage Analysis      - Stan Weinstein 4-stage method (30-week MA + slope)
  6. Aggressive Buybacks - trailing 12mo buyback yield (repurchases / market cap)

Run: python rank_stocks.py
Requires: requirements.txt
"""

import datetime as dt
import time
import traceback

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Universe: edit this list to whatever you want to track. Kept to a curated
# set of large, liquid, well-covered names by default so the run stays fast
# and reliable. Swap in your own watchlist or the full S&P 500 list if you
# want broader coverage (Yahoo has no hard daily cap, but very large lists
# take longer and are more prone to occasional throttling / missing fields).
# ---------------------------------------------------------------------------
TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL",
    "CRM", "ADBE", "AMD", "QCOM", "TXN", "INTC", "CSCO", "IBM", "NOW", "PANW",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "SCHW", "BLK",
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY",
    "HD", "LOW", "MCD", "SBUX", "NKE", "TGT", "COST", "WMT", "TJX", "BKNG",
    "XOM", "CVX", "COP", "SLB", "EOG",
    "CAT", "DE", "HON", "GE", "RTX", "LMT", "BA", "UPS", "UNP", "EMR",
    "PG", "KO", "PEP", "PM", "MO", "CL",
    "LIN", "SHW", "APD", "FCX", "NEM",
    "NEE", "DUK", "SO",
    "DIS", "CMCSA", "T", "VZ", "TMUS",
    "PLD", "AMT", "SPG",
]

MIN_MARKET_CAP = 10e9      # $10B minimum, matches the original site's stated screen
MIN_ANALYSTS = 3           # minimum analyst coverage to be included
STEADY_CLIMBER_MAX_DD = -0.15  # no worse than -15% drawdown from 52wk high
STEADY_CLIMBER_MIN_6M_RET = 0.0
DIVIDEND_MIN_STREAK_YEARS = 3
MAX_SUSTAINABLE_PAYOUT = 0.75  # 75% of earnings, a common "sustainable" cutoff


def safe_get(d, key, default=None):
    v = d.get(key, default)
    return default if v is None else v


def weinstein_stage(hist: pd.DataFrame) -> str:
    """Classify a stock into Stan Weinstein's 4 stages using a 30-week
    (~150 trading day) moving average and its recent slope."""
    close = hist["Close"].dropna()
    if len(close) < 160:
        return "Unknown"
    ma30w = close.rolling(150).mean()
    price = close.iloc[-1]
    ma_now = ma30w.iloc[-1]
    ma_prev = ma30w.iloc[-20]  # ~4 weeks earlier
    if pd.isna(ma_now) or pd.isna(ma_prev):
        return "Unknown"
    ma_rising = ma_now > ma_prev
    ma_falling = ma_now < ma_prev
    above = price > ma_now
    below = price < ma_now

    if above and ma_rising:
        return "Stage 2 (Advancing)"
    if below and ma_falling:
        return "Stage 4 (Declining)"
    if above and not ma_rising:
        return "Stage 3 (Topping)"
    if below and not ma_falling:
        return "Stage 1 (Basing)"
    return "Unknown"


def max_drawdown_from_high(hist: pd.DataFrame) -> float:
    close = hist["Close"].dropna()
    if close.empty:
        return np.nan
    rolling_high = close.cummax()
    dd = (close / rolling_high - 1.0)
    return dd.iloc[-1]  # current drawdown from the running all-time (window) high


def six_month_return(hist: pd.DataFrame) -> float:
    close = hist["Close"].dropna()
    if len(close) < 2:
        return np.nan
    cutoff = close.index[-1] - pd.Timedelta(days=182)
    past = close[close.index <= cutoff]
    if past.empty:
        past_price = close.iloc[0]
    else:
        past_price = past.iloc[-1]
    return close.iloc[-1] / past_price - 1.0


def dividend_growth_streak(divs: pd.Series) -> int:
    """Count consecutive years (most recent first) with a higher total
    annual dividend than the prior year."""
    if divs is None or divs.empty:
        return 0
    annual = divs.groupby(divs.index.year).sum()
    years = sorted(annual.index)
    if len(years) < 2:
        return 0
    streak = 0
    for i in range(len(years) - 1, 0, -1):
        if annual[years[i]] > annual[years[i - 1]]:
            streak += 1
        else:
            break
    return streak


def buyback_yield(ticker: yf.Ticker, market_cap: float) -> float:
    try:
        cf = ticker.cashflow
        if cf is None or cf.empty:
            return np.nan
        for label in ("Repurchase Of Capital Stock", "Common Stock Repurchased"):
            if label in cf.index:
                trailing = cf.loc[label].iloc[0]  # most recent fiscal year column
                if pd.isna(trailing) or market_cap in (None, 0) or pd.isna(market_cap):
                    return np.nan
                return abs(trailing) / market_cap  # outflow is negative in statements
    except Exception:
        pass
    return np.nan


def price_vs_moving_average(hist: pd.DataFrame, window: int) -> float:
    """Current price as a percentage above/below its N-day simple moving
    average, e.g. 0.08 means price is 8% above the MA."""
    close = hist["Close"].dropna()
    if len(close) < window:
        return np.nan
    ma = close.rolling(window).mean().iloc[-1]
    if pd.isna(ma) or ma == 0:
        return np.nan
    return close.iloc[-1] / ma - 1.0


def fetch_one(symbol: str) -> dict:
    t = yf.Ticker(symbol)
    info = t.info or {}
    hist = t.history(period="2y", auto_adjust=True)
    if hist.empty:
        raise ValueError("no price history")

    price = safe_get(info, "currentPrice") or hist["Close"].iloc[-1]
    market_cap = safe_get(info, "marketCap")
    target_mean = safe_get(info, "targetMeanPrice")
    num_analysts = safe_get(info, "numberOfAnalystOpinions", 0)
    payout_ratio = safe_get(info, "payoutRatio")
    sector = safe_get(info, "sector", "Unknown")
    name = safe_get(info, "shortName", symbol)
    forward_pe = safe_get(info, "forwardPE")

    upside = (target_mean / price - 1.0) if (target_mean and price) else np.nan
    dd = max_drawdown_from_high(hist)
    ret6m = six_month_return(hist)
    stage = weinstein_stage(hist)
    bb_yield = buyback_yield(t, market_cap)
    div_streak = dividend_growth_streak(t.dividends)
    vs_ma50 = price_vs_moving_average(hist, 50)
    vs_ma200 = price_vs_moving_average(hist, 200)

    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "price": price,
        "market_cap": market_cap,
        "num_analysts": num_analysts,
        "target_mean": target_mean,
        "upside": upside,
        "drawdown_from_high": dd,
        "return_6m": ret6m,
        "stage": stage,
        "buyback_yield": bb_yield,
        "dividend_streak_years": div_streak,
        "payout_ratio": payout_ratio,
        "forward_pe": forward_pe,
        "vs_ma50": vs_ma50,
        "vs_ma200": vs_ma200,
    }


def build_dataset() -> pd.DataFrame:
    rows = []
    for i, sym in enumerate(TICKERS):
        try:
            rows.append(fetch_one(sym))
        except Exception as e:
            print(f"[skip] {sym}: {e}")
        time.sleep(0.4)  # be gentle with Yahoo's endpoints
        if (i + 1) % 20 == 0:
            print(f"...{i + 1}/{len(TICKERS)} fetched")
    df = pd.DataFrame(rows)
    return df


def compute_categories(df: pd.DataFrame) -> dict:
    base = df[
        (df["market_cap"].fillna(0) >= MIN_MARKET_CAP)
        & (df["num_analysts"].fillna(0) >= MIN_ANALYSTS)
        & df["upside"].notna()
    ].copy()

    highest_upside = base.sort_values("upside", ascending=False).head(25)

    steady = base[
        (df["return_6m"] >= STEADY_CLIMBER_MIN_6M_RET)
        & (df["drawdown_from_high"] >= STEADY_CLIMBER_MAX_DD)
        & (df["upside"] > 0)
    ].copy()
    steady = steady.sort_values("upside", ascending=False)

    # Top Conviction: composite score within the Steady Climbers universe
    if not steady.empty:
        s = steady.copy()
        s["_upside_pct"] = s["upside"].rank(pct=True)
        s["_stage2"] = (s["stage"] == "Stage 2 (Advancing)").astype(float)
        s["_bb_pct"] = s["buyback_yield"].fillna(0).rank(pct=True)
        s["conviction_score"] = (
            0.5 * s["_upside_pct"] + 0.3 * s["_stage2"] + 0.2 * s["_bb_pct"]
        ) * 100
        top_conviction = s.sort_values("conviction_score", ascending=False).head(20)
    else:
        top_conviction = steady

    dividend_growth = base[
        (df["dividend_streak_years"] >= DIVIDEND_MIN_STREAK_YEARS)
        & (df["payout_ratio"].fillna(1.0) <= MAX_SUSTAINABLE_PAYOUT)
    ].sort_values("upside", ascending=False)

    stage2 = df[df["stage"] == "Stage 2 (Advancing)"].sort_values(
        "return_6m", ascending=False
    )
    stage4 = df[df["stage"] == "Stage 4 (Declining)"].sort_values("return_6m")

    buybacks = df[df["buyback_yield"].notna()].sort_values(
        "buyback_yield", ascending=False
    ).head(25)

    return {
        "highest_upside": highest_upside,
        "steady_climbers": steady,
        "top_conviction": top_conviction,
        "dividend_growth": dividend_growth,
        "stage2": stage2,
        "stage4": stage4,
        "buybacks": buybacks,
    }


def fmt_pct(x):
    return "-" if pd.isna(x) else f"{x * 100:.1f}%"


def fmt_price(x):
    return "-" if pd.isna(x) else f"${x:,.2f}"


def fmt_int(x):
    return "-" if pd.isna(x) else f"{int(x)}"


def fmt_ratio(x):
    return "-" if pd.isna(x) else f"{x:.1f}x"


def fmt_pct_signed(x):
    if pd.isna(x):
        return "-"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x * 100:.1f}%"


def table_rows(df: pd.DataFrame, columns: list) -> str:
    rows_html = []
    for _, r in df.iterrows():
        cells = "".join(f"<td>{r[c]}</td>" for c in columns)
        rows_html.append(f"<tr>{cells}</tr>")
    return "\n".join(rows_html)


def render_table(title: str, description: str, df: pd.DataFrame, col_defs: list) -> str:
    if df is None or df.empty:
        body = "<p class='empty' data-empty-msg>No stocks currently qualify for this screen.</p>"
    else:
        headers = "".join(f"<th>{c[0]}</th>" for c in col_defs)
        rows = []
        for _, r in df.iterrows():
            cells = "".join(f"<td>{c[1](r)}</td>" for c in col_defs)
            # data-search carries a lowercase "TICKER name" string used by the
            # search bar's client-side filter — kept off-screen, not rendered.
            search_key = f"{r['symbol']} {r['name']}".lower()
            rows.append(f'<tr data-search="{search_key}">{cells}</tr>')
        body = (
            f"<div class='tablewrap'><table><thead><tr>{headers}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
            f"<p class='empty no-results' hidden>No matches in this section.</p>"
        )
    return f"""
    <section class="card" data-section>
      <h2>{title}</h2>
      <p class="desc">{description}</p>
      {body}
    </section>
    """


def build_html(cats: dict, generated_at: str) -> str:
    upside_cols = [
        ("Symbol", lambda r: f"<b>{r['symbol']}</b>"),
        ("Name", lambda r: r["name"]),
        ("Price", lambda r: fmt_price(r["price"])),
        ("Target", lambda r: fmt_price(r["target_mean"])),
        ("Upside", lambda r: fmt_pct(r["upside"])),
        ("Fwd P/E", lambda r: fmt_ratio(r["forward_pe"])),
        ("Analysts", lambda r: fmt_int(r["num_analysts"])),
    ]
    steady_cols = upside_cols + [
        ("6mo Return", lambda r: fmt_pct(r["return_6m"])),
        ("Drawdown", lambda r: fmt_pct(r["drawdown_from_high"])),
    ]
    conviction_cols = steady_cols + [
        ("Stage", lambda r: r["stage"]),
        ("Score", lambda r: f"{r['conviction_score']:.0f}/100"),
    ]
    dividend_cols = upside_cols + [
        ("Div. Streak", lambda r: f"{fmt_int(r['dividend_streak_years'])} yrs"),
        ("Payout Ratio", lambda r: fmt_pct(r["payout_ratio"])),
    ]
    stage_cols = [
        ("Symbol", lambda r: f"<b>{r['symbol']}</b>"),
        ("Name", lambda r: r["name"]),
        ("Price", lambda r: fmt_price(r["price"])),
        ("vs 50D MA", lambda r: fmt_pct_signed(r["vs_ma50"])),
        ("vs 200D MA", lambda r: fmt_pct_signed(r["vs_ma200"])),
        ("6mo Return", lambda r: fmt_pct(r["return_6m"])),
        ("Sector", lambda r: r["sector"]),
    ]
    buyback_cols = [
        ("Symbol", lambda r: f"<b>{r['symbol']}</b>"),
        ("Name", lambda r: r["name"]),
        ("Price", lambda r: fmt_price(r["price"])),
        ("Buyback Yield (TTM)", lambda r: fmt_pct(r["buyback_yield"])),
        ("Upside", lambda r: fmt_pct(r["upside"])),
    ]

    sections = (
        render_table(
            "Highest Upside",
            "Ranked by Wall Street price target upside &mdash; the biggest gap between "
            "today's price and analysts' average 12-month target.",
            cats["highest_upside"], upside_cols,
        )
        + render_table(
            "Steady Climbers",
            "Positive 6-month return, drawdown from the 52-week high no worse than -15%, "
            "and analysts still see upside.",
            cats["steady_climbers"], steady_cols,
        )
        + render_table(
            "Top Conviction",
            "Highest-conviction picks from the Steady Climbers universe &mdash; a 0-100 "
            "blend of Wall Street upside, Stage 2 technicals, and buyback activity.",
            cats["top_conviction"], conviction_cols,
        )
        + render_table(
            "Dividend Growth",
            f"{DIVIDEND_MIN_STREAK_YEARS}+ consecutive years of dividend increases, payout "
            f"ratio under {int(MAX_SUSTAINABLE_PAYOUT*100)}%, and Wall Street still sees upside.",
            cats["dividend_growth"], dividend_cols,
        )
        + render_table(
            "Stage Analysis &mdash; Stage 2 (Advancing)",
            "Stan Weinstein-style stage analysis: price above a rising 30-week moving average.",
            cats["stage2"].head(30), stage_cols,
        )
        + render_table(
            "Stage Analysis &mdash; Stage 4 (Declining)",
            "Price below a falling 30-week moving average.",
            cats["stage4"].head(30), stage_cols,
        )
        + render_table(
            "Aggressive Buybacks",
            "Ranked by trailing-12-month share repurchases as a percentage of market cap.",
            cats["buybacks"], buyback_cols,
        )
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>My Wall Street Favorites</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  :root {{
    --bg: #0b0d12; --card: #12151c; --border: #23283344; --text: #e7e9ee;
    --muted: #9aa3b2; --accent: #4f8cff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 16px 80px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); margin-top: 0; font-size: 0.9rem; }}
  .card {{
    background: var(--card); border: 1px solid #ffffff14; border-radius: 12px;
    padding: 20px 22px; margin: 22px 0;
  }}
  .card h2 {{ margin-top: 0; font-size: 1.15rem; }}
  .desc {{ color: var(--muted); font-size: 0.88rem; margin-bottom: 14px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid #ffffff10; white-space: nowrap; }}
  th {{ color: var(--muted); font-weight: 600; }}
  tbody tr:hover {{ background: #ffffff08; }}
  .empty {{ color: var(--muted); font-style: italic; }}
  footer {{ color: var(--muted); font-size: 0.78rem; text-align: center; margin-top: 40px; }}
  .tablewrap {{ overflow-x: auto; }}
  .searchbar {{
    position: sticky; top: 0; z-index: 5; background: var(--bg);
    padding: 10px 0 16px;
  }}
  .searchbar input {{
    width: 100%; padding: 12px 14px; border-radius: 10px; font-size: 1rem;
    border: 1px solid #ffffff22; background: var(--card); color: var(--text);
  }}
  .searchbar input:focus {{ outline: 2px solid var(--accent); }}
  .searchbar .hint {{ color: var(--muted); font-size: 0.78rem; margin: 6px 2px 0; }}
  section[data-section][hidden] {{ display: none; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>My Wall Street Favorites</h1>
    <p class="subtitle">Personal replica &middot; auto-refreshed daily before market open &middot; last updated {generated_at}</p>
    <div class="searchbar">
      <input type="text" id="stockSearch" placeholder="Search by ticker or company name (e.g. AAPL, Apple)..." autocomplete="off" />
      <p class="hint" id="searchHint"></p>
    </div>
    {sections}
    <footer>
      Data via Yahoo Finance (yfinance). Not investment advice &mdash; for personal research only.
      Universe: {len(TICKERS)} large-cap tickers, market cap &ge; ${int(MIN_MARKET_CAP/1e9)}B, &ge;{MIN_ANALYSTS} analysts.
    </footer>
  </div>
  <script>
    (function () {{
      const input = document.getElementById('stockSearch');
      const hint = document.getElementById('searchHint');
      const sections = Array.from(document.querySelectorAll('section[data-section]'));

      function applyFilter() {{
        const q = input.value.trim().toLowerCase();
        let totalMatches = 0;

        sections.forEach(function (section) {{
          const rows = Array.from(section.querySelectorAll('tbody tr'));
          let sectionMatches = 0;

          rows.forEach(function (row) {{
            const key = row.getAttribute('data-search') || '';
            const match = q === '' || key.indexOf(q) !== -1;
            row.hidden = !match;
            if (match) sectionMatches++;
          }});

          totalMatches += sectionMatches;

          const noResults = section.querySelector('.no-results');
          const hasRows = rows.length > 0;
          if (noResults) {{
            noResults.hidden = !(q !== '' && hasRows && sectionMatches === 0);
          }}
          // Hide the whole section only when it has real rows but none match.
          section.hidden = q !== '' && hasRows && sectionMatches === 0;
        }});

        hint.textContent = q === ''
          ? ''
          : (totalMatches === 0
              ? 'No stocks match "' + input.value.trim() + '" in this dashboard.'
              : totalMatches + ' match' + (totalMatches === 1 ? '' : 'es') + ' across all sections.');
      }}

      input.addEventListener('input', applyFilter);
    }})();
  </script>
</body>
</html>
"""


def main():
    print(f"Fetching data for {len(TICKERS)} tickers...")
    df = build_dataset()
    print(f"Fetched {len(df)} / {len(TICKERS)} successfully.")
    cats = compute_categories(df)
    generated_at = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = build_html(cats, generated_at)
    with open("docs/index.html", "w") as f:
        f.write(html)
    print("Wrote docs/index.html")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
