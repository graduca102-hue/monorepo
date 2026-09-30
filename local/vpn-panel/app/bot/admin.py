"""Admin panel: servers, users, plans, stats, broadcast.

Gated by ``settings.admin_ids``. Adding a server is the headline feature: paste
``host login password name`` lines and the panel installs Xray + MTProto and
starts serving.
"""
from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import db, keys, provisioner
from ..provisioner.remote import norm_hex32
from .common import UNITS, back_kb, esc, fmt_dt, is_admin

router = Router()


class Flow(StatesGroup):
    add_server = State()
    user_lookup = State()
    grant_days = State()
    add_balance = State()
    broadcast = State()
    add_plan = State()
    add_sponsor = State()


def admin_only(cb: CallbackQuery) -> bool:
    return is_admin(cb.from_user.id)


# --- root menu -------------------------------------------------------

def _menu_kb() -> "InlineKeyboardBuilder":
    b = InlineKeyboardBuilder()
    b.button(text="🖥 Серверы", callback_data="adm:servers")
    b.button(text="👥 Пользователи", callback_data="adm:users")
    b.button(text="🏷 Тарифы", callback_data="adm:plans")
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="📣 Спонсор. канал", callback_data="adm:spon")
    b.button(text="📢 Рассылка", callback_data="adm:bcast")
    b.button(text="↻ Синхронизировать всё", callback_data="adm:syncall")
    b.button(text="‹ В меню", callback_data="menu")
    b.adjust(2, 2, 2, 1, 1)
    return b


@router.message(Command("admin"))
async def admin_cmd(msg: Message) -> None:
    if not is_admin(msg.from_user.id):
        return
    await msg.answer("<b>🛠 Админка</b>", reply_markup=_menu_kb().as_markup())


