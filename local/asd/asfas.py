from playwright.sync_api import sync_playwright

# Ваши итоговые куки, объединенные для Playwright
cookies = [
    {
        "name": "sessionid",
        "value": "15221214319%3AI123WzbTT6v155%3A23%3AAYiOmH-XWLhd9Sh7kBcmg7KHUeTZ0ormIKIKfS5W8w",
        "domain": ".instagram.com",
        "path": "/",
        "httpOnly": True,
        "secure": True
    },
    {
        "name": "ds_user_id",
        "value": "15221214319",
        "domain": ".instagram.com",
        "path": "/",
        "httpOnly": False,
        "secure": True
    },
    {
        "name": "csrftoken",
        "value": "Z3X7ksd4mudRrZnSwGqhqVTmbDVEn9E1",
        "domain": ".instagram.com",
        "path": "/",
        "httpOnly": False,
        "secure": True
    },
    {
        "name": "mid",
        "value": "akJeDwALAAGZ3v26HNHyg8-3urVQ",
        "domain": ".instagram.com",
        "path": "/",
        "httpOnly": True,
        "secure": True
    }
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    
    # Устанавливаем куки в контекст
    context.add_cookies(cookies)
    
    page = context.new_page()
    
    # Переходим на Instagram
    print("Переход на instagram.com...")
    page.goto("https://www.instagram.com/")
    
    # Ждём загрузки страницы
    page.wait_for_timeout(5000)
    
    print("Текущий URL:", page.url)
    
    input("Нажми Enter для выхода...")
    browser.close()