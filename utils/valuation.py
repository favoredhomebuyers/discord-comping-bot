# utils/valuation.py

import os
import requests
from haversine import haversine, Unit
from typing import List, Dict, Tuple
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# --- Environment & Hosts ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY", "")
ATTOM_KEY   = os.getenv("ATTOM_API_KEY", "")

ZILLOW_HEADERS = {
    "x-rapidapi-host": ZILLOW_HOST,
    "x-rapidapi-key": ZILLOW_KEY,
}

ATTOM_HEADERS = {
    "apikey": ATTOM_KEY
}

# --- Core ----------------------------------------------------------------------------

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    """
    Returns ({"zpid": ...} or {}) plus a subject_info dict containing at least
    sqft, beds, baths, year, lot, garage, pool, stories, latitude, longitude.
    """
    print(f"[VAL] get_subject_data: address={address!r}")

    zpid = await find_zpid_by_address_async(address)
    if zpid:
        print(f"[VAL]  → found ZPID: {zpid}")
        details = fetch_property_details(zpid)
        print(f"[VAL]  → Raw Zillow details: {details}")
        info = (
            details.get("hdpData", {}).get("homeInfo")
            or details.get("homeInfo")
            or details
        )

        # pull out fields
        sqft = info.get("livingArea") or info.get("homeSize") or info.get("buildingSize")
        beds = info.get("bedrooms")
        baths = info.get("bathrooms")
        year = info.get("yearBuilt")
        lot  = info.get("lotSize") or info.get("lotSizeArea")
        garage = info.get("garageType")
        pool   = info.get("hasPool", False)
        stories= info.get("floorCount")
        lat = info.get("latitude") or info.get("latLong", {}).get("latitude")
        lon = info.get("longitude") or info.get("latLong", {}).get("longitude")

        return {"zpid": zpid}, {
            "sqft": sqft,
            "beds": beds,
            "baths": baths,
            "year": year,
            "lot": lot,
            "garage": garage,
            "pool": pool,
            "stories": stories,
            "latitude": lat,
            "longitude": lon,
        }

    # --- Zillow failed, fall back to coordinates + Attom  -----------------------------
    lat, lon, *_ = get_coordinates(address)
    print(f"[VAL]  ! No ZPID, coords fallback: ({lat}, {lon})")

    # Try Attom for square footage
    sqft = None
    if ATTOM_KEY:
        try:
            sqft = fetch_attom_sqft(address)
            print(f"[VAL]  → Attom sqft: {sqft}")
        except Exception as e:
            print(f"[VAL]  ! Attom lookup failed: {e}")

    return {}, {
        "sqft": sqft,
        "beds": None,
        "baths": None,
        "year": None,
        "lot": None,
        "garage": None,
        "pool": False,
        "stories": None,
        "latitude": lat,
        "longitude": lon,
    }


def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    resp = requests.get(url, headers=ZILLOW_HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        print(f"[VAL]  ! Zillow details failed (status {resp.status_code}) for ZPID {zpid}")
        return {}
    return resp.json()


def fetch_attom_sqft(address: str) -> int:
    """
    Calls Attom's property detail endpoint by splitting the address into
    street, city, state & zip. Returns the living area (sqft) or raises.
    """
    print(f"[VAL] fetch_attom_sqft: {address!r}")
    # crude parse: "1705 Magnolia Ave, San Bernardino, CA 92411"
    street, rest = address.split(",", 1)
    city, state_zip = rest.strip().split(",", 1)
    state, postal = state_zip.strip().split(" ", 1)

    url = "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/detail"
    params = {
        "address1": street.strip(),
        "address2": "",
        "address3": city.strip(),
        "state": state.strip(),
        "postalcode": postal.strip(),
    }
    resp = requests.get(url, headers=ATTOM_HEADERS, params=params)
    resp.raise_for_status()
    data = resp.json()

    # navigate Attom JSON to find totalLivingArea
    prop_list = data.get("property", [])
    if not prop_list:
        raise ValueError("no property returned by Attom")

    struct = prop_list[0].get("structure", {})
    sizes  = struct.get("actualSize") or {}
    sqft = sizes.get("totalLivingArea") or sizes.get("livingArea")
    if sqft is None:
        raise ValueError("Attom JSON missing totalLivingArea")

    return int(sqft)


def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    url = f"https://{ZILLOW_HOST}/propertyComps"
    resp = requests.get(url, headers=ZILLOW_HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        print(f"[VAL]  ! Zillow comps failed (status {resp.status_code})")
        return []
    data = resp.json()
    for key in ("compResults", "comps", "comparables", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    # ... your existing comp-filtering logic stays exactly the same ...
    lat, lon = subject["latitude"], subject["longitude"]
    actual_sqft = subject.get("sqft")

    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F+")]
    chosen = []
    # ... copy your current loop here ...
    # (omitted for brevity)

    # ... then format results & compute avg_psf ...
    # (omitted for brevity)

    return chosen, 0.0  # placeholder


async def get_comp_summary(address: str) -> Tuple[List[dict], float, int]:
    print(f"[VAL] get_comp_summary for address={address}")
    subj, subject = await get_subject_data(address)
    if "zpid" in subj:
        comps_raw = fetch_zillow_comps(subj["zpid"])
    else:
        print("[VAL]  → No zpid, skipping comps fetch")
        comps_raw = []

    clean_comps, avg_psf = get_clean_comps(subject, comps_raw)
    return clean_comps, avg_psf, subject.get("sqft") or 0
