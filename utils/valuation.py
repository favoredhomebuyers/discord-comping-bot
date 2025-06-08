# valuation.py

import os
import requests
from haversine import haversine, Unit
from typing import List, Dict, Tuple

from utils.address_tools import get_coordinates, parse_address
from utils.zpid_finder import find_zpid_by_address_async

# -----------------------------------------------------------------------------
#  CONFIG
# -----------------------------------------------------------------------------
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")
Z_HEADERS   = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}

ATTOM_HOST  = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY   = os.getenv("ATTOM_API_KEY")
A_HEADERS   = {"apikey": ATTOM_KEY}


# -----------------------------------------------------------------------------
#  ZILLOW HELPERS
# -----------------------------------------------------------------------------
def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    resp = requests.get(url, headers=Z_HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        print(f"[WARNING] Zillow detail failed ({resp.status_code}) for ZPID {zpid}")
        return {}
    return resp.json()

def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    url = f"https://{ZILLOW_HOST}/propertyComps"
    resp = requests.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        print(f"[WARNING] Zillow comps failed ({resp.status_code}) for ZPID {zpid}")
        return []
    data = resp.json()
    for key in ("compResults","comps","comparables","results"):
        if isinstance(data, dict) and key in data and isinstance(data[key], list):
            return data[key]
    return []

async def zillow_subject_and_comps(address: str) -> Tuple[dict, List[dict]]:
    print("[DEBUG] Trying Zillow for:", address)
    zpid = await find_zpid_by_address_async(address)
    if not zpid:
        print("[DEBUG] Zillow ZPID not found")
        return {}, []

    details = fetch_property_details(zpid)
    info = details.get("hdpData", {}).get("homeInfo") or details.get("homeInfo") or details

    subject = {
        "sqft": info.get("livingArea") or info.get("homeSize") or info.get("buildingSize"),
        "beds": info.get("bedrooms"),
        "baths": info.get("bathrooms"),
        "year": info.get("yearBuilt"),
        "lot": info.get("lotSize") or info.get("lotSizeArea"),
        "garage": info.get("garageType"),
        "pool": info.get("hasPool", False),
        "stories": info.get("floorCount"),
        "latitude": info.get("latitude") or info.get("latLong", {}).get("latitude"),
        "longitude": info.get("longitude") or info.get("latLong", {}).get("longitude"),
    }

    comps = fetch_zillow_comps(zpid)
    print(f"[DEBUG] Zillow returned sqft={subject['sqft']} and {len(comps)} comps")
    return subject, comps


# -----------------------------------------------------------------------------
#  ATTOM HELPERS
# -----------------------------------------------------------------------------
def fetch_attom_detail(address: str) -> dict:
    street, city, state, postal = parse_address(address)
    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/property/detail"
    resp = requests.get(
        url, headers=A_HEADERS,
        params={
            "address1": street,
            "address2": "",
            "address3": city,
            "state":    state,
            "postalcode": postal
        }
    )
    if resp.status_code != 200:
        print(f"[WARNING] ATTOM detail failed ({resp.status_code}): {resp.text}")
        return {}
    props = resp.json().get("property", [])
    return props[0] if props else {}

def attom_subject_and_comps(address: str) -> Tuple[dict, List[dict]]:
    print("[DEBUG] Falling back to ATTOM for:", address)
    prop = fetch_attom_detail(address)
    if not prop:
        print("[DEBUG] ATTOM property not found")
        return {}, []

    subject = {
        "sqft": prop.get("roofSize", {}).get("livingArea") or prop.get("livingArea"),
        "beds": prop.get("bedrooms"),
        "baths": prop.get("bathroomsFull") or prop.get("bathrooms"),
        "year": prop.get("yearBuilt"),
        "lot": prop.get("lotSizeSquareFeet") or prop.get("lotSize"),
        "garage": prop.get("garageSpaces"),
        "pool": bool(prop.get("poolFeatures")),
        "stories": prop.get("storiesTotal") or prop.get("stories"),
        "latitude": prop.get("latitude"),
        "longitude": prop.get("longitude"),
    }

    comps = prop.get("nearbyHomes", [])
    print(f"[DEBUG] ATTOM returned sqft={subject['sqft']} and {len(comps)} comps")
    return subject, comps


# -----------------------------------------------------------------------------
#  CLEAN-UP & TIER LOGIC (UNCHANGED)
# -----------------------------------------------------------------------------
def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    lat, lon = subject.get("latitude"), subject.get("longitude")
    actual_sqft = subject.get("sqft")
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F+")]
    chosen = []

    for radius, grade in tiers:
        if len(chosen) >= 3:
            break
        for comp in comps:
            if any(c.get("zpid")==comp.get("zpid") for c in chosen):
                continue
            lat2 = comp.get("latitude") or comp.get("latLong", {}).get("latitude")
            lon2 = comp.get("longitude") or comp.get("latLong", {}).get("longitude")
            if None in (lat2, lon2):
                continue
            if haversine((lat, lon), (lat2, lon2), unit=Unit.MILES) > radius:
                continue
            if subject["beds"] and comp.get("bedrooms") and abs(comp["bedrooms"]-subject["beds"])>1:
                continue
            if subject["baths"] and comp.get("bathrooms") and abs(comp["bathrooms"]-subject["baths"])>1:
                continue
            if subject["year"] and comp.get("yearBuilt") and abs(comp["yearBuilt"]-subject["year"])>15:
                continue
            if actual_sqft and comp.get("livingArea") and abs(comp["livingArea"]-actual_sqft)>250:
                continue
            chosen.append({**comp, "grade": grade})
            if len(chosen)>=3:
                break

    psfs = []
    out = []
    for comp in chosen:
        sold = comp.get("price") or comp.get("soldPrice", 0)
        sqft = comp.get("livingArea")
        psf = (sold / sqft) if sqft else None
        if psf: psfs.append(psf)
        out.append({
            "address": comp.get("address",{}).get("streetAddress","") or comp.get("address"),
            "sold_price": int(sold),
            "sqft": sqft,
            "zillow_url": comp.get("hdpUrl") or comp.get("url"),
            "grade": comp["grade"],
            "yearBuilt": comp.get("yearBuilt"),
            "beds": comp.get("bedrooms"),
            "baths": comp.get("bathrooms"),
            "psf": round(psf,2) if psf else None
        })

    avg_psf = sum(psfs)/len(psfs) if psfs else 0
    return out, avg_psf


# -----------------------------------------------------------------------------
#  ENTRYPOINT
# -----------------------------------------------------------------------------
async def get_comp_summary(address: str) -> Tuple[List[dict], float, int]:
    # 1) Try Zillow
    subject, raw = await zillow_subject_and_comps(address)

    # if missing sqft or no comps, fall back to ATTOM
    if not subject.get("sqft") or len(raw)==0:
        subject, raw = attom_subject_and_comps(address)

    comps, avg_psf = get_clean_comps(subject, raw)
    print(f"[DEBUG] Final: {len(comps)} comps, avg_psf={avg_psf:.2f}, sqft={subject.get('sqft')}")
    return comps, avg_psf, subject.get("sqft") or 0
