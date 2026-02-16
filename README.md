# FT Sector Pipeline (US/KR/JP)

Automated preprocessing pipeline for model-ready sector classification data.

## What this builds

- US universe: Russell 3000 proxy membership from IWV holdings, sector labels from FT
- JP universe: TOPIX 500 membership from JPX file, sector labels from FT
- KR universe: KOSPI200 + KOSDAQ150 from KRX, WICS labels from Naver, mapped to ICB proxy
- Point-in-time history and current primary snapshot
- Country CSV exports with `stock_code` and `stock_name`

## Runtime constraints

- No headless browser
- FT parsing uses HTTP + deterministic parsing logic
- No LLM calls in runtime mapping path

## Requirements

- Python 3.11+
- `pandas`
- `pyarrow` (or `fastparquet`) for parquet I/O

Install minimal dependencies:

```bash
python -m pip install pandas pyarrow
```

## Run end-to-end build

```bash
python -m sector.pipeline.build \
  --as-of 2026-01-31 \
  --universes us:russell3000 kr:kospi200,kosdaq150 jp:topix500 \
  --kr-proxy-level level1,level3 \
  --primary-listing-only
```

## Run yearly historical backfill (free-source mode)

```bash
python -m sector.pipeline.backfill_yearly \
  --start-year 2010 \
  --end-year 2026 \
  --month-day 12-31
```

Outputs are organized by `as_of` year under `data/model_ready/yearly/<YYYY-MM-DD>/`.

Notes for free-source history:
- US uses iShares Russell holdings endpoint with `asOfDate` + short lookback.
- KR uses KRX constituents and WiseIndex WICS history with short lookback.
- JP uses a free proxy mode (`topix500_proxy`) for historical years.

## Quality checks

```bash
pytest -q

python -m sector.qa.check_schema \
  --input data/model_ready/sector_current_primary.parquet \
  --required security_id,issuer_id,ticker,stock_code,stock_name,exchange,country,universe,native_taxonomy_label,taxonomy_source,taxonomy_version,icb_proxy_level1,icb_proxy_level3,mapping_version,mapping_method,confidence_score,confidence_band,effective_from,effective_to,is_current,source_url,source_ts,run_id

python -m sector.qa.check_pit \
  --input data/normalized/sector_history_pit.parquet \
  --key security_id --from effective_from --to effective_to

python -m sector.qa.check_primary_dedupe \
  --input data/model_ready/sector_current_primary.parquet \
  --key issuer_id
```

## Key outputs

- `data/model_ready/sector_current_primary.parquet`
- `data/normalized/sector_history_pit.parquet`
- `data/model_ready/csv/us_sector_current.csv`
- `data/model_ready/csv/kr_sector_current.csv`
- `data/model_ready/csv/jp_sector_current.csv`

## Publish to GitHub

```bash
git init
git branch -M main
git add .
git commit -m "Initialize sector pipeline"

# option A: with gh CLI
gh repo create <repo-name> --private --source=. --remote=origin --push

# option B: manual remote
git remote add origin https://github.com/<your-id>/<repo-name>.git
git push -u origin main
```

Windows PowerShell helper (after `gh auth login`):

```powershell
.\scripts\publish_github.ps1 -RepoName <repo-name> -Visibility private
```
