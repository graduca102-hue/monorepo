"""Хэндлеры: владелец кидает файл, посетители забирают строки по utm-ссылке."""
from __future__ import annotations

import html
import io
import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, Message

from .config import Config
from .storage import Storage, normalize_lines

log = logging.getLogger(__name__)

# Тот же алфавит, что принимает Telegram в start-параметре.
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")
MAX_FILE_BYTES = 20 * 1024 * 1024
# Выше этого выдачу отправляем файлом, а не текстом.
MAX_TEXT_CHARS = 3500

OWNER_HELP = (
    "<b>Файл → ссылка → 10 строк</b>\n\n"
    "1. Кидаешь сюда <code>.txt</code> (или .csv) со строками.\n"
    "2. Бот отдаёт одноразовые ссылки вида <code>?start=КОД</code>.\n"
    "3. Каждая ссылка срабатывает ОДИН раз: первый перешедший забирает {n} "
    "строк, ссылка сгорает. Раздаёшь по ссылке на человека.\n\n"
    "<b>Команды</b>\n"
    "/drops — партии: сколько строк всего и сколько осталось\n"
    "/gen [id] N — начеканить N одноразовых ссылок на партию\n"
    "/links [id] — все ссылки (utm) партии: свежая / 🔒 использована\n"
    "/link [id] [utm] — одна новая ссылка, можно со своим кодом\n"
    "/add [id] + файл — дописать строки в партию\n"
    "/who [id] — кто и что забрал (файл-отчёт)\n"
    "/off id, /on id — закрыть / открыть выдачу партии\n"
    "/offlink код, /onlink код — то же для одной ссылки\n"
    "/drop_delete id — удалить партию вместе со статистикой\n"
    "/stats — общая сводка\n\n"
    "Без <code>id</code> команды работают с последней загруженной партией."
)


