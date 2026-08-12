"""Read-only layer cho inbox intents — 2 nguồn, cùng schema, cùng cách chấm điểm.

- fb     : pancake_inbox_intents (fb_chatbot webhook ghi)
- tiktok : tiktok_inbox_intents  (tiktok_inbox_sync.py ghi)
"""
import os
import re
import time
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager

_PHONE_RE = re.compile(r'0[35789][0-9]{8}')

_stats_cache: dict = {}
_stats_cache_ts: dict = {}

# Hai kênh, hai bảng, CÙNG tên cột nên dùng chung mọi truy vấn bên dưới.
# tiktok_inbox_intents do tiktok_inbox_sync.py ghi (gắn campaign qua ad_clicks
# của Pancake, vì TikTok Ads API không trả kết quả tin nhắn cho camp MESSAGE_CLUE).
TABLES = {"fb": "pancake_inbox_intents", "tiktok": "tiktok_inbox_intents"}
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


def _table(source: str) -> str:
    return TABLES.get(source, TABLES["fb"])


def table_exists(source: str = "fb") -> bool:
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = %s
                )
            """, (_table(source),))
            return cur.fetchone()[0]
    except Exception:
        return False


def query_all(days: int = 7, campaign_id: str = None, no_campaign: bool = False,
              label: str = None, search: str = None, limit: int = 500,
              source: str = "fb") -> dict:
    """Single-connection query: stats + messages + sync_status.

    source="fb" → inbox Facebook (Pancake webhook); "tiktok" → inbox TikTok.
    """
    global _stats_cache, _stats_cache_ts

    TBL = _table(source)
    now = time.time()
    cached = _stats_cache.get(source) or {}
    use_cache = (now - _stats_cache_ts.get(source, 0) < CACHE_TTL
                 and cached.get("days") == days)

    try:
        with _conn() as conn:
            cur = conn.cursor()
            if True:
                # --- Stats (cached) ---
                # Đơn vị đếm = HỘI THOẠI (DISTINCT conv_id), không phải từng tin —
                # webhook lưu mọi tin của khách nên đếm tin sẽ phồng số + loãng %.
                if not use_cache:
                    cur.execute(f"""
                        SELECT label, COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                        GROUP BY label
                    """, (f"{days} days",))
                    by_label = {r[0]: r[1] for r in cur.fetchall()}

                    cur.execute(f"""
                        SELECT campaign_id, campaign_name, label, COUNT(DISTINCT conv_id) as n
                        FROM {TBL}
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

                    # tổng hội thoại per campaign (1 conv có nhiều label vẫn đếm 1)
                    cur.execute(f"""
                        SELECT campaign_id, COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    for cid, n in cur.fetchall():
                        key = cid or "__no_ad__"
                        if key in campaigns:
                            campaigns[key]["total"] = n

                    # hội thoại có SĐT — overall
                    cur.execute(f"""
                        SELECT COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                        AND message ~ '0[35789][0-9]{{8}}'
                    """, (f"{days} days",))
                    phone_count = cur.fetchone()[0] or 0

                    # hội thoại có SĐT per campaign
                    cur.execute(f"""
                        SELECT campaign_id, COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                        AND message ~ '0[35789][0-9]{{8}}'
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    camp_phone = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}

                    # khách mất tích per campaign: khách chỉ gửi ≤ 1 tin rồi không rep nữa
                    cur.execute(f"""
                        SELECT campaign_id, COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                          AND cust_msg_count IS NOT NULL AND cust_msg_count <= 1
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    camp_ghost = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}
                    ghost_count = sum(camp_ghost.values())

                    # số hội thoại đã quét (có cust_msg_count) — để tính % ghost
                    cur.execute(f"""
                        SELECT campaign_id, COUNT(DISTINCT conv_id) FROM {TBL}
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

                    cur.execute(f"""
                        SELECT COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                    """, (f"{days} days",))
                    total_all = cur.fetchone()[0] or 0
                    _stats_cache[source] = {
                        "days": days,
                        "by_label": by_label,
                        "total": total_all,
                        "campaigns": campaigns,
                        "phone_count": phone_count,
                        "ghost_count": ghost_count,
                        "scanned_count": scanned_count,
                    }
                    _stats_cache_ts[source] = now

                stats = _stats_cache[source]

                # --- Sync status ---
                cur.execute(f"SELECT COUNT(*), MAX(synced_at) FROM {TBL}")
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
                # Gom theo HỘI THOẠI: 1 conv = 1 card (tin mới nhất), cờ SĐT/địa chỉ
                # xét trên toàn bộ tin trong conv chứ không riêng tin cuối.
                cur.execute(f"""
                    WITH filtered AS (
                        SELECT * FROM {TBL} WHERE {where}
                    ), agg AS (
                        SELECT conv_id,
                               COUNT(*)                                AS conv_msgs,
                               BOOL_OR(label = 'addr')                 AS has_addr,
                               BOOL_OR(message ~ '0[35789][0-9]{{8}}') AS has_phone
                        FROM filtered GROUP BY conv_id
                    )
                    SELECT * FROM (
                        SELECT DISTINCT ON (f.conv_id)
                               f.msg_id, f.conv_id, f.page_id, f.ad_id,
                               f.campaign_id, f.campaign_name,
                               f.customer_name, f.message, f.cust_msg_count,
                               f.msg_ts AT TIME ZONE 'Asia/Ho_Chi_Minh' AS msg_ts,
                               f.label, a.conv_msgs, a.has_addr, a.has_phone
                        FROM filtered f JOIN agg a USING (conv_id)
                        ORDER BY f.conv_id, f.msg_ts DESC
                    ) t ORDER BY msg_ts DESC LIMIT %s
                """, params + [limit])

                cols = [d[0] for d in cur.description]
                msgs = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("msg_ts"):
                        d["msg_ts"] = d["msg_ts"].isoformat()
                    cc = d.get("cust_msg_count")
                    d["is_ghost"] = (cc is not None and cc <= 1)
                    d["is_phone"] = bool(d.pop("has_phone", False))
                    # conv từng hỏi địa chỉ nhưng tin cuối là label khác → giữ badge địa chỉ
                    if d.pop("has_addr", False) and d.get("label") != "addr":
                        d["label"] = "addr"
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
