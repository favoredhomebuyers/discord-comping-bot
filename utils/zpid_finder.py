import re
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

async def find_zpid_by_address_async(address: str) -> str:
    """
    Uses Playwright async to load the Zillow listing page and extract the ZPID.
    """
    try:
        query = quote_plus(address)
        url = f"https://www.zillow.com/homes/{query}_rb/"
        print("[ZPID-Finder] 🔍 Launching Playwright for:", address)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=15000)
            content = await page.content()
            await browser.close()

        match = re.search(r'"zpid":\s*"?(?P<zpid>\d{8,})"?', content)
        if match:
            zpid = match.group("zpid")
            print("[ZPID-Finder] ✅ Found ZPID:", zpid)
            return zpid
        else:
            print("[ZPID-Finder] ❌ ZPID not found in HTML")
            return None

    except Exception as e:
        print("[ZPID-Finder] ❌ Exception occurred:", e)
        return None
