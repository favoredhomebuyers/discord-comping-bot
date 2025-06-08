# utils/zpid_finder.py
import os
import asyncio  # Import asyncio
import httpx     # Import httpx
from typing import Optional

ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY")
HEADERS = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}

# ... (imports and constants are fine) ...

# Ordered list of RapidAPI endpoints to try - UPDATED
SEARCH_ENDPOINTS = [
    "propertyExtendedSearch",
    "property"
]

# ... (the rest of the file is fine) ...

async def find_zpid_by_address_async(address: str) -> Optional[str]:
    """
    Attempts to find a Zillow ZPID via RapidAPI.
    Retries on 429 rate limits with exponential backoff.
    """
    async with httpx.AsyncClient() as client:
        for ep in SEARCH_ENDPOINTS:
            url = f"https://{ZILLOW_HOST}/{ep}"
            params = {"location": address}
            for attempt in range(3):
                resp = await client.get(url, headers=HEADERS, params=params)
                if resp.status_code == 429:
                    # rate limit, back off and retry asynchronously
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    # Common JSON paths for list of results
                    results = (
                        data.get("searchResults", {}).get("list") or
                        data.get("props") or
                        []
                    )
                    if results:
                        first = results[0]
                        zpid = first.get("zpid") or first.get("property_id")
                        if zpid:
                            return str(zpid)
                break
    return None
