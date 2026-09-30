from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime
from pathlib import Path


RANDOM_START_PARAM = re.compile(r"^(?=.{8}$)(?=.*[a-z])(?=.*[A-Z])[A-Za-z0-9]+$")


def matching_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT user_id, start_param, purchases_total
        FROM users
        WHERE start_param IS NOT NULL AND start_param != ''
        """
    ).fetchall()
    return [row for row in rows if RANDOM_START_PARAM.fullmatch(str(row["start_param"]))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove random mixed-case 8-character start tags without deleting users."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    database = args.database.resolve()
    if not database.is_file():
        raise SystemExit(f"Database does not exist: {database}")

    connection = sqlite3.connect(database, timeout=30)
    rows = matching_rows(connection)
    distinct_codes = sorted({str(row["start_param"]) for row in rows})
    purchases_total = sum(float(row["purchases_total"] or 0.0) for row in rows)
    print(
        f"matched_codes={len(distinct_codes)} "
        f"matched_users={len(rows)} purchases_total={purchases_total:.2f}"
    )
    if not args.apply or not rows:
        connection.close()
        return

    backup_dir = (args.backup_dir or database.parent / "backups").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{database.stem}.before-random-start-cleanup-{timestamp}{database.suffix}"
    with sqlite3.connect(backup_path) as backup_connection:
        connection.backup(backup_connection)

    user_ids = [int(row["user_id"]) for row in rows]
    placeholders = ",".join("?" for _ in user_ids)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        f"""
        UPDATE users
        SET start_param=NULL,
            utm_source=NULL,
            utm_medium=NULL,
            utm_campaign=NULL,
            utm_content=NULL,
            utm_term=NULL
        WHERE user_id IN ({placeholders})
        """,
        user_ids,
    )
    connection.commit()
    remaining = len(matching_rows(connection))
    connection.close()
    print(f"updated_users={len(user_ids)} remaining_matches={remaining}")
    print(f"backup={backup_path}")


if __name__ == "__main__":
    main()
