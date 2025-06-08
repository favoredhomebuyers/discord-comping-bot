import os
import re
import logging
import googlemaps
from typing import Tuple, Optional

# ─── Logging Setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger("AddressTools")

# ─── Google Maps Client ────────────────────────────────────────────────────────
GMAPS_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
gmaps = googlemaps.Client(key=GMAPS_KEY) if GMAPS_KEY else None


def get_coordinates(address: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Geocode an address via Google Maps and return (latitude, longitude, formatted_address).
    """
    if not gmaps:
        logger.error("Google Maps API key not configured.")
        return None, None, None

    logger.debug(f"📍 Geocoding address: {address}")
    try:
        results = gmaps.geocode(address, region="us")
    except Exception as e:
        logger.error(f"Geocoding error for {address}: {e}")
        return None, None, None

    if not results:
        logger.warning(f"No geocode results for: {address}")
        return None, None, None

    top = results[0]
    loc = top.get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lng = loc.get("lng")
    formatted = top.get("formatted_address")
    logger.debug(f"↳ got coordinates: ({lat}, {lng}) formatted: {formatted}")
    return lat, lng, formatted


def parse_address(text: str) -> Tuple[str, str, Optional[int], str, str]:
    """
    Expects a block of lines:
      1) address
      2) Notes: ...
      3) optional Sqft: <number>
      4) Exit: ...
      5) Level: ...
    Returns (address, notes, manual_sqft, exit_str, level)
    manual_sqft is int or None if missing.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        raise ValueError("Expected at least 4 non-empty lines (address, notes, exit, level)")

    address = lines[0]
    notes = ""
    manual_sqft = None
    exit_str = ""
    level = ""

    for line in lines[1:]:
        low = line.lower()
        if low.startswith("notes"):  # Notes: ...
            parts = line.split(":", 1)
            notes = parts[1].strip() if len(parts) > 1 else ""
        elif low.startswith("sqft"):  # Sqft: 1234
            parts = line.split(":", 1)
            val = parts[1].strip() if len(parts) > 1 else ""
            num = re.sub(r"[^0-9]", "", val)
            try:
                manual_sqft = int(num) if num else None
            except ValueError:
                manual_sqft = None
        elif low.startswith("exit"):  # Exit: Cash
            parts = line.split(":", 1)
            exit_str = parts[1].strip() if len(parts) > 1 else ""
        elif low.startswith("level"):  # Level: 2
            parts = line.split(":", 1)
            level = parts[1].strip() if len(parts) > 1 else ""

    return address, notes, manual_sqft, exit_str, level
