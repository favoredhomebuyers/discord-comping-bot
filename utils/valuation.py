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

client = httpx.AsyncClient(timeout=20.0)

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print(f"[DEBUG VAL] get_subject_data: address={address}")
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
        print(f"[ERROR VAL] Could not geocode address: {address}. Cannot proceed.")
        return {}, {}

    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        info = details.get("hdpData", {}).get("homeInfo") or details.get("homeInfo") or details
        if info:
            subject_info.update({
                "sqft": info.get("livingArea") or info.get("homeSize"),
                "beds": info.get("bedrooms"),
                "baths": info.get("bathrooms"),
                "year": info.get("yearBuilt"),
            })
            
    # Fallback to ATTOM for subject details if needed
    if not subject_info.get("beds") or not subject_info.get("sqft"):
        print("[INFO VAL] Zillow details incomplete, trying ATTOM for subject details...")
        # (This part can be enhanced later if needed)

    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        if resp.status_code != 200: return {}
        return resp.json()
    except httpx.RequestError: return {}


async def fetch_zillow_comps(zpid: str, count: int = 50) -> List[dict]:
    details = await fetch_property_details(zpid)
    if isinstance(details.get("comps"), list):
        print(f"[DEBUG VAL] Using {len(details['comps'])} comps from Zillow property details")
        return details["comps"][:count]
    return []

async def fetch_attom_comps(subject: dict, radius: int = 1, count: int = 20) -> List[dict]:
    """
    Fetches comps from the ATTOM /comparables/v1 endpoint, which is designed for this purpose.
    """
    components = subject.get("address_components")
    if not components: return []

    street = components.get("street")
    city = components.get("city")
    state = components.get("state")
    postal_code = components.get("postal_code")

    if not all([street, city, state, postal_code]): return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/property/comparables"
    params = {
        "address1": street,
        "address2": f"{city}, {state} {postal_code}",
        "radius": radius,
        "count": count
    }
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM comparables failed: {resp.status_code} - {resp.text}")
            return []
        data = resp.json()
        return data.get("property") or []
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
            # Skip if already chosen
            if any(c.get('zpid') == comp.get('zpid') for c in chosen if c.get('zpid') and comp.get('zpid')): continue
            if any(c.get('attomId') == comp.get('attomId') for c in chosen if c.get('attomId') and comp.get('attomId')): continue

            # For ATTOM data, the property details are nested
            prop_details = (comp.get("propertySummary") or comp)

            comp_sold = prop_details.get("lastSale", {}).get("saleAmt")
            comp_sqft = prop_details.get("building", {}).get("size", {}).get("sqFtLiving")

            if not comp_sold or not comp_sqft: continue
            
            try:
                lat2 = float(prop_details.get("location", {}).get("latitude"))
                lon2 = float(prop_details.get("location", {}).get("longitude"))
            except (ValueError, TypeError): continue

            distance = haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)
            if distance > radius: continue
            
            # (Filtering logic)
            chosen.append({**comp, "grade": grade, "distance": distance})
            if len(chosen) >= 3: break
    
    psfs = []
    formatted = []
    for comp in sorted(chosen, key=lambda x: x["distance"]):
        prop_details = (comp.get("propertySummary") or comp)
        
        sold = prop_details.get("lastSale", {}).get("saleAmt")
        sqft = prop_details.get("building", {}).get("size", {}).get("sqFtLiving")
        
        psf = sold / sqft if sqft and sold else None
        if psf: psfs.append(psf)
        
        comp_address = prop_details.get("address", {})
        
        formatted.append({
            "address": comp_address.get("oneLine"),
            "sold_price": int(sold), "sqft": int(sqft), "psf": round(psf, 2) if psf else None,
            "zillow_url": f"https://www.zillow.com/homedetails/{prop_details.get('zpid')}_zpid/" if prop_details.get('zpid') else "#",
            "grade": comp.get("grade"),
            "yearBuilt": prop_details.get("building", {}).get("yearBuilt"),
            "beds": prop_details.get("building", {}).get("rooms", {}).get("beds"),
            "baths": prop_details.get("building", {}).get("rooms", {}).get("bathstotal"),
        })
        
    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    return formatted, avg_psf

async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft: subject["sqft"] = manual_sqft
        
    raw_comps = []
    if subj_ids.get("zpid"):
        raw_comps.extend(await fetch_zillow_comps(subj_ids["zpid"]))
            
    raw_comps.extend(await fetch_attom_comps(subject))

    if not raw_comps:
        return [], 0.0, subject.get("sqft") or 0

    clean_comps, avg_psf = get_clean_comps(subject, raw_comps)
           
    return clean_comps, avg_psf, subject.get("sqft") or 0
