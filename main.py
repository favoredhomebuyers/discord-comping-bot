# main.py
import os
import logging
import asyncio

import discord
from discord import Embed
from utils.address_tools import parse_address
from utils.valuation import get_comp_summary

# ─── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PricingDeptBot")

# ─── Discord Client ───────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # we need read access to message content
bot = discord.Client(intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    logger.debug(f"Connected guilds: {[g.name for g in bot.guilds]}")

@bot.event
async def on_message(message: discord.Message):
    # ignore bot messages & DMs
    if message.author.bot or not message.guild:
        return

    logger.debug(f"📨 Message from {message.author} in {message.channel}: {message.content!r}")

    try:
        # ─── Parse the 5-line input ───────────────────────────────────────────
        address, notes, manual_sqft, exit_str, level = parse_address(message.content)
        logger.info(f"↳ parsing address: {address!r}")
        if manual_sqft:
            logger.info(f"↳ manual Sqft detected: {manual_sqft}")
    except Exception as e:
        logger.error(f"Parsing error: {e}")
        return  # ignore any messages that don’t fit our expected format

    try:
        # ─── Fetch comps & summary ────────────────────────────────────────────
        logger.debug(f"[MAIN] calling get_comp_summary(address={address!r}, manual_sqft={manual_sqft})")
        comps, avg_psf, subject_sqft = await get_comp_summary(address, manual_sqft)
        logger.debug(f"[MAIN] Received {len(comps)} comps, avg_psf={avg_psf:.2f}, subject_sqft={subject_sqft}")
    except Exception as e:
        logger.exception("Error fetching comps")
        await message.reply("⚠️ Something went wrong fetching comparables. See logs for details.")
        return

    # ─── Build reply ───────────────────────────────────────────────────────────
    if not comps:
        await message.reply(f"⚠️ No comparable sales found for `{address}`.")
        return

    embed = Embed(
        title=f"Comps for {address}",
        description=(
            f"Notes: {notes}\n"
            f"Sqft: {subject_sqft}\n"
            f"Exit: {exit_str}\n"
            f"Level: {level}\n"
            f"Avg $/sqft of comps: ${avg_psf:.2f}"
        ),
        color=0x007AFF
    )
    for c in comps:
        embed.add_field(
            name=f"{c['grade']} • {c['address']} • ${c['sold_price']:,}",
            value=(
                f"Beds: {c['beds']}, Baths: {c['baths']}, {c['sqft']} sqft\n"
                f"Year: {c['yearBuilt']}, $/sqft: ${c['psf']}\n"
                f"[View on Zillow]({c['zillow_url']})"
            ),
            inline=False
        )

    logger.info("↳ sending comps embed")
    await message.reply(embed=embed)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN is not set!")
        exit(1)
    bot.run(DISCORD_TOKEN)
