import os
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# Zillow RapidAPI config
iZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
iZILLOW_KEY = os.getenv("ZILLOW_RAPIDAPI_KEY", "")

HEADERS = {
    "x-rapidapi-host": iZILLOW_HOST,
    "x-rapidapi-key": iZILLOW_KEY,
}

async def find_zpid_by_address_async(address: str) -> Optional[str]:
    """
    Given a full address string, query Zillow via RapidAPI to find the property's ZPID.
    Returns the ZPID as a string, or None if not found.
    """
    # Endpoint for search results
    url = f"https://{iZILLOW_HOST}/search"
    params = {"location": address}

    logger.debug(f"[ZPID] Searching for ZPID: URL={url}, params={params}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS, params=params) as resp:
                text = await resp.text()
                logger.debug(f"[ZPID] Raw response text (first 500 chars): {text[:500]}")
                data = await resp.json()
    except Exception as e:
        logger.exception(f"[ZPID] HTTP error while fetching ZPID for {address}: {e}")
        return None

    # Attempt to extract ZPID from JSON
    zpid = None
    try:
        # Zillow RapidAPI typically returns "props": [...], each has "zpid"
        props = data.get("props") or data.get("cat1")
        if isinstance(props, list) and props:
            # pick the first result
            first = props[0]
            zpid = first.get("zpid") or first.get("propertyId")
        logger.debug(f"[ZPID] Parsed response JSON, extracted zpid={zpid}")
    except Exception as e:
        logger.exception(f"[ZPID] Error parsing ZPID JSON for {address}: {e}")

    return zpid
