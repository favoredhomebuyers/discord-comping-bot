# main.py
import os
import logging
import asyncio

import discord
from discord import Embed
from utils.address_tools import parse_address
from utils.valuation import get_comp_summary
from utils.geodata import ai_extract_county_state, get_market_info_by_county, infer_market_type
from utils.comps import get_comps_and_arv
from utils.pitch_generator import generate_pitch

# ─── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PricingDeptBot")

# ─── Discord Client ───────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    logger.debug(f"Connected guilds: {[g.name for g in bot.guilds]}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    logger.debug(f"📨 Message from {message.author} in {message.channel}: {message.content!r}")

    try:
        address, notes, manual_sqft, exit_str, level_str = parse_address(message.content)
        level = int(level_str) if level_str.isdigit() else 1
        logger.info(f"↳ Parsing successful for address: {address!r}")
    except Exception as e:
        logger.debug(f"Ignoring message that failed parsing: {e}")
        return

    async with message.channel.typing():
        try:
            # --- 1. Fetch Comps & Valuation ---
            comps, avg_psf, subject_sqft = await get_comp_summary(address, manual_sqft)
            logger.info(f"↳ Received {len(comps)} comps, avg_psf=${avg_psf:.2f}, subject_sqft={subject_sqft}")

            if not comps:
                await message.reply(f"⚠️ No comparable sales found for `{address}`.")
                return

            # --- 2. Get Market Info ---
            county, state = await asyncio.to_thread(ai_extract_county_state, address)
            market_info = {}
            if county and state:
                market_info = get_market_info_by_county(county, state)
            market_type = infer_market_type(market_info.get("Days on Market", ""))
            
            # --- 3. Get Deal Breakdowns ---
            deals = get_comps_and_arv(subject_sqft, avg_psf, level)

            # --- 4. Generate Sales Pitch ---
            pitch = generate_pitch(notes, exit_str)

        except Exception as e:
            logger.exception("Error during comp processing pipeline")
            await message.reply("⚠️ Something went wrong while processing comps. See logs for details.")
            return

    # --- 5. Assemble the Final Response ---
    main_content = (
        f"**Exit Strategies:** {exit_str}\n"
        f"**Notes:** {notes}\n"
        f"**Address:** {address}\n\n"
        f"**Market Info:**\n"
        f"• **Market:** {market_info.get('Market', 'Unknown')}\n"
        f"• **Population:** {market_info.get('Population', 'Unknown')}\n"
        f"• **Home Value Growth (YoY):** {market_info.get('Home Value Growth (YoY)', 'Unknown')}\n"
        f"• **Home Value:** {market_info.get('Home Value', 'Unknown')}\n"
        f"• **Price Cut %:** {market_info.get('Price Cut %', 'Unknown')}\n"
        f"• **Days on Market:** {market_info.get('Days on Market', 'Unknown')}\n\n"
        f"**Comps:**\n"
    )

    for c in comps:
        main_content += f"• **{c['address']}**: ${c['sold_price']:,} (${c['psf']}/sqft)\n"

    main_content += (
        f"\n**Deal Breakdowns:**\n"
        f"\n— **Cash** —\n"
        f"  • **ARV:** {subject_sqft} sqft × ${avg_psf:,.2f}/sqft = ${deals['arv']:,.0f}\n"
        f"  • **Repairs:** -${deals['rehab_cost']:,}\n"
        f"  • **Fee:** -${deals['fee']:,}\n"
        f"  • **MOA Cash:** `${deals['cash_offer']:,}`\n"
        f"\n— **RBP** —\n"
        f"  • **As-Is Value:** ${deals['arv']:,.0f}\n"
        f"  • **RBP factor (90%):** ×0.90 = ${deals['as_is_value_rbp']:,.0f}\n"
        f"  • **Fee:** -${deals['fee']:,}\n"
        f"  • **MOA RBP:** `${deals['rbp_offer']:,}`\n\n"
        f"**Pitch:**\n{pitch}"
    )

    embed = Embed(
        title=f"Zillow Links for {address}",
        color=0x007AFF
    )
    for i, c in enumerate(comps):
        embed.add_field(
            name=f"Comp #{i+1}: {c['address']}",
            value=f"[View on Zillow]({c['zillow_url']})",
            inline=False
        )

    logger.info("↳ Sending final detailed response to Discord.")
    await message.reply(content=main_content, embed=embed)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN is not set!")
        exit(1)
    
    # Corrected validation for all essential API keys
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set!")
    if not os.getenv("ZILLOW_RAPIDAPI_KEY"):
        logger.error("ZILLOW_RAPIDAPI_KEY is not set!")
    if not os.getenv("ATTOM_API_KEY"):
        logger.error("ATTOM_API_KEY is not set!")
    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        logger.error("GOOGLE_MAPS_API_KEY is not set!")

    bot.run(DISCORD_TOKEN)
