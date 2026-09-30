"""Add the "UTM-ссылки" screen to the SousPartners Mini App cabinet.

Reads the pristine copies in ../live/, writes patched files next to this script.
Re-runnable: it always starts from ../live/.
"""
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
LIVE = HERE.parent / "live"


def patch(name: str, edits: list[tuple[str, str]]) -> None:
    # /opt/botshop mixes LF and CRLF files: match on LF, write the original back.
    with open(LIVE / name, "r", encoding="utf-8", newline="") as handle:
        raw = handle.read()
    crlf = '\r\n' in raw
    text = raw.replace('\r\n', '\n')
    for old, new in edits:
        if old not in text:
            raise SystemExit(f"{name}: anchor not found: {old[:200]}")
        if text.count(old) != 1:
            raise SystemExit(f"{name}: anchor is not unique: {old[:120]}")
        text = text.replace(old, new)
    with open(HERE / name, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace('\n', '\r\n') if crlf else text)
    print(f"patched {name}")


# ---------------------------------------------------------------- data.py ----
DATA_ANCHOR = """def reserve_email_activation(
    *,
    user_id: int,"""

DATA_NEW = '''def delete_partner_bot_utm_link(bot_id: int, source_word: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM partner_bot_utm_links WHERE bot_id = ? AND source_word = ?",
        (int(bot_id), str(source_word)),
    )
    deleted = cursor.rowcount or 0
    conn.commit()
    conn.close()
    return deleted > 0


''' + DATA_ANCHOR


# ------------------------------------------------------------- miniapp.py ----
MINIAPP_IMPORT_ANCHOR = """    set_partner_bot_text,
    delete_partner_bot_text,
"""
MINIAPP_IMPORT_NEW = """    set_partner_bot_text,
    delete_partner_bot_text,
    get_partner_bot_utm_stats,
    list_partner_bot_utm_links,
    upsert_partner_bot_utm_link,
    delete_partner_bot_utm_link,
"""

MINIAPP_ROUTE_ANCHOR = """        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/api/banners", self.handle_partner_banners)"""
MINIAPP_ROUTE_NEW = """        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/api/utm", self.handle_partner_utm)
        app.router.add_post("/partner/{bot_slug:[A-Za-z0-9_]+}/api/utm", self.handle_partner_utm)
        app.router.add_get("/partner/{bot_slug:[A-Za-z0-9_]+}/api/banners", self.handle_partner_banners)"""

MINIAPP_HANDLER_ANCHOR = """    async def handle_partner_banners(self, request: web.Request) -> web.Response:"""

MINIAPP_HANDLER_NEW = '''    async def handle_partner_utm(self, request: web.Request) -> web.Response:
        """GET: tracked links with per-link stats.  POST: create or delete one.

        The links themselves are already understood by the bot: a ``?start=``
        payload is parsed by ``tracking.parse_start_tracking`` and stored in
        ``partner_bot_starts``, so the cabinet only creates the link and reads
        back what that table recorded.
        """
        partner_bot, _ = await self._resolve_partner_owner(request)
        partner_id = int(partner_bot["id"])
        bot_username = str(partner_bot.get("bot_username") or "").strip().lstrip("@")

        def payload() -> dict:
            links = list_partner_bot_utm_links(partner_id, limit=PARTNER_UTM_LINK_LIMIT)
            stats = get_partner_bot_utm_stats(partner_id, limit=PARTNER_UTM_STATS_LIMIT)
            by_param = {str(row.get("start_param")): row for row in stats.get("params", [])}
            items = []
            for row in links:
                start_param = str(row.get("start_param") or "")
                measured = by_param.get(start_param) or {}
                items.append({
                    "source": str(row.get("source_word") or ""),
                    "start_param": start_param,
                    "url": f"https://t.me/{bot_username}?start={start_param}" if bot_username else "",
                    "clicks": int(measured.get("total") or 0),
                    "revenue": round(float(measured.get("purchases_total") or 0.0), 2),
                })
            top_sources = [
                {
                    "source": str(row.get("utm_source") or "—"),
                    "clicks": int(row.get("total") or 0),
                    "revenue": round(float(row.get("purchases_total") or 0.0), 2),
                }
                for row in stats.get("sources", [])[:8]
            ]
            return {
                "items": items,
                "top_sources": top_sources,
                "bot_username": bot_username,
                "limit": PARTNER_UTM_LINK_LIMIT,
            }

        if request.method == "GET":
            return web.json_response(payload())

        try:
            body = await request.json()
        except (TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid request"}), content_type="application/json")

        source = normalize_partner_utm_source(str(body.get("source") or ""))
        if not source:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Название: латиница, цифры, дефис — например instagram"}),
                content_type="application/json",
            )
        if bool(body.get("delete")):
            delete_partner_bot_utm_link(partner_id, source)
            return web.json_response({"ok": True, **payload()})

        existing = list_partner_bot_utm_links(partner_id, limit=PARTNER_UTM_LINK_LIMIT + 1)
        known = {str(row.get("source_word") or "") for row in existing}
        if source not in known and len(known) >= PARTNER_UTM_LINK_LIMIT:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": f"Максимум {PARTNER_UTM_LINK_LIMIT} ссылок — удалите ненужную"}),
                content_type="application/json",
            )
        if not bot_username:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "Сначала подключите бота"}),
                content_type="application/json",
            )
        upsert_partner_bot_utm_link(partner_id, source, f"utm_{source}")
        return web.json_response({"ok": True, **payload()})

''' + MINIAPP_HANDLER_ANCHOR


