"""DB layer cho FB post comments — Railway Postgres.

Schema: fb_post_comments (1 row / comment_id, unique).
Incremental sync: mỗi post chỉ fetch comments mới hơn MAX(created_time) trong DB.
"""

import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

PRICE_KW = ["giá", "bao nhiêu", "bnh", "bn", "price", "cost", "tiền", "phí", "bao tiền", "mấy tiền", "báo giá", "quote"]
ADDR_KW  = ["địa chỉ", "địa chi", "ở đâu", "chỗ nào", "cửa hàng", "chi nhánh", "hà nội", "hcm", "sài gòn", "bắc ninh", "hải phòng", "hanoi"]
POS_KW   = ["tuyệt", "đẹp", "ok", "oke", "hay", "tốt", "thích", "love", "ngon", "xịn", "chất", "ưng", "ổn", "nhanh", "nhiệt tình", "vui", "xinh", "đỉnh", "chuẩn", "xịn xò"]
NEG_KW   = ["tệ", "dở", "chán", "xấu", "không ok", "không ổn", "kém", "thất vọng", "phàn nàn", "hoàn tiền", "lừa", "giả", "fake", "spam", "report", "báo cáo", "dở tệ"]


def classify(msg: str) -> str:
    if not msg:
        return "other"
    m = msg.lower()
    if any(k in m for k in NEG_KW):   return "neg"
    if any(k in m for k in PRICE_KW): return "price"
    if any(k in m for k in ADDR_KW):  return "addr"
    if any(k in m for k in POS_KW):   return "pos"
    return "other"


def _conn():
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    return psycopg2.connect(url, connect_timeout=10)


def ensure_table():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fb_post_comments (
                    comment_id     TEXT PRIMARY KEY,
                    post_id        TEXT NOT NULL,
                    page_id        TEXT,
                    ad_id          TEXT,
                    campaign_id    TEXT,
                    campaign_name  TEXT,
                    ad_name        TEXT,
                    commenter_name TEXT,
                    message        TEXT,
                    created_time   TIMESTAMPTZ,
                    like_count     INT DEFAULT 0,
                    label          TEXT DEFAULT 'other',
                    label_source   TEXT DEFAULT 'rule',
                    synced_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_fpc_campaign ON fb_post_comments(campaign_id);
                CREATE INDEX IF NOT EXISTS idx_fpc_created  ON fb_post_comments(created_time DESC);
                CREATE INDEX IF NOT EXISTS idx_fpc_label    ON fb_post_comments(label);
                CREATE INDEX IF NOT EXISTS idx_fpc_post     ON fb_post_comments(post_id);
            """)
        conn.commit()


def get_latest_ts_by_post() -> dict:
    """Trả {post_id: unix_timestamp} của comment mới nhất trong DB."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT post_id, EXTRACT(EPOCH FROM MAX(created_time))::BIGINT
                    FROM fb_post_comments
                    GROUP BY post_id
                """)
                return {row[0]: row[1] for row in cur.fetchall()}
    except Exception:
        return {}


def upsert_comments(rows: list) -> int:
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    data = [
        (
            r["comment_id"], r["post_id"], r["page_id"], r["ad_id"],
            r["campaign_id"], r["campaign_name"], r["ad_name"],
            r["commenter_name"], r["message"], r["created_time"],
            r["like_count"], r["label"], r["label_source"], now,
        )
        for r in rows
    ]
    with _conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO fb_post_comments
                    (comment_id, post_id, page_id, ad_id, campaign_id, campaign_name,
                     ad_name, commenter_name, message, created_time, like_count,
                     label, label_source, synced_at)
                VALUES %s
                ON CONFLICT (comment_id) DO UPDATE SET
                    like_count = EXCLUDED.like_count,
                    synced_at  = EXCLUDED.synced_at
            """, data)
        conn.commit()
    return len(rows)


def query_stats(days: int = 7) -> dict:
    """Stats tổng hợp: by label + by campaign."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT label, COUNT(*) FROM fb_post_comments
                    WHERE created_time > NOW() - INTERVAL '%s days'
                    GROUP BY label
                """, (days,))
                by_label = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute("""
                    SELECT campaign_id, campaign_name, label, COUNT(*) as n
                    FROM fb_post_comments
                    WHERE created_time > NOW() - INTERVAL '%s days'
                    GROUP BY campaign_id, campaign_name, label
                    ORDER BY n DESC
                """, (days,))
                camp_rows = cur.fetchall()

                cur.execute("""
                    SELECT DATE(created_time AT TIME ZONE 'Asia/Ho_Chi_Minh') as day,
                           label, COUNT(*) as n
                    FROM fb_post_comments
                    WHERE created_time > NOW() - INTERVAL '%s days'
                    GROUP BY day, label
                    ORDER BY day
                """, (days,))
                trend_rows = cur.fetchall()

        campaigns = {}
        for cid, cname, lbl, n in camp_rows:
            if cid not in campaigns:
                campaigns[cid] = {"campaign_name": cname, "total": 0, "by_label": {}}
            campaigns[cid]["by_label"][lbl] = n
            campaigns[cid]["total"] += n

        trend: dict = {}
        for day, lbl, n in trend_rows:
            d = str(day)
            trend.setdefault(d, {})[lbl] = n

        return {
            "by_label": by_label,
            "total": sum(by_label.values()),
            "campaigns": campaigns,
            "trend": trend,
        }
    except Exception as e:
        return {"by_label": {}, "total": 0, "campaigns": {}, "trend": {}, "error": str(e)}


def query_comments(days: int = 7, campaign_id: str = None,
                   label: str = None, search: str = None, limit: int = 300) -> list:
    conds = ["created_time > NOW() - INTERVAL %s"]
    params: list = [f"{days} days"]

    if campaign_id:
        conds.append("campaign_id = %s")
        params.append(campaign_id)
    if label:
        conds.append("label = %s")
        params.append(label)
    if search:
        conds.append("message ILIKE %s")
        params.append(f"%{search}%")

    where = " AND ".join(conds)
    try:
        with _conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT comment_id, post_id, ad_id, campaign_id, campaign_name,
                           ad_name, commenter_name, message,
                           created_time AT TIME ZONE 'Asia/Ho_Chi_Minh' AS created_time,
                           like_count, label
                    FROM fb_post_comments
                    WHERE {where}
                    ORDER BY created_time DESC
                    LIMIT %s
                """, params + [limit])
                rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("created_time"):
                d["created_time"] = d["created_time"].isoformat()
            result.append(d)
        return result
    except Exception:
        return []


def get_sync_status() -> dict:
    """Tổng số comment trong DB + thời gian sync gần nhất."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*), MAX(synced_at) FROM fb_post_comments")
                total, last = cur.fetchone()
        return {
            "total_in_db": total or 0,
            "last_synced_at": last.isoformat() if last else None,
        }
    except Exception:
        return {"total_in_db": 0, "last_synced_at": None}
