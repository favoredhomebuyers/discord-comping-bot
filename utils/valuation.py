# utils/valuation.py
import os
import asyncio
import httpx
from haversine import haversine, Unit
from typing import List, Tuple
from datetime import datetime, timedelta
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# --- Constants and Headers ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

client = httpx.AsyncClient(timeout=30.0)

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    zpid = await find_zpid_by_address_async(address)
    subject_info = {}
    subj_ids = {}

    gmaps_info = get_coordinates(address)
    if gmaps_info:
        subject_info.update({
            "latitude": gmaps_info.get("lat"),
            "longitude": gmaps_info.get("lng"),
            "address_components": gmaps_info.get("components")
        })
    else:
        return {}, {}

    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        if details:
            subject_info.update({
                "sqft": details.get("livingArea"),
                "beds": details.get("bedrooms"),
                "baths": details.get("bathrooms"),
                "year": details.get("yearBuilt"),
            })
            
    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        return resp.json() if resp.status_code == 200 else {}
    except httpx.RequestError: return {}

async def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    details = await fetch_property_details(zpid)
    if isinstance(details.get("comps"), list) and details["comps"]:
        print(f"[INFO VAL] Found {len(details['comps'])} comps in Zillow property details.")
        return details["comps"]

    print("[INFO VAL] No comps in property details, trying dedicated /propertyComps endpoint.")
    url = f"https://{ZILLOW_HOST}/propertyComps"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
        if resp.status_code != 200: return []
        data = resp.json()
        return data.get("results", []) or data.get("comparables", [])
    except httpx.RequestError: return []

async def fetch_attom_comps_fallback(subject: dict, radius: int = 2, count: int = 20) -> List[dict]:
    components = subject.get("address_components")
    if not components: return []
    street, city, state = components.get("street"), components.get("city"), components.get("state")
    if not all([street, city, state]): return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/comparables"
    params = {"address1": street, "address2": f"{city}, {state}", "radius": radius, "count": count}
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM comparables failed: {resp.status_code} - {resp.text}")
            return []
        return resp.json().get("compProperties") or []
    except httpx.RequestError: return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    if not subject.get("latitude") or not subject.get("sqft") or not subject.get("year"):
        print("[ERROR VAL] Subject property missing key data (lat/sqft/year). Cannot filter comps.")
        return [], 0.0

    s_lat, s_lon, actual_sqft, actual_year = float(subject["latitude"]), float(subject["longitude"]), subject["sqft"], subject["year"]
    one_year_ago = datetime.now() - timedelta(days=365)
    chosen, chosen_ids = [], set()
    
    for comp in comps:
        if len(chosen) >= 3: break
        
        # --- Universal Data Parser for Zillow & Attom ---
        prop = comp.get("propertySummary") or comp # Attom data is nested
        
        comp_id = prop.get("zpid") or prop.get("attomId")
        if not comp_id or comp_id in chosen_ids: continue

        sold_date_str = prop.get("lastSoldDate") or (prop.get("lastSale") or {}).get("saleDate")
        if sold_date_str:
            try:
                sale_date = datetime.fromtimestamp(sold_date_str / 1000) if isinstance(sold_date_str, int) else datetime.strptime(sold_date_str.split('T')[0], "%Y-%m-%d")
                if sale_date < one_year_ago: continue
            except (ValueError, TypeError): continue
        else: continue

        sqft = prop.get("livingArea") or (prop.get("building", {}).get("size", {}) or {}).get("sqFtLiving")
        if not sqft or abs(sqft - actual_sqft) > 400: continue

        year = prop.get("yearBuilt") or (prop.get("building", {}) or {}).get("yearBuilt")
        if not year or abs(year - actual_year) > 20: continue

        sold_price = prop.get("lastSoldPrice") or (prop.get("lastSale") or {}).get("saleAmt")
        if not sold_price: continue

        chosen.append(comp)
        chosen_ids.add(comp_id)

    if not chosen: return [], 0.0

    # --- Format final results ---
    psfs = []
    formatted = []
    for comp in chosen:
        prop = comp.get("propertySummary") or comp
        sold = prop.get("lastSoldPrice") or (prop.get("lastSale") or {}).get("saleAmt")
        sqft = prop.get("livingArea") or (prop.get("building", {}).get("size", {}) or {}).get("sqFtLiving")
        psf = sold / sqft
        psfs.append(psf)
        
        formatted.append({
            "address": prop.get("address", {}).get("oneLine") or prop.get("address", {}).get("streetAddress"),
            "sold_price": int(sold), "sqft": int(sqft), "psf": round(psf, 2),
            "zillow_url": f"https://www.zillow.com/homedetails/{prop.get('zpid')}_zpid/" if prop.get('zpid') else "#",
            "grade": "A+" # Placeholder, can be refined with distance tiers later if needed
        })
        
    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    return formatted, avg_psf

async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft: subject["sqft"] = manual_sqft
        
    raw_comps = []
    if subj_ids.get("zpid"):
        raw_comps = await fetch_zillow_comps(subj_ids["zpid"])
            
    if not raw_comps:
        print("[INFO VAL] Zillow returned no comps, trying ATTOM fallback.")
        raw_comps = await fetch_attom_comps_fallback(subject)

    if not raw_comps:
        return [], 0.0, subject.get("sqft") or 0

    clean_comps, avg_psf = get_clean_comps(subject, raw_comps)
           
    return clean_comps, avg_psf, subject.get("sqft") or 0