MINIAPP_TOP_ANCHOR = "\nclass MiniAppServer"
MINIAPP_TOP_NEW = '''
# Tracked ("UTM") links a partner can keep in the cabinet at once, and how many
# rows the stats query returns so every link finds its counters.
PARTNER_UTM_LINK_LIMIT = 20
PARTNER_UTM_STATS_LIMIT = 100


def normalize_partner_utm_source(value: str) -> str:
    """Like the bot-side UTM screen (keyboard.py), but underscore-free.

    ``tracking.parse_start_tracking`` reads ``utm_<source>`` as the source only
    while the payload holds a single underscore, so a source word with one of
    its own would land in the stats as the raw payload.
    """
    normalized = re.sub(r"[\\s_]+", "-", (value or "").strip().lower())
    normalized = re.sub(r"[^a-zA-Z0-9-]+", "", normalized)
    return normalized.strip("-")[:40]

''' + MINIAPP_TOP_ANCHOR


# ----------------------------------------------------------------- app.js ----
APP_CELL_ANCHOR = """        cell({ tint:'t-teal', icon:'share', title:'Реферальная ссылка', subtitle:'Приглашайте партнёров', attrs:'data-tab="referral"' }),"""
APP_CELL_NEW = APP_CELL_ANCHOR + """
        cell({ tint:'t-blue', icon:'link', title:'UTM-ссылки', subtitle:'Отслеживайте, откуда приходят клиенты', attrs:'data-tab="utm"' }),"""

APP_SCREEN_ANCHOR = """  /* ---- Картинки меню ---------------------------------------------- */"""

