"""
TalentFlow — Company Hiring Intelligence API
Powered by Apollo.io (API-agnostic, Proxycurl-swappable)
"""
import os
import httpx
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="TalentFlow", version="1.0.0")

APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "demo")
APOLLO_BASE = "https://api.apollo.io/v1"

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
    "paylocity": {
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
    key = company.lower().strip()
    data = MOCK_DATA.get(key, MOCK_DATA["paylocity"])

    # Scale counts by time window
    scale = months / 6
    sources = []
    total = 0
    for s in data["sources"]:
        count = max(1, round(s["count"] * scale))
        total += count
        sources.append({"company": s["company"], "count": count})

    result = []
    for s in sources:
        result.append(HireSource(
            company=s["company"],
            count=s["count"],
            percentage=round(s["count"] / total * 100, 1)
        ))
    result.sort(key=lambda x: x.count, reverse=True)

    return HiresResponse(
        company=company,
        time_window_months=months,
        total_hires_analyzed=total,
        sources=result,
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        is_demo=True
    )

# ─── Apollo.io integration ────────────────────────────────────────────────────

async def fetch_apollo_hires(company: str, months: int) -> HiresResponse:
    """
    Use Apollo /mixed_people/search to find recent hires at `company`,
    then aggregate their previous employers.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": APOLLO_API_KEY,
    }

    # We'll paginate up to 5 pages (100 results max per page)
    all_people = []
    page = 1
    per_page = 100

    async with httpx.AsyncClient(timeout=30.0) as client:
        # First: resolve company domain/id via organization search
        org_resp = await client.post(
            f"{APOLLO_BASE}/mixed_companies/search",
            headers=headers,
            json={"q_organization_name": company, "per_page": 1}
        )
        if org_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Apollo org search failed: {org_resp.status_code}"
            )
        org_data = org_resp.json()
        orgs = org_data.get("organizations", [])
        if not orgs:
            raise HTTPException(
                status_code=404,
                detail=f"Company '{company}' not found in Apollo"
            )

        org_id = orgs[0].get("id")
        org_name = orgs[0].get("name", company)

        while page <= 5:
            payload = {
                "q_organization_name": org_name,
                "organization_ids": [org_id],
                "per_page": per_page,
                "page": page,
                "person_seniority": [],
                "include_past_organizations": True,
            }
            resp = await client.post(
                f"{APOLLO_BASE}/mixed_people/search",
                headers=headers,
                json=payload
            )
            if resp.status_code == 429:
                raise HTTPException(status_code=429, detail="Apollo rate limit hit")
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Apollo people search failed: {resp.status_code}"
                )
            data = resp.json()
            people = data.get("people", [])
            if not people:
                break
            all_people.extend(people)
            total_pages = data.get("pagination", {}).get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1

    # Filter to recent hires and extract previous employers
    prev_employer_counts: dict[str, int] = defaultdict(int)
    hire_count = 0

    for person in all_people:
        employment_history = person.get("employment_history", [])
        if not employment_history:
            continue

        # Sort by start_date descending to find current role
        sorted_history = sorted(
            [e for e in employment_history if e.get("start_date")],
            key=lambda e: e.get("start_date", ""),
            reverse=True
        )

        if not sorted_history:
            continue

        current = sorted_history[0]
        current_org = (current.get("organization_name") or "").lower()
        current_start = current.get("start_date", "")

        # Check if current role is at the target company and started after cutoff
        if (
            org_name.lower() not in current_org
            and company.lower() not in current_org
        ):
            continue

        if current_start < cutoff_str:
            continue

        hire_count += 1

        # Get the role BEFORE the current one
        if len(sorted_history) < 2:
            continue

        prev = sorted_history[1]
        prev_company = prev.get("organization_name", "").strip()
        if prev_company and prev_company.lower() != org_name.lower():
            prev_employer_counts[prev_company] += 1

    if hire_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No recent hires found at '{company}' in the last {months} months"
        )

    total_with_prev = sum(prev_employer_counts.values())

    sources = []
    for emp, count in sorted(prev_employer_counts.items(), key=lambda x: x[1], reverse=True):
        sources.append(HireSource(
            company=emp,
            count=count,
            percentage=round(count / max(hire_count, 1) * 100, 1)
        ))

    return HiresResponse(
        company=org_name,
        time_window_months=months,
        total_hires_analyzed=hire_count,
        sources=sources[:20],  # top 20
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        is_demo=False
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "demo_mode": APOLLO_API_KEY in ("", "demo")}


@app.get("/api/hires", response_model=HiresResponse)
async def get_hires(
    company: str = Query(..., description="Company name to analyze"),
    months: int = Query(6, ge=1, le=24, description="Time window in months")
):
    """Return hiring sources breakdown for a company."""
    if not company.strip():
        raise HTTPException(status_code=400, detail="Company name required")

    # Demo mode
    if APOLLO_API_KEY in ("", "demo"):
        return get_mock_response(company, months)

    return await fetch_apollo_hires(company.strip(), months)


# ─── Static frontend ──────────────────────────────────────────────────────────

UI_DIR = os.path.join(os.path.dirname(__file__), "..", "ui", "dist")

if os.path.exists(UI_DIR):
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="static")