@router.callback_query(F.data == "admin")
async def admin_menu(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        await cb.answer("Недоступно", show_alert=True)
        return
    await cb.message.edit_text("<b>🛠 Админка</b>", reply_markup=_menu_kb().as_markup())
    await cb.answer()


# --- stats ----------------------------------------------------------

@router.callback_query(F.data == "adm:stats")
async def adm_stats(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    s = await db.stats()
    txt = (
        "<b>📊 Статистика</b>\n\n"
        f"Пользователи: <b>{s['users_total']}</b>\n"
        f"  • пробный: {s['users_trial']}\n"
        f"  • активные: {s['users_active']}\n"
        f"  • истёкшие: {s['users_expired']}\n"
        f"Серверы активны: <b>{s['servers_active']}</b>\n"
        f"Пополнений всего: <b>{s['revenue_units']} {UNITS}</b>"
    )
    await cb.message.edit_text(txt, reply_markup=back_kb("admin"))
    await cb.answer()


# --- servers --------------------------------------------------------

@router.callback_query(F.data == "adm:servers")
async def adm_servers(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    rows = await db.servers()
    b = InlineKeyboardBuilder()
    for s in rows:
        mark = {"active": "🟢", "error": "🔴", "provisioning": "🟡",
                "disabled": "⚪️", "new": "⚫️"}.get(s["status"], "❔")
        b.button(text=f"{mark} {s['name']} ({s['host']})", callback_data=f"adm:srv:{s['id']}")
    b.button(text="➕ Добавить сервер(ы)", callback_data="adm:srv:add")
    b.button(text="‹ Назад", callback_data="admin")
    b.adjust(1)
    txt = "<b>🖥 Серверы</b>\n\n" + (
        "Пока нет серверов." if not rows else f"Всего: {len(rows)}"
    )
    await cb.message.edit_text(txt, reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data == "adm:srv:add")
async def adm_srv_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    await state.set_state(Flow.add_server)
    await cb.message.edit_text(
        "<b>➕ Добавить серверы</b>\n\n"
        "Пришли одну или несколько строк, по серверу на строку:\n\n"
        "<code>host login password name</code>\n\n"
        "Разделители — пробел, <code>:</code> или <code>|</code>. "
        "Поле <code>name</code> необязательно. Примеры:\n"
        "<code>1.2.3.4 root Pass123 Netherlands-1</code>\n"
        "<code>root:Pass123:5.6.7.8:Germany</code>\n"
        "<code>1.2.3.4:2222:root:Pass123:FIN</code> (host:port:...)\n\n"
        "Дальше я сам поставлю Xray + MTProto и выдам ключи. /cancel — отмена.",
        reply_markup=back_kb("adm:servers"),
    )
    await cb.answer()


@router.message(Command("cancel"))
async def cancel(msg: Message, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
        await msg.answer("Отменено.")


@router.message(Flow.add_server)
async def adm_srv_add_recv(msg: Message, state: FSMContext) -> None:
    await state.clear()
    ids, errors = await provisioner.add_from_lines(msg.text or "")
    if errors:
        await msg.answer("⚠️ Пропущены строки:\n" + "\n".join(f"• {e}" for e in errors))
    if not ids:
        await msg.answer("Не добавлено ни одного сервера.")
        return
    status = await msg.answer(f"Добавлено серверов: {len(ids)}. Начинаю установку…")

    async def logger(line: str) -> None:
        with contextlib.suppress(Exception):
            await status.answer(line)

    async def run() -> None:
        for sid in ids:
            await provisioner.provision_server(sid, logger)

    asyncio.create_task(run())


@router.callback_query(F.data.regexp(r"^adm:srv:\d+$"))
async def adm_srv_detail(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    sid = int(cb.data.split(":")[2])
    s = await db.get_server(sid)
    if not s:
        await cb.answer("Нет такого", show_alert=True)
        return
    txt = (
        f"<b>🖥 {esc(s['name'])}</b>\n\n"
        f"Хост: <code>{esc(s['host'])}:{s['ssh_port']}</code> ({esc(s['ssh_user'])})\n"
        f"Статус: <b>{s['status']}</b>\n"
        f"Xray: порт {s['xray_port']} · {'вкл' if s['enable_xray'] else 'выкл'} · "
        f"ключ {'есть' if s['reality_public_key'] else 'нет'}\n"
        f"MTProto: порт {s['mtproto_port']} · {'вкл' if s['enable_mtproto'] else 'выкл'}\n"
        f"REALITY dest: <code>{esc(s['reality_dest'])}</code> (SNI {esc(s['reality_sni'])})\n"
        f"Синхр.: {fmt_dt(s['last_sync_at'])}\n"
    )
    if s["last_error"]:
        txt += f"\n⚠️ <code>{esc(s['last_error'])}</code>"
    b = InlineKeyboardBuilder()
    b.button(text="↻ Пересинхр.", callback_data=f"adm:srv:{sid}:sync")
    b.button(text="🔧 Переустановить", callback_data=f"adm:srv:{sid}:prov")
    b.button(text="🩺 Проверить", callback_data=f"adm:srv:{sid}:probe")
    b.button(text=("⏸ Отключить" if s["status"] == "active" else "▶️ Включить"),
             callback_data=f"adm:srv:{sid}:toggle")
    b.button(text="🗑 Удалить", callback_data=f"adm:srv:{sid}:del")
    b.button(text="‹ Назад", callback_data="adm:servers")
    b.adjust(2, 2, 1, 1)
    await cb.message.edit_text(txt, reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:srv:\d+:(sync|prov|probe|toggle|del)$"))
async def adm_srv_action(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    _, _, sid_s, action = cb.data.split(":")
    sid = int(sid_s)
    s = await db.get_server(sid)
    if not s:
        await cb.answer("Нет такого", show_alert=True)
        return

    if action == "del":
        await db.delete_server(sid)
        await cb.answer("Удалён")
        await adm_servers(cb)
        return

    if action == "toggle":
        new = "disabled" if s["status"] == "active" else "active"
        await db.set_server(sid, status=new)
        await cb.answer(f"Статус: {new}")
        await adm_srv_detail(cb)
        return

    if action == "probe":
        await cb.answer("Проверяю…")
        state = await provisioner.probe_server(sid)
        await cb.message.answer(f"🩺 {s['name']}: {esc(state)}")
        return

    await cb.answer("Запущено, смотри сообщения ниже")
    status = await cb.message.answer(f"⏳ {s['name']}: {action}…")

    async def logger(line: str) -> None:
        with contextlib.suppress(Exception):
            await status.answer(line)

    coro = provisioner.provision_server(sid, logger) if action == "prov" \
        else provisioner.sync_server(sid, logger)
    asyncio.create_task(coro)


@router.callback_query(F.data == "adm:syncall")
async def adm_syncall(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    await cb.answer("Синхронизирую все серверы…")
    msg = await cb.message.answer("↻ Синхронизация…")

    async def run() -> None:
        ok, bad = await provisioner.sync_all(
            lambda line: msg.answer(line)  # type: ignore[arg-type,return-value]
        )
        with contextlib.suppress(Exception):
            await msg.answer(f"Готово: ок {ok}, ошибок {bad}")

    asyncio.create_task(run())


# --- sponsor channel (MTProto promoted channel) --------------------

async def _spon_text(rows: list) -> str:
    secret = await db.mtproto_house_secret()
    mtp_srv = [s for s in await db.servers()
               if s["status"] == "active" and s["enable_mtproto"]]
    reg = ["<b>1. Зарегистрируй прокси в @MTProxybot</b>",
           "<code>/newproxy</code>, затем по одному:"]
    if mtp_srv:
        s0 = mtp_srv[0]
        reg += [
            f"• сервер: <code>{esc(s0['host'])}</code>",
            f"• порт: <code>{s0['mtproto_port']}</code>",
            f"• секрет (hex): <code>{esc(secret)}</code>",
        ]
        links = "\n".join(
            f"• {esc(s['name'])}: <code>{esc(keys.mtproto_link(s['host'], s['mtproto_port'], secret, s['mtproto_faketls_domain']))}</code>"
            for s in mtp_srv
        )
        reg.append("\nПубличная ссылка на прокси (делись ей):\n" + links)
    else:
        reg.append("⚠️ Нет активных серверов с MTProto — добавь сервер в «🖥 Серверы».")

    steps = (
        "<b>2. Привяжи канал</b>\n"
        "В @MTProxybot: выбери прокси → <b>Attach channel</b> → укажи свой канал. "
        "Бот вернёт строку <code>tag: xxxx…</code> (32 hex).\n\n"
        "<b>3. Добавь канал сюда</b>\n"
        "Строкой: <code>Название | @канал | тег</code> (<code>@канал</code> необязателен).\n"
        "Пример: <code>Наш канал | @mychan | 3c09c680b76ee91a4c25ad51f742267d</code>\n"
        "После выбора активным панель впишет тег в прокси и перезапустит его."
    )
    active = next((c for c in rows if c["active"]), None)
    tail = (f"\n\nСейчас активен: <b>{esc(active['title'])}</b>"
            if active else "\n\nСпонсорский канал сейчас <b>выключен</b>.")
    return ("<b>📣 Спонсорский канал MTProto</b>\n\n"
            "Прокси бесплатный для всех и показывает выбранный канал закреплённым.\n\n"
            + "\n".join(reg) + "\n\n" + steps + tail)


def _spon_kb(rows: list) -> "InlineKeyboardBuilder":
    b = InlineKeyboardBuilder()
    for c in rows:
        mark = "🟢" if c["active"] else "⚪️"
        label = c["title"] + (f" ({c['username']})" if c["username"] else "")
        b.button(text=f"{mark} {label}", callback_data=f"adm:spon:{c['id']}")
    b.button(text="➕ Добавить канал", callback_data="adm:spon:add")
    if any(c["active"] for c in rows):
        b.button(text="🚫 Выключить спонсорство", callback_data="adm:spon:off")
    b.button(text="‹ Назад", callback_data="admin")
    b.adjust(1)
    return b


@router.callback_query(F.data == "adm:spon")
async def adm_spon(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    rows = await db.sponsor_channels()
    await cb.message.edit_text(await _spon_text(rows),
                               reply_markup=_spon_kb(rows).as_markup(),
                               disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "adm:spon:add")
async def adm_spon_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    await state.set_state(Flow.add_sponsor)
    await cb.message.edit_text(
        "Пришли канал одной строкой:\n"
        "<code>Название | @канал | тег</code>\n"
        "<code>@канал</code> можно опустить. /cancel — отмена.",
        reply_markup=back_kb("adm:spon"),
    )
    await cb.answer()


@router.message(Flow.add_sponsor)
async def adm_spon_add_recv(msg: Message, state: FSMContext) -> None:
    await state.clear()
    parts = [p.strip() for p in (msg.text or "").split("|") if p.strip()]
    tag = norm_hex32(parts[-1]) if parts else ""
    if len(parts) < 2 or not tag:
        await msg.answer(
            "Не разобрал. Нужно: <code>Название | @канал | тег</code>, "
            "где тег — 32 hex-символа от @MTProxybot.",
            reply_markup=back_kb("adm:spon"),
        )
        return
    mid = parts[1:-1]
    username = ""
    title_bits = []
    for p in [parts[0], *mid]:
        if p.startswith("@") and not username:
            username = p
        else:
            title_bits.append(p)
    title = " ".join(title_bits).strip() or (username or "Канал")
    cid = await db.add_sponsor_channel(title, username, tag)
    await db.set_active_sponsor_channel(cid)
    asyncio.create_task(_bg_sync())
    await msg.answer(
        f"✅ Канал «{esc(title)}» добавлен и включён. Разливаю на серверы…",
        reply_markup=back_kb("adm:spon"),
    )


@router.callback_query(F.data == "adm:spon:off")
async def adm_spon_off(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    await db.set_active_sponsor_channel(None)
    asyncio.create_task(_bg_sync())
    await cb.answer("Спонсорство выключено, синхронизирую")
    await adm_spon(cb)


@router.callback_query(F.data.regexp(r"^adm:spon:\d+$"))
async def adm_spon_detail(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    cid = int(cb.data.split(":")[2])
    c = await db.get_sponsor_channel(cid)
    if not c:
        await cb.answer("Нет такого", show_alert=True)
        return
    txt = (
        f"<b>📣 {esc(c['title'])}</b>\n\n"
        f"Канал: {esc(c['username'] or '—')}\n"
        f"Тег: <code>{esc(c['ad_tag'])}</code>\n"
        f"Статус: <b>{'активен' if c['active'] else 'не активен'}</b>"
    )
    b = InlineKeyboardBuilder()
    if not c["active"]:
        b.button(text="✅ Сделать активным", callback_data=f"adm:spon:{cid}:on")
    b.button(text="🗑 Удалить", callback_data=f"adm:spon:{cid}:del")
    b.button(text="‹ Назад", callback_data="adm:spon")
    b.adjust(1)
    await cb.message.edit_text(txt, reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:spon:\d+:(on|del)$"))
async def adm_spon_action(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    _, _, cid_s, action = cb.data.split(":")
    cid = int(cid_s)
    c = await db.get_sponsor_channel(cid)
    if not c:
        await cb.answer("Нет такого", show_alert=True)
        return
    if action == "on":
        await db.set_active_sponsor_channel(cid)
        await cb.answer("Включён, синхронизирую")
    else:
        if c["active"]:
            await db.set_active_sponsor_channel(None)
        await db.delete_sponsor_channel(cid)
        await cb.answer("Удалён")
    asyncio.create_task(_bg_sync())
    await adm_spon(cb)


# --- users ---------------------------------------------------------

@router.callback_query(F.data == "adm:users")
async def adm_users(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    rows = await db.all_users(limit=15)
    b = InlineKeyboardBuilder()
    for u in rows:
        b.button(
            text=f"{u['tg_id']} @{u['username'] or '—'} · {u['status']}",
            callback_data=f"adm:u:{u['tg_id']}",
        )
    b.button(text="🔎 Найти по ID / @username", callback_data="adm:u:find")
    b.button(text="‹ Назад", callback_data="admin")
    b.adjust(1)
    await cb.message.edit_text("<b>👥 Пользователи</b> (последние 15)",
                               reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data == "adm:u:find")
async def adm_u_find(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    await state.set_state(Flow.user_lookup)
    await cb.message.edit_text("Пришли числовой ID или @username. /cancel — отмена.",
                               reply_markup=back_kb("adm:users"))
    await cb.answer()


@router.message(Flow.user_lookup)
async def adm_u_lookup(msg: Message, state: FSMContext) -> None:
    await state.clear()
    q = (msg.text or "").strip().lstrip("@")
    user = None
    if q.isdigit():
        user = await db.get_user(int(q))
    else:
        cur = await db.db().execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (q,))
        user = await cur.fetchone()
    if not user:
        await msg.answer("Не найден.")
        return
    await msg.answer(await _user_card(user), reply_markup=_user_kb(user["tg_id"], user["status"]))


async def _user_card(u) -> str:
    plan = await db.get_plan(u["plan_id"]) if u["plan_id"] else None
    return (
        f"<b>👤 {u['tg_id']}</b> @{esc(u['username'] or '—')}\n\n"
        f"Статус: <b>{u['status']}</b>\n"
        f"Тариф: {esc(plan['name']) if plan else '—'}\n"
        f"До: {fmt_dt(u['expires_at'])}\n"
        f"Баланс: {u['balance']} {UNITS}\n"
        f"Триал использован: {'да' if u['trial_used'] else 'нет'}\n"
        f"sub_token: <code>{esc(u['sub_token'])}</code>"
    )


def _user_kb(uid: int, status: str):
    b = InlineKeyboardBuilder()
    b.button(text="+30 дней", callback_data=f"adm:u:{uid}:grant")
    b.button(text="+ баланс", callback_data=f"adm:u:{uid}:bal")
    b.button(text=("Разбанить" if status == "banned" else "Забанить"),
             callback_data=f"adm:u:{uid}:ban")
    b.button(text="Сбросить триал", callback_data=f"adm:u:{uid}:untrial")
    b.button(text="‹ Назад", callback_data="adm:users")
    b.adjust(2, 2, 1)
    return b.as_markup()


@router.callback_query(F.data.regexp(r"^adm:u:\d+$"))
async def adm_u_detail(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    uid = int(cb.data.split(":")[2])
    u = await db.get_user(uid)
    if not u:
        await cb.answer("Нет", show_alert=True)
        return
    await cb.message.edit_text(await _user_card(u), reply_markup=_user_kb(uid, u["status"]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:u:\d+:(ban|untrial)$"))
async def adm_u_quick(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    _, _, uid_s, action = cb.data.split(":")
    uid = int(uid_s)
    u = await db.get_user(uid)
    if not u:
        await cb.answer("Нет", show_alert=True)
        return
    if action == "ban":
        await db.set_user(uid, status="expired" if u["status"] == "banned" else "banned")
    else:
        await db.set_user(uid, trial_used=0)
    asyncio.create_task(_bg_sync())
    u = await db.get_user(uid)
    await cb.message.edit_text(await _user_card(u), reply_markup=_user_kb(uid, u["status"]))
    await cb.answer("Готово")


@router.callback_query(F.data.regexp(r"^adm:u:\d+:grant$"))
async def adm_u_grant(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    uid = int(cb.data.split(":")[2])
    await state.set_state(Flow.grant_days)
    await state.update_data(uid=uid)
    await cb.message.edit_text("Сколько дней добавить? Пришли число. /cancel",
                               reply_markup=back_kb(f"adm:u:{uid}"))
    await cb.answer()


@router.message(Flow.grant_days)
async def adm_u_grant_recv(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if not (msg.text or "").strip().lstrip("-").isdigit():
        await msg.answer("Нужно число.")
        return
    days = int(msg.text.strip())
    await db.grant_time(data["uid"], days, status="active")
    asyncio.create_task(_bg_sync())
    u = await db.get_user(data["uid"])
    await msg.answer(f"Добавлено {days} дн.\n\n" + await _user_card(u),
                     reply_markup=_user_kb(data["uid"], u["status"]))


@router.callback_query(F.data.regexp(r"^adm:u:\d+:bal$"))
async def adm_u_bal(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    uid = int(cb.data.split(":")[2])
    await state.set_state(Flow.add_balance)
    await state.update_data(uid=uid)
    await cb.message.edit_text(f"На сколько {UNITS} изменить баланс? "
                               f"Можно отрицательное. /cancel",
                               reply_markup=back_kb(f"adm:u:{uid}"))
    await cb.answer()


@router.message(Flow.add_balance)
async def adm_u_bal_recv(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if not (msg.text or "").strip().lstrip("-").isdigit():
        await msg.answer("Нужно число.")
        return
    await db.add_balance(data["uid"], int(msg.text.strip()), method="admin")
    u = await db.get_user(data["uid"])
    await msg.answer(await _user_card(u), reply_markup=_user_kb(data["uid"], u["status"]))


async def _bg_sync() -> None:
    with contextlib.suppress(Exception):
        await provisioner.sync_all()


# --- plans --------------------------------------------------------

@router.callback_query(F.data == "adm:plans")
async def adm_plans(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    rows = await db.plans(active_only=False)
    b = InlineKeyboardBuilder()
    for p in rows:
        mark = "🟢" if p["active"] else "⚪️"
        b.button(text=f"{mark} {p['name']} — {p['price']}{UNITS}/{p['duration_days']}д",
                 callback_data=f"adm:plan:{p['id']}:toggle")
    b.button(text="➕ Новый тариф", callback_data="adm:plan:add")
    b.button(text="‹ Назад", callback_data="admin")
    b.adjust(1)
    await cb.message.edit_text(
        "<b>🏷 Тарифы</b>\nНажми на тариф, чтобы включить/выключить.",
        reply_markup=b.as_markup(),
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:plan:\d+:toggle$"))
async def adm_plan_toggle(cb: CallbackQuery) -> None:
    if not admin_only(cb):
        return
    pid = int(cb.data.split(":")[2])
    p = await db.get_plan(pid)
    if p:
        await db.upsert_plan(pid, active=0 if p["active"] else 1)
    await adm_plans(cb)


@router.callback_query(F.data == "adm:plan:add")
async def adm_plan_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    await state.set_state(Flow.add_plan)
    await cb.message.edit_text(
        "Новый тариф одной строкой:\n"
        "<code>Название;цена;дней;трафик_ГБ;устройств</code>\n"
        "Пример: <code>2 недели;79;14;0;2</code>  (0 ГБ = безлимит). /cancel",
        reply_markup=back_kb("adm:plans"),
    )
    await cb.answer()


@router.message(Flow.add_plan)
async def adm_plan_add_recv(msg: Message, state: FSMContext) -> None:
    await state.clear()
    parts = [p.strip() for p in (msg.text or "").split(";")]
    try:
        name, price, days, gb, dev = parts
        await db.upsert_plan(None, name=name, price=int(price), duration_days=int(days),
                             traffic_gb=int(gb), device_limit=int(dev),
                             sort=int(days), active=1)
    except (ValueError, TypeError):
        await msg.answer("Формат не распознан. Нужно 5 полей через <code>;</code>.")
        return
    await msg.answer(f"Тариф «{name}» добавлен.")


# --- broadcast ---------------------------------------------------

@router.callback_query(F.data == "adm:bcast")
async def adm_bcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not admin_only(cb):
        return
    await state.set_state(Flow.broadcast)
    await cb.message.edit_text("Пришли сообщение для рассылки всем пользователям. /cancel",
                               reply_markup=back_kb("admin"))
    await cb.answer()


@router.message(Flow.broadcast)
async def adm_bcast_recv(msg: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    users = await db.all_users(limit=100000)
    await msg.answer(f"Рассылаю на {len(users)}…")
    sent = failed = 0
    for u in users:
        try:
            await bot.copy_message(u["tg_id"], msg.chat.id, msg.message_id)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
        await asyncio.sleep(0.05)
    await msg.answer(f"Готово: доставлено {sent}, ошибок {failed}.")
