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
    if message.author.bot:
        return

    content = message.content.strip()
    if not content:
        return

    logger.debug(f"📨 Message from {message.author} in {message.channel}: '''{content}'''")

    # Split into lines: first line = address, rest = notes
    lines = content.splitlines()
    address = lines[0].strip()
    notes   = "\n".join(lines[1:]) if len(lines) > 1 else ""

    logger.info(f"↳ parsing address: '{address}'")

    # 1) Try to extract manual Sqft:
    manual_sqft = None
    m = re.search(r"(?i)\bSqft[:\s]*([0-9,]+)", notes)
    if m:
        try:
            manual_sqft = int(m.group(1).replace(",", ""))
            logger.info(f"↳ manual Sqft detected: {manual_sqft}")
        except ValueError:
            logger.warning(f"↳ could not parse manual Sqft: {m.group(1)}")

    # 2) Fetch core subject data via Zillow (and fallback)
    subj, subject = await get_subject_data(address)

    # 3) Override subject sqft if manual provided
    if manual_sqft is not None:
        subject['sqft'] = manual_sqft
        logger.debug(f"↳ overriding subject['sqft'] with manual value: {manual_sqft}")

    # 4) If still no sqft, ask user to supply it
    if not subject.get('sqft'):
        reply = (
            f"⚠️ Could not determine square footage for `{address}`.\n"
            "Please include it manually in your message, for example:\n"
            "`Sqft: 1200`"
        )
        logger.info("↳ asking user to provide Sqft")
        await message.channel.send(reply)
        return

    # 5) Fetch comps list based on ZPID
    comps_raw = []
    if subj.get('zpid'):
        comps_raw = fetch_zillow_comps(subj['zpid'])
    clean_comps, avg_psf = get_clean_comps(subject, comps_raw)
    logger.debug(f"↳ clean_comps={clean_comps}, avg_psf={avg_psf}")

    # 6) Build and send embed of results
    embed = discord.Embed(
        title=f"📊 Comps for {address}",
        description=f"Subject Sqft: **{subject['sqft']}** | Avg $/sqft: **${avg_psf:.2f}**",
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

# ─── Run ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🔌 Starting bot…")
    bot.run(DISCORD_TOKEN)
