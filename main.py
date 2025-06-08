import os
import logging

import discord
from dotenv import load_dotenv

from utils.address_tools import parse_address
from utils.valuation import get_comp_summary

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set in your environment")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(intents=intents)


# -----------------------------------------------------------------------------
# EVENTS
# -----------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    # 1) Ignore other bots (including ourselves)
    if message.author.bot:
        return

    # 2) Ignore blank / whitespace messages
    content = message.content or ""
    if not content.strip():
        return

    logger.debug(f"📨 Message from {message.author}: {content!r}")

    # 3) Parse user's 5-line block (address, notes, sqft, exit, level)
    try:
        address, notes, manual_sqft, exit_str, level = parse_address(content)
    except Exception:
        # parsing failed (malformed input), just skip
        logger.exception("Failed to parse address block; skipping")
        return

    logger.info(f"↳ parsing address: '{address}'")
    if manual_sqft:
        logger.info(f"↳ manual Sqft detected: {manual_sqft}")

    # 4) Fetch comps — pass only address (your valuation code
    #    already checks for manual_sqft internally)
    try:
        comps, avg_psf, subject_sqft = await get_comp_summary(address)
    except Exception:
        logger.exception("Error in get_comp_summary")
        await message.channel.send(f"⚠️ Could not fetch comps for `{address}`.")
        return

    # 5) No comps → notify
    if not comps:
        await message.channel.send(f"⚠️ No comparable sales found for `{address}`.")
        return

    # 6) Build & send embed
    embed = discord.Embed(
        title=f"🏠 Comps for {address}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Subject Sqft", value=str(subject_sqft), inline=True)
    embed.add_field(name="Avg $/sqft", value=f"${avg_psf:.2f}", inline=True)

    for comp in comps:
        # each comp is expected to have keys:
        #   address, sold_price, sqft, beds, baths, grade, zillow_url
        comp_block = (
            f"**{comp['address']}**\n"
            f"Sold: ${comp['sold_price']:,}\n"
            f"Sqft: {comp['sqft']}\n"
            f"Beds/Baths: {comp['beds']}/{comp['baths']}\n"
            f"Grade: {comp['grade']}\n"
            f"[View on Zillow]({comp['zillow_url']})"
        )
        embed.add_field(name="\u200b", value=comp_block, inline=False)

    await message.channel.send(embed=embed)


# -----------------------------------------------------------------------------
# RUN
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
