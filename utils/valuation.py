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


async def fetch_zillow_comps(zpid: str) -> List[dict]:
    details = await fetch_property_details(zpid)
    if isinstance(details.get("comps"), list):
        return details.get("comps", [])
    return []

async def fetch_attom_comps_fallback(subject: dict, radius: int = 5, count: int = 50) -> List[dict]:
    lat = subject.get("latitude")
    lon = subject.get("longitude")
    if not lat or not lon: return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    params = {"latitude": lat, "longitude": lon, "radius": radius, "pageSize": count}
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM fallback failed: {resp.status_code} - {resp.text}")
            return []
        return resp.json().get("property", [])
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error on ATTOM fallback: {e}")
        return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    try:
        s_lat = float(subject.get("latitude"))
        s_lon = float(subject.get("longitude"))
    except (ValueError, TypeError): return [], 0.0

    actual_sqft = subject.get("sqft")
    chosen = []
    
    for comp_data in comps:
        if len(chosen) >= 3: break
        
        prop_details = (comp_data.get("property") or [comp_data])[0]
        
        # Using the correct JSON Paths from your documentation
        sold = prop_details.get("lastSoldPrice") or (comp_data.get("sale", {}).get("amount", {}) or {}).get("saleAmt")
        sqft = prop_details.get("livingArea") or (prop_details.get("building", {}).get("size", {}) or {}).get("livingsize")

        if not sold or not sqft: continue
            
        comp_id = prop_details.get("zpid") or (prop_details.get("identifier") or {}).get("attomId")
        if any(c.get('id') == comp_id for c in chosen if c.get('id')): continue
        
        chosen.append({**comp_data, "id": comp_id})
        if len(chosen) >= 3: break
    
    psfs = []
    formatted = []
    for comp in chosen:
        prop_details = (comp.get("property") or [comp])[0]
        sold = prop_details.get("lastSoldPrice") or (comp.get("sale", {}).get("amount", {}) or {}).get("saleAmt")
        sqft = prop_details.get("livingArea") or (prop_details.get("building", {}) or {}).get("size", {}).get("livingsize")
        
        psf = sold / sqft if sold and sqft else 0
        psfs.append(psf)
        
        comp_address = prop_details.get("address", {})
        
        formatted.append({
            "address": comp_address.get("oneLine") or prop_details.get("streetAddress"),
            "sold_price": int(sold), "sqft": int(sqft), "psf": round(psf, 2),
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
