# utils/address_tools.py
import os
import requests
from typing import Tuple

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

def get_coordinates(address: str) -> Tuple[float, float, str, str, str]:
    """
    Geocodes the address via Google Maps and returns:
    (latitude, longitude, city, state, postal_code)
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "region": "us", "key": GOOGLE_MAPS_API_KEY}
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("results"):
        raise ValueError(f"Could not geocode address: {address}")
    first = data["results"][0]
    loc = first["geometry"]["location"]
    lat = loc["lat"]
    lng = loc["lng"]
    city = state = postal_code = ""
    for comp in first.get("address_components", []):
        types = comp.get("types", [])
        if "locality" in types:
            city = comp.get("long_name", "")
        if "administrative_area_level_1" in types:
            state = comp.get("short_name", "")
        if "postal_code" in types:
            postal_code = comp.get("long_name", "")
    return lat, lng, city, state, postal_code


def parse_address(address: str) -> Tuple[str, str, str, str]:
    """
    Parses an address string like '123 Main St, Anytown, CA 12345' into
    (street, city, state, postal_code).
    """
    parts = [p.strip() for p in address.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Address '{address}' not in expected format 'street, city, state ZIP'")
    street = parts[0]
    city = parts[1]
    state_zip = parts[2].split()
    if len(state_zip) < 2:
        raise ValueError(f"State and ZIP not found in '{parts[2]}'")
    state = state_zip[0]
    postal_code = state_zip[1]
    return street, city, state, postal_code
