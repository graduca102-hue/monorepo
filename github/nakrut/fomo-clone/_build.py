"""
Патчер сохранённой копии страницы fomo.family.

Берёт HTML, снятый WebScrapBook'ом, убирает служебные атрибуты снимка
и подключает скрипты симуляции (fomo-config.js / fomo-pump.js / fomo-chart.js).

Запуск (из папки fomo-clone):
    python _build.py

Скрипт идемпотентен: повторный запуск ничего не ломает.
"""

import io
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = r"C:\Users\ewg\Downloads\WebScrapBook\data\20260830050920283"

MAIN = "index.html"
CHART = "index_1.html"


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def write(path, text):
    io.open(path, "w", encoding="utf-8", newline="").write(text)


def restore_from_capture(name):
    """Всегда начинаем с чистого снимка, чтобы патч не накладывался дважды."""
    src = os.path.join(CAPTURE, name)
    if os.path.isfile(src):
        shutil.copyfile(src, os.path.join(HERE, name))


def strip_scrapbook(html, drop_canvas=False):
    # служебный лоадер WebScrapBook
    html = re.sub(
        r'<script data-scrapbook-elem="basic-loader">.*?</script>', "", html, flags=re.S
    )
    if drop_canvas:
        html = re.sub(r'\s*data-scrapbook-canvas="[^"]*"', "", html)
    html = re.sub(r'\s*data-scrapbook-(create|source|type)="[^"]*"', "", html)
    # битая картинка facebook-пикселя из снимка
    html = re.sub(r"<noscript><img[^>]*urn:scrapbook[^>]*></noscript>", "", html)
    return html


def inject(html, scripts):
    tags = "\n" + "\n".join(
        '<script src="%s"></script>' % s for s in scripts
    ) + "\n"
    if all(s in html for s in scripts):
        return html
    return html.replace("</body>", tags + "</body>", 1)


def main():
    restore_from_capture(MAIN)
    restore_from_capture(CHART)

    html = read(os.path.join(HERE, MAIN))
    html = strip_scrapbook(html)
    html = inject(
        html,
        ["fomo-config.js", "fomo-format.js", "fomo-core.js", "fomo-pump.js"],
    )
    write(os.path.join(HERE, MAIN), html)
    print("patched", MAIN, len(html))

    html = read(os.path.join(HERE, CHART))
    html = strip_scrapbook(html, drop_canvas=True)
    html = inject(html, ["fomo-config.js", "fomo-format.js", "fomo-chart.js"])
    write(os.path.join(HERE, CHART), html)
    print("patched", CHART, len(html))


if __name__ == "__main__":
    main()
