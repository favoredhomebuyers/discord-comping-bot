import os
import asyncio
import httpx
from haversine import haversine, Unit
from typing import List, Tuple
from datetime import datetime, timedelta
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# --- Constants and Headers ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

client = httpx.AsyncClient(timeout=30.0)

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    zpid = await find_zpid_by_address_async(address)
    subject_info = {}
    subj_ids = {}

    gmaps_info = get_coordinates(address)
    if gmaps_info:
        subject_info.update({
            "latitude": gmaps_info.get("lat"),
            "longitude": gmaps_info.get("lng"),
            "address_components": gmaps_info.get("components"),
            "address": gmaps_info.get("formatted")
        })
    else:
        return {}, {}

    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        if details:
            subject_info.update({
                "sqft": details.get("livingArea"),
                "beds": details.get("bedrooms"),
                "baths": details.get("bathrooms"),
                "year": details.get("yearBuilt"),
            })

    if not all(subject_info.get(k) for k in ["sqft", "beds", "baths", "year"]):
        attom_subject_list = await fetch_attom_comps_fallback(subject_info, radius=0.1)
        if attom_subject_list:
            prop_details = (attom_subject_list[0].get("property") or [{}])[0]
            if prop_details:
                if not subject_info.get("sqft"): subject_info["sqft"] = (prop_details.get("building", {}).get("size", {}) or {}).get("bldgsize")
                if not subject_info.get("beds"): subject_info["beds"] = (prop_details.get("building", {}).get("rooms", {}) or {}).get("beds")
                if not subject_info.get("baths"): subject_info["baths"] = (prop_details.get("building", {}).get("rooms", {}) or {}).get("bathstotal")
                if not subject_info.get("year"): subject_info["year"] = (prop_details.get("summary", {}) or {}).get("yearbuilt")

    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        return resp.json() if resp.status_code == 200 else {}
    except httpx.RequestError:
        return {}

async def fetch_zillow_comps(zpid: str) -> List[dict]:
    details = await fetch_property_details(zpid)
    if isinstance(details.get("comps"), list) and details["comps"]:
        return details.get("comps", [])
    url = f"https://{ZILLOW_HOST}/propertyComps"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": 20})
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("results", []) or data.get("comparables", [])
    except httpx.RequestError:
        return []

async def fetch_attom_comps_fallback(subject: dict, radius: int = 10, count: int = 50) -> List[dict]:
    lat = subject.get("latitude")
    lon = subject.get("longitude")
    if not lat or not lon:
        return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    params = {"latitude": lat, "longitude": lon, "radius": radius, "pageSize": count}
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM fallback failed: {resp.status_code} - {resp.text}")
            return []
        return resp.json().get("property", [])
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error on ATTOM fallback: {e}")
        return []


def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    """
    Simplified comp filtering: keep comps by location tiers with grade and sale date within last 12 months.
    """
    if not all(subject.get(k) for k in ["latitude", "longitude"]):
        return [], 0.0

    s_lat = float(subject["latitude"])
    s_lon = float(subject["longitude"])
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]
    twelve_months_ago = datetime.now() - timedelta(days=365)
    filtered = []

    for comp_data in comps:
        is_attom = "identifier" in comp_data
        prop = (comp_data.get("property") or [comp_data])[0] if is_attom else comp_data

        comp_id = prop.get("zpid") or (comp_data.get("identifier") or {}).get("attomId")
        if not comp_id:
            continue

        # Calculate distance
        try:
            lat2 = float(prop.get("latitude") or (prop.get("location", {}) or {}).get("latitude"))
            lon2 = float(prop.get("longitude") or (prop.get("location", {}) or {}).get("longitude"))
            distance = haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)
        except (ValueError, TypeError):
            continue

        # Determine grade by tiers
        grade = None
        for radius, g in tiers:
            if distance <= radius:
                grade = g
                break
        if not grade:
            continue

        # Extract and parse sale date
        sale_raw = (comp_data.get("sale", {}) or {}).get("saleDate") or prop.get("lastSoldDate")
        if sale_raw is None:
            continue
        # Handle numeric timestamps (milliseconds) or strings
        if isinstance(sale_raw, (int, float)):
            try:
                sale_date = datetime.utcfromtimestamp(sale_raw / 1000)
            except Exception:
                continue
        else:
            date_str = str(sale_raw)
            # Strip trailing Z
            if date_str.endswith("Z"):
                date_str = date_str[:-1]
            try:
                sale_date = datetime.fromisoformat(date_str)
            except ValueError:
                try:
                    sale_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                except Exception:
                    continue

        # Filter by last 12 months
        if sale_date < twelve_months_ago:
            continue

        filtered.append({
            "id": comp_id,
            "grade": grade,
            "distance": distance,
            "sale_date": sale_date.isoformat()
        })

    return filtered, 0.0

async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft:
        subject["sqft"] = manual_sqft

    raw_comps = []
    if subj_ids.get("zpid"):
        raw_comps = await fetch_zillow_comps(subj_ids["zpid"])

    if not raw_comps:
        print("[INFO VAL] Zillow returned no comps, trying ATTOM fallback.")
        raw_comps = await fetch_attom_comps_fallback(subject)

    if not raw_comps:
        return [], 0.0, subject.get("sqft") or 0

    clean_comps, avg_psf = get_clean_comps(subject, raw_comps)
    return clean_comps, avg_psf, subject.get("sqft") or 0
