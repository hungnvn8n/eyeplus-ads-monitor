"""adjust_history — "Lịch sử điều chỉnh gần nhất" cho mỗi chiến dịch.

Gom các lần ĐIỀU CHỈNH THẬT (đổi ngân sách, tắt/bật) gần nhất của từng chiến
dịch để cột "Điều chỉnh gần nhất" ở bảng tab Inbox hiển thị: giờ-phút + làm gì.

Lưu ở **Postgres ROLLUP** (bảng ads_adjust_log), KHÔNG dùng file cục bộ:
Railway chạy nhiều bản sao (replica) và xoá container mỗi lần deploy, nên file
jsonl ghi ở bản này thì bản khác đọc không thấy — đó là lý do cột từng trống
dù thao tác thành công. Postgres dùng chung mọi bản + bền qua deploy.

Vẫn gộp thêm tiktok_log (shadow.db) theo tên cho lịch sử TikTok cũ. CỐ Ý KHÔNG
lấy auto_pause_log — đó chỉ là cảnh báo đạt ngưỡng, app không tự tắt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("adjust_history")

_table_ready = False


def _ensure_table(cur) -> None:
    global _table_ready
    if _table_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ads_adjust_log (
            id          BIGSERIAL PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            name        TEXT,
            txt         TEXT NOT NULL,
            platform    TEXT,
            ts          TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_ads_adjust_cid_ts "
                "ON ads_adjust_log (campaign_id, ts DESC)")
    _table_ready = True


def log(campaign_id: str, campaign_name: str, text: str, platform: str = "fb") -> None:
    """Ghi 1 điều chỉnh (tắt/đổi NS) vào Postgres. Nuốt lỗi — không chặn thao tác."""
    cid = str(campaign_id or "")
    if not cid:
        return
    try:
        import inbox_db
        with inbox_db._conn() as conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                cur.execute(
                    "INSERT INTO ads_adjust_log (campaign_id, name, txt, platform) "
                    "VALUES (%s,%s,%s,%s)",
                    (cid, campaign_name or "", text, platform),
                )
            conn.commit()
    except Exception as e:
        logger.warning(f"adjust_history.log lỗi: {e}")


def log_budget(campaign_id: str, campaign_name: str, pct=None, delta=None,
               source: str = "") -> None:
    """Ghi 1 lần đổi ngân sách — tự diễn giải 'làm gì' từ pct/delta."""
    if pct is not None:
        verb = "Tăng" if pct >= 0 else "Giảm"
        body = f"{verb} NS {abs(round(pct))}%"
    elif isinstance(delta, (int, float)) and delta:
        body = ("Tăng" if delta > 0 else "Giảm") + " ngân sách"
    else:
        body = "Đổi ngân sách"
    if (source or "") not in ("user_button", "user", ""):
        body = "Tự " + body[0].lower() + body[1:]
    log(campaign_id, campaign_name, body, "fb")


def latest(days: int = 60) -> dict:
    """Trả {'by_id': {cid: {ts, text}}, 'by_name': {name: {ts, text}}}.
    Client tra by_id[campaign_id] trước, không có thì by_name[campaign_name]."""
    by_id: dict = {}
    by_name: dict = {}

    # 1) Postgres: bản ghi MỚI NHẤT mỗi chiến dịch trong khoảng ngày
    try:
        import inbox_db
        with inbox_db._conn() as conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                cur.execute(
                    "SELECT DISTINCT ON (campaign_id) campaign_id, txt, ts "
                    "FROM ads_adjust_log "
                    "WHERE ts > now() - make_interval(days => %s) "
                    "ORDER BY campaign_id, ts DESC",
                    (int(days),),
                )
                for cid, txt, ts in cur.fetchall():
                    by_id[str(cid)] = {"ts": ts.isoformat(), "text": txt}
    except Exception as e:
        logger.warning(f"adjust_history.latest (Postgres) lỗi: {e}")

    # 2) Thao tác TikTok cũ từ shadow.db — map theo tên (lịch sử trước khi dùng
    #    Postgres). Chỉ bổ sung những tên chưa có bản Postgres tương ứng.
    try:
        import shadow
        from_date = (datetime.now() - timedelta(days=days)).date().isoformat()
        with shadow._conn() as c:
            rows = c.execute(
                "SELECT created_at, campaign_name, action FROM tiktok_log "
                "WHERE log_date >= ? ORDER BY created_at",
                (from_date,),
            ).fetchall()
        for created_at, name, action in rows:
            if name and created_at:
                cur = by_name.get(name)
                if cur is None or created_at > cur["ts"]:
                    by_name[name] = {"ts": created_at, "text": action or "Điều chỉnh"}
    except Exception as e:
        logger.warning(f"adjust_history.latest (tiktok_log) lỗi: {e}")

    return {"by_id": by_id, "by_name": by_name}
