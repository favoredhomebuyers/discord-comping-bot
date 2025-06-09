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

# Logger for debugging
logger = logging.getLogger("PricingDeptBot")

# --- Constants and Headers ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY = os.getenv("ATTOM_API_KEY")

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
        "latitude": coords.get("lat"),
        "longitude": coords.get("lng"),
        "address": coords.get("formatted"),
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

    if not all(subject_info.get(k) for k in ["sqft", "beds", "baths", "year"]):
        fb = await fetch_attom_fallback(subject_info, radius=0.1, count=1)
        if fb:
            prop = (fb[0].get("property") or [fb[0]])[0]
            subject_info.setdefault("sqft", (prop.get("building") or {}).get("size", {}).get("bldgsize"))
            subject_info.setdefault("beds", (prop.get("building") or {}).get("rooms", {}).get("beds"))
            subject_info.setdefault("baths", (prop.get("building") or {}).get("rooms", {}).get("bathstotal"))
            subject_info.setdefault("year", (prop.get("summary") or {}).get("yearbuilt"))

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
    for attempt in range(3):
        try:
            resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": 20})
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return []
            data = resp.json()
            # Debug: log raw payload
            logger.debug(f"[DEBUG raw payload] {json.dumps(data, indent=2)}")
            comps = data.get("comparables") or data.get("results") or []
            # Debug: log parsed comp count
            logger.info(f"[DEBUG] Parsed {len(comps)} comps from Zillow")
            return comps
        except httpx.RequestError:
            break
    return []

async def fetch_attom_fallback(subject: dict, radius: int = 10, count: int = 50) -> List[dict]:
    lat = subject.get("latitude")
    lon = subject.get("longitude")
    if lat is None or lon is None:
        return []

    # Transactions endpoint (validate path in ATTOM docs)
    tx_url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/transactions"
    params = {"latitude": lat, "longitude": lon, "radius": radius, "pageSize": count}
    try:
        resp = await client.get(tx_url, headers=A_HEADERS, params=params)
        if resp.status_code == 200:
            return resp.json().get("property", [])
    except httpx.RequestError:
        pass

    # Snapshot fallback
    snap_url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    try:
        resp2 = await client.get(snap_url, headers=A_HEADERS, params=params)
        if resp2.status_code == 200:
            return resp2.json().get("property", [])
    except httpx.RequestError:
        pass

    return []

async def fetch_price_and_tax_history(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/priceAndTaxHistory"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        return resp.json() if resp.status_code == 200 else {}
    except httpx.RequestError:
        return {}

def extract_last_sale(history: dict) -> Tuple[float, str]:
    events = history.get("priceAndTaxHistory") or history.get("history") or []
    sales = [e for e in events if e.get("eventType") == "Sale"]
    if not sales:
        return 0.0, ""
    latest = max(sales, key=lambda e: e.get("date", ""))
    return latest.get("price", 0.0), latest.get("date", "")

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    if not subject.get("latitude") or not subject.get("longitude"):
        return [], 0.0

    s_lat = float(subject["latitude"])
    s_lon = float(subject["longitude"])
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]
    cutoff = datetime.now() - timedelta(days=365)
    filtered: List[dict] = []

    for c in comps:
        is_attom = "identifier" in c
        prop = (c.get("property") or [c])[0] if is_attom else c
        cid = prop.get("zpid") or (c.get("identifier") or {}).get("attomId")
        if not cid:
            continue

        try:
            lat2 = float(prop.get("latitude") or prop.get("location", {}).get("latitude"))
            lon2 = float(prop.get("longitude") or prop.get("location", {}).get("longitude"))
            dist = haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)
        except:
            continue

        grade = next((g for r, g in tiers if dist <= r), None)
        if not grade:
            continue

        raw_date = (c.get("sale") or {}).get("saleDate") or prop.get("lastSoldDate")
        if not raw_date:
            continue

        try:
            if isinstance(raw_date, (int, float)):
                sd = datetime.utcfromtimestamp(raw_date / 1000)
            else:
                ds = str(raw_date).rstrip("Z")
                try:
                    sd = datetime.fromisoformat(ds)
                except:
                    sd = datetime.strptime(ds[:10], "%Y-%m-%d")
        except:
            continue

        if sd < cutoff:
            continue

        filtered.append({
            "id":         cid,
            "grade":      grade,
            "distance":   round(dist, 2),
            "sale_date":  sd.isoformat(),
        })

    return filtered, 0.0

async def get_comp_summary_by_zpid(
    zpid: str,
    subject: dict,
    manual_sqft: Optional[int] = None
) -> Tuple[List[dict], float, int]:
    if manual_sqft:
        subject["sqft"] = manual_sqft

    raw = await fetch_zillow_comps(zpid)
    if not raw:
        # Optionally re-enable ATTOM fallback here
        raw = []

    if not raw:
        return [], 0.0, subject.get("sqft") or 0

    comps, _ = get_clean_comps(subject, raw)

    # Enrich with last sale price & date
    for comp in comps:
        history = await fetch_price_and_tax_history(comp["id"])
        price, date = extract_last_sale(history)
        comp["last_sold_price"] = price
        comp["last_sold_date"] = date

    return comps, 0.0, subject.get("sqft") or 0
