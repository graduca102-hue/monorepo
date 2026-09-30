from playwright.async_api import async_playwright

async def str_day():
    async with async_playwright() as p:
        browser = await  p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_viewport_size({"width": 2560, "height": 1440})
        await page.goto('http://schedule.ckstr.ru/hg.htm')
        await page.screenshot(path="screenshot.png")
        await browser.close()
    return "screenshot.png"