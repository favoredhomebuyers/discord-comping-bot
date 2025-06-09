# main.py
import os
import logging
from typing import Optional

import discord
from discord.ext import commands

from utils.valuation import get_subject_data, get_comp_summary_by_zpid

# --- Bot & Logger Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PricingDeptBot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")


@bot.command(name="price")
async def price_command(ctx, *, body: str):
    """
    Usage:
    !price
    123 Any St, City, ST 12345
    Sqft: 2,300
    Exit: Takedown
    Level: 1
    """
    # --- 1) Parse the address ---
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    address: str = ""
    for line in lines:
        if "," in line and any(ch.isdigit() for ch in line):
            address = line
            break

    if not address:
        await ctx.send("❗️ Could not parse an address. Make sure the first line is the full address.")
        return

    # --- 2) Parse manual Sqft override ---
    manual_sqft: Optional[int] = None
    for line in lines:
        if line.lower().startswith("sqft"):
            try:
                manual_sqft = int(line.split(":", 1)[1].strip().replace(",", ""))
            except ValueError:
                manual_sqft = None

    # --- 3) Single ZPID lookup + basic subject data ---
    subj_ids, subject = await get_subject_data(address)
    zpid = subj_ids.get("zpid")
    if not zpid:
        await ctx.send(f"⚠️ Could not find a ZPID for {address}.")
        return

    # --- 4) Fetch comps by ZPID (no further ZPID lookups) ---
    comps, avg_psf, subj_sqft = await get_comp_summary_by_zpid(
        zpid=zpid,
        subject=subject,
        manual_sqft=manual_sqft
    )

    # --- 5) Format and send results ---
    if not comps:
        await ctx.send(f"⚠️ No comparable sales found for {address}.")
        return

    # Build a simple markdown table
    table = ["Grade | Sale Date | Price", "---|---|---"]
    for c in comps:
        date = c["last_sold_date"][:10] if c.get("last_sold_date") else "N/A"
        price = f"${c.get('last_sold_price', 0):,}"
        table.append(f"{c['grade']} | {date} | {price}")

    embed = discord.Embed(
        title=f"Comps for {address}",
        description="\n".join(table)
    )
    embed.set_footer(text=f"Subject Sqft: {subj_sqft}")
    await ctx.send(embed=embed)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
