import os
import requests
from haversine import haversine, Unit
from typing import List, Dict, Tuple
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")

HEADERS = {
    "x-rapidapi-host": ZILLOW_HOST,
    "x-rapidapi-key": ZILLOW_KEY,
}

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print("[DEBUG] Attempting to find ZPID for:", address)
    zpid = await find_zpid_by_address_async(address)

    if not zpid:
        lat, lon, *_ = get_coordinates(address)
        print("[WARNING] Could not find ZPID for:", address)
        return {}, {
            "sqft": None, "beds": None, "baths": None,
            "year": None, "lot": None, "garage": None,
            "pool": False, "stories": None,
            "latitude": lat, "longitude": lon,
        }

    details = fetch_property_details(zpid)
    print("[DEBUG] Raw Zillow Details:", details)

    info = details.get("hdpData", {}).get("homeInfo") or details.get("homeInfo") or details

    lat = info.get("latitude") or info.get("latLong", {}).get("latitude")
    lon = info.get("longitude") or info.get("latLong", {}).get("longitude")

    subject_info = {
        "sqft": info.get("livingArea") or info.get("homeSize") or info.get("buildingSize"),
        "beds": info.get("bedrooms"),
        "baths": info.get("bathrooms"),
        "year": info.get("yearBuilt"),
        "lot": info.get("lotSize") or info.get("lotSizeArea"),
        "garage": info.get("garageType"),
        "pool": info.get("hasPool", False),
        "stories": info.get("floorCount"),
        "latitude": lat,
        "longitude": lon,
    }
    return {"zpid": zpid}, subject_info

def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        print("[WARNING] Failed to fetch details for ZPID:", zpid)
        return {}
    return resp.json()

def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    url = f"https://{ZILLOW_HOST}/propertyComps"
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        return []
    data = resp.json()
    for key in ("compResults", "comps", "comparables", "results"):
        if isinstance(data, dict) and key in data and isinstance(data[key], list):
            return data[key]
    return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    lat, lon = subject.get("latitude"), subject.get("longitude")
    actual_sqft = subject.get("sqft")

    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F+")]
    chosen = []

    for radius, grade in tiers:
        if len(chosen) >= 3:
            break
        for comp in comps:
            if any(c.get("zpid") == comp.get("zpid") for c in chosen):
                continue
            lat2 = comp.get("latitude") or comp.get("latLong", {}).get("latitude")
            lon2 = comp.get("longitude") or comp.get("latLong", {}).get("longitude")
            if None in (lat2, lon2) or haversine((lat, lon), (lat2, lon2), unit=Unit.MILES) > radius:
                continue
            if subject["beds"] and comp.get("bedrooms") and abs(comp["bedrooms"] - subject["beds"]) > 1:
                continue
            if subject["baths"] and comp.get("bathrooms") and abs(comp["bathrooms"] - subject["baths"]) > 1:
                continue
            if subject["year"] and comp.get("yearBuilt") and abs(comp["yearBuilt"] - subject["year"]) > 15:
                continue
            if actual_sqft and comp.get("livingArea") and abs(comp["livingArea"] - actual_sqft) > 250:
                continue
            chosen.append({**comp, "grade": grade})
            if len(chosen) >= 3:
                break

    psfs = []
    formatted = []
    for comp in chosen:
        sold = comp.get("price") or comp.get("soldPrice", 0)
        sqft = comp.get("livingArea")
        if sqft:
            psf = sold / sqft
            psfs.append(psf)
        else:
            psf = None
        formatted.append({
            "address": comp.get("address", {}).get("streetAddress") or "",
            "sold_price": int(sold),
            "sqft": sqft,
            "zillow_url": f"https://www.zillow.com/homedetails/{comp.get('zpid')}_zpid/",
            "grade": comp.get("grade"),
            "yearBuilt": comp.get("yearBuilt"),
            "beds": comp.get("bedrooms"),
            "baths": comp.get("bathrooms"),
            "psf": round(psf, 2) if psf else None,
        })

    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    return formatted, avg_psf

async def get_comp_summary(address: str) -> Tuple[List[dict], float, int]:
    subj, subject = await get_subject_data(address)
    zpid = subj.get("zpid")
    comps_raw = fetch_zillow_comps(zpid) if zpid else []
    clean_comps, avg_psf = get_clean_comps(subject, comps_raw)
    return clean_comps, avg_psf, subject.get("sqft") or 0
