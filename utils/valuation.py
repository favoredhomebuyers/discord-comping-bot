import os
import asyncio
import httpx
import json
from haversine import haversine, Unit
from typing import List, Tuple, Dict, Any
from datetime import datetime, timedelta
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async

# --- Constants and Headers ---
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")
ATTOM_HOST  = os.getenv("ATTOM_HOST",    "api.gateway.attomdata.com")
ATTOM_KEY   = os.getenv("ATTOM_API_KEY")

Z_HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}
A_HEADERS = {"apikey": ATTOM_KEY}

client = httpx.AsyncClient(timeout=30.0)

async def get_subject_data(address: str) -> Tuple[dict, dict]:
    # Debug: track ZPID lookup
    print(f"[DEBUG] 🏷  Looking up ZPID for address: {address}")
    zpid = await find_zpid_by_address_async(address)
    print(f"[DEBUG] 🔍  zpid → {zpid}")

    subject_info: Dict[str, Any] = {}
    subj_ids: Dict[str, str] = {}

    # 1) Geocode via Google
    gmaps_info = get_coordinates(address)
    if not gmaps_info:
        return {}, {}
    subject_info.update({
        "latitude": gmaps_info.get("lat"),
        "longitude": gmaps_info.get("lng"),
        "address_components": gmaps_info.get("components"),
        "address": gmaps_info.get("formatted")
    })

    # 2) Zillow details if ZPID available
    if zpid:
        subj_ids["zpid"] = zpid
        details = await fetch_property_details(zpid)
        subject_info.update({
            "sqft": details.get("livingArea"),
            "beds": details.get("bedrooms"),
            "baths": details.get("bathrooms"),
            "year": details.get("yearBuilt"),
        })

    # 3) ATTOM fallback for missing fields
    if not all(subject_info.get(k) for k in ["sqft", "beds", "baths", "year"]):
        fb = await fetch_attom_fallback(subject_info, radius=0.1, count=1)
        if fb:
            prop = (fb[0].get("property") or [fb[0]])[0]
            subject_info.setdefault("sqft",  (prop.get("building") or {}).get("size", {}).get("bldgsize"))
            subject_info.setdefault("beds",  (prop.get("building") or {}).get("rooms", {}).get("beds"))
            subject_info.setdefault("baths", (prop.get("building") or {}).get("rooms", {}).get("bathstotal"))
            subject_info.setdefault("year",  (prop.get("summary")  or {}).get("yearbuilt"))

    return subj_ids, subject_info

async def fetch_property_details(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/property"
    try:
        r = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        return r.json() if r.status_code == 200 else {}
    except httpx.RequestError:
        return {}

async def fetch_zillow_comps(zpid: str) -> List[dict]:
    # Debug: ensure comps endpoint is called
    print(f"[DEBUG] ▶️ fetch_zillow_comps called with zpid={zpid}")
    url = f"https://{ZILLOW_HOST}/propertyComps"
    try:
        r = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid, "count": 20})
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("comparables") or data.get("results") or []
    except httpx.RequestError:
        return []

async def fetch_attom_fallback(subject: dict, radius: int = 10, count: int = 50) -> List[dict]:
    lat = subject.get("latitude")
    lon = subject.get("longitude")
    if lat is None or lon is None:
        return []

    # Try ATTOM transactions endpoint
    tx_url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/transactions"
    params = {"latitude": lat, "longitude": lon, "radius": radius, "pageSize": count}
    try:
        r = await client.get(tx_url, headers=A_HEADERS, params=params)
        if r.status_code == 200:
            return r.json().get("property", [])
    except httpx.RequestError:
        pass

    # Fallback to snapshot
    snap_url = f"https://{ATTOM_HOST}/propertyapi/v1.0.0/sale/snapshot"
    try:
        r2 = await client.get(snap_url, headers=A_HEADERS, params=params)
        if r2.status_code == 200:
            return r2.json().get("property", [])
    except httpx.RequestError:
        pass

    return []

async def fetch_price_and_tax_history(zpid: str) -> dict:
    url = f"https://{ZILLOW_HOST}/priceAndTaxHistory"
    try:
        r = await client.get(url, headers=Z_HEADERS, params={"zpid": zpid})
        return r.status_code == 200 and r.json() or {}
    except httpx.RequestError:
        return {}

def extract_last_sale(history: dict) -> Tuple[float, str]:
    events = history.get("priceAndTaxHistory") or history.get("history") or []
    sales = [e for e in events if e.get("eventType") == "Sale"]
    if not sales:
        return 0.0, ""
    latest = max(sales, key=lambda e: e.get("date", ""))
    return latest.get("price", 0.0), latest.get("date", "")


```python
# Temporarily comment out ATTOM fallback in get_comp_summary
```

```diff
 async def get_comp_summary(address: str, manual_sqft: int = None) -> Tuple[List[dict], float, int]:
     subj_ids, subject = await get_subject_data(address)
     if manual_sqft:
         subject["sqft"] = manual_sqft

     raw: List[dict] = []
     if subj_ids.get("zpid"):
         raw = await fetch_zillow_comps(subj_ids["zpid"])

-    if not raw:
-        raw = await fetch_attom_fallback(subject)
+    # if not raw:
+    #     raw = await fetch_attom_fallback(subject)

     if not raw:
         return [], 0.0, subject.get("sqft") or 0

     comps, _ = get_clean_comps(subject, raw)

     for comp in comps:
         hist = await fetch_price_and_tax_history(comp["id"])
         price, date = extract_last_sale(hist)
         comp["last_sold_price"] = price
         comp["last_sold_date"] = date

     return comps, 0.0, subject.get("sqft") or 0
