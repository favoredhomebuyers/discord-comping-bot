# main.py

import os
import logging
import discord
from dotenv import load_dotenv

from utils.address_tools import parse_address
from utils.valuation import (
    get_subject_data,
    fetch_zillow_comps,
    fetch_attom_comps,       # ← implement this if you haven’t already
    get_clean_comps,
)

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)7s %(message)s",
)

intents = discord.Intents.default()
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    # 1) ignore other bots
    if message.author.bot:
        return

    logging.debug(f"📨 Message from {message.author}: {message.content!r}")

    # 2) parse the Discord message
    address, notes, manual_sqft, exit_str, level = parse_address(message.content)
    logging.info(f"↳ parsed address: '{address}'")
    logging.debug(f"[PARSE] notes={notes!r}, manual_sqft={manual_sqft}, exit={exit_str!r}, level={level!r}")

    # 3) fetch subject data (ZPID + fallback to coords)
    logging.debug(f"[VAL] get_subject_data for '{address}'")
    subj, subject = await get_subject_data(address)
    logging.debug(f"[VAL] subject (pre‐override): {subject}")

    # 4) override with manual sqft if provided
    if manual_sqft is not None:
        subject["sqft"] = manual_sqft
        logging.info(f"↳ manual Sqft detected, overriding subject['sqft'] → {manual_sqft}")

    # 5) attempt Zillow comps first
    zpid = subj.get("zpid")
    clean_comps, avg_psf = [], 0.0
    if zpid:
        comps_raw = fetch_zillow_comps(zpid)
        logging.debug(f"[VAL] fetched {len(comps_raw)} raw Zillow comps")
        clean_comps, avg_psf = get_clean_comps(subject, comps_raw)
        logging.debug(f"[VAL] after Zillow filtering → {len(clean_comps)} comps, avg_psf={avg_psf:.2f}")

    # 6) if no Zillow comps, try ATTOM
    if not clean_comps:
        logging.info("↳ no Zillow comps found, falling back to ATTOM")
        attom_raw = fetch_attom_comps(address)
        logging.debug(f"[VAL] fetched {len(attom_raw)} raw ATTOM comps")
        clean_comps, avg_psf = get_clean_comps(subject, attom_raw)
        logging.debug(f"[VAL] after ATTOM filtering → {len(clean_comps)} comps, avg_psf={avg_psf:.2f}")

    # 7) reply
    if not clean_comps:
        # still nothing?
        reply = f"⚠️ No comparable sales found for `{address}`."
        logging.info(f"↳ sending reply: {reply}")
        await message.channel.send(reply)
        return

    # 8) build the embed
    embed = discord.Embed(
        title=f"🏘 Comparable Sales for {address}",
        color=0x2ecc71,
    )
    embed.add_field(name="Subject Sqft", value=subject["sqft"], inline=True)
    embed.add_field(name="Avg $/Sqft", value=f"${avg_psf:,.2f}", inline=True)
    embed.add_field(name="Notes", value=notes or "—", inline=False)
    embed.add_field(name="Exit", value=exit_str or "—", inline=True)
    embed.add_field(name="Level", value=level or "—", inline=True)

    for comp in clean_comps:
        desc = (
            f"💰 ${comp['sold_price']:,}\n"
            f"📐 {comp['sqft']} sqft\n"
            f"🛏 {comp['beds']} | 🛁 {comp['baths']}\n"
            f"Grade: {comp['grade']}"
        )
        embed.add_field(name=comp["address"], value=desc, inline=False)
        embed.add_field(name="🔗 Link", value=comp["zillow_url"], inline=False)

    logging.info("↳ sending comps embed")
    await message.channel.send(embed=embed)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
