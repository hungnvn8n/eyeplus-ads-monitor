"""Lưu dữ liệu TikTok Ads THEO NGÀY vào Postgres dùng chung (ROLLUP_DATABASE_URL).

Vì sao: TikTok API trả số liệu gộp theo khoảng ngày yêu cầu — trước đây mỗi lần
đổi khoảng ngày (kéo lùi 1 ngày, đổi 7↔15↔30 ngày...) là gọi lại TOÀN BỘ API dù
phần lớn ngày không đổi. Giờ fetch theo NGÀY (dimension stat_time_day), lưu mỗi
ngày 1 dòng/campaign hoặc /ad — ngày nào đã có trong DB thì đọc lại, không gọi
API nữa. Chỉ ngày HÔM NAY được coi là "chưa chốt", luôn fetch lại (TTL ngắn, xem
tiktok_fetcher._missing_ranges).

2 bảng cùng schema (khác cấp): tiktok_daily_campaign (1 dòng/ngày/campaign),
tiktok_daily_ad (1 dòng/ngày/ad). Chỉ lưu số THÔ cộng dồn được (spend, impressions,
clicks, conversions...) — các tỉ lệ (CTR/CPM/ROAS...) tính lại sau khi gộp nhiều
ngày, KHÔNG lưu sẵn (cộng CTR của nhiều ngày là sai).

tiktok_fetch_log đánh dấu (advertiser_id, kind, date) đã fetch — kể cả ngày
0 campaign có chi tiêu (để không hỏi API lại vô ích).
"""
import os
from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
import psycopg2.pool

# Bể kết nối dùng chung — mở 1 kết nối mới qua proxy công cộng
# switchyard.proxy.rlwy.net tốn ~1,7-2s (đo thực tế 09/09/2026 khi test module
# này), trong khi mỗi lần đổi khoảng ngày gọi vài lượt DB (missing_ranges +
# upsert + mark_fetched + get_*_days) → cộng dồn rất chậm nếu mở/đóng riêng
# từng lần. Cùng pattern inbox_db.py trong repo này.
_pool: psycopg2.pool.ThreadedConnectionPool = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 6, url, connect_timeout=15)
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()  # reset state — nơi cần ghi đã tự conn.commit() bên trong
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


_CAMPAIGN_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tiktok_daily_campaign (
    date TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    campaign_name TEXT,
    spend NUMERIC DEFAULT 0,
    impressions BIGINT DEFAULT 0,
    reach BIGINT DEFAULT 0,
    clicks BIGINT DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    purchases INTEGER DEFAULT 0,
    purchase_value NUMERIC DEFAULT 0,
    engagements INTEGER DEFAULT 0,
    don_offline INTEGER DEFAULT 0,
    PRIMARY KEY (date, campaign_id)
)
"""

_AD_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tiktok_daily_ad (
    date TEXT NOT NULL,
    advertiser_id TEXT NOT NULL,
    campaign_id TEXT,
    campaign_name TEXT,
    adgroup_id TEXT,
    adgroup_name TEXT,
    ad_id TEXT NOT NULL,
    ad_name TEXT,
    spend NUMERIC DEFAULT 0,
    impressions BIGINT DEFAULT 0,
    reach BIGINT DEFAULT 0,
    clicks BIGINT DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    purchases INTEGER DEFAULT 0,
    purchase_value NUMERIC DEFAULT 0,
    engagements INTEGER DEFAULT 0,
    don_offline INTEGER DEFAULT 0,
    PRIMARY KEY (date, ad_id)
)
"""

_FETCH_LOG_SQL = """
CREATE TABLE IF NOT EXISTS tiktok_fetch_log (
    advertiser_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    date TEXT NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (advertiser_id, kind, date)
)
"""


def ensure_tables() -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CAMPAIGN_TABLE_SQL)
            cur.execute(_AD_TABLE_SQL)
            cur.execute(_FETCH_LOG_SQL)
        conn.commit()


