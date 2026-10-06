# trader: public market data layer

A GitHub Action refreshes market data every 30 minutes on weekdays and publishes it with GitHub Pages as plain JSON files:
`technicals.json`, `news.json`, `market.json`, `calendar.json`, `prices.json`, `meta.json`.

**This repo contains no personal data.** No funds, quantities, purchase prices, values, or links to private sheets.
The only portfolio-related file is `watchlist.json` (tickers). The personal dashboard runs on your own computer, reads your holdings from your sheet, and combines them with these public files in your browser.

## One-time setup
1. Push this repo to https://github.com/ashumathura/trader (`./push.sh` does it). The workflow already lives in `.github/workflows/update-data.yml`; pushing workflow files needs a token with the `workflow` scope.
2. Repo Settings > Pages > Source: **GitHub Actions**.
3. Optional: Settings > Secrets and variables > Actions > New secret `AV_KEY` (your Alpha Vantage key) for earnings dates. Never commit the key.
4. Actions tab > "Update market data" > Run workflow. After about a minute, https://ashumathura.github.io/trader/data/meta.json should open.

## Local dashboard (private)
Run `python3 dashboard/serve.py`. It builds the public data on your computer (Yahoo rate-limits GitHub's servers, so the Action often gets HTTP 429 and publishes no prices), refreshes it every 20 minutes and opens the dashboard at http://localhost:8765. Only `dashboard/index.html` and `docs/` are served, on this computer only.
`dashboard/index.html` is **not** published (Pages only serves `docs/`). It also works opened straight from disk, but then it can only use the GitHub Pages data. It fetches the public JSON from Pages and your holdings from a published-CSV link of your sheet (or a CSV file you pick). The link is kept only in that browser's localStorage. Columns: `ticker` (same names as `watchlist.json`), `quantity`, optional `cost` and `currency`; a row with ticker `CASH` is a cash position. Tickers missing from `watchlist.json` get no market data, so add them there.
Do not save exports of your sheet inside this repo; `dashboard/local/` and `*.private.*` are git-ignored as a safety net.

## Development
`python3 -m unittest discover -s tests` runs the tests (also run in CI before each build). Technical signals use the last *completed* daily bar: while a market is open, the forming bar is dropped.

## Edit the watchlist
Add or remove tickers in `watchlist.json` (`ticker` = the Google Finance style name from your sheet, `yahoo` = Yahoo symbol). Commit and the next run picks it up. `earnings_overrides` lets you pin an earnings date, `macro_events` adds Fed, CPI and similar dates.

## Privacy notes
- The ticker list is public if the repo is public. If you want to hide which stocks you own, add extra well-known tickers; the local dashboard only shows those you actually hold.
- The Google Sheet link/ID must never be added here. Your sheet is link-viewable, so anyone with its ID can read it.
- Run `python3 pipeline/build_data.py` locally to test (needs internet, Python 3.8+).
