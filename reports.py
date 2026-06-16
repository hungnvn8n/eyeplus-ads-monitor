"""MKT Team Reports — báo cáo cuối ngày, cuối tuần, cuối tháng."""
import os
import sqlite3
from pathlib import Path

try:
    _vol = Path("/data")
    ROOT = _vol if _vol.exists() and _vol.is_dir() else Path(os.getcwd())
except Exception:
    ROOT = Path(__file__).resolve().parent

DB_PATH = ROOT / "shadow.db"

DEFAULT_MEMBERS = ["Loan", "Quyên", "Tùng", "Trang", "Đạt", "Thắng", "Hạnh"]


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS mkt_members (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS mkt_report_daily (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_name TEXT NOT NULL,
          report_date TEXT NOT NULL,
          q1 TEXT DEFAULT '',
          q2 TEXT DEFAULT '',
          q3 TEXT DEFAULT '',
          q4 TEXT DEFAULT '',
          unfinished TEXT DEFAULT '',
          tomorrow_plan TEXT DEFAULT '',
          blockers TEXT DEFAULT '',
          created_at TEXT DEFAULT (datetime('now', 'localtime')),
          UNIQUE(member_name, report_date)
        );
        CREATE TABLE IF NOT EXISTS mkt_report_monthly (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_name TEXT NOT NULL,
          report_month TEXT NOT NULL,
          kpis TEXT DEFAULT '',
          highlights TEXT DEFAULT '',
          challenges TEXT DEFAULT '',
          next_month_plan TEXT DEFAULT '',
          support_needed TEXT DEFAULT '',
          created_at TEXT DEFAULT (datetime('now', 'localtime')),
          UNIQUE(member_name, report_month)
        );
        CREATE TABLE IF NOT EXISTS mkt_report_weekly (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          member_name TEXT NOT NULL,
          week_start TEXT NOT NULL,
          done_items TEXT DEFAULT '',
          pending_items TEXT DEFAULT '',
          priorities TEXT DEFAULT '',
          lessons TEXT DEFAULT '',
          support_needed TEXT DEFAULT '',
          created_at TEXT DEFAULT (datetime('now', 'localtime')),
          UNIQUE(member_name, week_start)
        );
        """)
        existing = conn.execute("SELECT COUNT(*) FROM mkt_members").fetchone()[0]
        if existing == 0:
            for m in DEFAULT_MEMBERS:
                try:
                    conn.execute("INSERT INTO mkt_members (name) VALUES (?)", (m,))
                except Exception:
                    pass
            conn.commit()


def get_members():
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name FROM mkt_members WHERE active=1 ORDER BY id"
        ).fetchall()
        return [r["name"] for r in rows]


def add_member(name: str):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mkt_members (name) VALUES (?)", (name.strip(),)
        )
        conn.commit()


def remove_member(name: str):
    with _conn() as conn:
        conn.execute("UPDATE mkt_members SET active=0 WHERE name=?", (name,))
        conn.commit()


# ── Daily ──────────────────────────────────────────────

def save_daily(member, date, q1, q2, q3, q4, unfinished, tomorrow_plan, blockers):
    with _conn() as conn:
        conn.execute("""
        INSERT INTO mkt_report_daily
          (member_name, report_date, q1, q2, q3, q4, unfinished, tomorrow_plan, blockers)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(member_name, report_date) DO UPDATE SET
          q1=excluded.q1, q2=excluded.q2, q3=excluded.q3, q4=excluded.q4,
          unfinished=excluded.unfinished, tomorrow_plan=excluded.tomorrow_plan,
          blockers=excluded.blockers
        """, (member, date, q1, q2, q3, q4, unfinished, tomorrow_plan, blockers))
        conn.commit()


def get_daily(member, date):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM mkt_report_daily WHERE member_name=? AND report_date=?",
            (member, date)
        ).fetchone()
        return dict(row) if row else None


def get_daily_all(date):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mkt_report_daily WHERE report_date=? ORDER BY member_name",
            (date,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_recent(days=7):
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM mkt_report_daily
               WHERE report_date >= date('now', ?, 'localtime')
               ORDER BY report_date DESC, member_name""",
            (f"-{days} days",)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Monthly ────────────────────────────────────────────

def save_monthly(member, month, kpis, highlights, challenges, next_month_plan, support_needed):
    with _conn() as conn:
        conn.execute("""
        INSERT INTO mkt_report_monthly
          (member_name, report_month, kpis, highlights, challenges, next_month_plan, support_needed)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(member_name, report_month) DO UPDATE SET
          kpis=excluded.kpis, highlights=excluded.highlights,
          challenges=excluded.challenges, next_month_plan=excluded.next_month_plan,
          support_needed=excluded.support_needed
        """, (member, month, kpis, highlights, challenges, next_month_plan, support_needed))
        conn.commit()


def get_monthly(member, month):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM mkt_report_monthly WHERE member_name=? AND report_month=?",
            (member, month)
        ).fetchone()
        return dict(row) if row else None


def get_monthly_all(month):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mkt_report_monthly WHERE report_month=? ORDER BY member_name",
            (month,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Weekly ─────────────────────────────────────────────

def save_weekly(member, week_start, done_items, pending_items, priorities, lessons, support_needed):
    with _conn() as conn:
        conn.execute("""
        INSERT INTO mkt_report_weekly
          (member_name, week_start, done_items, pending_items, priorities, lessons, support_needed)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(member_name, week_start) DO UPDATE SET
          done_items=excluded.done_items, pending_items=excluded.pending_items,
          priorities=excluded.priorities, lessons=excluded.lessons,
          support_needed=excluded.support_needed
        """, (member, week_start, done_items, pending_items, priorities, lessons, support_needed))
        conn.commit()


def get_weekly(member, week_start):
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM mkt_report_weekly WHERE member_name=? AND week_start=?",
            (member, week_start)
        ).fetchone()
        return dict(row) if row else None


def get_weekly_all(week_start):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mkt_report_weekly WHERE week_start=? ORDER BY member_name",
            (week_start,)
        ).fetchall()
        return [dict(r) for r in rows]
