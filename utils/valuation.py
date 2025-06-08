# utils/valuation.py
import os
import asyncio
import httpx
from haversine import haversine, Unit
from typing import List, Tuple
from datetime import datetime
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
        if resp.status_code != 200: return {}
        return resp.json()
    except httpx.RequestError: return {}

async def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    """
    Fetches comps from Zillow using a robust two-step process.
    """
    # Step 1: Check for comps embedded in the main property details.
    details = await fetch_property_details(zpid)
    if isinstance(details.get("comps"), list) and details["comps"]:
        print(f"[INFO VAL] Found {len(details['comps'])} comps in Zillow property details.")
        return details["comps"][:count]

    # Step 2: If no comps found, try the dedicated /propertyComps endpoint as a fallback.
    print("[INFO VAL] No comps in property details, trying dedicated /propertyComps endpoint.")
    url = f"https://{ZILLOW_HOST}/propertyComps"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
        if resp.status_code != 200:
            print(f"[WARNING VAL] Zillow /propertyComps endpoint failed: {resp.status_code}")
            return []
        data = resp.json()
        for key in ("results", "comparables", "comps"):
            if isinstance(data.get(key), list):
                print(f"[INFO VAL] Found {len(data[key])} comps from Zillow /propertyComps endpoint.")
                return data[key]
        return []
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error fetching Zillow propertyComps: {e}")
        return []

async def fetch_attom_comps(subject: dict, radius: int = 2) -> List[dict]:
    components = subject.get("address_components")
    if not components: return []

    street = components.get("street")
    city = components.get("city")
    state = components.get("state") # Correctly define state
    postal_code = components.get("postal_code")

    if not all([street, city, state, postal_code]): return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/comparables"
    params = {"address1": street, "address2": f"{city}, {state}", "radius": radius}
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM comparables failed: {resp.status_code} - {resp.text}")
            return []
        data = resp.json()
        return data.get("compProperties") or []
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error fetching ATTOM comps: {e}")
        return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    try:
        s_lat = float(subject.get("latitude"))
        s_lon = float(subject.get("longitude"))
    except (ValueError, TypeError): return [], 0.0

    actual_sqft = subject.get("sqft")
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]
    chosen = []
    
    for radius, grade in tiers:
        if len(chosen) >= 3: break
        for comp in comps:
            # Universal parser for Zillow and ATTOM data structures
            prop_details = comp.get("propertySummary") or comp
            
            sold = prop_details.get("lastSoldPrice") or (prop_details.get("lastSale") or {}).get("saleAmt")
            sqft = prop_details.get("livingArea") or (prop_details.get("building", {}).get("size", {}) or {}).get("sqFtLiving")

            if not sold or not sqft: continue
            
            comp_id = prop_details.get("zpid") or (prop_details.get("attomId"))
            if any(c.get('id') == comp_id for c in chosen if c.get('id')): continue
            
            try:
                lat2 = float(prop_details.get("latitude") or (prop_details.get("location", {}) or {}).get("latitude"))
                lon2 = float(prop_details.get("longitude") or (prop_details.get("location", {}) or {}).get("longitude"))
            except (ValueError, TypeError): continue

            distance = haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)
            if distance > radius: continue
            
            chosen.append({**comp, "id": comp_id, "grade": grade, "distance": distance})
            if len(chosen) >= 3: break
    
    psfs = []
    formatted = []
    for comp in sorted(chosen, key=lambda x: x["distance"]):
        prop_details = comp.get("propertySummary") or comp
        
        sold = prop_details.get("lastSoldPrice") or (prop_details.get("lastSale") or {}).get("saleAmt")
        sqft = prop_details.get("livingArea") or (prop_details.get("building", {}) or {}).get("size", {}).get("sqFtLiving")
        
        psf = sold / sqft if sqft and sold else None
        if psf: psfs.append(psf)
        
        comp_address = prop_details.get("address", {})
        
        formatted.append({
            "address": comp_address.get("oneLine") or prop_details.get("streetAddress"),
            "sold_price": int(sold), "sqft": int(sqft), "psf": round(psf, 2) if psf else None,
            "zillow_url": f"https://www.zillow.com/homedetails/{prop_details.get('zpid')}_zpid/" if prop_details.get('zpid') else "#",
            "grade": comp.get("grade"),
            "yearBuilt": prop_details.get("yearBuilt") or (prop_details.get("building", {}) or {}).get("yearBuilt"),
            "beds": prop_details.get("bedrooms") or (prop_details.get("building", {}) or {}).get("rooms", {}).get("beds"),
            "baths": prop_details.get("bathrooms") or (prop_details.get("building", {}) or {}).get("rooms", {}).get("bathstotal"),
        })
        
    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    return formatted, avg_psf

async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft: subject["sqft"] = manual_sqft
        
    raw_comps = []
    if subj_ids.get("zpid"):
        raw_comps.extend(await fetch_zillow_comps(subj_ids["zpid"]))
            
    if not raw_comps:
        print("[INFO VAL] Zillow returned no comps, trying ATTOM.")
        raw_comps.extend(await fetch_attom_comps(subject))

    if not raw_comps:
        return [], 0.0, subject.get("sqft") or 0

    clean_comps, avg_psf = get_clean_comps(subject, raw_comps)
           
    return clean_comps, avg_psf, subject.get("sqft") or 0
