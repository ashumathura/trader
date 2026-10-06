# trader: public market data layer

A GitHub Action refreshes market data every 30 minutes on weekdays and publishes it with GitHub Pages as plain JSON files:
`technicals.json`, `news.json`, `market.json`, `calendar.json`, `prices.json`, `meta.json`.

**This repo contains no personal data.** No funds, quantities, purchase prices, values, or links to private sheets.
The only portfolio-related file is `watchlist.json` (tickers). The personal dashboard runs on your own computer, reads your holdings from your sheet, and combines them with these public files in your browser.

## One-time setup
1. Run `./push.sh` in this folder (it places the workflow file in `.github/workflows/` and pushes). Or push this folder manually to https://github.com/ashumathura/trader (see `push.sh`).
2. Repo Settings > Pages > Source: **GitHub Actions**.
3. Optional: Settings > Secrets and variables > Actions > New secret `AV_KEY` (your Alpha Vantage key) for earnings dates. Never commit the key.
4. Actions tab > "Update market data" > Run workflow. After about a minute, https://ashumathura.github.io/trader/data/meta.json should open.

## Edit the watchlist
Add or remove tickers in `watchlist.json` (`ticker` = the Google Finance style name from your sheet, `yahoo` = Yahoo symbol). Commit and the next run picks it up. `earnings_overrides` lets you pin an earnings date, `macro_events` adds Fed, CPI and similar dates.

## Privacy notes
- The ticker list is public if the repo is public. If you want to hide which stocks you own, add extra well-known tickers; the local dashboard only shows those you actually hold.
- The Google Sheet link/ID must never be added here. Your sheet is link-viewable, so anyone with its ID can read it.
- Run `python3 pipeline/build_data.py` locally to test (needs internet, Python 3.8+).
