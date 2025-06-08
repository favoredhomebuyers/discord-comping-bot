import os
import json
import pandas as pd
import openai
from typing import Optional, Tuple, Dict

# Initialize OpenAI client
openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI()

CSV_PATH = "reventure-metro-data.csv"

# Load once at module level
df = pd.read_csv(CSV_PATH)

def normalize(text: str) -> str:
    return str(text).strip().lower()

def ai_extract_county_state(address: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Uses OpenAI to extract county and state from an address.
    Returns (county, state) or (None, None) on failure.
    """
    prompt = f"""
Extract the county and state from this address: {address}
Return only county and state in JSON format like:
{{"county": "Fulton", "state": "GA"}}
Respond ONLY with JSON.
""".strip()

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = "\n".join(content.split("\n")[1:-1]).strip()
        data = json.loads(content)
        return data.get("county"), data.get("state")
    except Exception as e:
        print(f"[GeoAI] ❌ Failed to extract county/state via OpenAI: {e}")
        return None, None

def get_market_info_by_county(county: str, state: str, city: str = "") -> Dict[str, str]:
    """
    Match county and state to a row in metro data CSV.
    Returns a market info dictionary or default values if not found.
    """
    county_norm = normalize(county).split()[0]
    state_norm = normalize(state)[:2]

    for _, row in df.iterrows():
        name = normalize(row.get("Name", ""))
        if county_norm in name and state_norm in name:
            return {
                "Market": row.get("Name", "Unknown"),
                "Population": row.get("Population", "Unknown"),
                "Home Value Growth (YoY)": row.get("Home Value Growth (YoY)", "Unknown"),
                "Home Value": row.get("Home Value", "Unknown"),
                "Price Cut %": row.get("Price Cut %", "Unknown"),
                "Days on Market": row.get("Days on Market", "Unknown"),
            }

    return {
        "Market": f"{county.title()}, {state.upper()}",
        "Population": "Unknown",
        "Home Value Growth (YoY)": "Unknown",
        "Home Value": "Unknown",
        "Price Cut %": "Unknown",
        "Days on Market": "Unknown",
    }

def infer_market_type(dom_str: str) -> str:
    """
    Convert 'Days on Market' string to a market type: cold / warm / hot.
    """
    try:
        dom = int(str(dom_str).replace("+", "").strip())
        if dom > 60:
            return "cold"
        elif dom < 30:
            return "hot"
        return "warm"
    except:
        return "warm"
