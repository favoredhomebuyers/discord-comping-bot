import os
import re
import logging
import discord
from datetime import datetime
from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async
from utils.valuation import get_subject_data, fetch_zillow_comps, get_clean_comps

# ─── Logging ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Discord Setup ───────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    logger.critical("DISCORD_BOT_TOKEN is not set!")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# ─── Events ──────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(f"🔑 Bot logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"   Connected to guilds: {[g.name for g in bot.guilds]}")

@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from bots (including self)
    if message.author.bot:
        return

    content = message.content.strip()
    if not content:
        return

    logger.debug(f"📨 Message from {message.author} in {message.channel}: '''{content}'''")

    # Parse: first line = address, remaining lines = notes
    lines = content.splitlines()
    address = lines[0].strip()
    notes   = "\n".join(lines[1:]) if len(lines) > 1 else ""

    logger.info(f"↳ parsing address: '{address}'")

    # Extract manual Sqft if provided
    manual_sqft = None
    match = re.search(r"(?i)\bSqft[:\s]*([0-9,]+)", notes)
    if match:
        try:
            manual_sqft = int(match.group(1).replace(",", ""))
            logger.info(f"↳ manual Sqft detected: {manual_sqft}")
        except ValueError:
            logger.warning(f"↳ invalid manual Sqft value: {match.group(1)}")

    # Get subject info (Zillow + fallbacks)
    try:
        subj, subject = await get_subject_data(address)
        logger.debug(f"↳ get_subject_data -> subj: {subj}, subject: {subject}")
    except Exception as e:
        logger.exception("❌ Error in get_subject_data")
        await message.channel.send(f"❌ Error fetching property data: `{e}`")
        return

    # Override sqft if user provided
    if manual_sqft is not None:
        subject["sqft"] = manual_sqft
        logger.debug(f"↳ overridden subject['sqft'] to: {manual_sqft}")

    # Ensure we have a sqft
    sqft = subject.get("sqft") or 0
    if sqft == 0:
        reply = (
            f"⚠️ Could not determine square footage for `{address}`.\n"
            "Please include it manually, e.g.: `Sqft: 1200`"
        )
        logger.info("↳ prompting user for sqft")
        await message.channel.send(reply)
        return

    # Fetch comps list if ZPID available
    comps_raw = []
    zpid = subj.get("zpid")
    if zpid:
        try:
            comps_raw = fetch_zillow_comps(zpid)
            logger.debug(f"↳ fetched raw comps: {comps_raw}")
        except Exception as e:
            logger.exception("❌ Error fetching comps from Zillow")

    # Clean and filter comps
    try:
        clean_comps, avg_psf = get_clean_comps(subject, comps_raw)
        logger.debug(f"↳ clean_comps: {clean_comps}, avg_psf: {avg_psf}")
    except Exception as e:
        logger.exception("❌ Error in get_clean_comps")
        await message.channel.send(f"❌ Error processing comparables: `{e}`")
        return

    # If no comps found, notify and return
    if not clean_comps:
        logger.info(f"↳ no comps found for {address}")
        await message.channel.send(f"⚠️ No comparable sales found for `{address}`.")
        return

    # Build embed for results
    embed = discord.Embed(
        title=f"📊 Comps for {address}",
        description=f"Subject Sqft: **{sqft}** | Avg $/sqft: **${avg_psf:.2f}**",
        color=0x00FF00,
        timestamp=datetime.utcnow(),
    )
    for comp in clean_comps:
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

    logger.info("↳ sending comps embed")
    await message.channel.send(embed=embed)

# ─── Bot Start ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🔌 Starting bot…")
    bot.run(DISCORD_TOKEN)
