"""Nạp tiền Ads — theo dõi số dư nạp quảng cáo Facebook.

Gốc: công cụ chạy máy lẻ của Tùng (Digital) gửi 28/07 qua Lark. Đưa vào app Quản lý Ads
với 3 thay đổi:
  1. Lưu ở Postgres (chung kho `ROLLUP_DATABASE_URL`) thay vì file JSON trên máy
     → nhiều người xem cùng một số, bền qua mỗi lần deploy.
  2. Không nhập token thủ công: dùng luôn 6 tài khoản quảng cáo + token đã cấu
     hình sẵn trong biến môi trường (daily_report.AD_ACCOUNTS).
  3. Ghi lại ai nạp, ai bấm đồng bộ.

Cách tính (giữ nguyên của bản gốc):
  - Đọc edge /activities của từng tài khoản, lọc sự kiện `ad_account_billing_charge`
    — đây đúng là mỗi lần Facebook thực sự trừ tiền thẻ/ví, khớp bảng
    "Hoạt động thanh toán" trong Trình quản lý quảng cáo.
  - Mỗi hoá đơn chỉ trừ MỘT lần (khoá theo mã giao dịch), đồng bộ lại không trừ trùng.
  - Lần đồng bộ ĐẦU TIÊN của một tài khoản chỉ ghi mốc, không trừ hoá đơn cũ —
    vì đó là tiền đã trả trước khi bắt đầu theo dõi.
  - Số dư = Tổng đã nạp − Tổng đã chi.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime

import psycopg2
import psycopg2.extras

GRAPH = "https://graph.facebook.com/v21.0"
BILLING_CHARGE_EVENT = "ad_account_billing_charge"


def _conn():
    import os
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    return psycopg2.connect(url, connect_timeout=15)


_da_tao = False


def init_db() -> None:
    global _da_tao
    if _da_tao:
        return
    with _conn() as c, c.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mkt_ads_topup (
            id SERIAL PRIMARY KEY,
            ngay TEXT NOT NULL,
            so_tien BIGINT NOT NULL,
            ghi_chu TEXT DEFAULT '',
            nguoi TEXT DEFAULT '',
            tao_luc TEXT
        );
        CREATE TABLE IF NOT EXISTS mkt_ads_spend (
            id SERIAL PRIMARY KEY,
            txn_id TEXT UNIQUE NOT NULL,
            acc_id TEXT NOT NULL,
            acc_ten TEXT DEFAULT '',
            ngay TEXT,
            so_tien BIGINT DEFAULT 0,
            la_moc BOOLEAN DEFAULT FALSE,   -- hoá đơn cũ, chỉ ghi nhận, không trừ
            ghi_luc TEXT
        );
        CREATE TABLE IF NOT EXISTS mkt_ads_sync (
            acc_id TEXT PRIMARY KEY,
            acc_ten TEXT DEFAULT '',
            dong_bo_luc TEXT,
            nguoi TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_mkt_ads_spend_acc ON mkt_ads_spend(acc_id);
        """)
    _da_tao = True


# ─── Tài khoản quảng cáo (lấy từ cấu hình sẵn có của app) ─────────────────────

def _tai_khoan():
    """6 tài khoản quảng cáo + token đã cấu hình sẵn của app."""
    import os
    from fetcher import AD_ACCOUNTS
    out = []
    for a in AD_ACCOUNTS:
        token = os.environ.get(a["token_env"], "")
        if token:
            out.append({"id": a["account_id"], "name": a["name"], "token": token})
    return out


# ─── Nạp tiền ─────────────────────────────────────────────────────────────────

def them_khoan_nap(ngay: str, so_tien, ghi_chu: str = "", nguoi: str = "") -> dict:
    init_db()
    try:
        so_tien = int(round(float(so_tien)))
    except (TypeError, ValueError):
        raise ValueError("Số tiền không hợp lệ")
    if so_tien == 0:
        raise ValueError("Số tiền phải khác 0")
    with _conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO mkt_ads_topup (ngay, so_tien, ghi_chu, nguoi, tao_luc) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (ngay or datetime.now().date().isoformat(), so_tien, ghi_chu, nguoi,
                     datetime.now().isoformat(timespec="seconds")))
    return trang_thai()


def sua_tong_nap(tong_moi, nguoi: str = "") -> dict:
    """Sửa thẳng con số 'Tổng đã nạp' — ghi một dòng điều chỉnh (có thể âm)
    để tổng khớp đúng số vừa gõ mà vẫn giữ lịch sử."""
    init_db()
    try:
        tong_moi = int(round(float(tong_moi)))
    except (TypeError, ValueError):
        raise ValueError("Số tiền không hợp lệ")
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT COALESCE(SUM(so_tien),0) FROM mkt_ads_topup")
        hien_tai = int(cur.fetchone()[0])
        chenh = tong_moi - hien_tai
        if chenh:
            cur.execute("INSERT INTO mkt_ads_topup (ngay, so_tien, ghi_chu, nguoi, tao_luc) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (datetime.now().date().isoformat(), chenh,
                         "Chỉnh sửa tổng nạp trực tiếp", nguoi,
                         datetime.now().isoformat(timespec="seconds")))
    return trang_thai()


