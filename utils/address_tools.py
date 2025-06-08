import os
import re
import requests
from typing import Tuple, Optional

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def get_coordinates(address: str) -> Tuple[float, float, Optional[str], Optional[str]]:
    """
    Given a street address, returns (latitude, longitude, city, state).
    Raises if no result is found.
    """
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY,
        "region": "us"
    }
    resp = requests.get(geocode_url, params=params)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    if not results:
        raise ValueError(f"No geocoding result for '{address}'")
    loc = results[0]["geometry"]["location"]
    lat, lon = loc["lat"], loc["lng"]

    # Reverse-geocode to get locality (city) and admin area level 1 (state)
    params2 = {
        "latlng": f"{lat},{lon}",
        "key": GOOGLE_MAPS_API_KEY,
        "result_type": "locality|administrative_area_level_1"
    }
    resp2 = requests.get(geocode_url, params=params2)
    resp2.raise_for_status()
    data2 = resp2.json()
    city = state = None
    for comp in data2.get("results", []):
        for ac in comp.get("address_components", []):
            types = ac.get("types", [])
            if "locality" in types:
                city = ac.get("long_name")
            elif "administrative_area_level_1" in types:
                state = ac.get("short_name")
        if city and state:
            break

    return lat, lon, city, state


def parse_address(content: str) -> Tuple[str, Optional[str], Optional[int], Optional[str], Optional[str]]:
    """
    Parses a multi-line Discord message into:
      - address (first line)
      - notes (value after 'Notes:')
      - sqft (int after 'Sqft:')
      - exit (value after 'Exit:')
      - level (value after 'Level:')
    Returns a 5-tuple: (address, notes, sqft, exit, level)
    Any missing or unparsable fields become None.
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    address = lines[0]

    notes = None
    sqft = None
    exit_str = None
    level = None

    for line in lines[1:]:
        key, sep, val = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        val = val.strip()
        if key == "notes":
            notes = val
        elif key == "sqft":
            # extract digits only
            digits = re.sub(r"[^\d]", "", val)
            if digits.isdigit():
                sqft = int(digits)
        elif key == "exit":
            exit_str = val
        elif key == "level":
            level = val

    return address, notes, sqft, exit_str, level
