"""SQLite-хранилище: партии строк, utm-ссылки, выдачи.

Модель выдачи: каждый новый посетитель получает `count` строк, которые
ФИЗИЧЕСКИ вырезаются из пула партии (`lines`) и копируются в `issued`.
Поэтому одна и та же строка не достанется двоим, а «файл» реально худеет:
100 строк по 10 = ровно 10 человек, дальше выдавать нечего.

Одноразовые ссылки: каждая utm-ссылка активируется РОВНО ОДИН РАЗ (уникальность
по `issues.code`). Первый перешедший забирает 10 строк, ссылка «сгорает».
Он же при повторном заходе видит свой набор из `issued`; чужой видит, что ссылка
уже использована. Раздаёшь по одной ссылке на человека.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS drops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    created_at INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_id    INTEGER NOT NULL,
    code       TEXT    NOT NULL,
    user_id    INTEGER NOT NULL,
    username   TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
-- одноразовые ссылки: одна активация на код ссылки.
CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_code ON issues(code);

-- свободный пул: выданные строки отсюда УДАЛЯЮТСЯ.
CREATE TABLE IF NOT EXISTS lines (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    text    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lines_drop ON lines(drop_id, id);

-- копия строк, отданных конкретной выдаче (для повторного показа тому же юзеру).
CREATE TABLE IF NOT EXISTS issued (
    issue_id INTEGER NOT NULL,
    seq      INTEGER NOT NULL,
    text     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issued_issue ON issued(issue_id);

CREATE TABLE IF NOT EXISTS links (
    code       TEXT    PRIMARY KEY,
    drop_id    INTEGER NOT NULL,
    label      TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_links_drop ON links(drop_id);
"""

# Telegram-deeplink допускает только [A-Za-z0-9_-] и до 64 символов.
CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def make_code(length: int = 8) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def normalize_lines(raw: str, dedupe: bool = True) -> list[str]:
    """Текст файла в список непустых строк (по желанию без дублей)."""
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if dedupe:
            if line in seen:
                continue
            seen.add(line)
        out.append(line)
    return out


@dataclass(frozen=True)
class Drop:
    id: int
    title: str
    created_at: int
    active: bool
    total: int   # всего строк было (свободные + уже выданные из этой партии)
    left: int    # осталось свободных сейчас


@dataclass(frozen=True)
class Link:
    code: str
    drop_id: int
    label: str
    active: bool
    issued_count: int


@dataclass(frozen=True)
class Issue:
    """Результат попытки выдачи по ссылке."""

    lines: list[str]
    repeat: bool          # тот же юзер повторно открыл свою ссылку, вернули набор
    left: int             # сколько свободных строк осталось в партии после выдачи
    drop_title: str
    spent: bool = False   # ссылка уже активирована другим человеком


