# utils/valuation.py
import os
import asyncio
import httpx
from haversine import haversine, Unit
from typing import List, Tuple
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# --- Constants and Headers ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

# --- Client for making API calls ---
client = httpx.AsyncClient(timeout=20.0)


async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print(f"[DEBUG VAL] get_subject_data: address={address}")
    zpid = await find_zpid_by_address_async(address)
    subject_info = {}
    subj_ids = {}

    # Try fetching details from Zillow first
    if zpid:
        details = await fetch_property_details(zpid)
        info = details.get("hdpData", {}).get("homeInfo") or details.get("homeInfo") or details
        if info:
            lat = info.get("latitude") or info.get("latLong", {}).get("latitude")
            lon = info.get("longitude") or info.get("latLong", {}).get("longitude")
            subject_info = {
                "sqft": info.get("livingArea") or info.get("homeSize") or info.get("buildingSize"),
                "beds": info.get("bedrooms"),
                "baths": info.get("bathrooms"),
                "year": info.get("yearBuilt"),
                "lot": info.get("lotSize") or info.get("lotSizeArea"),
                "latitude": lat,
                "longitude": lon,
            }
            subj_ids["zpid"] = zpid
    
    # If Zillow fails or doesn't have coordinates, use Google Maps
    if not subject_info.get("latitude"):
        gmaps_info = get_coordinates(address)
        if gmaps_info:
            subject_info.update({
                "latitude": gmaps_info.get("lat"),
                "longitude": gmaps_info.get("lng"),
                "address_components": gmaps_info.get("components")
            })
        else:
             print(f"[WARNING VAL] No ZPID and no geocode for {address}")

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
    # First try comps embedded in the property details
    details = await fetch_property_details(zpid)
    nearby = details.get("nearbyHomes")
    if isinstance(nearby, list) and nearby:
        print(f"[DEBUG VAL] Using {len(nearby)} nearbyHomes from Zillow details")
        return nearby[:count]
    
    # Fallback to propertyComps endpoint
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
    """
    Fetches comps from ATTOM. We fetch from a wide 10-mile radius by default
    so the tiered filtering in get_clean_comps has enough data to work with.
    """
    components = subject.get("address_components")
    if not components:
        print(f"[WARNING VAL] ATTOM comps failed: No address components")
        return []

    street = components.get("street")
    city = components.get("city")
    state = components.get("state")
    
    if not all([street, city, state]):
        print(f"[WARNING VAL] ATTOM comps failed: Incomplete address for {street}")
        return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    params = {
        "address1": street,
        "address2": f"{city}, {state}",
        "radius": radius,
        "size": count,
        "propertytypes": "SFR",
        "orderby": "saledate"
    }
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM comps failed for {street}: {resp.status_code} - {resp.text}")
            return []
        data = resp.json()
        return data.get("property") or []
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error fetching ATTOM comps for {street}: {e}")
        return []


def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    """
    Filters a list of raw comps based on a tiered distance system, seeking 3 good comps.
    Starts with the tightest radius and expands if necessary.
    """
    lat, lon = subject.get("latitude"), subject.get("longitude")
    actual_sqft = subject.get("sqft")

    # Define the tiered search system as requested
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]
    
    chosen = []
    
    for radius, grade in tiers:
        # If we already have 3 comps, we can stop searching
        if len(chosen) >= 3:
            break
            
        for comp in comps:
            # Skip if we already added this comp
            if any(c.get("zpid") == comp.get("zpid") for c in chosen if c.get("zpid")):
                 continue
            if any(c.get("id") == comp.get("id") for c in chosen if c.get("id")):
                 continue

            lat2 = comp.get("latitude") or comp.get("location", {}).get("latitude")
            lon2 = comp.get("longitude") or comp.get("location", {}).get("longitude")

            # Ensure the comp has coordinates to check distance
            if not all([lat, lon, lat2, lon2]):
                continue
            
            # Check if comp is within the current tier's radius
            distance = haversine((lat, lon), (lat2, lon2), unit=Unit.MILES)
            if distance > radius:
                continue

            # --- Apply Property Similarity Filters ---
            sqft_tolerance = 500  # A looser, more realistic tolerance
            year_tolerance = 25   # A looser, more realistic tolerance

            comp_beds = comp.get("bedrooms") or comp.get("building", {}).get("rooms", {}).get("beds")
            if subject["beds"] and comp_beds and abs(comp_beds - subject["beds"]) > 1:
                continue

            comp_baths = comp.get("bathrooms") or comp.get("building", {}).get("rooms", {}).get("bathTotal")
            if subject["baths"] and comp_baths and abs(comp_baths - subject["baths"]) > 1:
                continue

            comp_year = comp.get("yearBuilt") or comp.get("summary",{}).get("yearBuilt")
            if subject["year"] and comp_year and abs(comp_year - subject["year"]) > year_tolerance:
                continue

            comp_sqft = comp.get("livingArea") or comp.get("building",{}).get("size",{}).get("livingSize")
            if actual_sqft and comp_sqft and abs(comp_sqft - actual_sqft) > sqft_tolerance:
                continue

            # If all checks pass, add it to our list with the correct grade
            chosen.append({**comp, "grade": grade, "distance": distance})
            
            # If we hit 3 comps, break the inner loop and move to formatting
            if len(chosen) >= 3:
                break
    
    # --- Format the chosen comps for the final output ---
    psfs = []
    formatted = []
    for comp in sorted(chosen, key=lambda x: x["distance"]):
        sold = comp.get("price") or comp.get("lastSoldPrice") or comp.get("sale", {}).get("amount") or 0
        sqft = comp.get("livingArea") or comp.get("building",{}).get("size",{}).get("livingSize")
        
        # zpid can come from multiple places
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
    
    # Try Zillow first
    if subj_ids.get("zpid"):
        raw_comps.extend(await fetch_zillow_comps(subj_ids["zpid"]))
            
    # Always try Attom as a backup or to supplement Zillow
    raw_comps.extend(await fetch_attom_comps(subject))

    if raw_comps:
        # Send all raw comps to the tiered filter
        clean_comps, avg_psf = get_clean_comps(subject, raw_comps)

    return clean_comps, avg_psf, subject.get("sqft") or 0
