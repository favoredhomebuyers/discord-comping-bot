# utils/zpid_finder.py
import os
import httpx
from typing import Optional

# must match your ZILLOW_HOST and Z_HEADERS in valuation.py
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")
Z_HEADERS   = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}

client = httpx.AsyncClient(timeout=30.0)

async def find_zpid_by_address_async(address: str) -> Optional[str]:
    """
    Uses Zillow’s propertyExtendedSearch to find the zpid for a given address,
    with debug logging to inspect the response payload.
    """
    url = f"https://{ZILLOW_HOST}/propertyExtendedSearch"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"location": address})
        if resp.status_code != 200:
            print(f"[ERROR ZPID] propertyExtendedSearch status {resp.status_code}")
            return None
        data = resp.json()
        # Debug: inspect top-level keys
        print(f"[DEBUG ZPID] response keys: {list(data.keys())}")
        # Try common result containers
        hits = data.get("results") or data.get("list") or []
        # Some versions nest under props.list
        if not hits and isinstance(data.get("props"), dict):
            hits = data["props"].get("list", [])
        if hits:
            zpid = hits[0].get("zpid")
            print(f"[DEBUG ZPID] found zpid: {zpid}")
            return zpid
    except Exception as e:
        print(f"[ERROR ZPID] exception: {e}")
    return None
