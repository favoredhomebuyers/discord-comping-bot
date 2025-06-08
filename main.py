import os
import discord
import logging
import asyncio

from utils.address_tools import get_coordinates
from utils.zpid_finder import find_zpid_by_address_async
from valuation import get_comp_summary  # your updated valuation logic

# ─── Setup basic logging to stdout ─────────────────────────────────────────────
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)8s %(message)s')
log = logging.getLogger(__name__)

# ─── Discord client with message content intent ────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ─── Event: Bot ready ────────────────────────────────────────────────────────────
@client.event
async def on_ready():
    log.info(f"✅ Bot logged in as {client.user} (ID: {client.user.id})")
    log.info(f"   Connected to guilds: {[g.name for g in client.guilds]}")

# ─── Event: Message received ─────────────────────────────────────────────────────
@client.event
async def on_message(message: discord.Message):
    # ignore messages from ourselves
    if message.author.id == client.user.id:
        return

    log.debug(f"📨 Message received from {message.author} in {message.channel}: {message.content!r}")

    # only respond to messages in DMs or a specific prefix or channel
    if not message.content.strip():
        log.debug("   ↳ empty message, skipping")
        return

    # Example: we treat any message as an address request
    address = message.content.strip().split("\n")[0]
    log.info(f"   ↳ parsing address: {address!r}")

    try:
        comps, avg_psf, sqft = await get_comp_summary(address)
        log.debug(f"   ↳ got comps: {comps}, avg_psf: {avg_psf}, sqft: {sqft}")

        if sqft == 0:
            reply = (
                f"⚠️ Could not find square footage for `{address}`.\n"
                "Please include approximate size in your notes like:\n"
                "`Notes: Vacant 20 years. Sqft: 1200`"
            )
        elif not comps:
            reply = f"⚠️ No comparable sales found for `{address}` within your criteria."
        else:
            lines = [f"🏠 Comps for **{address}** (sqft: {sqft}, avg $/sqft: ${avg_psf:.2f}):"]
            for comp in comps:
                lines.append(
                    f"- {comp['address']} — ${comp['sold_price']} · "
                    f"{comp['sqft']} sqft · ${comp['psf']}/sqft · Grade {comp['grade']}"
                )
            reply = "\n".join(lines)

        log.info(f"   ↳ sending reply:\n{reply}")
        await message.reply(reply)

    except Exception as e:
        log.exception("❌ Error handling message")
        await message.reply(f"❌ An error occurred while processing `{address}`:\n```{e}```")

# ─── Run the bot ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        log.critical("DISCORD_BOT_TOKEN is not set in environment variables!")
        exit(1)

    log.info("🔑 Starting bot...")
    client.run(token)