def xoa_khoan_nap(topup_id: int) -> dict:
    init_db()
    with _conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM mkt_ads_topup WHERE id = %s", (topup_id,))
    return trang_thai()


# ─── Đồng bộ hoá đơn đã thanh toán ───────────────────────────────────────────

def _goi_graph(url: str):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": {"message": str(e)}}


def _lay_hoat_dong(acc_id: str, token: str, so_trang: int = 5) -> tuple:
    items, url, trang = [], (
        f"{GRAPH}/{acc_id}/activities?fields=event_type,event_time,extra_data"
        f"&limit=100&access_token={urllib.parse.quote(token)}"), 0
    while url and trang < so_trang:
        d = _goi_graph(url)
        if d.get("error"):
            return items, d["error"].get("message") or "Lỗi không xác định"
        items.extend(d.get("data") or [])
        url = (d.get("paging") or {}).get("next")
        trang += 1
    return items, None


def dong_bo(nguoi: str = "") -> dict:
    """Quét hoá đơn mới của tất cả tài khoản, trừ vào số dư."""
    init_db()
    them, loi, moc = [], [], 0
    now = datetime.now().isoformat(timespec="seconds")

    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT acc_id FROM mkt_ads_sync")
        da_biet = {r[0] for r in cur.fetchall()}

        for acc in _tai_khoan():
            acc_id = str(acc["id"]).replace("act_", "")
            ten = acc.get("name") or acc_id
            items, err = _lay_hoat_dong(f"act_{acc_id}", acc["token"])
            if err:
                loi.append(f"{ten}: {err}")
                continue

            lan_dau = acc_id not in da_biet
            for it in items:
                if it.get("event_type") != BILLING_CHARGE_EVENT:
                    continue
                raw = it.get("extra_data")
                try:
                    extra = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    extra = {}
                txn = str(extra.get("transaction_id") or f'{acc_id}_{it.get("event_time")}')
                try:
                    tien = int(round(abs(float(extra.get("new_value") or 0))))
                except (TypeError, ValueError):
                    tien = 0
                if tien <= 0:
                    continue
                ngay = (it.get("event_time") or "")[:10] or datetime.now().date().isoformat()
                cur.execute(
                    "INSERT INTO mkt_ads_spend (txn_id, acc_id, acc_ten, ngay, so_tien, la_moc, ghi_luc) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (txn_id) DO NOTHING RETURNING id",
                    (txn, acc_id, ten, ngay, 0 if lan_dau else tien, lan_dau, now))
                if cur.fetchone():
                    if lan_dau:
                        moc += 1
                    else:
                        them.append({"acc": ten, "ngay": ngay, "so_tien": tien})

            cur.execute("INSERT INTO mkt_ads_sync (acc_id, acc_ten, dong_bo_luc, nguoi) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT (acc_id) DO UPDATE "
                        "SET acc_ten=EXCLUDED.acc_ten, dong_bo_luc=EXCLUDED.dong_bo_luc, "
                        "nguoi=EXCLUDED.nguoi",
                        (acc_id, ten, now, nguoi))

    return {"them": them, "moc": moc, "loi": loi, "trang_thai": trang_thai()}


# ─── Trạng thái cho giao diện ─────────────────────────────────────────────────

def trang_thai() -> dict:
    init_db()
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COALESCE(SUM(so_tien),0) AS t FROM mkt_ads_topup")
        tong_nap = int(cur.fetchone()["t"])
        cur.execute("SELECT COALESCE(SUM(so_tien),0) AS t FROM mkt_ads_spend WHERE NOT la_moc")
        tong_chi = int(cur.fetchone()["t"])
        cur.execute("SELECT id, ngay, so_tien, ghi_chu, nguoi FROM mkt_ads_topup "
                    "ORDER BY ngay DESC, id DESC LIMIT 100")
        naps = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT ngay, acc_ten, so_tien FROM mkt_ads_spend "
                    "WHERE NOT la_moc ORDER BY ngay DESC, id DESC LIMIT 100")
        chis = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT acc_id, acc_ten, dong_bo_luc FROM mkt_ads_sync ORDER BY acc_ten")
        tks = [dict(r) for r in cur.fetchall()]
        # chi tiêu 7 ngày gần nhất để ước lượng số ngày còn trụ được
        cur.execute("SELECT COALESCE(SUM(so_tien),0) AS t FROM mkt_ads_spend "
                    "WHERE NOT la_moc AND ngay >= to_char(now() - interval '7 day','YYYY-MM-DD')")
        chi_7ngay = int(cur.fetchone()["t"])

    so_du = tong_nap - tong_chi
    tb_ngay = round(chi_7ngay / 7) if chi_7ngay else 0
    con_ngay = round(so_du / tb_ngay, 1) if tb_ngay > 0 and so_du > 0 else None
    return {
        "tong_nap": tong_nap, "tong_chi": tong_chi, "so_du": so_du,
        "tb_ngay": tb_ngay, "con_ngay": con_ngay,
        "naps": naps, "chis": chis, "tai_khoan": tks,
        "tong_tk": len(_tai_khoan()),
    }
