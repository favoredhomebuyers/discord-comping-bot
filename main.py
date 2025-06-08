# main.py

import os
import logging
import discord
import asyncio
from datetime import datetime
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async
from utils.valuation import get_comp_summary  # corrected path

  
# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    logger.error("DISCORD_BOT_TOKEN not set in environment!")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    logger.info(f"🔑 Bot logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"   Connected to guilds: {[g.name for g in bot.guilds]}")

@bot.event
async def on_message(message):
    # ignore yourself
    if message.author.bot:
        return

    logger.debug(f"📨 Message from {message.author.name} in {message.channel.name}: '''{message.content}'''")

    # parse address = first line
    lines = message.content.strip().splitlines()
    if not lines:
        return

    address = lines[0].strip()
    logger.info(f"↳ parsing address: '{address}'")

    # fetch comps
    try:
        comps, avg_psf, sqft = await get_comp_summary(address)
        logger.debug(f"    ↳ get_comp_summary -> comps: {comps!r}")
        logger.debug(f"    ↳ get_comp_summary -> avg_psf: {avg_psf!r}, sqft: {sqft!r}")
    except Exception as e:
        logger.exception("❌ Exception in get_comp_summary")
        await message.channel.send(f"⚠️ Sorry, I ran into an error fetching comps: `{e}`")
        return

    # if no sqft, ask user for notes
    if not sqft:
        reply = (
            f"⚠️ Could not find square footage for `{address}`.\n"
            "Please include approximate size in your notes like:\n"
            "`Notes: Vacant 20 years. Sqft: 1200`"
        )
        logger.info("↳ sending reply: asking for sqft")
        await message.channel.send(reply)
        return

    # build a simple embed of the results
    embed = discord.Embed(
        title=f"📊 Comps for {address}",
        description=f"Subject Sqft: **{sqft}** | Avg PSF: **${avg_psf:.2f}**",
        color=0x00FF00,
        timestamp=datetime.utcnow(),
    )

    for comp in comps:
        embed.add_field(
            name=f"{comp['address']} ({comp['grade']})",
            value=(
                f"${comp['sold_price']:,}  |  "
                f"{comp['sqft']} sqft  |  "
                f"{comp['beds']}bd/{comp['baths']}ba  |  "
                f"[Zillow]({comp['zillow_url']})"
            ),
            inline=False,
        )

    logger.info("↳ sending embed with comps")
    await message.channel.send(embed=embed)

if __name__ == "__main__":
    logger.info("🔌 Starting bot…")
    bot.run(DISCORD_TOKEN)
