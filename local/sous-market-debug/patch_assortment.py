"""Patch /opt/botshop/miniapp.py: serve /assortment/ from an in-memory snapshot.

Idempotent-ish: refuses to run twice (checks for the marker).
Creates a timestamped backup next to the file and under /opt/botshop/backups/.
"""
import datetime
import py_compile
import shutil
import sys
from pathlib import Path

STAMP = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
TARGET = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/opt/botshop/miniapp.py")
DRY_RUN = len(sys.argv) > 2
BACKUP_DIR = Path("/opt/botshop/backups") / f"assortment-snapshot-{STAMP}"

src = TARGET.read_text(encoding="utf-8")

if "_ASSORTMENT_SNAPSHOT" in src:
    sys.exit("already patched; aborting")

OLD_1 = 'MARKET_SESSION_COOKIE = "sous_market_session"\n'
NEW_1 = (
    'MARKET_SESSION_COOKIE = "sous_market_session"\n'
    '_ASSORTMENT_SNAPSHOT: dict[str, Any] = {"html": None, "built_at": 0.0}\n'
    '_ASSORTMENT_SNAPSHOT_TTL_SECONDS = 900.0\n'
    '_assortment_refresh_task: "asyncio.Task | None" = None\n'
)

OLD_2 = (
    "        await configure_bot_menu_button(self.source_bot, self.main_bot_username, MINIAPP_MAIN_BRAND_NAME)\n"
)
NEW_2 = (
    "        await configure_bot_menu_button(self.source_bot, self.main_bot_username, MINIAPP_MAIN_BRAND_NAME)\n"
    "        self._schedule_assortment_refresh()\n"
)

OLD_3 = (
    "    async def handle_assortment(self, request: web.Request) -> web.Response:\n"
    "        try:\n"
    "            categories = await get_market_categories()\n"
)
NEW_3 = '''    async def handle_assortment(self, request: web.Request) -> web.Response:
        page_html = await self._get_assortment_html()
        return web.Response(text=page_html, content_type="text/html", charset="utf-8")

    async def _get_assortment_html(self) -> str:
        """Serve the all-products page from a cached in-memory snapshot.

        Rendering the page fetches every category from the upstream marketplace
        and regularly took 8+ seconds (sometimes longer than the nginx proxy
        timeout, which produced an empty page).  Keep the last rendered HTML in
        memory: a fresh snapshot is returned immediately, a stale snapshot is
        still returned at once while a single background task rebuilds it, and
        only the first call after a restart pays the full build cost inline.
        """
        now = time.monotonic()
        snapshot = _ASSORTMENT_SNAPSHOT
        if snapshot["html"] is not None:
            if now - float(snapshot["built_at"]) >= _ASSORTMENT_SNAPSHOT_TTL_SECONDS:
                self._schedule_assortment_refresh()
            return snapshot["html"]
        # No snapshot yet (first request after a restart).  If a background
        # build is already running (e.g. the startup prewarm), wait for it
        # instead of starting a second identical crawl.
        task = _assortment_refresh_task
        if task is not None and not task.done():
            try:
                await task
            except Exception:
                pass
        if _ASSORTMENT_SNAPSHOT["html"] is not None:
            return _ASSORTMENT_SNAPSHOT["html"]
        page_html = await self._render_assortment_html()
        _ASSORTMENT_SNAPSHOT["html"] = page_html
        _ASSORTMENT_SNAPSHOT["built_at"] = time.monotonic()
        return page_html

    def _schedule_assortment_refresh(self) -> None:
        global _assortment_refresh_task
        task = _assortment_refresh_task
        if task is not None and not task.done():
            return

        async def _refresh() -> None:
            global _assortment_refresh_task
            try:
                page_html = await self._render_assortment_html()
                _ASSORTMENT_SNAPSHOT["html"] = page_html
                _ASSORTMENT_SNAPSHOT["built_at"] = time.monotonic()
            except Exception:
                logging.exception("assortment snapshot refresh failed")
            finally:
                _assortment_refresh_task = None

        try:
            _assortment_refresh_task = asyncio.create_task(_refresh())
        except RuntimeError:
            _assortment_refresh_task = None

    async def _render_assortment_html(self) -> str:
        try:
            categories = await get_market_categories()
'''

OLD_4 = (
    '        return web.Response(text=page_html, content_type="text/html", charset="utf-8")\n'
    "\n"
    "    async def handle_products(self, request: web.Request) -> web.Response:\n"
)
NEW_4 = (
    "        return page_html\n"
    "\n"
    "    async def handle_products(self, request: web.Request) -> web.Response:\n"
)

for i, (old, new) in enumerate([(OLD_1, NEW_1), (OLD_2, NEW_2), (OLD_3, NEW_3), (OLD_4, NEW_4)], 1):
    n = src.count(old)
    if n != 1:
        sys.exit(f"chunk {i}: expected exactly 1 match, found {n}")
    src = src.replace(old, new, 1)

if DRY_RUN:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
    print(f"DRY_RUN patched + compiled OK: {TARGET}")
    sys.exit(0)

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
shutil.copy2(TARGET, BACKUP_DIR / "miniapp.py")
BAK = TARGET.parent / f"miniapp.py.bak_assortment_{STAMP}"
shutil.copy2(TARGET, BAK)

TARGET.write_text(src, encoding="utf-8")
py_compile.compile(str(TARGET), doraise=True)
print(f"patched OK; backups: {BACKUP_DIR}/miniapp.py ; {BAK}")