def build_router(cfg: Config, store: Storage) -> Router:
    router = Router()
    # Ожидание файла для /add: owner_id -> drop_id.
    pending_append: dict[int, int] = {}

    def is_owner(message: Message) -> bool:
        return message.from_user is not None and message.from_user.id == cfg.owner_id

    async def link_url(bot: Bot, code: str) -> str:
        me = await bot.me()
        return f"https://t.me/{me.username}?start={code}"

    async def resolve_drop_id(arg: str | None) -> int | None:
        if arg:
            try:
                return int(arg)
            except ValueError:
                return None
        return await store.last_drop_id()

    # --- посетитель ------------------------------------------------------

    @router.message(CommandStart(deep_link=True))
    async def on_deeplink(message: Message, command: CommandObject, bot: Bot) -> None:
        code = (command.args or "").strip()
        user = message.from_user
        if not code or not CODE_RE.match(code):
            await message.answer("Ссылка не распознана.")
            return
        username = (user.username or "") if user else ""
        issue = await store.issue(code, user.id if user else 0, username, cfg.lines_per_user)
        if issue is None:
            await message.answer("Ссылка недействительна или выдача по ней закрыта.")
            return
        if issue.spent:
            await message.answer("Эта ссылка уже активирована другим человеком.")
            return
        if not issue.lines:
            await message.answer("Всё уже разобрали — строк больше нет.")
            return

        head = "Твои строки (выдавались ранее):" if issue.repeat else "Готово, вот твои строки:"
        body = "\n".join(issue.lines)
        if len(body) <= MAX_TEXT_CHARS:
            await message.answer(f"{head}\n\n<pre>{html.escape(body)}</pre>")
        else:
            await message.answer_document(
                BufferedInputFile((body + "\n").encode("utf-8"), filename=f"{code}.txt"),
                caption=head,
            )

        if cfg.notify_owner and not issue.repeat:
            who = f"@{username}" if username else f"id{user.id if user else 0}"
            await bot.send_message(
                cfg.owner_id,
                f"📤 Выдано {len(issue.lines)} строк — {who}\n"
                f"ссылка <code>{html.escape(code)}</code>, партия «{html.escape(issue.drop_title)}»\n"
                f"осталось строк: {issue.left}",
            )

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        if is_owner(message):
            await message.answer(OWNER_HELP.format(n=cfg.lines_per_user))
        else:
            await message.answer("Привет. Файл выдаётся только по персональной ссылке.")

    # --- владелец --------------------------------------------------------

    @router.message(Command("help"), F.from_user.id == cfg.owner_id)
    async def cmd_help(message: Message) -> None:
        await message.answer(OWNER_HELP.format(n=cfg.lines_per_user))

    @router.message(Command("add"), F.from_user.id == cfg.owner_id)
    async def cmd_add(message: Message, command: CommandObject) -> None:
        drop_id = await resolve_drop_id((command.args or "").split()[0] if command.args else None)
        if drop_id is None or await store.get_drop(drop_id) is None:
            await message.answer("Не нашёл такую партию. /drops — список.")
            return
        pending_append[cfg.owner_id] = drop_id
        await message.answer(f"Жду файл — строки допишу в партию #{drop_id}.")

    @router.message(F.document, F.from_user.id == cfg.owner_id)
    async def on_document(message: Message, bot: Bot) -> None:
        doc = message.document
        assert doc is not None
        if doc.file_size and doc.file_size > MAX_FILE_BYTES:
            await message.answer("Файл больше 20 МБ — Telegram не даст его скачать боту.")
            return
        buf = io.BytesIO()
        await bot.download(doc, destination=buf)
        raw = buf.getvalue()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1251", errors="replace")
        lines = normalize_lines(text, dedupe=cfg.dedupe_lines)
        if not lines:
            await message.answer("В файле нет ни одной непустой строки.")
            return

        append_to = pending_append.pop(cfg.owner_id, None)
        if append_to is not None:
            added = await store.append_lines(append_to, lines)
            drop = await store.get_drop(append_to)
            left = drop.left if drop else added
            await message.answer(
                f"Дописал {added} строк в партию #{append_to}. Свободно строк: {left}."
            )
            return

        title = doc.file_name or f"drop-{int(time.time())}"
        drop_id, code = await store.add_drop(title, lines)
        url = await link_url(bot, code)
        packs = len(lines) // cfg.lines_per_user
        await message.answer(
            f"Партия #{drop_id} «{html.escape(title)}» — {len(lines)} строк "
            f"(хватит на {packs} активаций по {cfg.lines_per_user}).\n\n"
            f"Первая одноразовая ссылка: {url}\n\n"
            f"Сразу на всех: <code>/gen {drop_id} {packs}</code> — {packs} ссылок, "
            f"по одной на человека."
        )

    @router.message(Command("drops"), F.from_user.id == cfg.owner_id)
    async def cmd_drops(message: Message) -> None:
        drops = await store.list_drops()
        if not drops:
            await message.answer("Партий пока нет — кинь файл.")
            return
        rows = [
            f"#{d.id} {'🟢' if d.active else '🔴'} «{html.escape(d.title)}» — "
            f"осталось {d.left} из {d.total}"
            for d in drops
        ]
        await message.answer("\n".join(rows))

    @router.message(Command("links"), F.from_user.id == cfg.owner_id)
    async def cmd_links(message: Message, command: CommandObject, bot: Bot) -> None:
        drop_id = await resolve_drop_id((command.args or "").split()[0] if command.args else None)
        if drop_id is None:
            await message.answer("Партий пока нет.")
            return
        links = await store.list_links(drop_id)
        if not links:
            await message.answer(f"У партии #{drop_id} нет ссылок. /link {drop_id}")
            return
        me = await bot.me()
        rows = []
        for lk in links:
            label = f" [{html.escape(lk.label)}]" if lk.label else ""
            if not lk.active:
                state = " 🔴 выключена"
            elif lk.issued_count:
                state = " 🔒 использована"
            else:
                state = " 🟢 свежая"
            rows.append(f"https://t.me/{me.username}?start={lk.code}{label}{state}")
        await message.answer(f"Ссылки партии #{drop_id}:\n" + "\n".join(rows))

    @router.message(Command("gen"), F.from_user.id == cfg.owner_id)
    async def cmd_gen(message: Message, command: CommandObject, bot: Bot) -> None:
        args = (command.args or "").split()
        drop_arg = args[0] if args and args[0].isdigit() and len(args) > 1 else None
        n_arg = args[1] if drop_arg else (args[0] if args else None)
        drop_id = await resolve_drop_id(drop_arg)
        if drop_id is None or await store.get_drop(drop_id) is None:
            await message.answer("Не нашёл такую партию. /drops — список.")
            return
        try:
            n = int(n_arg) if n_arg else 0
        except ValueError:
            n = 0
        if not 1 <= n <= 500:
            await message.answer("Сколько ссылок? Так: <code>/gen 1 10</code> (1..500).")
            return
        codes = await store.mint_links(drop_id, n)
        me = await bot.me()
        urls = "\n".join(f"https://t.me/{me.username}?start={c}" for c in codes)
        payload = f"{urls}\n"
        if len(urls) <= 3500:
            await message.answer(
                f"{n} одноразовых ссылок на партию #{drop_id}:\n{urls}"
            )
        else:
            await message.answer_document(
                BufferedInputFile(payload.encode("utf-8"), filename=f"links-{drop_id}.txt"),
                caption=f"{n} одноразовых ссылок на партию #{drop_id}",
            )

    @router.message(Command("link"), F.from_user.id == cfg.owner_id)
    async def cmd_link(message: Message, command: CommandObject, bot: Bot) -> None:
        args = (command.args or "").split()
        drop_arg = args[0] if args and args[0].isdigit() else None
        utm = args[1] if drop_arg and len(args) > 1 else (args[0] if args and not drop_arg else None)
        drop_id = await resolve_drop_id(drop_arg)
        if drop_id is None or await store.get_drop(drop_id) is None:
            await message.answer("Не нашёл такую партию. /drops — список.")
            return
        if utm is not None and not CODE_RE.match(utm):
            await message.answer("В utm-коде можно только латиницу, цифры, <code>_</code> и <code>-</code>.")
            return
        if utm is not None and await store.get_link(utm) is not None:
            await message.answer("Такой код уже занят — придумай другой.")
            return
        code = await store.add_link(drop_id, code=utm, label=utm or "")
        url = await link_url(bot, code)
        await message.answer(f"Ссылка на партию #{drop_id}:\n{url}")

    @router.message(Command("who"), F.from_user.id == cfg.owner_id)
    async def cmd_who(message: Message, command: CommandObject) -> None:
        drop_id = await resolve_drop_id((command.args or "").split()[0] if command.args else None)
        if drop_id is None or await store.get_drop(drop_id) is None:
            await message.answer("Не нашёл такую партию.")
            return
        report = await store.issues_report(drop_id)
        if not report:
            await message.answer(f"По партии #{drop_id} никто ещё ничего не забрал.")
            return
        header = "user_id\tusername\tutm\tкогда\tстрок"
        body = "\n".join(
            "\t".join(
                [
                    str(user_id),
                    username or "-",
                    code,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(when)),
                    str(n),
                ]
            )
            for user_id, username, code, when, n in report
        )
        await message.answer_document(
            BufferedInputFile(f"{header}\n{body}\n".encode("utf-8"), filename=f"who-{drop_id}.tsv"),
            caption=f"Партия #{drop_id}: выдач {len(report)}",
        )

    @router.message(Command("off"), F.from_user.id == cfg.owner_id)
    async def cmd_off(message: Message, command: CommandObject) -> None:
        await _toggle_drop(message, command, active=False)

    @router.message(Command("on"), F.from_user.id == cfg.owner_id)
    async def cmd_on(message: Message, command: CommandObject) -> None:
        await _toggle_drop(message, command, active=True)

    async def _toggle_drop(message: Message, command: CommandObject, active: bool) -> None:
        drop_id = await resolve_drop_id((command.args or "").split()[0] if command.args else None)
        if drop_id is None or not await store.set_drop_active(drop_id, active):
            await message.answer("Не нашёл такую партию.")
            return
        await message.answer(f"Партия #{drop_id}: выдача {'включена' if active else 'закрыта'}.")

    @router.message(Command("offlink"), F.from_user.id == cfg.owner_id)
    async def cmd_offlink(message: Message, command: CommandObject) -> None:
        await _toggle_link(message, command, active=False)

    @router.message(Command("onlink"), F.from_user.id == cfg.owner_id)
    async def cmd_onlink(message: Message, command: CommandObject) -> None:
        await _toggle_link(message, command, active=True)

    async def _toggle_link(message: Message, command: CommandObject, active: bool) -> None:
        code = (command.args or "").strip()
        if not code or not await store.set_link_active(code, active):
            await message.answer("Укажи существующий код ссылки: /offlink КОД")
            return
        await message.answer(f"Ссылка <code>{html.escape(code)}</code>: "
                             f"{'работает' if active else 'закрыта'}.")

    @router.message(Command("drop_delete"), F.from_user.id == cfg.owner_id)
    async def cmd_delete(message: Message, command: CommandObject) -> None:
        arg = (command.args or "").strip()
        if not arg.isdigit():
            await message.answer("Так: /drop_delete 3 — удалит партию #3 со ссылками и статистикой.")
            return
        if not await store.delete_drop(int(arg)):
            await message.answer("Не нашёл такую партию.")
            return
        await message.answer(f"Партия #{arg} удалена.")

    @router.message(Command("stats"), F.from_user.id == cfg.owner_id)
    async def cmd_stats(message: Message) -> None:
        s = await store.stats()
        await message.answer(
            f"Партий: {s['drops']}\n"
            f"Строк всего: {s['lines_total']}, свободно: {s['lines_left']}\n"
            f"Выдач: {s['issues']} (уникальных пользователей: {s['users']})"
        )

    @router.message(F.from_user.id == cfg.owner_id)
    async def fallback_owner(message: Message) -> None:
        await message.answer(OWNER_HELP.format(n=cfg.lines_per_user))

    @router.message()
    async def fallback_guest(message: Message) -> None:
        await message.answer("Файл выдаётся только по персональной ссылке.")

    return router
