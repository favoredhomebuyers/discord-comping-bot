import os
import requests
from haversine import haversine, Unit
from typing import List, Tuple
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")

HEADERS = {
    "x-rapidapi-host": ZILLOW_HOST,
    "x-rapidapi-key":  ZILLOW_KEY,
}

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print(f"[DEBUG] get_subject_data() → address: {address!r}")
    zpid = await find_zpid_by_address_async(address)
    print(f"[DEBUG]   → Zillow ZPID: {zpid!r}")

    if not zpid:
        lat, lon, *_ = get_coordinates(address)
        print(f"[WARNING]   No ZPID; falling back to coords: {lat}, {lon}")
        return {}, {
            "sqft": None,
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

    details = fetch_property_details(zpid)
    print(f"[DEBUG]   Raw Zillow JSON for {zpid}:")
    print(details)

    # dig into any of the common wrappers
    info = (
        details.get("hdpData", {}).get("homeInfo")
        or details.get("homeInfo")
        or details
    )
    print(f"[DEBUG]   homeInfo object:")
    print(info)

    # Try all the different keys Zillow might use
    sqft = (
        info.get("livingArea")
        or info.get("homeSize")
        or info.get("buildingSize")
    )
    beds  = info.get("bedrooms")
    baths = info.get("bathrooms")
    year  = info.get("yearBuilt")
    lot   = info.get("lotSize") or info.get("lotSizeArea")
    garage   = info.get("garageType")
    pool     = info.get("hasPool", False)
    stories  = info.get("floorCount")
    lat      = info.get("latitude") or info.get("latLong", {}).get("latitude")
    lon      = info.get("longitude") or info.get("latLong", {}).get("longitude")

    subject_info = {
        "sqft":       sqft,
        "beds":       beds,
        "baths":      baths,
        "year":       year,
        "lot":        lot,
        "garage":     garage,
        "pool":       pool,
        "stories":    stories,
        "latitude":   lat,
        "longitude":  lon,
    }
    print(f"[DEBUG]   Parsed subject_info:")
    print(subject_info)
    return {"zpid": zpid}, subject_info

def fetch_property_details(zpid: str) -> dict:
    url  = f"https://{ZILLOW_HOST}/property"
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        print(f"[WARNING] fetch_property_details({zpid}) failed:", resp.status_code, resp.text)
        return {}
    return resp.json()

def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    url  = f"https://{ZILLOW_HOST}/propertyComps"
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        print(f"[WARNING] fetch_zillow_comps({zpid}) failed:", resp.status_code, resp.text)
        return []
    data = resp.json()
    print(f"[DEBUG] raw comps JSON for {zpid}:")
    print(data)
    for key in ("compResults", "comps", "comparables", "results"):
        if isinstance(data, dict) and key in data and isinstance(data[key], list):
            print(f"[DEBUG]   using key `{key}` with {len(data[key])} comps")
            return data[key]
    print("[DEBUG]   no comps list found in the response")
    return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    print(f"[DEBUG] get_clean_comps() → subject: {subject}, raw comps count: {len(comps)}")
    lat, lon     = subject.get("latitude"), subject.get("longitude")
    actual_sqft  = subject.get("sqft")
    tiers        = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F+")]
    chosen, psfs = [], []

    for radius, grade in tiers:
        if len(chosen) >= 3:
            break
        for comp in comps:
            if any(c["zpid"] == comp.get("zpid") for c in chosen):
                continue
            lat2 = comp.get("latitude") or comp.get("latLong", {}).get("latitude")
            lon2 = comp.get("longitude") or comp.get("latLong", {}).get("longitude")
            if lat2 is None or lon2 is None:
                continue
            dist = haversine((lat, lon), (lat2, lon2), unit=Unit.MILES)
            if dist > radius:
                continue
            # bed/bath/year/building size filters...
            if actual_sqft and comp.get("livingArea"):
                if abs(comp["livingArea"] - actual_sqft) > 250:
                    continue
            chosen.append({**comp, "grade": grade})
            if len(chosen) >= 3:
                break

    print(f"[DEBUG]   chosen comps: {chosen}")
    formatted = []
    for comp in chosen:
        sold = comp.get("price") or comp.get("soldPrice", 0)
        sqft = comp.get("livingArea")
        psf  = sold / sqft if sqft else None
        if psf:
            psfs.append(psf)
        formatted.append({
            "address":     comp.get("address", {}).get("streetAddress", ""),
            "sold_price":  int(sold),
            "sqft":        sqft,
            "zillow_url":  f"https://www.zillow.com/homedetails/{comp.get('zpid')}_zpid/",
            "grade":       comp.get("grade"),
            "yearBuilt":   comp.get("yearBuilt"),
            "beds":        comp.get("bedrooms"),
            "baths":       comp.get("bathrooms"),
            "psf":         round(psf, 2) if psf else None,
        })

    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    print(f"[DEBUG]   avg_psf: {avg_psf}")
    return formatted, avg_psf

async def get_comp_summary(address: str) -> Tuple[List[dict], float, int]:
    subj, subject = await get_subject_data(address)
    print(f"[DEBUG] get_comp_summary() → subj: {subj}, subject: {subject}")
    comps_raw = fetch_zillow_comps(subj.get("zpid")) if subj.get("zpid") else []
    clean, avg = get_clean_comps(subject, comps_raw)
    print(f"[DEBUG] get_comp_summary() → clean: {clean}, avg: {avg}, sqft: {subject.get('sqft')}")
    return clean, avg, subject.get("sqft") or 0
