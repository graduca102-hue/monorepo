from playwright.async_api import async_playwright

test = "https://docs.google.com/forms/d/e/1FAIpQLSe4zXpOw74Eb3oVEOMKLMwOLbBclbzJ2qbewtPcVSuI0Et4ew/viewform"

async def str_day():
    async with async_playwright() as p:
        browser = await  p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_viewport_size({"width": 2560, "height": 1440})
        await page.goto(test)
        time.sleep(3)
        
        await browser.close()
    return "screenshot.png"