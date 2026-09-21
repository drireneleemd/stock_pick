# My Wall Street Favorites

A personal, automatically-refreshing replica of the Wall Street Favorites
categories (Highest Upside, Steady Climbers, Top Conviction, Dividend Growth,
Stage Analysis, Aggressive Buybacks), built on free data (Yahoo Finance via
`yfinance` — no API key needed) and hosted for free on GitHub Pages.

It refreshes itself automatically every weekday morning before market open,
with no need to message anyone or run anything by hand.

## One-time setup (~10 minutes)

1. **Create a GitHub account** if you don't have one: https://github.com/join

2. **Create a new repository**
   - Go to https://github.com/new
   - Name it whatever you like (e.g. `my-wallstreet-favorites`)
   - Set it to **Public** (required for free GitHub Pages) — the dashboard
     itself contains no personal data, just stock rankings
   - Don't initialize with a README (we already have one)

3. **Upload these files to the repo**
   - Easiest way: on the new repo's page, click "uploading an existing file"
     and drag in this whole folder (keep the folder structure — the
     `.github/workflows/daily-rank.yml` path matters).
   - Or, if you're comfortable with git:
     ```
     git init
     git add .
     git commit -m "Initial commit"
     git branch -M main
     git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
     git push -u origin main
     ```

4. **Enable GitHub Pages**
   - In your repo, go to **Settings → Pages**
   - Under "Build and deployment", set **Source** to "Deploy from a branch"
   - Set **Branch** to `main` and folder to `/docs`, then Save
   - GitHub will give you a URL like
     `https://YOUR_USERNAME.github.io/YOUR_REPO/` — that's your dashboard link

5. **Run it once manually to generate real data**
   - Go to the **Actions** tab in your repo
   - Click "Daily stock ranking refresh" in the left sidebar
   - Click **Run workflow** → **Run workflow** (green button)
   - Wait ~2-3 minutes, then refresh your Pages URL — you should see real
     rankings instead of the placeholder page

That's it. From now on, it runs automatically every weekday at 11:00 UTC
(6-7 AM Eastern, before the market opens) and pushes the updated dashboard —
your bookmarked link always shows the latest data.

## Customizing

- **Universe**: edit the `TICKERS` list at the top of `rank_stocks.py` to
  add/remove stocks or point at a different list entirely (e.g. full S&P 500
  via a Wikipedia scrape — ask me if you want that swapped in).
- **Thresholds**: `MIN_MARKET_CAP`, `MIN_ANALYSTS`, `STEADY_CLIMBER_MAX_DD`,
  `DIVIDEND_MIN_STREAK_YEARS`, `MAX_SUSTAINABLE_PAYOUT` are all constants near
  the top of the script.
- **Conviction score weights**: in `compute_categories()`, the blend is
  `0.5 * upside + 0.3 * Stage 2 + 0.2 * buyback percentile` — adjust those
  weights to taste.
- **Schedule**: change the cron line in `.github/workflows/daily-rank.yml`
  if you want a different run time.

## Notes and limitations

- Analyst price targets, payout ratios, and buyback figures come from Yahoo
  Finance's data feed via `yfinance`. Coverage and freshness can vary by
  ticker and occasionally a field is missing — those tickers are skipped
  for that category rather than shown with bad data.
- This is a simplified, personal-use replica built from publicly described
  methodology, not an official or endorsed version of Wall Street Favorites.
- Nothing here is investment advice.