def upsert_campaign_days(rows: list) -> None:
    if not rows:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO tiktok_daily_campaign
                    (date, advertiser_id, campaign_id, campaign_name, spend, impressions,
                     reach, clicks, conversions, purchases, purchase_value, engagements, don_offline)
                VALUES %s
                ON CONFLICT (date, campaign_id) DO UPDATE SET
                    advertiser_id = EXCLUDED.advertiser_id,
                    campaign_name = EXCLUDED.campaign_name,
                    spend = EXCLUDED.spend, impressions = EXCLUDED.impressions,
                    reach = EXCLUDED.reach, clicks = EXCLUDED.clicks,
                    conversions = EXCLUDED.conversions, purchases = EXCLUDED.purchases,
                    purchase_value = EXCLUDED.purchase_value, engagements = EXCLUDED.engagements,
                    don_offline = EXCLUDED.don_offline
            """, [(r["date"], r["advertiser_id"], r["campaign_id"], r["campaign_name"],
                   r["spend"], r["impressions"], r["reach"], r["clicks"], r["conversions"],
                   r["purchases"], r["purchase_value"], r["engagements"], r["don_offline"])
                  for r in rows])
        conn.commit()


def upsert_ad_days(rows: list) -> None:
    if not rows:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO tiktok_daily_ad
                    (date, advertiser_id, campaign_id, campaign_name, adgroup_id, adgroup_name,
                     ad_id, ad_name, spend, impressions, reach, clicks, conversions,
                     purchases, purchase_value, engagements, don_offline)
                VALUES %s
                ON CONFLICT (date, ad_id) DO UPDATE SET
                    advertiser_id = EXCLUDED.advertiser_id,
                    campaign_id = EXCLUDED.campaign_id, campaign_name = EXCLUDED.campaign_name,
                    adgroup_id = EXCLUDED.adgroup_id, adgroup_name = EXCLUDED.adgroup_name,
                    ad_name = EXCLUDED.ad_name,
                    spend = EXCLUDED.spend, impressions = EXCLUDED.impressions,
                    reach = EXCLUDED.reach, clicks = EXCLUDED.clicks,
                    conversions = EXCLUDED.conversions, purchases = EXCLUDED.purchases,
                    purchase_value = EXCLUDED.purchase_value, engagements = EXCLUDED.engagements,
                    don_offline = EXCLUDED.don_offline
            """, [(r["date"], r["advertiser_id"], r["campaign_id"], r["campaign_name"],
                   r["adgroup_id"], r["adgroup_name"], r["ad_id"], r["ad_name"],
                   r["spend"], r["impressions"], r["reach"], r["clicks"], r["conversions"],
                   r["purchases"], r["purchase_value"], r["engagements"], r["don_offline"])
                  for r in rows])
        conn.commit()


def mark_fetched(advertiser_id: str, kind: str, dates: list) -> None:
    if not dates:
        return
    with _conn() as conn:
        now = datetime.now()
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO tiktok_fetch_log (advertiser_id, kind, date, fetched_at)
                VALUES %s
                ON CONFLICT (advertiser_id, kind, date) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
            """, [(advertiser_id, kind, d, now) for d in dates])
        conn.commit()


def fetched_dates(advertiser_id: str, kind: str, date_from: str, date_to: str) -> dict:
    """{date: fetched_at datetime} cho advertiser+kind trong khoảng."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, fetched_at FROM tiktok_fetch_log
                WHERE advertiser_id = %s AND kind = %s AND date BETWEEN %s AND %s
            """, (advertiser_id, kind, date_from, date_to))
            return {r[0]: r[1] for r in cur.fetchall()}


def get_campaign_days(date_from: str, date_to: str) -> list:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM tiktok_daily_campaign WHERE date BETWEEN %s AND %s",
                (date_from, date_to))
            return [dict(r) for r in cur.fetchall()]


def get_ad_days(date_from: str, date_to: str) -> list:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM tiktok_daily_ad WHERE date BETWEEN %s AND %s",
                (date_from, date_to))
            return [dict(r) for r in cur.fetchall()]
