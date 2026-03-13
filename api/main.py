"""
TalentFlow — Company Hiring Intelligence API
Powered by EnrichLayer (Proxycurl-compatible)
"""
import os
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="TalentFlow", version="1.1.0")

ENRICHLAYER_KEY = os.getenv("ENRICHLAYER_KEY", "demo")
ENRICHLAYER_BASE = "https://enrichlayer.com/api/v2"

# ─── Models ───────────────────────────────────────────────────────────────────

class HireSource(BaseModel):
    company: str
    count: int
    percentage: float

class HiresResponse(BaseModel):
    company: str
    time_window_months: int
    total_hires_analyzed: int
    sources: list[HireSource]
    as_of: str
    is_demo: bool = False

# ─── Mock data ────────────────────────────────────────────────────────────────

MOCK_DATA = {
    "default": {
        "sources": [
            {"company": "ADP", "count": 18},
            {"company": "Workday", "count": 14},
            {"company": "Paychex", "count": 11},
            {"company": "SAP SuccessFactors", "count": 9},
            {"company": "UKG (Ultimate Kronos Group)", "count": 8},
            {"company": "Ceridian HCM", "count": 7},
            {"company": "BambooHR", "count": 5},
            {"company": "Rippling", "count": 4},
            {"company": "Gusto", "count": 3},
            {"company": "Namely", "count": 2},
        ],
        "total": 81,
    }
}

def get_mock_response(company: str, months: int) -> HiresResponse:
    data = MOCK_DATA["default"]
    scale = months / 6
    sources = []
    total = 0
    for s in data["sources"]:
        count = max(1, round(s["count"] * scale))
        total += count
        sources.append(HireSource(
            company=s["company"],
            count=count,
            percentage=round(count / max(total, 1) * 100, 1)
        ))
    # Recalculate percentages with final total
    for s in sources:
        s.percentage = round(s.count / total * 100, 1)
    sources.sort(key=lambda x: x.count, reverse=True)
    return HiresResponse(
        company=company,
        time_window_months=months,
        total_hires_analyzed=total,
        sources=sources,
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        is_demo=True
    )

# ─── EnrichLayer integration ──────────────────────────────────────────────────

async def resolve_company_url(client: httpx.AsyncClient, company_name: str) -> str:
    """Resolve company name to LinkedIn URL."""
    resp = await client.get(
        f"{ENRICHLAYER_BASE}/company/resolve",
        params={"company_name": company_name},
        headers={"Authorization": f"Bearer {ENRICHLAYER_KEY}"}
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found")
    data = resp.json()
    url = data.get("url", "")
    if not url:
        raise HTTPException(status_code=404, detail=f"Company '{company_name}' not found")
    return url

async def get_employee_profiles(client: httpx.AsyncClient, company_url: str, limit: int = 50) -> list[str]:
    """Get a list of employee LinkedIn profile URLs."""
    resp = await client.get(
        f"{ENRICHLAYER_BASE}/company/employees",
        params={
            "url": company_url,
            "sort_by": "recently_joined",
            "page": 1,
        },
        headers={"Authorization": f"Bearer {ENRICHLAYER_KEY}"}
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    employees = data.get("employees", [])
    return [e.get("linkedin_profile_url", e.get("profile_url", "")) for e in employees[:limit] if e.get("linkedin_profile_url") or e.get("profile_url")]

async def get_person_experiences(client: httpx.AsyncClient, profile_url: str) -> list[dict]:
    """Fetch a person's experience history."""
    await asyncio.sleep(1.1)  # respect ~1 req/sec rate limit
    resp = await client.get(
        f"{ENRICHLAYER_BASE}/profile",
        params={"linkedin_profile_url": profile_url, "use_cache": "if-present"},
        headers={"Authorization": f"Bearer {ENRICHLAYER_KEY}"}
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("experiences", [])

def parse_experience_date(exp: dict) -> Optional[datetime]:
    """Parse start date from experience dict."""
    starts = exp.get("starts_at") or {}
    try:
        y = starts.get("year")
        m = starts.get("month") or 1
        d = starts.get("day") or 1
        if y:
            return datetime(y, m, d, tzinfo=timezone.utc)
    except Exception:
        pass
    return None

async def fetch_enrichlayer_hires(company: str, months: int) -> HiresResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Resolve company LinkedIn URL
        company_url = await resolve_company_url(client, company)

        # Step 2: Get recently joined employees
        profile_urls = await get_employee_profiles(client, company_url, limit=40)
        if not profile_urls:
            return get_mock_response(company, months)

        # Step 3: For each profile, get experience history
        prev_employers: dict[str, int] = defaultdict(int)
        analyzed = 0

        for url in profile_urls:
            if not url:
                continue
            try:
                experiences = await get_person_experiences(client, url)
                if not experiences:
                    continue

                # Sort experiences by start date descending (most recent first)
                dated = [(parse_experience_date(e), e) for e in experiences]
                dated = [(dt, e) for dt, e in dated if dt is not None]
                dated.sort(key=lambda x: x[0], reverse=True)

                if not dated:
                    continue

                # Most recent job: started after cutoff = recent hire
                current_start, current_exp = dated[0]
                if current_start < cutoff:
                    continue  # Not a recent hire

                analyzed += 1

                # Previous employer: second most recent
                if len(dated) >= 2:
                    _, prev_exp = dated[1]
                    prev_company = prev_exp.get("company", "").strip()
                    if prev_company:
                        prev_employers[prev_company] += 1

            except Exception:
                continue

        if analyzed == 0 or not prev_employers:
            return get_mock_response(company, months)

        total = sum(prev_employers.values())
        sources = [
            HireSource(
                company=co,
                count=cnt,
                percentage=round(cnt / total * 100, 1)
            )
            for co, cnt in sorted(prev_employers.items(), key=lambda x: x[1], reverse=True)[:15]
        ]

        return HiresResponse(
            company=company,
            time_window_months=months,
            total_hires_analyzed=analyzed,
            sources=sources,
            as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            is_demo=False
        )

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/hires", response_model=HiresResponse)
async def get_hires(
    company: str = Query(..., description="Company name to look up"),
    months: int = Query(6, ge=1, le=24, description="Time window in months")
):
    if ENRICHLAYER_KEY == "demo":
        return get_mock_response(company, months)
    try:
        return await fetch_enrichlayer_hires(company, months)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health():
    return {"status": "ok", "data_source": "enrichlayer" if ENRICHLAYER_KEY != "demo" else "demo"}

# ─── Static frontend ──────────────────────────────────────────────────────────

@app.get("/")
async def serve_root():
    return FileResponse("ui/dist/index.html")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    # Only serve index.html for non-API routes
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse("ui/dist/index.html")
