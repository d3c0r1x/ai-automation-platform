from playwright.async_api import async_playwright


async def read_page(url: str) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        title = await page.title()
        text = (await page.locator("body").inner_text())[:2000]
        await browser.close()
    return {"url": url, "title": title, "text": text}
