"""Проверка выдачи: одноразовые ссылки, строки вырезаются, 10 строк на активацию."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite  # noqa: E402

from app.storage import Storage, normalize_lines  # noqa: E402


def test_normalize_lines() -> None:
    raw = "a\r\nb\n\n  c  \nb\n"
    assert normalize_lines(raw) == ["a", "b", "c"]
    assert normalize_lines(raw, dedupe=False) == ["a", "b", "c", "b"]


async def scenario() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp) / "t.sqlite3")
        await store.open()

        lines = [f"line-{i}" for i in range(1, 26)]
        drop_id, code = await store.add_drop("test.txt", lines)
        assert (await store.get_drop(drop_id)).total == 25

        # первый по ссылке забирает 10, строки вырезаются из пула
        first = await store.issue(code, user_id=111, username="a", count=10)
        assert first is not None and first.lines == lines[:10] and first.left == 15
        assert (await store.get_drop(drop_id)).left == 15

        # автор повторно открыл свою ссылку - тот же набор, пул не тронут
        again = await store.issue(code, user_id=111, username="a", count=10)
        assert again is not None and again.repeat and again.lines == first.lines
        assert (await store.get_drop(drop_id)).left == 15

        # ЧУЖОЙ по той же ссылке - она уже сгорела, строк не даёт
        other = await store.issue(code, user_id=222, username="b", count=10)
        assert other is not None and other.spent and other.lines == []
        assert (await store.get_drop(drop_id)).left == 15

        # вторая ссылка - своя одна активация, следующие 10 строк
        code2 = await store.add_link(drop_id, code="tiktok", label="tiktok")
        second = await store.issue(code2, user_id=222, username="b", count=10)
        assert second is not None and not second.repeat and second.lines == lines[10:20]
        assert second.left == 5

        # один и тот же человек МОЖЕТ активировать другую (свежую) ссылку
        code3 = await store.add_link(drop_id, code="tg", label="tg")
        third = await store.issue(code3, user_id=111, username="a", count=10)
        assert third is not None and not third.repeat and third.lines == lines[20:]
        assert third.left == 0

        # пул пуст: новая ссылка отдаёт пусто (но не spent)
        code4 = await store.add_link(drop_id, code="empty", label="empty")
        empty = await store.issue(code4, user_id=333, username="c", count=10)
        assert empty is not None and not empty.spent and empty.lines == []

        stats = await store.stats()
        assert stats["issues"] == 3 and stats["lines_left"] == 0 and stats["lines_given"] == 25
        assert len(await store.issues_report(drop_id)) == 3

        # дубль активации одной ссылки запрещён на уровне БД
        try:
            await store.db.execute(
                "INSERT INTO issues(drop_id, code, user_id, username, created_at)"
                " VALUES (?,?,?,?,?)",
                (drop_id, code, 777, "x", 0),
            )
            raise AssertionError("вторая активация ссылки должна быть запрещена")
        except aiosqlite.IntegrityError:
            await store.db.rollback()

        await store.close()


async def scenario_100() -> None:
    """100 строк, 10 одноразовых ссылок = 10 человек по 10 строк, пул пуст."""
    with tempfile.TemporaryDirectory() as tmp:
        store = Storage(Path(tmp) / "t.sqlite3")
        await store.open()
        drop_id, _ = await store.add_drop("100.txt", [f"acc-{i}" for i in range(100)])
        codes = await store.mint_links(drop_id, 10)
        assert len(codes) == 10 and len(set(codes)) == 10
        for i, c in enumerate(codes):
            res = await store.issue(c, user_id=2000 + i, username=f"u{i}", count=10)
            assert res is not None and len(res.lines) == 10 and not res.repeat
        assert (await store.get_drop(drop_id)).left == 0
        extra = (await store.mint_links(drop_id, 1))[0]
        late = await store.issue(extra, user_id=9999, username="late", count=10)
        assert late is not None and late.lines == []
        await store.close()


def test_issue_flow() -> None:
    asyncio.run(scenario())


def test_100_lines_10_links() -> None:
    asyncio.run(scenario_100())


if __name__ == "__main__":
    test_normalize_lines()
    test_issue_flow()
    test_100_lines_10_links()
    print("OK")
