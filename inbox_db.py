"""Read-only layer cho pancake_inbox_intents (viết bởi fb_chatbot webhook)."""
import os
import re
import time
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager

_PHONE_RE = re.compile(r'0[35789][0-9]{8}')

_stats_cache: dict = {}
_stats_cache_ts: float = 0
CACHE_TTL = 60  # seconds

_pool = None  # psycopg2.pool.ThreadedConnectionPool


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 3, url, connect_timeout=15)
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()  # reset state
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def table_exists() -> bool:
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'pancake_inbox_intents'
                )
            """)
            return cur.fetchone()[0]
    except Exception:
        return False


def query_all(days: int = 7, campaign_id: str = None, no_campaign: bool = False,
              label: str = None, search: str = None, limit: int = 500) -> dict:
    """Single-connection query: stats + messages + sync_status."""
    global _stats_cache, _stats_cache_ts

    now = time.time()
    use_cache = (now - _stats_cache_ts < CACHE_TTL
                 and _stats_cache.get("days") == days)

    try:
        with _conn() as conn:
            cur = conn.cursor()
            if True:
                # --- Stats (cached) ---
                if not use_cache:
                    cur.execute("""
                        SELECT label, COUNT(*) FROM pancake_inbox_intents
                        WHERE msg_ts > NOW() - INTERVAL %s
                        GROUP BY label
                    """, (f"{days} days",))
                    by_label = {r[0]: r[1] for r in cur.fetchall()}

                    cur.execute("""
                        SELECT campaign_id, campaign_name, label, COUNT(*) as n
                        FROM pancake_inbox_intents
                        WHERE msg_ts > NOW() - INTERVAL %s
                        GROUP BY campaign_id, campaign_name, label
                        ORDER BY n DESC
                    """, (f"{days} days",))
                    camp_rows = cur.fetchall()

                    campaigns = {}
                    for cid, cname, lbl, n in camp_rows:
                        key = cid or "__no_ad__"
                        if key not in campaigns:
                            campaigns[key] = {
                                "campaign_id": cid,
                                "campaign_name": cname or "(chưa link)",
                                "total": 0, "by_label": {}
                            }
                        campaigns[key]["by_label"][lbl] = n
                        campaigns[key]["total"] += n

                    # phone numbers — overall
                    cur.execute("""
                        SELECT COUNT(*) FROM pancake_inbox_intents
                        WHERE msg_ts > NOW() - INTERVAL %s
                        AND message ~ '0[35789][0-9]{8}'
                    """, (f"{days} days",))
                    phone_count = cur.fetchone()[0] or 0

                    # phone per campaign
                    cur.execute("""
                        SELECT campaign_id, COUNT(*) FROM pancake_inbox_intents
                        WHERE msg_ts > NOW() - INTERVAL %s
                        AND message ~ '0[35789][0-9]{8}'
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    camp_phone = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}

                    # khách mất tích per campaign: khách chỉ gửi ≤ 1 tin rồi không rep nữa
                    cur.execute("""
                        SELECT campaign_id, COUNT(*) FROM pancake_inbox_intents
                        WHERE msg_ts > NOW() - INTERVAL %s
                          AND cust_msg_count IS NOT NULL AND cust_msg_count <= 1
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    camp_ghost = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}
                    ghost_count = sum(camp_ghost.values())

                    # số hội thoại đã quét (có cust_msg_count) — để tính % ghost
                    cur.execute("""
                        SELECT campaign_id, COUNT(*) FROM pancake_inbox_intents
                        WHERE msg_ts > NOW() - INTERVAL %s AND cust_msg_count IS NOT NULL
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    camp_scanned = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}
                    scanned_count = sum(camp_scanned.values())

                    # merge per-campaign vào campaigns dict
                    for key in campaigns:
                        campaigns[key]["phone"]   = camp_phone.get(key, 0)
                        campaigns[key]["ghost"]   = camp_ghost.get(key, 0)
                        campaigns[key]["scanned"] = camp_scanned.get(key, 0)

                    total_all = sum(by_label.values())
                    _stats_cache = {
                        "days": days,
                        "by_label": by_label,
                        "total": total_all,
                        "campaigns": campaigns,
                        "phone_count": phone_count,
                        "ghost_count": ghost_count,
                        "scanned_count": scanned_count,
                    }
                    _stats_cache_ts = now

                stats = _stats_cache

                # --- Sync status ---
                cur.execute("SELECT COUNT(*), MAX(synced_at) FROM pancake_inbox_intents")
                total_db, last_sync = cur.fetchone()

                # --- Messages ---
                conds = ["msg_ts > NOW() - INTERVAL %s"]
                params: list = [f"{days} days"]

                if no_campaign:
                    conds.append("campaign_id IS NULL")
                elif campaign_id:
                    conds.append("campaign_id = %s")
                    params.append(campaign_id)
                if label:
                    conds.append("label = %s")
                    params.append(label)
                if search:
                    conds.append("message ILIKE %s")
                    params.append(f"%{search}%")

                where = " AND ".join(conds)
                cur.execute(f"""
                    SELECT msg_id, conv_id, page_id, ad_id, campaign_id, campaign_name,
                           customer_name, message, cust_msg_count,
                           msg_ts AT TIME ZONE 'Asia/Ho_Chi_Minh' AS msg_ts,
                           label
                    FROM pancake_inbox_intents
                    WHERE {where}
                    ORDER BY msg_ts DESC LIMIT %s
                """, params + [limit])

                cols = [d[0] for d in cur.description]
                msgs = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("msg_ts"):
                        d["msg_ts"] = d["msg_ts"].isoformat()
                    txt = d.get("message") or ""
                    cc = d.get("cust_msg_count")
                    d["is_ghost"] = (cc is not None and cc <= 1)
                    d["is_phone"] = bool(_PHONE_RE.search(txt))
                    msgs.append(d)

        out = dict(stats)
        out["messages"] = msgs
        out["total_in_db"] = total_db or 0
        out["last_synced_at"] = last_sync.isoformat() if last_sync else None
        return out

    except Exception as e:
        return {
            "by_label": {}, "total": 0, "campaigns": {},
            "messages": [], "total_in_db": 0, "last_synced_at": None,
            "error": str(e)
        }
