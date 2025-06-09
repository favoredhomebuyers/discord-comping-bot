# utils/valuation.py
import os
import asyncio
import httpx
import json
import logging
from haversine import haversine, Unit
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timedelta
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

logger = logging.getLogger("PricingDeptBot")

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST  = os.getenv("ATTOM_HOST",    "api.gateway.attomdata.com")
ATTOM_KEY   = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

client = httpx.AsyncClient(timeout=30.0)

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    logger.info(f"[DEBUG] 🏷 Looking up ZPID for address: {address}")
    zpid = await find_zpid_by_address_async(address)
    logger.info(f"[DEBUG] 🔍 zpid → {zpid}")

    subject_info: Dict[str, Any] = {}
    subj_ids: Dict[str, str] = {}

    coords = get_coordinates(address)
    if not coords:
        return {}, {}
    subject_info.update({
        "latitude":  coords["lat"],
        "longitude": coords["lng"],
        "address":   coords["formatted"],
    })

    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        subject_info.update({
            "sqft": details.get("livingArea"),
            "beds": details.get("bedrooms"),
            "baths": details.get("bathrooms"),
            "year": details.get("yearBuilt"),
        })

    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    for attempt in range(3):
        try:
            resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            return resp.json() if resp.status_code == 200 else {}
        except httpx.RequestError:
            break
    return {}

async def fetch_zillow_comps(zpid: str) -> List[dict]:
    logger.info(f"[DEBUG] ▶️ fetch_zillow_comps called with zpid={zpid}")
    url = f"https://{ZILLOW_HOST}/propertyComps"
    resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": 20})
    if resp.status_code != 200:
        return []
    data = resp.json()
    logger.info(f"[DEBUG raw payload] {json.dumps(data, indent=2)}")

    # dynamic extract
    if isinstance(data, list):
        comps = data
    else:
        comps = data.get("comps") or data.get("comparables") or data.get("results") or []
        if not comps:
            for v in data.values():
                if isinstance(v, list):
                    comps = v
                    break

    logger.info(f"[DEBUG] Parsed {len(comps)} comps from Zillow")
    return comps or []

async def fetch_price_and_tax_history(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/priceAndTaxHistory"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        return resp.json() if resp.status_code == 200 else {}
    except:
        return {}

def extract_last_sale(history: dict) -> Tuple[float, datetime]:
    events = history.get("priceAndTaxHistory", []) + history.get("history", [])
    sales = [e for e in events if e.get("eventType") == "Sale"]
    if not sales:
        return 0.0, None
    latest = max(sales, key=lambda e: e.get("date", ""))
    date_str = latest.get("date", "")
    # parse into datetime
    dt = datetime.fromisoformat(date_str.rstrip("Z")) if date_str else None
    return latest.get("price", 0.0), dt

def get_clean_comps(subject: dict, raw_comps: List[dict]) -> List[Dict[str, Any]]:
    """
    Only grade & distance filtering here.
    """
    if not subject.get("latitude") or not subject.get("longitude"):
        return []

    s_lat = float(subject["latitude"])
    s_lon = float(subject["longitude"])
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]

    out = []
    seen = set()
    for c in raw_comps:
        z = c.get("zpid") or (c.get("identifier") or {}).get("attomId")
        if not z or z in seen:
            continue

        # get lat/lon
        prop = (c.get("property") or [c])[0]
        try:
            lat2 = float(prop.get("latitude") or prop.get("location", {}).get("latitude"))
            lon2 = float(prop.get("longitude") or prop.get("location", {}).get("longitude"))
        except:
            continue

        dist = haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)
        grade = next((g for r, g in tiers if dist <= r), None)
        if not grade:
            continue

        seen.add(z)
        out.append({
            "id":       z,
            "grade":    grade,
            "distance": round(dist, 2),
        })

    # take top 3 closest
    out = sorted(out, key=lambda x: x["distance"])[:3]
    return out

async def get_comp_summary_by_zpid(
    zpid: str,
    subject: dict,
    manual_sqft: Optional[int] = None
) -> Tuple[List[dict], float, int]:
    if manual_sqft:
        subject["sqft"] = manual_sqft

    raw = await fetch_zillow_comps(zpid)
    if not raw:
        return [], 0.0, subject.get("sqft") or 0

    # 1) grade & distance only
    candidates = get_clean_comps(subject, raw)

    # 2) enrich with history & filter by last‐sale date
    cutoff = datetime.now() - timedelta(days=365)
    final = []
    psf_list = []
    for c in candidates:
        history = await fetch_price_and_tax_history(c["id"])
        price, sale_dt = extract_last_sale(history)
        if not sale_dt or sale_dt < cutoff:
            continue
        sqft = subject.get("sqft") or 0
        psf_list.append(price / sqft if sqft else 0)
        final.append({
            **c,
            "last_sold_price": price,
            "last_sold_date":  sale_dt.date().isoformat(),
            "psf":             round(price / sqft, 2) if sqft else None,
        })

    avg_psf = sum(psf_list) / len(psf_list) if psf_list else 0.0
    return final, avg_psf, subject.get("sqft") or 0
