import os
import httpx
import asyncio
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta

from .zpid_finder import find_zpid_by_address_async

# Zillow RapidAPI configuration
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}

# Shared HTTP client
client = httpx.AsyncClient(timeout=30.0)

async def get_subject_data(zpid: str) -> Optional[Dict]:
    """
    Fetches the subject property details (including livingArea) from Zillow.
    """
    url = f"https://{ZILLOW_HOST}/property"
    resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
    resp.raise_for_status()
    return resp.json().get("hdpData", {}).get("homeInfo")

async def fetch_zillow_comps(zpid: str, count: int = 20) -> List[Dict]:
    """
    Retrieves comparable sales (comps) for a given ZPID, with simple 429 retry logic.
    """
    url = f"https://{ZILLOW_HOST}/propertyComps"
    for attempt in range(3):
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
        if resp.status_code == 429:
            await asyncio.sleep(1)
            continue
        resp.raise_for_status()
        return resp.json().get("comps", [])
    return []

async def fetch_sale_info(zpid: int) -> Tuple[Optional[float], Optional[datetime]]:
    """
    Fetches the sale price and sale date for a given ZPID via the /property endpoint,
    with 429 retry logic.
    """
    url = f"https://{ZILLOW_HOST}/property"
    for attempt in range(3):
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        if resp.status_code == 429:
            await asyncio.sleep(1)
            continue
        resp.raise_for_status()
        home_info = resp.json().get("hdpData", {}).get("homeInfo", {})
        price = home_info.get("price")
        date_str = home_info.get("dateSold")
        date_sold = None
        if date_str:
            try:
                date_sold = datetime.fromisoformat(date_str)
            except ValueError:
                pass
        return price, date_sold
    return None, None


def grade_and_filter(comps: List[Dict]) -> List[Dict]:
    """
    Placeholder for your existing distance/grade logic.
    Should return a list of comps each with keys: 'id' or 'zpid', 'livingArea', etc.
    """
    # --- your grading/distance code goes here ---
    return comps

async def get_comp_summary_by_zpid(
    zpid: str
) -> Optional[Tuple[List[Dict], float, float]]:
    """
    Main entrypoint: for a subject ZPID, fetches and filters comps,
    pulls sale dates & prices, excludes older than 12 months,
    and then returns:
      (filtered_comps, avg_price_per_sqft, subject_living_area)
    """
    raw_comps = await fetch_zillow_comps(zpid)
    if not raw_comps:
        return None

    # apply your grading/distance filters
    candidates = grade_and_filter(raw_comps)

    cutoff = datetime.utcnow() - timedelta(days=365)
    valid: List[Dict] = []
    for comp in candidates:
        comp_id = comp.get("id") or comp.get("zpid")
        if not comp_id:
            continue
        price, sold_date = await fetch_sale_info(comp_id)
        if price and sold_date and sold_date >= cutoff:
            comp["sold_price"] = price
            comp["sold_date"] = sold_date
            valid.append(comp)

    if not valid:
        return None

    # average $/sqft across filtered comps
    psf_values = [c["sold_price"] / c.get("livingArea", 1) for c in valid if c.get("livingArea")]
    avg_psf = sum(psf_values) / len(psf_values)

    # get subject living area
    subj = await get_subject_data(zpid)
    subj_sqft = subj.get("livingArea") if subj else 0

    return valid, avg_psf, subj_sqft
