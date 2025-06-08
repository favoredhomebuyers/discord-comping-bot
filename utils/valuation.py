# utils/valuation.py
import os
import requests
from haversine import haversine, Unit
from typing import List, Tuple
from utils.address_tools import get_coordinates  # Removed parse_address
from utils.zpid_finder import find_zpid_by_address_async

# ... (keep existing ZILLOW/ATTOM constants and headers) ...

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print(f"[DEBUG VAL] get_subject_data: address={address}")
    zpid = await find_zpid_by_address_async(address)
    subject_info = {}
    subj_ids = {}

    # Try fetching details from Zillow first
    if zpid:
        details = fetch_property_details(zpid)
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
        gmaps_info = get_coordinates(address) # This function gets components now
        if gmaps_info:
            subject_info.update({
                "latitude": gmaps_info.get("lat"),
                "longitude": gmaps_info.get("lng"),
                "address_components": gmaps_info.get("components")
            })
        else:
             print(f"[WARNING VAL] No ZPID and no geocode for {address}")

    return subj_ids, subject_info

# ... (keep fetch_property_details and fetch_zillow_comps) ...

def fetch_attom_comps(subject: dict, radius: int = 1, count: int = 20) -> List[dict]:
    components = subject.get("address_components")
    if not components:
        print(f"[WARNING VAL] ATTOM comps failed: No address components")
        return []

    street = components.get("street")
    city = components.get("city")
    state = components.get("state")
    postal = components.get("postal_code")
    
    if not all([street, city, state, postal]):
         print(f"[WARNING VAL] ATTOM comps failed: Incomplete address for {street}")
         return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/comparables"
    params = {
        "address1": street,
        "address2": f"{city}, {state} {postal}",
        "radius": radius,
        "count": count
    }
    resp = requests.get(url, headers=A_HEADERS, params=params)
    if resp.status_code != 200:
        print(f"[WARNING VAL] ATTOM comps failed for {street}: {resp.status_code}")
        return []
    data = resp.json()
    return data.get("property") or []

async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft:
        subject["sqft"] = manual_sqft
        
    clean_comps, avg_psf = [], 0.0
    
    # Try Zillow first
    if subj_ids.get("zpid"):
        raw_zillow = fetch_zillow_comps(subj_ids["zpid"])
        if raw_zillow:
            clean_comps, avg_psf = get_clean_comps(subject, raw_zillow)
            
    # If Zillow fails, fallback to Attom
    if not clean_comps:
        print("[INFO VAL] Zillow comps failed, trying ATTOM...")
        raw_attom = fetch_attom_comps(subject)
        if raw_attom:
            clean_comps, avg_psf = get_clean_comps(subject, raw_attom)

    return clean_comps, avg_psf, subject.get("sqft") or 0
