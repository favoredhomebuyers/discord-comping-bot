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
    """
    Finds subject property data, using Zillow first and ATTOM as a fallback for details.
    """
    print(f"[DEBUG VAL] get_subject_data: address={address}")
    zpid = await find_zpid_by_address_async(address)
    subject_info = {}
    subj_ids = {}

    # First, always get coordinates from Google, which are reliable
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

    # Try to get details from Zillow
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
                "lot": info.get("lotSize") or info.get("lotSizeArea"),
            })

    # NEW: If Zillow didn't provide details, use ATTOM as a fallback
    if not subject_info.get("beds") or not subject_info.get("sqft"):
        print("[INFO VAL] Zillow details incomplete, trying ATTOM for subject details...")
        # Make a targeted call for the subject property (tiny radius)
        attom_details_list = await fetch_attom_comps(subject_info, radius=0.1, count=1)
        if attom_details_list:
            attom_details = attom_details_list[0]
            # Fill in missing details only
            if not subject_info.get("sqft"):
                subject_info["sqft"] = attom_details.get("building",{}).get("size",{}).get("livingSize")
            if not subject_info.get("beds"):
                subject_info["beds"] = attom_details.get("building", {}).get("rooms", {}).get("beds")
            if not subject_info.get("baths"):
                subject_info["baths"] = attom_details.get("building", {}).get("rooms", {}).get("bathTotal")
            if not subject_info.get("year"):
                subject_info["year"] = attom_details.get("summary",{}).get("yearBuilt")
            if not subject_info.get("lot"):
                subject_info["lot"] = attom_details.get("lot", {}).get("lotSize1")

    return subj_ids, subject_info


async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        if resp.status_code != 200:
            print(f"[WARNING VAL] Failed to fetch details for ZPID: {zpid}")
            return {}
        return resp.json()
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error fetching details for ZPID {zpid}: {e}")
        return {}


async def fetch_zillow_comps(zpid: str, count: int = 50) -> List[dict]:
    details = await fetch_property_details(zpid)
    nearby = details.get("nearbyHomes")
    if isinstance(nearby, list) and nearby:
        print(f"[DEBUG VAL] Using {len(nearby)} nearbyHomes from Zillow details")
        return nearby[:count]
    
    url = f"https://{ZILLOW_HOST}/propertyComps"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
        if resp.status_code != 200:
            print(f"[WARNING VAL] Failed to fetch comps for ZPID: {zpid}")
            return []
        data = resp.json()
        for key in ("compResults", "comps", "comparables", "results"):
            if isinstance(data, dict) and key in data and isinstance(data[key], list):
                return data[key]
        return []
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error fetching comps for ZPID {zpid}: {e}")
        return []


async def fetch_attom_comps(subject: dict, radius: int = 10, count: int = 50) -> List[dict]:
    lat = subject.get("latitude")
    lon = subject.get("longitude")

    if not lat or not lon:
        print(f"[WARNING VAL] ATTOM comps failed: No latitude/longitude for subject property.")
        return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/property/snapshot"
    params = {
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
        "pageSize": count, # Correct parameter name for ATTOM is pageSize
    }
    
    try:
        print(f"[DEBUG VAL] Querying ATTOM with params: {params}")
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM comps failed: {resp.status_code} - {resp.text}")
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
    except (ValueError, TypeError):
        print("[ERROR VAL] Subject property has invalid coordinates.")
        return [], 0.0

    actual_sqft = subject.get("sqft")
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]
    chosen = []
    
    for radius, grade in tiers:
        if len(chosen) >= 3:
            break
            
        for comp in comps:
            prop_class = comp.get("summary", {}).get("propclass")
            if prop_class and "Single Family" not in prop_class:
                continue

            if any(c.get("zpid") == comp.get("zpid") for c in chosen if c.get("zpid") and comp.get("zpid")):
                 continue
            if any(c.get("id") == comp.get("id") for c in chosen if c.get("id") and comp.get("id")):
                 continue

            try:
                lat2 = float(comp.get("latitude") or comp.get("location", {}).get("latitude"))
                lon2 = float(comp.get("longitude") or comp.get("location", {}).get("longitude"))
            except (ValueError, TypeError):
                continue

            distance = haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)
            if distance > radius:
                continue

            sqft_tolerance = 500
            year_tolerance = 25

            subject_beds = subject.get("beds")
            comp_beds = comp.get("bedrooms") or comp.get("building", {}).get("rooms", {}).get("beds")
            if subject_beds and comp_beds and abs(comp_beds - subject_beds) > 1:
                continue

            subject_baths = subject.get("baths")
            comp_baths = comp.get("bathrooms") or comp.get("building", {}).get("rooms", {}).get("bathTotal")
            if subject_baths and comp_baths and abs(comp_baths - subject_baths) > 1:
                continue

            subject_year = subject.get("year")
            comp_year = comp.get("yearBuilt") or comp.get("summary",{}).get("yearBuilt")
            if subject_year and comp_year and abs(comp_year - subject_year) > year_tolerance:
                continue

            comp_sqft = comp.get("livingArea") or comp.get("building",{}).get("size",{}).get("livingSize")
            if actual_sqft and comp_sqft and abs(comp_sqft - actual_sqft) > sqft_tolerance:
                continue

            chosen.append({**comp, "grade": grade, "distance": distance})
            
            if len(chosen) >= 3:
                break
    
    psfs = []
    formatted = []
    for comp in sorted(chosen, key=lambda x: x["distance"]):
        sale_info = comp.get("sale") or {}
        sold = comp.get("price") or comp.get("lastSoldPrice") or sale_info.get("amount") or 0
        sqft = comp.get("livingArea") or comp.get("building",{}).get("size",{}).get("livingSize")
        
        comp_zpid = comp.get('zpid')
        zillow_url = f"https://www.zillow.com/homedetails/{comp_zpid}_zpid/" if comp_zpid else "#"

        psf = sold / sqft if sqft and sold else None
        if psf:
            psfs.append(psf)
            
        formatted.append({
            "address": comp.get("address", {}).get("streetAddress") or f"{comp.get('address',{}).get('line1')} {comp.get('address',{}).get('line2')}",
            "sold_price": int(sold),
            "sqft": sqft,
            "zillow_url": zillow_url,
            "grade": comp.get("grade"),
            "yearBuilt": comp.get("yearBuilt") or comp.get("summary",{}).get("yearBuilt"),
            "beds": comp.get("bedrooms") or comp.get("building", {}).get("rooms", {}).get("beds"),
            "baths": comp.get("bathrooms") or comp.get("building", {}).get("rooms", {}).get("bathTotal"),
            "psf": round(psf, 2) if psf else None,
        })
        
    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    return formatted, avg_psf


async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft:
        subject["sqft"] = manual_sqft
        
    clean_comps, avg_psf = [], 0.0
    raw_comps = []
    
    if subj_ids.get("zpid"):
        raw_comps.extend(await fetch_zillow_comps(subj_ids["zpid"]))
            
    raw_comps.extend(await fetch_attom_comps(subject))

    if raw_comps:
        def get_sale_date(comp):
            date_str = (comp.get("sale") or {}).get("saleDate")
            if not date_str:
                return datetime.min

            # Handle different possible date formats gracefully
            if '+' in date_str:
                date_str = date_str.split('+')[0]
            
            try:
                if '.' in date_str:
                    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    return datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                return datetime.min

        sorted_comps = sorted(raw_comps, key=get_sale_date, reverse=True)
        clean_comps, avg_psf = get_clean_comps(subject, sorted_comps)

    return clean_comps, avg_psf, subject.get("sqft") or 0
