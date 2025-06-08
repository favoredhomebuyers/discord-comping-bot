import os
import logging
import aiohttp
from typing import Optional, Dict, Any

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
    Given a full address string, attempt multiple Zillow RapidAPI endpoints to find the property's ZPID.
    Returns the ZPID as a string, or None if not found.
    """
    logger.debug(f"[ZPID] Finding ZPID for address: {address}")
    params = {"location": address}
    zpid = None
    # Try various endpoints until one returns a valid JSON payload
    for ep in ["GetSearchResults", "getSearchResults", "Search", "search"]:
        url = f"https://{iZILLOW_HOST}/{ep}"
        logger.debug(f"[ZPID] Trying endpoint '{ep}': URL={url}, params={params}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=HEADERS, params=params) as resp:
                    text = await resp.text()
                    logger.debug(f"[ZPID] {ep} raw response (200 first 200 chars or error message): status={resp.status}, text={text[:200]}")
                    if resp.status != 200:
                        continue
                    data = await resp.json()
        except Exception as e:
            logger.exception(f"[ZPID] HTTP error on endpoint '{ep}': {e}")
            continue

        # Attempt to extract ZPID
        try:
            props = data.get("props") or data.get("cat1") or data.get("results")
            if isinstance(props, list) and props:
                first = props[0]
                zpid = first.get("zpid") or first.get("propertyId")
                logger.debug(f"[ZPID] Parsed zpid='{zpid}' from endpoint '{ep}'")
                if zpid:
                    break
        except Exception as e:
            logger.exception(f"[ZPID] Error parsing JSON for endpoint '{ep}': {e}")

    if not zpid:
        logger.warning(f"[ZPID] No ZPID found for address: {address}")
    return zpid
