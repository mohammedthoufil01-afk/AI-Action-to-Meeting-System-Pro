"""Copy data from an existing NEXUS SQLite database into the PostgreSQL/Supabase database.

Usage (after `flask db upgrade` has created the empty tables in the target):
    python scripts/migrate_sqlite_to_postgres.py --sqlite path/to/nexus.db
    python scripts/migrate_sqlite_to_postgres.py --sqlite path/to/nexus.db --target "postgresql://..."

The target defaults to DATABASE_URL from your .env file. The script refuses to run when the target
tables already contain rows, keeps the original ids and password hashes, fixes the PostgreSQL id
counters, and verifies the row counts at the end. The SQLite file is only read, never changed.
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import Config, build_engine_options, normalize_database_url  # noqa: E402

TABLE_ORDER = ["user", "meeting", "task", "chat_message"]
VALID_STATUSES = {"Pending", "In Progress", "Completed"}
VALID_ROLES = {"user", "assistant"}


def quote(name):
    return f'"{name}"'


def read_rows(source, table):
    columns = [c["name"] for c in inspect(source).get_columns(table)]
    with source.connect() as connection:
        rows = connection.execute(text(f"SELECT * FROM {quote(table)} ORDER BY id")).mappings().all()
    return columns, [dict(row) for row in rows]


def clean_row(table, row, valid_user_ids, valid_meeting_ids):
    """Return the row if it can be stored under the new constraints, otherwise None (and say why)."""
    if table == "task":
        if row["meeting_id"] not in valid_meeting_ids:
            return None, "task without a meeting"
        if row.get("status") not in VALID_STATUSES:
            row["status"] = "Pending"
        row["reminder_sent"] = bool(row.get("reminder_sent"))
    if table == "meeting" and row["user_id"] not in valid_user_ids:
        return None, "meeting without a user"
    if table == "chat_message":
        if row["meeting_id"] not in valid_meeting_ids or row["user_id"] not in valid_user_ids:
            return None, "chat message without a meeting/user"
        if row.get("role") not in VALID_ROLES:
            return None, "chat message with an unknown role"
    return row, None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", required=True, help="path to the old SQLite file (for example instance/nexus.db)")
    parser.add_argument("--target", help="target database URL (default: DATABASE_URL from .env)")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.is_file():
        sys.exit(f"SQLite file not found: {sqlite_path}")
    target_url = normalize_database_url(args.target) if args.target else Config.SQLALCHEMY_DATABASE_URI
    if target_url.startswith("sqlite") and not args.target:
        sys.exit("DATABASE_URL is not set to PostgreSQL. Set it in .env or pass --target.")

    source = create_engine(f"sqlite:///{sqlite_path.resolve()}")
    target = create_engine(target_url, **build_engine_options(target_url))

    missing = [t for t in TABLE_ORDER if t not in inspect(target).get_table_names()]
    if missing:
        sys.exit(f"The target is missing tables {missing}. Run `flask db upgrade` first.")
    source_tables = set(inspect(source).get_table_names())

    with target.connect() as connection:
        existing = {t: connection.execute(text(f"SELECT COUNT(*) FROM {quote(t)}")).scalar() for t in TABLE_ORDER}
    if any(existing.values()):
        sys.exit(f"The target already contains data {existing}. Refusing to copy into a non-empty database.")

    copied, skipped = {}, {}
    valid_user_ids, valid_meeting_ids = set(), set()
    with target.begin() as connection:  # one transaction: all rows or nothing
        for table in TABLE_ORDER:
            if table not in source_tables:
                copied[table] = 0
                continue
            source_columns, rows = read_rows(source, table)
            target_columns = {c["name"] for c in inspect(target).get_columns(table)}
            columns = [c for c in source_columns if c in target_columns]
            count = 0
            for row in rows:
                row, reason = clean_row(table, row, valid_user_ids, valid_meeting_ids)
                if row is None:
                    skipped.setdefault(table, []).append(reason)
                    continue
                connection.execute(
                    text(f"INSERT INTO {quote(table)} ({', '.join(quote(c) for c in columns)}) "
                         f"VALUES ({', '.join(':' + c for c in columns)})"),
                    {c: row.get(c) for c in columns},
                )
                if table == "user":
                    valid_user_ids.add(row["id"])
                if table == "meeting":
                    valid_meeting_ids.add(row["id"])
                count += 1
            copied[table] = count
        if target.dialect.name == "postgresql":
            for table in TABLE_ORDER:
                connection.execute(
                    text(f"SELECT setval(pg_get_serial_sequence('{quote(table)}', 'id'), "
                         f"COALESCE((SELECT MAX(id) FROM {quote(table)}), 0) + 1, false)")
                )

    print("Rows copied:", copied)
    if skipped:
        print("Rows skipped (could not satisfy constraints):", {t: len(v) for t, v in skipped.items()})
    with target.connect() as connection:
        final = {t: connection.execute(text(f"SELECT COUNT(*) FROM {quote(t)}")).scalar() for t in TABLE_ORDER}
    expected = {t: copied.get(t, 0) for t in TABLE_ORDER}
    if final != expected:
        sys.exit(f"Verification FAILED: target has {final} but {expected} were copied.")
    print("Verification passed: target row counts match what was copied:", final)


if __name__ == "__main__":
    main()
