# utils/valuation.py
import os
import asyncio
import httpx
from haversine import haversine, Unit
from typing import List, Tuple
from datetime import datetime
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# --- Constants and Headers ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST = os.getenv("ATTOM_HOST", "api.gateway.attomdata.com")
ATTOM_KEY = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

client = httpx.AsyncClient(timeout=20.0)

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    print(f"[DEBUG VAL] get_subject_data: address={address}")
    zpid = await find_zpid_by_address_async(address)
    subject_info = {}
    subj_ids = {}

    gmaps_info = get_coordinates(address)
    if gmaps_info:
        subject_info.update({
            "latitude": gmaps_info.get("lat"),
            "longitude": gmaps_info.get("lng"),
            "address_components": gmaps_info.get("components")
        })
    else:
        print(f"[ERROR VAL] Could not geocode address: {address}. Cannot proceed.")
        return {}, {}

    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        info = details.get("hdpData", {}).get("homeInfo") or details.get("homeInfo") or details
        if info:
            subject_info.update({
                "sqft": info.get("livingArea") or info.get("homeSize"),
                "beds": info.get("bedrooms"),
                "baths": info.get("bathrooms"),
                "year": info.get("yearBuilt"),
                "lot": info.get("lotSize") or info.get("lotSizeArea"),
            })

    if not subject_info.get("beds") or not subject_info.get("sqft"):
        print("[INFO VAL] Zillow details incomplete, trying ATTOM for subject details...")
        # Use the NEW sale/snapshot endpoint for subject details too
        attom_details_list = await fetch_attom_sale_comps(subject_info, radius=0.1, count=1)
        if attom_details_list:
            attom_property = attom_details_list[0].get("property")
            if attom_property:
                attom_details = attom_property[0]
                if not subject_info.get("sqft"):
                    subject_info["sqft"] = attom_details.get("building",{}).get("size",{}).get("livingSize")
                if not subject_info.get("beds"):
                    subject_info["beds"] = attom_details.get("building", {}).get("rooms", {}).get("beds")
                if not subject_info.get("baths"):
                    subject_info["baths"] = attom_details.get("building", {}).get("rooms", {}).get("bathTotal")
                if not subject_info.get("year"):
                    subject_info["year"] = attom_details.get("summary",{}).get("yearBuilt")
                if not subject_info.get("lot"):
                    subject_info["lot"] = attom_details.get("lot", {}).get("lotSize1")

    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        if resp.status_code != 200: return {}
        return resp.json()
    except httpx.RequestError: return {}


