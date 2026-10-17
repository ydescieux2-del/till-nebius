"""What Till remembers between conversations: cash alerts, reviewed charges, the last brief,
and any books a user imported. One SQLite file, keyed by business id."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("TILL_DB", Path(__file__).resolve().parent.parent / "data" / "till_state.db"))

_SCHEMA = """
create table if not exists alerts  (business text, kind text, threshold real, created text,
                                    primary key (business, kind));
create table if not exists reviews (business text, charge_id text, verdict text, note text, at text,
                                    primary key (business, charge_id));
create table if not exists briefs  (business text, at text, balance real, low_point real);
create table if not exists books   (business text primary key, rows text, opening real, imported text);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def set_alert(business: str, kind: str, threshold: float) -> None:
    with _db() as con:
        con.execute("insert or replace into alerts values (?,?,?,?)", (business, kind, threshold, _now()))


def alerts(business: str) -> list[dict]:
    with _db() as con:
        return [dict(r) for r in con.execute("select kind, threshold, created from alerts where business=?",
                                             (business,))]


def review(business: str, charge_id: str, verdict: str, note: str = "") -> None:
    with _db() as con:
        con.execute("insert or replace into reviews values (?,?,?,?,?)",
                    (business, charge_id, verdict, note, _now()))


def reviews(business: str) -> dict[str, dict]:
    with _db() as con:
        return {r["charge_id"]: dict(r) for r in
                con.execute("select charge_id, verdict, note, at from reviews where business=?", (business,))}


def last_brief(business: str) -> dict | None:
    with _db() as con:
        r = con.execute("select at, balance, low_point from briefs where business=? order by at desc limit 1",
                        (business,)).fetchone()
        return dict(r) if r else None


def record_brief(business: str, balance: float, low_point: float) -> None:
    with _db() as con:
        con.execute("insert into briefs values (?,?,?,?)", (business, _now(), balance, low_point))


def save_books(business: str, rows: list[dict], opening: float) -> None:
    with _db() as con:
        con.execute("insert or replace into books values (?,?,?,?)",
                    (business, json.dumps(rows), opening, _now()))


def books(business: str) -> tuple[list[dict], float] | None:
    with _db() as con:
        r = con.execute("select rows, opening from books where business=?", (business,)).fetchone()
        return (json.loads(r["rows"]), r["opening"]) if r else None


def forget(business: str) -> None:
    with _db() as con:
        for table in ("alerts", "reviews", "briefs", "books"):
            con.execute(f"delete from {table} where business=?", (business,))
