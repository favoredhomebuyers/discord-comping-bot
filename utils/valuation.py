import os
import requests
import logging
from haversine import haversine, Unit
from typing import List, Tuple
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# Configure module-level logger
logger = logging.getLogger(__name__)

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")

HEADERS = {
    "x-rapidapi-host": ZILLOW_HOST,
    "x-rapidapi-key": ZILLOW_KEY,
}

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    logger.debug("[VAL] get_subject_data: address=%s", address)
    zpid = await find_zpid_by_address_async(address)
    logger.debug("[VAL] find_zpid_by_address_async -> zpid=%s", zpid)

    if not zpid:
        lat, lon, *_ = get_coordinates(address)
        logger.warning("[VAL] No ZPID for %s, falling back to coords (%s, %s)", address, lat, lon)
        return {}, {
            "sqft": None, "beds": None, "baths": None,
            "year": None, "lot": None, "garage": None,
            "pool": False, "stories": None,
            "latitude": lat, "longitude": lon,
        }

    details = fetch_property_details(zpid)
    logger.debug("[VAL] Raw Zillow details for %s: %s", zpid, details)

    info = (details.get("hdpData", {}).get("homeInfo")
            or details.get("homeInfo")
            or details)

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
    logger.debug("[VAL] Parsed subject_info: %s", subject_info)
    return {"zpid": zpid}, subject_info


def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    logger.debug("[VAL] fetch_property_details URL=%s", url)
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid})
    if resp.status_code != 200:
        logger.warning("[VAL] Failed to fetch property details for ZPID %s: status=%s", zpid, resp.status_code)
        return {}
    data = resp.json()
    logger.debug("[VAL] fetch_property_details JSON for %s: %s", zpid, data)
    return data


def fetch_zillow_comps(zpid: str, count: int = 20) -> List[dict]:
    url = f"https://{ZILLOW_HOST}/propertyComps"
    logger.debug("[VAL] fetch_zillow_comps URL=%s", url)
    resp = requests.get(url, headers=HEADERS, params={"zpid": zpid, "count": count})
    if resp.status_code != 200:
        logger.warning("[VAL] Failed to fetch comps for ZPID %s: status=%s", zpid, resp.status_code)
        return []
    data = resp.json()
    logger.debug("[VAL] fetch_zillow_comps raw data: %s", data)
    for key in ("compResults", "comps", "comparables", "results"):
        if isinstance(data, dict) and key in data and isinstance(data[key], list):
            logger.debug("[VAL] Using comps key '%s', %d items", key, len(data[key]))
            return data[key]
    logger.debug("[VAL] No comps list found in response")
    return []


def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    lat, lon = subject.get("latitude"), subject.get("longitude")
    actual_sqft = subject.get("sqft")
    logger.debug("[VAL] get_clean_comps subject lat/lon=%s/%s sqft=%s", lat, lon, actual_sqft)

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
            # bedroom, bath, year, sqft filters
            if subject.get("beds") and comp.get("bedrooms") and abs(comp["bedrooms"] - subject["beds"]) > 1:
                continue
            if subject.get("baths") and comp.get("bathrooms") and abs(comp["bathrooms"] - subject["baths"]) > 1:
                continue
            if subject.get("year") and comp.get("yearBuilt") and abs(comp["yearBuilt"] - subject["year"]) > 15:
                continue
            if actual_sqft and comp.get("livingArea") and abs(comp["livingArea"] - actual_sqft) > 250:
                continue
            chosen.append({**comp, "grade": grade})
            logger.debug("[VAL] Added comp %s grade=%s dist=%.2f", comp.get("zpid"), grade, dist)
            if len(chosen) >= 3:
                break

    logger.debug("[VAL] Selected %d comps", len(chosen))

    psfs = []
    formatted = []
    for comp in chosen:
        sold = comp.get("price") or comp.get("soldPrice", 0)
        sqft = comp.get("livingArea")
        psf = (sold / sqft) if sqft else None
        if psf:
            psfs.append(psf)
        formatted.append({
            "address": comp.get("address", {}).get("streetAddress", ""),
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
    logger.debug("[VAL] Formatted comps: %s", formatted)
    logger.debug("[VAL] Computed avg_psf=%.2f", avg_psf)
    return formatted, avg_psf

async def get_comp_summary(address: str) -> Tuple[List[dict], float, int]:
    logger.debug("[VAL] get_comp_summary for address=%s", address)
    subj, subject = await get_subject_data(address)
    logger.debug("[VAL] get_subject_data returned subj=%s subject=%s", subj, subject)

    zpid = subj.get("zpid")
    if not zpid:
        logger.debug("[VAL] No zpid, skipping comps fetch")
        return [], 0, subject.get("sqft") or 0

    comps_raw = fetch_zillow_comps(zpid)
    logger.debug("[VAL] Raw comps fetched: %s", comps_raw)

    clean_comps, avg_psf = get_clean_comps(subject, comps_raw)
    logger.debug("[VAL] clean_comps=%s avg_psf=%.2f sqft=%s", clean_comps, avg_psf, subject.get("sqft"))

    return clean_comps, avg_psf, subject.get("sqft") or 0
