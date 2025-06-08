import os
import logging

import discord
from dotenv import load_dotenv

from utils.address_tools import parse_address
from utils.valuation import get_comp_summary

# -----------------------------------------------------------------------------
#  CONFIG / BOILERPLATE
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
#  EVENTS
# -----------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    # 1) Ignore messages from other bots (including ourselves)
    if message.author.bot:
        return

    # 2) Ignore blank / whitespace-only messages
    if not message.content or not message.content.strip():
        return

    logger.debug(f"📨 Message from {message.author}: {message.content!r}")

    # 3) Parse the block of text into its pieces
    try:
        address, notes, manual_sqft, exit_str, level = parse_address(
            message.content
        )
    except Exception as e:
        logger.error("Error parsing user input, skipping message", exc_info=e)
        return

    logger.info(f"↳ parsing address: '{address}'")
    if manual_sqft:
        logger.info(f"↳ manual Sqft detected: {manual_sqft}")

    # 4) Fetch comps (pass manual_sqft through)
    try:
        comps, avg_psf, subject_sqft = await get_comp_summary(
            address, manual_sqft
        )
    except Exception as e:
        logger.error("Error fetching comps", exc_info=e)
        await message.channel.send(
            f"⚠️ Failed to fetch comps for `{address}`."
        )
        return

    # 5) No comps? tell the user
    if not comps:
        await message.channel.send(
            f"⚠️ No comparable sales found for `{address}`."
        )
        return

    # 6) Build and send a Discord embed with the results
    embed = discord.Embed(
        title=f"🏠 Comps for {address}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Subject Sqft", value=str(subject_sqft), inline=True)
    embed.add_field(
        name="Avg $/sqft", value=f"${avg_psf:.2f}", inline=True
    )

    for comp in comps:
        comp_text = (
            f"**{comp['address']}**\n"
            f"Sold: ${comp['sold_price']:,}\n"
            f"Sqft: {comp['sqft']}\n"
            f"Beds/Baths: {comp['beds']}/{comp['baths']}\n"
            f"Grade: {comp['grade']}\n"
            f"[View on Zillow]({comp['zillow_url']})"
        )
        embed.add_field(name="\u200b", value=comp_text, inline=False)

    await message.channel.send(embed=embed)


# -----------------------------------------------------------------------------
#  RUN BOT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
