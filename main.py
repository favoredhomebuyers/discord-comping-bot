import os
import re
import asyncio
import discord

from utils.address_tools import get_coordinates
from utils.geodata import get_market_info_by_county, ai_extract_county_state
from utils.valuation import get_comp_summary, get_subject_data
from pitch_generator import generate_pitch

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
client = discord.Client(intents=intents)

def parse_message(content: str) -> tuple[str, str, str, int]:
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    addr_line = lines[0] if lines else ""
    notes_match = re.search(r"Notes:\s*(.*)", content, re.IGNORECASE)
    notes = notes_match.group(1).strip() if notes_match else ""
    exit_match = re.search(r"Exit\s*(?:Strategies)?:\s*(.*)", content, re.IGNORECASE)
    exit_type = exit_match.group(1).strip() if exit_match else ""
    level_match = re.search(r"Level:\s*(\d+)", content, re.IGNORECASE)
    level = int(level_match.group(1)) if level_match else 3
    return addr_line, exit_type, notes, level

@client.event
async def on_ready():
    print(f"✅ Underwriting Bot is online as {client.user}")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    content = message.content
    if "exit" not in content.lower() or "notes:" not in content.lower():
        return

    address, exit_type, notes, level = parse_message(content)
    exit_type = exit_type or ""

    lat, lon, city, state, county = get_coordinates(address)
    if not county or not state:
        county, state = ai_extract_county_state(address)

    market_info = get_market_info_by_county(county, state)
    market_lines = "\n".join(f"• {k}: {v}" for k, v in market_info.items())

    subj_raw, subject = await get_subject_data(address)
    subject_sqft = subject.get("sqft") or 0

    if not subject_sqft:
        await message.channel.send(
            f"⚠️ Could not find square footage for {address}.\n"
            "Please include approximate size in your notes like:\n"
            "Notes: Vacant 20 years. Sqft: 1200"
        )
        return

    comps, avg_psf, _ = await get_comp_summary(address)

    comps_text = "\n".join(
        f"• [{c['address']}]({c['zillow_url']}) [{c['grade']}]: "
        f"${c['sold_price']:,} ({c['sqft']} sqft)"
        for c in comps
    ) if comps else "• No comparables found."

    pitch = generate_pitch(notes, exit_type)
    deals = [d.strip().lower() for d in exit_type.split(",") if d.strip()]

    response  = f"📍 Address: {address}\n"
    response += f"📝 Notes: {notes}\n"
    response += f"🔧 Rehab Level: {level}\n"
    response += f"💼 Exit Strategies: {exit_type}\n\n"
    response += f"📊 Market Info:\n{market_lines}\n\n"
    response += f"🏠 Subject Property:\n"
    response += (
        f"• {subject.get('beds','?')} bd / {subject.get('baths','?')} ba   "
        f"{subject_sqft:,} sqft   Built {subject.get('year','?')}\n"
        f"• Lot: {subject.get('lot','?')}   Garage: {subject.get('garage','N/A')}   "
        f"Pool: {'Yes' if subject.get('pool') else 'No'}   Stories: {subject.get('stories','?')}\n"
    )

    arv = avg_psf * subject_sqft if subject_sqft else 0
    as_is = avg_psf * subject_sqft if subject_sqft else 0
    repairs = level * subject_sqft
    fee = 40000

    cash_pct = 0.55 if arv <= 100000 else 0.65 if arv <= 150000 else 0.70 if arv <= 250000 else 0.75 if arv <= 350000 else 0.80 if arv <= 500000 else 0.85
    cash_offer = arv * cash_pct - repairs - fee
    rbp_offer = as_is * 0.95 - fee
    takedown_offer = as_is * 0.95 - 75000

    response += "\n💰 Underwriting Summary:\n"
    response += f"• As-Is Value: ${as_is:,.0f}\n"
    response += f"• ARV: ${arv:,.0f}\n"
    response += f"• Estimated Rehab Cost: ${repairs:,.0f}\n\n"
    response += f"📉 Offer Ranges by Strategy:\n"
    response += f"• Cash Max Offer: ${cash_offer:,.0f}\n"
    response += f"• RBP Max Offer: ${rbp_offer:,.0f}\n"
    response += f"• Takedown Max Offer: ${takedown_offer:,.0f}\n\n"
    response += f"🗣 Pitch:\n{pitch}"

    await message.channel.send(response)

if __name__ == "__main__":
    asyncio.run(client.start(os.getenv("DISCORD_BOT_TOKEN")))