async def fetch_zillow_comps(zpid: str, count: int = 50) -> List[dict]:
    details = await fetch_property_details(zpid)
    if isinstance(details.get("nearbyHomes"), list):
        return details["nearbyHomes"][:count]
    
    url = f"https://{ZILLOW_HOST}/propertyComps"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": count})
        if resp.status_code != 200: return []
        data = resp.json()
        for key in ("compResults", "comps", "comparables", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    except httpx.RequestError: return []


async def fetch_attom_sale_comps(subject: dict, radius: int = 10, count: int = 50) -> List[dict]:
    """
    Fetches comps from the ATTOM /sale/snapshot endpoint, which is designed to return sales data.
    """
    components = subject.get("address_components")
    if not components:
        return []

    street = components.get("street")
    city = components.get("city")
    state = components.get("state")
    
    if not all([street, city, state]):
        return []

    url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    params = {
        "address1": street,
        "address2": f"{city}, {state}",
        "radius": radius,
        "propertytypes": "SFR",
        "orderby": "saledate",
        "pageSize": count
    }
    
    try:
        resp = await client.get(url, headers=A_HEADERS, params=params)
        if resp.status_code != 200:
            print(f"[WARNING VAL] ATTOM sale snapshot failed: {resp.status_code} - {resp.text}")
            return []
        data = resp.json()
        return data.get("property") or []
    except httpx.RequestError as e:
        print(f"[ERROR VAL] HTTP error fetching ATTOM sale comps: {e}")
        return []

def get_clean_comps(subject: dict, comps: List[dict]) -> Tuple[List[dict], float]:
    try:
        s_lat = float(subject.get("latitude"))
        s_lon = float(subject.get("longitude"))
    except (ValueError, TypeError): return [], 0.0

    actual_sqft = subject.get("sqft")
    tiers = [(1, "A+"), (2, "B+"), (3, "C+"), (5, "D+"), (10, "F")]
    chosen = []
    
    for radius, grade in tiers:
        if len(chosen) >= 3: break
            
        for comp_data in comps:
            # The sale snapshot endpoint nests the property data
            comp = (comp_data.get("property") or [{}])[0]
            
            comp_sold_obj = (comp_data.get("sale") or {}).get("saleAmountData") or {}
            comp_sold = comp.get("lastSoldPrice") or comp_sold_obj.get("saleAmt")
            comp_sqft = comp.get("livingArea") or (comp.get("building", {}).get("size", {}) or {}).get("livingSize")

            if not comp_sold or not comp_sqft: continue

            if any(c.get("attomId") == comp.get("attomId") for c in chosen if c.get("attomId") and comp.get("attomId")): continue
            if any(c.get("zpid") == comp.get("zpid") for c in chosen if c.get("zpid") and comp.get("zpid")): continue

            try:
                lat2 = float(comp.get("latitude") or (comp.get("location", {}) or {}).get("latitude"))
                lon2 = float(comp.get("longitude") or (comp.get("location", {}) or {}).get("longitude"))
            except (ValueError, TypeError): continue

            if haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES) > radius: continue
            
            # (Filtering logic remains the same)
            
            chosen.append({**comp, **comp_data, "grade": grade, "distance": haversine((s_lat, s_lon), (lat2, lon2), unit=Unit.MILES)})
            
            if len(chosen) >= 3: break
    
    psfs = []
    formatted = []
    for comp in sorted(chosen, key=lambda x: x["distance"]):
        sold = comp.get("lastSoldPrice") or (comp.get("sale", {}).get("saleAmountData", {}) or {}).get("saleAmt")
        sqft = comp.get("livingArea") or (comp.get("building",{}).get("size",{}) or {}).get("livingSize")
        
        psf = sold / sqft if sqft and sold else None
        if psf: psfs.append(psf)
            
        comp_address = (comp.get("address", {}) or {})
        
        formatted.append({
            "address": comp_address.get("streetAddress") or f"{comp_address.get('line1')} {comp_address.get('line2')}",
            "sold_price": int(sold), "sqft": int(sqft), "psf": round(psf, 2) if psf else None,
            "zillow_url": f"https://www.zillow.com/homedetails/{comp.get('zpid')}_zpid/" if comp.get('zpid') else "#",
            "grade": comp.get("grade"),
            "yearBuilt": comp.get("yearBuilt") or (comp.get("summary",{}) or {}).get("yearBuilt"),
            "beds": comp.get("bedrooms") or (comp.get("building", {}).get("rooms", {}) or {}).get("beds"),
            "baths": comp.get("bathrooms") or (comp.get("building", {}).get("rooms", {}) or {}).get("bathTotal"),
        })
        
    avg_psf = sum(psfs) / len(psfs) if psfs else 0
    return formatted, avg_psf

async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
    subj_ids, subject = await get_subject_data(address)
    if manual_sqft: subject["sqft"] = manual_sqft
        
    raw_comps = []
    if subj_ids.get("zpid"):
        raw_comps.extend(await fetch_zillow_comps(subj_ids["zpid"]))
            
    raw_comps.extend(await fetch_attom_sale_comps(subject))

    if not raw_comps:
        return [], 0.0, subject.get("sqft") or 0

    def get_sale_date(comp):
        date_str = (comp.get("sale") or {}).get("saleAmountData", {}).get("saleRecDate") or comp.get("lastSoldDate")
        if not date_str: return datetime.min
        if isinstance(date_str, int): return datetime.fromtimestamp(date_str / 1000)
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError): return datetime.min

    sorted_comps = sorted(raw_comps, key=get_sale_date, reverse=True)
    clean_comps, avg_psf = get_clean_comps(subject, sorted_comps)
           
    return clean_comps, avg_psf, subject.get("sqft") or 0
