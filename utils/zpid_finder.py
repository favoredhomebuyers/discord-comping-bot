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
    Uses Zillow’s propertyExtendedSearch to find the zpid for a given address.
    """
    url = f"https://{ZILLOW_HOST}/propertyExtendedSearch"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"location": address})
        if resp.status_code != 200:
            print(f"[ERROR ZPID] status {resp.status_code}")
            return None
        data = resp.json()
        # Zillow may return under "results" or "list" depending on version
        hits = data.get("results") or data.get("list") or []
        if hits:
            return hits[0].get("zpid")
    except Exception as e:
        print(f"[ERROR ZPID] {e}")
    return None