class Storage:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        # Выдача строк читает и пишет в несколько шагов: сериализуем, чтобы
        # двум пользователям не ушли одни и те же строки.
        self._issue_lock = asyncio.Lock()

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        # Не залипать намертво, если вдруг второй процесс держит запись
        # (например watchdog на миг поднял дубль) — подождать, а не падать.
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._migrate()
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def _migrate(self) -> None:
        """Переезд со старой схемы (lines.issue_id) на пул + issued."""
        db = self._db
        assert db is not None
        # Прежние лимиты жили на индексе idx_issues_user (по (drop_id,user_id)
        # или по user_id). Теперь лимит на ссылке — этот индекс больше не нужен.
        # Дропаем ТОЛЬКО если он реально есть: в норме старт без схемных записей,
        # иначе два параллельных старта дерутся за write-lock на issues.
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_issues_user'"
        )
        if await cur.fetchone():
            await db.execute("DROP INDEX IF EXISTS idx_issues_user")
            await db.commit()

        cur = await db.execute("PRAGMA table_info(lines)")
        cols = {r["name"] for r in await cur.fetchall()}
        if "issue_id" in cols:
            # issued может ещё не существовать — создаём заранее.
            await db.executescript(
                "CREATE TABLE IF NOT EXISTS issued ("
                " issue_id INTEGER NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL);"
            )
            # Уже выданные строки переносим в issued и вырезаем из пула.
            cur = await db.execute(
                "SELECT id, issue_id, text FROM lines WHERE issue_id IS NOT NULL ORDER BY issue_id, id"
            )
            rows = await cur.fetchall()
            seqs: dict[int, int] = {}
            for r in rows:
                iid = int(r["issue_id"])
                seqs[iid] = seqs.get(iid, 0) + 1
                await db.execute(
                    "INSERT INTO issued(issue_id, seq, text) VALUES (?, ?, ?)",
                    (iid, seqs[iid], str(r["text"])),
                )
            await db.execute("DELETE FROM lines WHERE issue_id IS NOT NULL")
            # Пересобираем lines без колонки issue_id.
            await db.execute("ALTER TABLE lines RENAME TO lines_old")
            await db.execute(
                "CREATE TABLE lines ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, drop_id INTEGER NOT NULL,"
                " line_no INTEGER NOT NULL, text TEXT NOT NULL)"
            )
            await db.execute(
                "INSERT INTO lines(id, drop_id, line_no, text)"
                " SELECT id, drop_id, line_no, text FROM lines_old"
            )
            await db.execute("DROP TABLE lines_old")
            await db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage не открыт")
        return self._db

    # --- партии ---------------------------------------------------------

    async def add_drop(self, title: str, lines: Sequence[str]) -> tuple[int, str]:
        """Создаёт партию строк и сразу первую ссылку. Возвращает (drop_id, code)."""
        now = int(time.time())
        cur = await self.db.execute(
            "INSERT INTO drops(title, created_at) VALUES (?, ?)", (title, now)
        )
        drop_id = int(cur.lastrowid)
        await self.db.executemany(
            "INSERT INTO lines(drop_id, line_no, text) VALUES (?, ?, ?)",
            [(drop_id, i, text) for i, text in enumerate(lines, start=1)],
        )
        code = await self._insert_link(drop_id, label="", commit=False)
        await self.db.commit()
        return drop_id, code

    async def append_lines(self, drop_id: int, lines: Sequence[str]) -> int:
        """Дописывает строки в существующую партию, возвращает сколько добавлено."""
        cur = await self.db.execute(
            "SELECT COALESCE(MAX(line_no), 0) AS m FROM lines WHERE drop_id = ?", (drop_id,)
        )
        row = await cur.fetchone()
        start = int(row["m"]) if row else 0
        await self.db.executemany(
            "INSERT INTO lines(drop_id, line_no, text) VALUES (?, ?, ?)",
            [(drop_id, start + i, text) for i, text in enumerate(lines, start=1)],
        )
        await self.db.commit()
        return len(lines)

    _DROP_SELECT = """
        SELECT d.id, d.title, d.created_at, d.active,
               (SELECT COUNT(*) FROM lines l WHERE l.drop_id = d.id) AS free_,
               (SELECT COUNT(*) FROM issued s JOIN issues i ON s.issue_id = i.id
                 WHERE i.drop_id = d.id) AS given_
        FROM drops d
    """

    async def get_drop(self, drop_id: int) -> Drop | None:
        cur = await self.db.execute(self._DROP_SELECT + " WHERE d.id = ?", (drop_id,))
        row = await cur.fetchone()
        return self._drop(row) if row else None

    async def list_drops(self) -> list[Drop]:
        cur = await self.db.execute(self._DROP_SELECT + " ORDER BY d.id DESC")
        return [self._drop(row) for row in await cur.fetchall()]

    async def last_drop_id(self) -> int | None:
        cur = await self.db.execute("SELECT MAX(id) AS m FROM drops")
        row = await cur.fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    async def set_drop_active(self, drop_id: int, active: bool) -> bool:
        cur = await self.db.execute(
            "UPDATE drops SET active = ? WHERE id = ?", (1 if active else 0, drop_id)
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def delete_drop(self, drop_id: int) -> bool:
        cur = await self.db.execute("DELETE FROM drops WHERE id = ?", (drop_id,))
        await self.db.execute("DELETE FROM lines WHERE drop_id = ?", (drop_id,))
        await self.db.execute("DELETE FROM links WHERE drop_id = ?", (drop_id,))
        await self.db.execute(
            "DELETE FROM issued WHERE issue_id IN (SELECT id FROM issues WHERE drop_id = ?)",
            (drop_id,),
        )
        await self.db.execute("DELETE FROM issues WHERE drop_id = ?", (drop_id,))
        await self.db.commit()
        return cur.rowcount > 0

    @staticmethod
    def _drop(row: aiosqlite.Row) -> Drop:
        free = int(row["free_"])
        given = int(row["given_"])
        return Drop(
            id=int(row["id"]),
            title=str(row["title"]),
            created_at=int(row["created_at"]),
            active=bool(row["active"]),
            total=free + given,
            left=free,
        )

    # --- ссылки ---------------------------------------------------------

    async def _insert_link(self, drop_id: int, label: str, commit: bool = True) -> str:
        for _ in range(20):
            code = make_code()
            try:
                await self.db.execute(
                    "INSERT INTO links(code, drop_id, label, created_at) VALUES (?, ?, ?, ?)",
                    (code, drop_id, label, int(time.time())),
                )
            except aiosqlite.IntegrityError:
                continue
            if commit:
                await self.db.commit()
            return code
        raise RuntimeError("не удалось подобрать свободный код ссылки")

    async def add_link(self, drop_id: int, code: str | None = None, label: str = "") -> str:
        """Новая utm-ссылка на партию. code=None - случайный."""
        if code is None:
            return await self._insert_link(drop_id, label)
        await self.db.execute(
            "INSERT INTO links(code, drop_id, label, created_at) VALUES (?, ?, ?, ?)",
            (code, drop_id, label or code, int(time.time())),
        )
        await self.db.commit()
        return code

    async def mint_links(self, drop_id: int, n: int, label: str = "") -> list[str]:
        """Начеканить n случайных одноразовых ссылок на партию."""
        codes = [await self._insert_link(drop_id, label, commit=False) for _ in range(n)]
        await self.db.commit()
        return codes

    async def get_link(self, code: str) -> Link | None:
        cur = await self.db.execute(
            """
            SELECT k.code, k.drop_id, k.label, k.active,
                   (SELECT COUNT(*) FROM issues i WHERE i.code = k.code) AS issued
            FROM links k WHERE k.code = ?
            """,
            (code,),
        )
        row = await cur.fetchone()
        return self._link(row) if row else None

    async def list_links(self, drop_id: int | None = None) -> list[Link]:
        sql = """
            SELECT k.code, k.drop_id, k.label, k.active,
                   (SELECT COUNT(*) FROM issues i WHERE i.code = k.code) AS issued
            FROM links k
        """
        params: tuple = ()
        if drop_id is not None:
            sql += " WHERE k.drop_id = ?"
            params = (drop_id,)
        sql += " ORDER BY k.drop_id DESC, k.created_at"
        cur = await self.db.execute(sql, params)
        return [self._link(row) for row in await cur.fetchall()]

    @staticmethod
    def _link(row: aiosqlite.Row) -> Link:
        return Link(
            code=str(row["code"]),
            drop_id=int(row["drop_id"]),
            label=str(row["label"]),
            active=bool(row["active"]),
            issued_count=int(row["issued"]),
        )

    async def set_link_active(self, code: str, active: bool) -> bool:
        cur = await self.db.execute(
            "UPDATE links SET active = ? WHERE code = ?", (1 if active else 0, code)
        )
        await self.db.commit()
        return cur.rowcount > 0

    # --- выдача ---------------------------------------------------------

    async def issue(self, code: str, user_id: int, username: str, count: int) -> Issue | None:
        """Выдать count строк по одноразовой ссылке code.

        Ссылка активируется ровно один раз. Первый перешедший забирает строки
        (они вырезаются из пула), ссылка «сгорает». Он же при повторном заходе
        видит свой набор; чужой получает Issue(spent=True). None - ссылки нет,
        она выключена или партия выключена.
        """
        async with self._issue_lock:
            link = await self.get_link(code)
            if link is None or not link.active:
                return None
            drop = await self.get_drop(link.drop_id)
            if drop is None or not drop.active:
                return None

            # Ссылка уже активирована? Автору покажем его набор, чужому — «сгорела».
            cur = await self.db.execute(
                "SELECT id, user_id FROM issues WHERE code = ? LIMIT 1", (code,)
            )
            row = await cur.fetchone()
            if row:
                if int(row["user_id"]) == user_id:
                    lines = await self._issued_lines(int(row["id"]))
                    return Issue(lines=lines, repeat=True, left=drop.left, drop_title=drop.title)
                return Issue(lines=[], repeat=False, left=drop.left,
                             drop_title=drop.title, spent=True)

            cur = await self.db.execute(
                "SELECT id, text FROM lines WHERE drop_id = ? ORDER BY id LIMIT ?",
                (drop.id, count),
            )
            picked = await cur.fetchall()
            if not picked:
                return Issue(lines=[], repeat=False, left=0, drop_title=drop.title)

            ids = [int(r["id"]) for r in picked]
            texts = [str(r["text"]) for r in picked]

            cur = await self.db.execute(
                "INSERT INTO issues(drop_id, code, user_id, username, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (drop.id, code, user_id, username, int(time.time())),
            )
            issue_id = int(cur.lastrowid)
            await self.db.executemany(
                "INSERT INTO issued(issue_id, seq, text) VALUES (?, ?, ?)",
                [(issue_id, i, t) for i, t in enumerate(texts, start=1)],
            )
            placeholders = ",".join("?" for _ in ids)
            await self.db.execute(
                f"DELETE FROM lines WHERE id IN ({placeholders})", ids
            )
            await self.db.commit()
            return Issue(
                lines=texts,
                repeat=False,
                left=drop.left - len(ids),
                drop_title=drop.title,
            )

    async def _issued_lines(self, issue_id: int) -> list[str]:
        cur = await self.db.execute(
            "SELECT text FROM issued WHERE issue_id = ? ORDER BY seq", (issue_id,)
        )
        return [str(r["text"]) for r in await cur.fetchall()]

    async def stats(self) -> dict[str, int]:
        cur = await self.db.execute(
            """
            SELECT (SELECT COUNT(*) FROM drops) AS drops,
                   (SELECT COUNT(*) FROM lines) AS lines_left,
                   (SELECT COUNT(*) FROM issued) AS lines_given,
                   (SELECT COUNT(*) FROM issues) AS issues,
                   (SELECT COUNT(DISTINCT user_id) FROM issues) AS users
            """
        )
        row = await cur.fetchone()
        out = {key: int(row[key]) for key in row.keys()}
        out["lines_total"] = out["lines_left"] + out["lines_given"]
        return out

    async def issues_report(self, drop_id: int) -> list[tuple[int, str, str, int, int]]:
        """(user_id, username, code, когда, сколько строк) по партии."""
        cur = await self.db.execute(
            """
            SELECT i.id, i.user_id, i.username, i.code, i.created_at,
                   (SELECT COUNT(*) FROM issued s WHERE s.issue_id = i.id) AS n
            FROM issues i WHERE i.drop_id = ? ORDER BY i.id
            """,
            (drop_id,),
        )
        return [
            (
                int(r["user_id"]),
                str(r["username"]),
                str(r["code"]),
                int(r["created_at"]),
                int(r["n"]),
            )
            for r in await cur.fetchall()
        ]
