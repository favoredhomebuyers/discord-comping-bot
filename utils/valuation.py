# utils/valuation.py
import os
import requests
from haversine import haversine, Unit
from typing import List, Tuple
from utils.address_tools import get_coordinates, parse_address
from utils.zpid_finder import find_zpid_by_address_async

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print(f"[DEBUG VAL] get_subject_data: address={address}")
    zpid = await find_zpid_by_address_async(address)
    if zpid:
        details = fetch_property_details(zpid)
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
    # fallback to geocode if no ZPID
    lat, lon, city, state, postal = get_coordinates(address)
    print(f"[WARNING VAL] No ZPID for {address}, coords fallback ({lat},{lon})")
    return {}, {
        "sqft": None, "beds": None, "baths": None, "year": None,
        "lot": None, "garage": None, "pool": False, "stories": None,
        "latitude": lat, "longitude": lon,
    }

def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    resp = requests.get(url, headers=Z_HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        print(f"[WARNING VAL] Failed to fetch details for ZPID: {zpid}")
        return {}
    return resp.json()

def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    # First try comps embedded in the property details
    details = fetch_property_details(zpid)
    nearby = details.get("nearbyHomes")
    if isinstance(nearby, list) and nearby:
        print(f"[DEBUG VAL] Using {len(nearby)} nearbyHomes from Zillow details")
        return nearby[:count]
    # fallback to propertyComps endpoint
    url = f"https://{ZILLOW_HOST}/propertyComps"
    resp = requests.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        print(f"[WARNING VAL] Failed to fetch comps for ZPID: {zpid}")
        return []
    data = resp.json()
    for key in ("compResults", "comps", "comparables", "results"):
        if isinstance(data, dict) and key in data and isinstance(data[key], list):
            return data[key]
    return []

def fetch_attom_comps(address: str, radius: int = 1, count: int = 20) -> List[dict]:
    street, city, state, postal = parse_address(address)
    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/comparables"
    params = {
        "address1": street,
        "address2": "",
        "address3": city,
        "state": state,
        "postalcode": postal,
        "radius": radius,
        "count": count
    }
    resp = requests.get(url, headers=A_HEADERS, params=params)
    if resp.status_code != 200:
        print(f"[WARNING VAL] ATTOM comps failed for {address}: {resp.status_code}")
        return []
    data = resp.json()
    return data.get("compProperties") or data.get("comparables") or []

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
        psf = sold / sqft if sqft else None
        if psf:
            psfs.append(psf)
        formatted.append({
            "address": comp.get("address", {}).get("streetAddress") or comp.get("address"),
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
    clean_comps, avg_psf = [], 0.0
    if subj.get("zpid"):
        raw = fetch_zillow_comps(subj["zpid"])
        clean_comps, avg_psf = get_clean_comps(subject, raw)
    if not clean_comps:
        raw = fetch_attom_comps(address)
        clean_comps, avg_psf = get_clean_comps(subject, raw)
    return clean_comps, avg_psf, subject.get("sqft") or 0
