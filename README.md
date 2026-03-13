# TalentFlow 🔍

**Company Hiring Intelligence** — See where a company's recent hires came from.

## What It Does

Enter any company name and TalentFlow shows you:
- Who they've been hiring recently
- Where those new hires came from (previous employer breakdown)
- Ranked list with percentages and counts
- Time window: 3mo / 6mo / 12mo / custom

## Tech Stack

- **Backend:** FastAPI (Python 3.11)
- **Frontend:** Vanilla HTML/JS (no build step)
- **Data:** Apollo.io People Search API
- **Deployment:** Cloud Run (GCP)

## Local Dev

```bash
# Install deps
pip install -r api/requirements.txt

# Run (demo mode — no API key needed)
uvicorn api.main:app --reload --port 8080

# With real Apollo key
APOLLO_API_KEY=your_key uvicorn api.main:app --reload --port 8080
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APOLLO_API_KEY` | `demo` | Apollo.io API key. If unset or "demo", returns mock data for Paylocity |

## Demo Mode

Without an Apollo API key, the app returns realistic mock data for "Paylocity" so the UI is fully demonstrable.

## Swapping Data Providers

The API layer is designed to be provider-agnostic. To swap Apollo for Proxycurl:
1. Implement a `fetch_proxycurl_hires()` function in `api/main.py`
2. Route to it based on a `DATA_PROVIDER` env var

## Deployment

Pushed to `main` → GitHub Actions builds + deploys to Cloud Run automatically.
