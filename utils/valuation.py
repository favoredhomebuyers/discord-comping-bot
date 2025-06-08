import os
import requests
import logging
from haversine import haversine, Unit
from typing import List, Tuple, Dict, Any
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# Configure logger
logger = logging.getLogger(__name__)

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY", "")

HEADERS = {
    "x-rapidapi-host": ZILLOW_HOST,
    "x-rapidapi-key": ZILLOW_KEY,
}

async def get_subject_data(address: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    logger.debug(f"[VAL] get_subject_data: address={address}")
    zpid = await find_zpid_by_address_async(address)
    logger.debug(f"[VAL] find_zpid_by_address_async -> zpid={zpid}")

    if not zpid:
        lat, lon, *_ = get_coordinates(address)
        logger.warning(f"[VAL] No ZPID for {address}, falling back to coords ({lat}, {lon})")
        return {}, {"sqft": None, "beds": None, "baths": None,
                   "year": None, "lot": None, "garage": None,
                   "pool": False, "stories": None,
                   "latitude": lat, "longitude": lon}

    details = fetch_property_details(zpid)
    logger.debug(f"[VAL] Raw Zillow details for {zpid}: {details}")
    info = details.get("hdpData", {}).get("homeInfo") or details.get("homeInfo") or details

    subject = {
        "sqft":     info.get("livingArea") or info.get("homeSize") or info.get("buildingSize"),
        "beds":     info.get("bedrooms"),
        "baths":    info.get("bathrooms"),
        "year":     info.get("yearBuilt"),
        "lot":      info.get("lotSize") or info.get("lotSizeArea"),
        "garage":   info.get("garageType"),
        "pool":     info.get("hasPool", False),
        "stories":  info.get("floorCount"),
        "latitude": info.get("latitude") or info.get("latLong", {}).get("latitude"),
        "longitude":info.get("longitude") or info.get("latLong", {}).get("longitude"),
    }
    logger.debug(f"[VAL] Parsed subject data: {subject}")
    return {"zpid": zpid}, subject


def fetch_property_details(zpid: str) -> Dict[str, Any]:
    url = f"https://{ZILLOW_HOST}/property"
    logger.debug(f"[VAL] fetch_property_details URL={url} for ZPID={zpid}")
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        logger.warning(f"[VAL] fetch_property_details failed status={resp.status_code} for ZPID {zpid}")
        return {}
    data = resp.json()
    return data


def fetch_zillow_comps(zpid: str, count: int = 20) -> List[Dict[str, Any]]:
    logger.debug(f"[VAL] fetch_zillow_comps: zpid={zpid}, count={count}")
    url = f"https://{ZILLOW_HOST}/propertyComps"
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        logger.warning(f"[VAL] fetch_zillow_comps failed status={resp.status_code}")
        return []
    data = resp.json()
    logger.debug(f"[VAL] Raw comps data: {data}")
    for key in ("compResults", "comps", "comparables", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            comps = data[key]
            logger.debug(f"[VAL] Found {len(comps)} comps under key '{key}'")
            return comps
    logger.debug("[VAL] No comps list found in response")
    return []


def get_clean_comps(subject: Dict[str, Any], comps: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float]:
    lat, lon = subject.get("latitude"), subject.get("longitude")
    actual_sqft = subject.get("sqft")
    logger.debug(f"[VAL] get_clean_comps: subject lat={lat}, lon={lon}, sqft={actual_sqft}")

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
            if None in (lat2, lon2):
                continue
            dist = haversine((lat, lon), (lat2, lon2), unit=Unit.MILES)
            if dist > radius:
                continue
            if subject.get("beds") and comp.get("bedrooms") and abs(comp["bedrooms"] - subject["beds"]) > 1:
                continue
            if subject.get("baths") and comp.get("bathrooms") and abs(comp["bathrooms"] - subject["baths"]) > 1:
                continue
            if subject.get("year") and comp.get("yearBuilt") and abs(comp["yearBuilt"] - subject["year"]) > 15:
                continue
            if actual_sqft and comp.get("livingArea") and abs(comp["livingArea"] - actual_sqft) > 250:
                continue
            chosen.append({**comp, "grade": grade})
            logger.debug(f"[VAL] Added comp {comp.get('zpid')} grade={grade} dist={dist:.2f}")
            if len(chosen) >= 3:
                break

    psfs = []
    formatted = []
    for comp in chosen:
        sold = comp.get("price") or comp.get("soldPrice", 0)
        sqft = comp.get("livingArea")
        psf = (sold / sqft) if sqft else None
        if psf:
            psfs.append(psf)
        item = {
            "address": comp.get("address", {}).get("streetAddress", ""),
            "sold_price": int(sold),
            "sqft":   sqft,
            "zillow_url": f"https://www.zillow.com/homedetails/{comp.get('zpid')}_zpid/",
            "grade":  comp.get("grade"),
            "yearBuilt": comp.get("yearBuilt"),
            "beds":    comp.get("bedrooms"),
            "baths":   comp.get("bathrooms"),
            "psf":     round(psf, 2) if psf else None,
        }
        logger.debug(f"[VAL] Formatted comp: {item}")
        formatted.append(item)

    avg_psf = sum(psfs) / len(psfs) if psfs else 0.0
    logger.debug(f"[VAL] avg_psf={avg_psf:.2f}")
    return formatted, avg_psf


async def get_comp_summary(address: str) -> Tuple[List[Dict[str, Any]], float, int]:
    logger.debug(f"[VAL] get_comp_summary: address={address}")
    subj, subject = await get_subject_data(address)
    zpid = subj.get("zpid")
    comps = fetch_zillow_comps(zpid) if zpid else []
    logger.debug(f"[VAL] fetch_zillow_comps returned {len(comps)} items")
    clean, avg = get_clean_comps(subject, comps)
    logger.debug(f"[VAL] get_clean_comps returned {len(clean)} comps, avg_psf={avg:.2f}")
    return clean, avg, subject.get("sqft") or 0
