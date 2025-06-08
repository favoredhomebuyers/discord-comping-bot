# utils/valuation.py
import os
import asyncio
import httpx
import json
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
    print("\n[DEBUG] === Step 1: Getting Subject Property Data ===")
    zpid = await find_zpid_by_address_async(address)
    print(f"[DEBUG] ZPID Found: {zpid}")
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
        print("[ERROR] Could not geocode address. Aborting.")
        return {}, {}

    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        print(f"[DEBUG] Raw Zillow Details for Subject: {json.dumps(details, indent=2)}")
        if details:
            subject_info.update({
                "sqft": details.get("livingArea"),
                "beds": details.get("bedrooms"),
                "baths": details.get("bathrooms"),
                "year": details.get("yearBuilt"),
            })
    
    print(f"[DEBUG] Subject Info after Zillow: {subject_info}")
    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        print(f"[DEBUG] fetch_property_details status: {resp.status_code}")
        return resp.json() if resp.status_code == 200 else {}
    except httpx.RequestError as e:
        print(f"[ERROR] fetch_property_details exception: {e}")
        return {}

async def fetch_zillow_comps(zpid: str) -> List[dict]:
    print(f"\n[DEBUG] === Step 2: Fetching Zillow Comps for ZPID {zpid} ===")
    details = await fetch_property_details(zpid)
    
    if isinstance(details.get("comps"), list) and details["comps"]:
        print(f"[DEBUG] Found {len(details['comps'])} comps in Zillow details['comps'].")
        return details.get("comps", [])

    print("[DEBUG] No comps in details['comps'].")
    return []

async def fetch_attom_comps_fallback(subject: dict, radius: int = 5, count: int = 50) -> List[dict]:
    print("\n[DEBUG] === Step 2b: Fetching Attom Comps as Fallback ===")
    lat = subject.get("latitude")
    lon = subject.get("longitude")
    if not lat or not lon: return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    params = {"latitude": lat, "longitude": lon, "radius": radius, "pageSize": count}
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        print(f"[DEBUG] ATTOM fallback response status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM fallback failed: {resp.text}")
            return []
        data = resp.json()
        print(f"[DEBUG] Raw ATTOM data received. Found {len(data.get('property', []))} properties.")
        return data.get("property", [])
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error on ATTOM fallback: {e}")
        return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    print(f"\n[DEBUG] === Step 3: Cleaning {len(comps)} Raw Comps ===")
    
    if not all(subject.get(k) for k in ["latitude", "longitude", "sqft", "year"]):
        print(f"[ERROR] Subject property missing key data needed for filtering: {subject}")
        return [], 0.0

    s_lat, s_lon, actual_sqft, actual_year = float(subject["latitude"]), float(subject["longitude"]), subject["sqft"], subject["year"]
    print(f"[DEBUG] Filtering against Subject: sqft={actual_sqft}, year={actual_year}")
    
    one_year_ago = datetime.now() - timedelta(days=365)
    filtered_comps = []

    for i, comp_data in enumerate(comps):
        print(f"\n--- Evaluating Raw Comp #{i+1} ---")
        prop_details = (comp_data.get("property") or [comp_data])[0]
        
        # Parse all fields first
        sale_date_str = (comp_data.get("sale") or {}).get("amount", {}).get("saleRecDate") or prop_details.get("lastSoldDate")
        sold_price = (comp_data.get("sale") or {}).get("amount", {}).get("saleAmt") or prop_details.get("lastSoldPrice")
        sqft = (prop_details.get("building", {}).get("size", {}) or {}).get("bldgsize") or prop_details.get("livingArea")
        year = (prop_details.get("summary", {}) or {}).get("yearbuilt") or prop_details.get("yearBuilt")
        
        print(f"  - Raw data: sale_date='{sale_date_str}', sold_price='{sold_price}', sqft='{sqft}', year='{year}'")

        # Filter 1: Must have basic data
        if not all([sale_date_str, sold_price, sqft, year]):
            print("  - FILTER FAILED: Missing one or more essential data points.")
            continue

        # Filter 2: Sale Date
        try:
            sale_date = datetime.fromtimestamp(sale_date_str / 1000) if isinstance(sale_date_str, int) else datetime.strptime(sale_date_str, "%Y-%m-%d")
            if sale_date < one_year_ago:
                print(f"  - FILTER FAILED: Sale date {sale_date.date()} is older than 1 year.")
                continue
        except (ValueError, TypeError):
            print("  - FILTER FAILED: Invalid date format.")
            continue
        print("  - FILTER PASSED: Sale Date")

        # Filter 3: Square Footage
        if abs(sqft - actual_sqft) > 400:
            print(f"  - FILTER FAILED: Sqft {sqft} is not within +/- 400 of subject's {actual_sqft}.")
            continue
        print("  - FILTER PASSED: Square Footage")

        # Filter 4: Year Built
        if abs(year - actual_year) > 20:
            print(f"  - FILTER FAILED: Year {year} is not within +/- 20 of subject's {actual_year}.")
            continue
        print("  - FILTER PASSED: Year Built")
        
        print("  >>> COMP IS VALID <<<")
        filtered_comps.append(comp_data)

    print(f"[DEBUG] Found {len(filtered_comps)} valid comps after filtering.")
    if not filtered_comps: return [], 0.0
        
    def get_distance(comp):
        prop = (comp.get("property") or [comp])[0]
        try:
            return haversine((s_lat, s_lon), (float(prop.get("location",{}).get("latitude")), float(prop.get("location",{}).get("longitude"))))
        except: return float('inf')

    sorted_by_distance = sorted(filtered_comps, key=get_distance)
    chosen_comps = sorted_by_distance[:3]
    print(f"[DEBUG] Chose the {len(chosen_comps)} closest valid comps.")

    psfs = []
    formatted = []
    for comp in chosen_comps:
        prop_details = (comp.get("property") or [comp])[0]
        sold = (comp.get("sale", {}).get("amount", {}) or {}).get("saleAmt") or prop_details.get("lastSoldPrice")
        sqft = (prop_details.get("building", {}).get("size", {}) or {}).get("bldgsize") or prop_details.get("livingArea")
        
        psf = sold / sqft if sold and sqft else 0
        psfs.append(psf)
        
        formatted.append({
            "address": prop_details.get("address", {}).get("oneLine"),
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
