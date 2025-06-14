# utils/zpid_finder.py
import os
import httpx
import logging
from typing import Optional

# must match your ZILLOW_HOST and Z_HEADERS in valuation.py
ZILLOW_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow-com1.p.rapidapi.com")
ZILLOW_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")
Z_HEADERS   = {"x-rapidapi-host": ZILLOW_HOST, "x-rapidapi-key": ZILLOW_KEY}

client = httpx.AsyncClient(timeout=30.0)
logger = logging.getLogger("PricingDeptBot")

async def find_zpid_by_address_async(address: str) -> Optional[str]:
    """
    Uses Zillow’s propertyExtendedSearch to find the zpid for a given address,
    logging debug info via the bot logger. Handles direct 'zpid' key.
    """
    url = f"https://{ZILLOW_HOST}/propertyExtendedSearch"
    try:
        resp = await client.get(url, headers=Z_HEADERS, params={"location": address})
        if resp.status_code != 200:
            logger.warning(f"[ERROR ZPID] propertyExtendedSearch status {resp.status_code}")
            return None

        data = resp.json()
        logger.info(f"[DEBUG ZPID] response keys: {list(data.keys())}")

        # Case 1: direct zpid
        direct = data.get("zpid")
        if isinstance(direct, (str, int)):
            logger.info(f"[DEBUG ZPID] found direct zpid: {direct}")
            return str(direct)

        # Case 2: results or list containers
        hits = data.get("results") or data.get("list") or []
        # Some payloads nest under props.list
        if not hits and isinstance(data.get("props"), dict):
            hits = data["props"].get("list", [])

        if hits and isinstance(hits, list):
            zpid = hits[0].get("zpid")
            if zpid:
                logger.info(f"[DEBUG ZPID] found zpid in list: {zpid}")
                return str(zpid)

    except Exception as e:
        logger.error(f"[ERROR ZPID] exception: {e}")

    return None