APP_SCREEN_NEW = """  /* ---- UTM-ссылки ---------------------------------------------- */
  let utmState = null;        // {items:[...], top_sources:[...]} once loaded

  const loadUtm = async () => {
    utmState = null;
    render();
    try {
      utmState = await api('/api/utm');
    } catch (error) {
      utmState = { error: error.message || 'Не удалось загрузить' };
    }
    render();
  };

  const utm = () => {
    if (!utmState) return `${titleBlock('', 'UTM-ссылки')}<div class="loading">Загрузка…</div>`;
    if (utmState.error) return `${titleBlock('', 'UTM-ссылки')}<div class="notice">${esc(utmState.error)}</div>`;
    const items = Array.isArray(utmState.items) ? utmState.items : [];
    const rows = items.length
      ? items.map((it) => `<div class="cell plain">
          <span class="cell-body"><b>${esc(it.source)}</b><small>${it.clicks || 0} переходов · ${money(it.revenue)}</small></span>
          <button class="inline-btn" data-copy="${esc(it.url)}">Копировать</button>
          <button class="inline-btn ghost" data-del-utm="${esc(it.source)}">Удалить</button>
        </div>`).join('')
      : `<div class="cell plain"><span class="cell-body"><b>Ссылок пока нет</b><small>Создайте первую — например instagram</small></span></div>`;
    const top = Array.isArray(utmState.top_sources) ? utmState.top_sources : [];
    const topBlock = top.length
      ? group('Откуда приходят', top.map((row) => `<div class="cell plain">
          <span class="cell-body"><b>${esc(row.source)}</b><small>${row.clicks || 0} переходов</small></span>
          <span class="cell-val accent">${esc(money(row.revenue))}</span>
        </div>`).join(''))
      : '';
    return `${titleBlock('', 'UTM-ссылки', 'Своя ссылка для каждой площадки — видно, откуда пришли клиенты и сколько они купили.')}
      <div class="field">
        <label>Название источника</label>
        <div class="control"><input id="utm-source" maxlength="40" placeholder="instagram"><button class="inline-btn" data-add-utm>Создать</button></div>
        <div class="hint">Латиница, цифры и дефис. Ссылка: <b>https://t.me/${esc(utmState.bot_username || 'bot')}?start=utm_<i>источник</i></b></div>
      </div>
      ${group('Мои ссылки', rows)}
      ${topBlock}`;
  };

  async function submitUtm(source, remove = false) {
    const value = String(source || '').trim();
    if (!value) { alert('Введите название источника'); return; }
    try {
      const result = await api('/api/utm', { method:'POST', body:JSON.stringify({ source: value, delete: remove }) });
      utmState = result;
      notify('success');
      render();
    } catch (error) { notify('error'); alert(error.message); }
  }

""" + APP_SCREEN_ANCHOR

APP_SCREENS_ANCHOR = """    stats, withdraw, referral, markup, broadcast, subscription, contacts, settings, texts, banners, profile,"""
APP_SCREENS_NEW = """    stats, withdraw, referral, markup, broadcast, subscription, contacts, settings, texts, banners, utm, profile,"""

APP_GOTAB_ANCHOR = """    if (next === 'banners') { loadBanners(); return; }"""
APP_GOTAB_NEW = """    if (next === 'banners') { loadBanners(); return; }
    if (next === 'utm') { loadUtm(); return; }"""

APP_BIND_ANCHOR = """    root.querySelectorAll('[data-edit-banner]')"""
APP_BIND_NEW = """    root.querySelectorAll('[data-add-utm]').forEach((element) => element.onclick = () => { haptic(); submitUtm(root.querySelector('#utm-source')?.value); });
    root.querySelectorAll('[data-del-utm]').forEach((element) => element.onclick = () => { haptic(); submitUtm(element.dataset.delUtm, true); });
    root.querySelectorAll('[data-edit-banner]')"""


STYLES_ANCHOR = """.btn.danger{"""
STYLES_NEW = """.inline-btn.ghost{color:var(--destructive);font-weight:500}
.btn.danger{"""


def main() -> None:
    patch("data.py", [(DATA_ANCHOR, DATA_NEW)])
    patch("miniapp.py", [
        (MINIAPP_IMPORT_ANCHOR, MINIAPP_IMPORT_NEW),
        (MINIAPP_TOP_ANCHOR, MINIAPP_TOP_NEW),
        (MINIAPP_ROUTE_ANCHOR, MINIAPP_ROUTE_NEW),
        (MINIAPP_HANDLER_ANCHOR, MINIAPP_HANDLER_NEW),
    ])
    patch("app.js", [
        (APP_CELL_ANCHOR, APP_CELL_NEW),
        (APP_SCREEN_ANCHOR, APP_SCREEN_NEW),
        (APP_SCREENS_ANCHOR, APP_SCREENS_NEW),
        (APP_GOTAB_ANCHOR, APP_GOTAB_NEW),
        (APP_BIND_ANCHOR, APP_BIND_NEW),
    ])
    patch("styles.css", [(STYLES_ANCHOR, STYLES_NEW)])


if __name__ == "__main__":
    main()
