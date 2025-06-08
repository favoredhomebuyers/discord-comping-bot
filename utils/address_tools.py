import os
import googlemaps
from typing import Optional, Tuple

# Initialize Google Maps client
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
if not GOOGLE_MAPS_API_KEY:
    raise EnvironmentError("Missing GOOGLE_MAPS_API_KEY")

gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

def get_coordinates(address: str) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str], Optional[str]]:
    """
    Geocode and reverse-geocode an address to get coordinates and locality data.

    Returns:
        (lat, lon, city, state, county)
    """
    try:
        geocode_result = gmaps.geocode(address, region="us")
        if not geocode_result:
            print(f"[Geo] ❌ No geolocation found for: {address}")
            return None, None, None, None, None

        location = geocode_result[0]["geometry"]["location"]
        lat, lon = location["lat"], location["lng"]

        rev = gmaps.reverse_geocode(
            (lat, lon), 
            result_type=["locality", "administrative_area_level_1", "administrative_area_level_2"]
        )

        city = state = county = None
        for comp in rev[0]["address_components"]:
            types = comp.get("types", [])
            if "locality" in types:
                city = comp["long_name"]
            if "administrative_area_level_1" in types:
                state = comp["short_name"]
            if "administrative_area_level_2" in types:
                county = comp["long_name"].replace(" County", "").strip()

        print(f"[Geo] ✅ {address} → ({lat}, {lon}), {city}, {state}, {county}")
        return lat, lon, city, state, county

    except Exception as e:
        print(f"[Geo] ❌ Error geocoding address '{address}': {e}")
        return None, None, None, None, None
