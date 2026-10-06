# trader: market intelligence site

A GitHub Action rebuilds a public web page every 30 minutes on weekdays and publishes it with GitHub Pages:

**https://ashumathura.github.io/trader/**

It covers a fixed list of ten stocks (see `watchlist.json`) with technical analysis, a hidden-Markov regime model, price levels, news, market trends and an event calendar. **It contains no personal data**: no holdings, quantities, purchase prices or links to private sheets.

## What the page shows
- **Top ideas and heat map**: stocks ranked by a 0 to 10 confluence score (ten yes/no bullish checks).
- **Per stock**: price chart with 50 and 200-day averages, RSI, ADX, MACD, Aroon, Hull MA, OBV, CMF, Bollinger, pivots, weekly overlay, mechanical entry/target/stop levels.
- **Regime model**: 3-state Gaussian HMM (bear, sideways, bull) with filtered probabilities, stickiness, expected duration, transition matrix, regime moments and an out-of-sample walk-forward check (see `quant-signals-guide.md` ideas in `pipeline/analysis.py`).
- **Market trends**: index, volatility, yield, FX and commodity quotes with trends, breadth across the ten stocks, headline themes, Fed and ECB releases.
- **Calendar**: earnings (pinned dates in `watchlist.json`, overridden by Alpha Vantage when it lists the ticker), Fed and ECB decisions, estimated ex-dividend dates.

Not financial advice. Everything is rule-based and descriptive.

## One-time setup
1. Settings > Pages > Source: **GitHub Actions**.
2. Settings > Secrets and variables > Actions > secret `AV_KEY` (Alpha Vantage key). Optional but recommended: used for earnings dates and as a price fallback. Never commit the key.
3. Actions tab > "Update market data" > Run workflow. After a couple of minutes the site is live.

## Where the data comes from
| Data | Source | Notes |
|---|---|---|
| Prices (2 years daily) | Yahoo Finance | GitHub's servers are often rate limited (HTTP 429), so the pipeline falls back to Yahoo through the Jina Reader proxy (`r.jina.ai`, public price data only), then to Alpha Vantage (last 100 sessions, 25 calls a day, no regime model with so little history) |
| Stock news | Google News RSS | one query per stock |
| Market news | CNBC, MarketWatch, Investing.com RSS | |
| Central banks | Federal Reserve and ECB RSS | |
| Earnings dates | `watchlist.json`, Alpha Vantage | |

## Edit the watchlist
`watchlist.json` holds the tickers and their public details (`yahoo` symbol, `av` Alpha Vantage symbol or `null`, name, sector, currency, news query), `earnings_overrides` and `macro_events`. Commit and the next run picks it up. Add or change macro dates when central banks publish new calendars.

## Develop
```
python3 -m unittest discover -s tests     # unit tests (also run in CI)
python3 pipeline/build_data.py            # builds docs/ locally (needs internet)
python3 dashboard/serve.py                # builds and serves locally at http://localhost:8765
```
Source for the page is `site/index.html`; the build copies it to `docs/`. Technical signals use the last *completed* daily bar: while a market is open the forming bar is dropped.

## Private holdings dashboard (optional, local only)
`dashboard/index.html` combines these public files with your own holdings CSV or published Google Sheet. It is **not** published (Pages serves only `docs/`) and keeps your sheet link in your browser only. Run `python3 dashboard/serve.py`, then pick your CSV in the page. Columns: `ticker`, `quantity`, optional `cost` and `currency`; a `CASH` row is a cash position. `dashboard/local/` and `*.private.*` are git-ignored; never commit exports of your sheet.
