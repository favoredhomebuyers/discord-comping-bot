import os
import requests
from typing import Tuple

# Load your Google Maps API key from environment
GMAPS_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def get_coordinates(address: str) -> Tuple[float, float, str, str, str]:
    """
    Geocode an address string using Google Maps Geocoding API.
    Returns a tuple: (latitude, longitude, city, state, postal_code).
    """
    if not GMAPS_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY environment variable not set")

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": GMAPS_KEY,
        "region": "us",
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results")
    if not results:
        raise ValueError(f"Geocoding failed for address: {address}")

    # Take the first result
    result = results[0]
    location = result["geometry"]["location"]
    lat = location.get("lat")
    lon = location.get("lng")

    # Parse address components
    city = ""
    state = ""
    postal_code = ""
    for comp in result.get("address_components", []):
        types = comp.get("types", [])
        if "locality" in types:
            city = comp.get("long_name")
        if "administrative_area_level_1" in types:
            state = comp.get("short_name")
        if "postal_code" in types:
            postal_code = comp.get("long_name")

    return lat, lon, city, state, postal_code


def parse_address(message: str) -> Tuple[str, str, str, str]:
    """
    Extract street, city, state, and postal code from the first non-empty line
    of a Discord message or free-form text. Assumes the format:
        1705 Magnolia Ave, San Bernardino, CA 92411
    Returns: (street, city, state, postal_code)
    """
    # Find first non-blank line
    first_line = ""
    for line in message.splitlines():
        line = line.strip()
        if line:
            first_line = line
            break
    if not first_line:
        return "", "", "", ""

    # Split by commas
    parts = [p.strip() for p in first_line.split(",")]  # e.g. [street, city, "CA 92411"]
    street = parts[0] if len(parts) > 0 else ""
    city = parts[1] if len(parts) > 1 else ""

    # Extract state and postal code
    state = ""
    postal_code = ""
    if len(parts) > 2:
        tail = parts[2].split()
        if tail:
            state = tail[0]
        if len(tail) > 1:
            postal_code = tail[1]

    return street, city, state, postal_code
