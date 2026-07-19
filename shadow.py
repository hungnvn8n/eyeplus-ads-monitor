"""Chế độ ĐỐI CHỨNG (shadow mode) — Quy tắc v3 chạy ngầm, CHỈ ghi nhận, không thực thi.

Mục đích: 2 tuần so 3 cột "quy tắc quyết gì — đội ngũ làm gì — kết quả thật"
trước khi áp dụng bán tự động. Xem /doichung trên dashboard.

Bật bằng env SHADOW_MODE=true (mặc định TẮT — chỉ máy admin bật, không công bố).

Quy tắc v3 — "Soi tin rẻ, nương tin đắt, tin vào đơn sớm":
- Đánh giá theo MỐC TIỀN ĐÃ CHI (cộng dồn 14 ngày), không theo ngày tuổi.
- Cổng 1 (chi ≥ 200K): 0 tin → TẠM DỪNG · có ≥1 khách → GIỮ
  · tin đắt >60K chưa khách → GIẢM 50% · tin rẻ ≤30K chưa khách → ĐÁNH DẤU
  (ĐÁNH DẤU mà chi tới 400K vẫn 0 khách: video → TẠM DỪNG, ảnh/khác → GIẢM 50%)
- Cổng 2 (chi ≥ 500K): so chi phí/1 khách với CHUẨN VÙNG
  · ≤0.8× chuẩn → TĂNG NS · ≤1.5× → GIỮ · >1.5× → GIẢM 50% (lặp lại → TẠM DỪNG)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# DÙNG volume /data khi có (Railway) để shadow.db BỀN qua mỗi lần deploy —
# khớp reports.py, nếu không 2 module trỏ 2 file khác nhau và log chỉnh sửa Ads
# (team_actions) + lịch sử đối chứng bị xoá mỗi deploy.
# Resolve LƯỜI (mỗi lần mở kết nối) thay vì hằng số ở import-time: volume /data
# có thể chưa mount xong lúc module này được import khi container vừa khởi động,
# khiến process "kẹt" vĩnh viễn ở đường dẫn /app (dữ liệu vẫn còn nguyên ở
# /data/shadow.db, chỉ là process đang đọc nhầm file rỗng).
def _db_path() -> Path:
    try:
        vol = Path("/data")
        root = vol if vol.exists() and vol.is_dir() else Path(__file__).resolve().parent
    except Exception:
        root = Path(__file__).resolve().parent
    return root / "shadow.db"


DB_PATH = _db_path()  # giữ để code cũ đọc shadow.DB_PATH không vỡ; _conn() tự resolve lại mỗi lần

# Cửa sổ cộng dồn dùng làm "đời ad" (ad trẻ quyết trong 14 ngày đầu là chính)
SHADOW_LOOKBACK_DAYS = int(os.getenv("SHADOW_LOOKBACK_DAYS", "14"))

# Mốc chi tiêu (đ)
GATE1_SPEND = int(os.getenv("SHADOW_GATE1_SPEND", "200000"))
GATE1_FLAG_DEADLINE = int(os.getenv("SHADOW_FLAG_DEADLINE", "400000"))
GATE2_SPEND = int(os.getenv("SHADOW_GATE2_SPEND", "500000"))

# Ngưỡng giá tin (đ/tin) cho Cổng 1
CPM_CHEAP = int(os.getenv("SHADOW_CPM_CHEAP", "30000"))
CPM_EXPENSIVE = int(os.getenv("SHADOW_CPM_EXPENSIVE", "60000"))

# Cổng 2 (ad trưởng thành): đánh giá theo CỬA SỔ TRƯỢT n ngày gần nhất (ngày ĐÃ hoàn chỉnh,
# KHÔNG tính hôm nay), KHÔNG cộng dồn. Lý do: ad lớn có quá khứ ngon kéo CPA đẹp, che hiện tại đang rò.
# v3.2 (2026-06-23): cửa sổ 3 → 7 ngày — đo độ trễ PM→mua cho thấy 87% khách mua trong 7 ngày
# (3 ngày chỉ ~74% → cắt non). Chỉ dùng cửa sổ khi chi gần đây đủ lớn; nếu ad gần dừng → fallback cộng dồn.
RECENT_WINDOW_DAYS = int(os.getenv("SHADOW_RECENT_DAYS", "7"))
RECENT_MIN_SPEND = int(os.getenv("SHADOW_RECENT_MIN_SPEND", "150000"))

# Bậc thang TẮT cho nhánh 0 khách (anh chốt 2026-06-23):
#   đủ tuổi REDUCE (3 ngày) mà 0 khách → GIẢM 50%; đủ tuổi KILL (7 ngày) + đã giảm + vẫn 0 khách → TẮT.
REDUCE_MIN_AGE_DAYS = int(os.getenv("SHADOW_REDUCE_AGE", "3"))
KILL_MIN_AGE_DAYS = int(os.getenv("SHADOW_KILL_AGE", "7"))

# Dùng ROAS CHÍNH THỨC của Facebook (số Ads Manager), KHÔNG nhân hệ số nội bộ nào
# (anh chốt 2026-07-19: một con số duy nhất, nhân sự tự kiểm tra được). Đã bỏ ROAS_REAL_FACTOR.

# Ngưỡng ROAS FB cho Cổng 2: TĂNG ≥ 2,3 (nhóm 25% cao nhất) · ĐẠT ≥ 2,0 (trung vị 49 ngày) · dưới 2,0 → GIẢM.
# Env đổi tên (bỏ giá trị cũ để ngưỡng "ROAS thực" cũ không âm thầm áp lại).
ROAS_SCALE = float(os.getenv("SHADOW_ROAS_SCALE_FB", "2.3"))
ROAS_KEEP = float(os.getenv("SHADOW_ROAS_PASS_FB", "2.0"))

# Mục tiêu chốt cho REVIEW định kỳ (phương án B, 2026-06-14)
TARGET_SPEND_MAX = int(os.getenv("TARGET_SPEND_MAX", "35000000"))
TARGET_CONV_MIN = int(os.getenv("TARGET_CONV_MIN", "700"))
TARGET_GIAMESS_MAX = int(os.getenv("TARGET_GIAMESS_MAX", "55000"))
TARGET_ROAS_MIN = float(os.getenv("TARGET_ROAS_MIN", "2.2"))


def _warehouse_url() -> str:
    url = os.getenv("ROLLUP_DATABASE_URL", "")
    if not url:
        for line in open(ROOT / ".env", encoding="utf-8"):
            if line.startswith("ROLLUP_DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    return url


def fetch_ad_ages(today: "date") -> dict:
    """Trả {ad_id: age_days} — số NGÀY ĐÃ CHI (ngày hoàn chỉnh, không tính hôm nay).

    age_days = (hôm qua − ngày chi đầu trong cửa sổ lookback) + 1.
    Ad chi đầu hôm qua → age 1; chi đầu 7 ngày trước → age 7. Dùng cho bậc thang TẮT.
    """
    import psycopg2
    url = _warehouse_url()
    if not url:
        return {}
    yest = today - timedelta(days=1)
    ages = {}
    try:
        conn = psycopg2.connect(url, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ad_id, MIN(date) FROM fb_ads_daily "
                    "WHERE date >= %s AND date <= %s AND spend_raw > 0 GROUP BY ad_id",
                    ((today - timedelta(days=SHADOW_LOOKBACK_DAYS)).isoformat(), yest.isoformat()))
                for ad_id, first_d in cur.fetchall():
                    if ad_id and first_d:
                        ages[str(ad_id)] = (yest - first_d).days + 1
        finally:
            conn.close()
    except Exception as e:
        print(f"[shadow] ⚠️ fetch_ad_ages: {e}")
    return ages

# Chuẩn chi phí/1 khách theo vùng (đ) — từ audit T6/2026, cập nhật tay mỗi tháng
REGION_CPA_BENCHMARK = {
    "HN": 200_000,
    "HCM": 300_000,
    "HP": 370_000,
    "BN": 310_000,
    "TQ": 200_000,      # toàn quốc — tạm theo HN
    "?": 250_000,        # không rõ vùng — trung bình hệ thống
}


# ─── Phân loại từ tên campaign ────────────────────────────────────────────────

def classify_region(name: str) -> str:
    s = (name or "").upper()
    if re.search(r"(?<![A-Z])(HCM|SG|SAIGON|SÀI)", s):
        return "HCM"
    if re.search(r"(?<![A-Z])(HN|HA NOI|HÀ NỘI)(?![A-Z])", s):
        return "HN"
    if re.search(r"(?<![A-Z])(HP|HAI PHONG|HẢI PHÒNG)(?![A-Z])", s):
        return "HP"
    if re.search(r"(?<![A-Z])(BN|BAC NINH|BẮC NINH)(?![A-Z])", s):
        return "BN"
    if re.search(r"(?<![A-Z])(TQ|TOAN QUOC|TOÀN QUỐC)(?![A-Z])", s):
        return "TQ"
    return "?"


def classify_ctype(full_name: str) -> str:
    """'video' / 'anh' / '?' — đọc từ tên campaign+adset+ad."""
    s = (full_name or "").upper()
    if re.search(r"(?<![A-Z])(VID|VIDEO|REEL)(?![A-Z])", s):
        return "video"
    if re.search(r"(?<![A-Z])(ANH|ẢNH|HINH|HÌNH|IMG)(?![A-Z0-9])", s):
        return "anh"
    return "?"


# ─── DB ───────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            snap_date TEXT, ad_id TEXT,
            campaign_id TEXT, campaign_name TEXT, account TEXT,
            region TEXT, ctype TEXT,
            effective_status TEXT, campaign_daily_budget INTEGER, adset_daily_budget INTEGER,
            spend_cum REAL, messages INTEGER, purchases INTEGER, roas REAL,
            account_id TEXT DEFAULT '', bm TEXT DEFAULT '',
            PRIMARY KEY (snap_date, ad_id)
        );
        CREATE TABLE IF NOT EXISTS decisions (
            snap_date TEXT, ad_id TEXT,
            campaign_id TEXT, campaign_name TEXT, account TEXT,
            region TEXT, ctype TEXT, gate TEXT,
            spend_cum REAL, messages INTEGER, purchases INTEGER,
            cost_per_msg INTEGER, cpa INTEGER, benchmark INTEGER, roas REAL,
            decision TEXT, reason TEXT,
            account_id TEXT DEFAULT '', bm TEXT DEFAULT '',
            eval_window TEXT DEFAULT '', win_spend REAL DEFAULT 0,
            win_purchases INTEGER DEFAULT 0, win_cpa INTEGER DEFAULT 0, win_roas REAL DEFAULT 0,
            PRIMARY KEY (snap_date, ad_id)
        );
        CREATE TABLE IF NOT EXISTS team_actions (
            action_date TEXT, ad_id TEXT,
            campaign_id TEXT, campaign_name TEXT,
            action TEXT, detail TEXT,
            PRIMARY KEY (action_date, ad_id, action)
        );
        CREATE TABLE IF NOT EXISTS scan_log (
            ts TEXT PRIMARY KEY, n_ads INTEGER, n_decisions INTEGER,
            n_team_actions INTEGER, duration_ms INTEGER, note TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_review (
            review_date TEXT PRIMARY KEY, created_at TEXT, payload TEXT
        );
        CREATE TABLE IF NOT EXISTS tiktok_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL,
            person TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            campaign_name TEXT,
            created_at TEXT NOT NULL
        );
        """)
        # Migration nhẹ cho db cũ: thêm cột account_id/bm + backfill theo tên TK
        for tbl in ("snapshots", "decisions"):
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
            for col in ("account_id", "bm"):
                if col not in cols:
                    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT DEFAULT ''")
        # v3.1: cột cửa sổ trượt cho decisions
        dcols = [r[1] for r in c.execute("PRAGMA table_info(decisions)")]
        for col, typ in (("eval_window", "TEXT DEFAULT ''"), ("win_spend", "REAL DEFAULT 0"),
                         ("win_purchases", "INTEGER DEFAULT 0"), ("win_cpa", "INTEGER DEFAULT 0"),
                         ("win_roas", "REAL DEFAULT 0")):
            if col not in dcols:
                c.execute(f"ALTER TABLE decisions ADD COLUMN {col} {typ}")
        try:
            from fetcher import AD_ACCOUNTS
            for acc in AD_ACCOUNTS:
                for tbl in ("snapshots", "decisions"):
                    c.execute(
                        f"UPDATE {tbl} SET account_id = ?, bm = ? "
                        f"WHERE account = ? AND (account_id = '' OR account_id IS NULL)",
                        (acc["account_id"], acc["bm"], acc["name"]))
        except Exception:
            pass


# ─── Quy tắc v3 ───────────────────────────────────────────────────────────────

def evaluate_v3(spend: float, messages: int, purchases: int,
                region: str, ctype: str, prev_decision: str | None,
                roas: float = 0.0, r3: dict | None = None,
                age_days: int = 0) -> tuple[str, str, str, dict]:
    """Trả (gate, decision, reason, win) — win = số liệu cửa sổ đánh giá thực tế.

    decision ∈ {GIỮ, THEO DÕI, ĐÁNH DẤU, GIẢM 50%, TẠM DỪNG, TĂNG NS}
    Thước đo chính theo audit T6/2026: CPA so chuẩn vùng + ROAS FB (giỏ hàng cao bù CPA).

    v4 (2026-07-19):
      • Cổng 2 (ad đã chi > 500K) đánh giá theo CỬA SỔ TRƯỢT 7 ngày (ngày đã hoàn chỉnh) — KHÔNG cộng dồn.
      • ROAS dùng để chấm là ROAS Facebook chính thức (KHÔNG nhân hệ số nội bộ).
      • Nhánh 0 khách theo BẬC THANG: đủ 3 ngày → GIẢM 50%; đủ 7 ngày + đã giảm + vẫn 0 → TẮT
        (age_days = số ngày ad đã chi tính tới ngày hoàn chỉnh gần nhất; ad mới chưa đủ tuổi → THEO DÕI).
    """
    empty_win = {"window": "cộng dồn", "spend": spend, "purchases": purchases,
                 "roas": roas, "cpa": int(spend / purchases) if purchases else 0}

    if spend < GATE1_SPEND:
        return "chưa đủ", "CHƯA XÉT", f"Chi {spend:,.0f}đ < mốc {GATE1_SPEND:,.0f}đ", empty_win

    benchmark = REGION_CPA_BENCHMARK.get(region, REGION_CPA_BENCHMARK["?"])

    # ── Cổng 2: trưởng thành — đánh giá theo cửa sổ trượt 7 ngày (ngày đã hoàn chỉnh) ──
    if spend >= GATE2_SPEND:
        r3 = r3 or {}                       # r3 = cửa sổ 7 ngày (giữ tên cũ cho gọn)
        w_spend = float(r3.get("spend") or 0)
        roas_pixel = float(r3.get("roas") or 0)
        # Dùng cửa sổ 7 ngày NẾU ad còn chi đáng kể gần đây; nếu gần như dừng → cộng dồn.
        if w_spend >= RECENT_MIN_SPEND:
            w_pu = int(r3.get("purchases") or 0)
            wlabel = f"{RECENT_WINDOW_DAYS} ngày gần nhất"
        else:
            w_spend, w_pu, roas_pixel = spend, purchases, roas
            wlabel = "cộng dồn (chi gần đây thấp)"
        w_roas = roas_pixel                        # ROAS Facebook chính thức
        win = {"window": wlabel, "spend": w_spend, "purchases": w_pu,
               "roas": round(w_roas, 2), "roas_pixel": round(roas_pixel, 2),
               "cpa": int(w_spend / w_pu) if w_pu else 0}

        # ── Nhánh 0 khách: BẬC THANG theo tuổi ad ──
        if w_pu == 0:
            if age_days >= KILL_MIN_AGE_DAYS and prev_decision == "GIẢM 50%":
                return ("cổng 2", "TẠM DỪNG",
                        f"Ad đã {age_days} ngày, đã GIẢM 50% mà {wlabel} vẫn 0 khách "
                        f"(chi {w_spend:,.0f}đ) → tắt", win)
            if age_days >= REDUCE_MIN_AGE_DAYS:
                return ("cổng 2", "GIẢM 50%",
                        f"{wlabel} 0 khách (ad {age_days} ngày, chi {w_spend:,.0f}đ) → giảm nửa, "
                        f"chờ tới mốc {KILL_MIN_AGE_DAYS} ngày", win)
            return ("cổng 2", "THEO DÕI",
                    f"Ad mới {age_days} ngày, chưa tới mốc {REDUCE_MIN_AGE_DAYS} ngày — chờ", win)

        cpa = w_spend / w_pu
        # Ngưỡng ROAS FB (chính thức): TĂNG ≥ 2,3 · ĐẠT ≥ 2,0 · dưới 2,0 → GIẢM
        if cpa <= 0.8 * benchmark or w_roas >= ROAS_SCALE:
            return ("cổng 2", "TĂNG NS",
                    f"CPA {wlabel} {cpa:,.0f}đ / ROAS FB {w_roas:.1f} tốt hơn hẳn chuẩn {region} ({benchmark:,.0f}đ)", win)
        if cpa <= 1.5 * benchmark or w_roas >= ROAS_KEEP:
            return ("cổng 2", "GIỮ",
                    f"CPA {wlabel} {cpa:,.0f}đ / ROAS FB {w_roas:.1f} quanh chuẩn {region} ({benchmark:,.0f}đ)", win)
        if prev_decision == "GIẢM 50%":
            return ("cổng 2", "TẠM DỪNG",
                    f"CPA {wlabel} {cpa:,.0f}đ kém >1.5× chuẩn {region}, ROAS FB {w_roas:.1f} < {ROAS_KEEP}, lần thứ 2 liên tiếp", win)
        return ("cổng 2", "GIẢM 50%",
                f"CPA {wlabel} {cpa:,.0f}đ kém >1.5× chuẩn {region} ({benchmark:,.0f}đ), ROAS FB {w_roas:.1f} < {ROAS_KEEP}", win)

    # ── Cổng 1: sàng sớm — ưu tiên ĐƠN trước (khách có thể đến từ đường xem/CAPI, không qua tin)
    if purchases >= 1:
        return "cổng 1", "GIỮ", f"Đã có {purchases} khách mua — tín hiệu mạnh nhất, giữ bất kể giá tin", empty_win
    if messages == 0:
        return "cổng 1", "TẠM DỪNG", f"Chi {spend:,.0f}đ nhưng 0 tin nhắn", empty_win

    cpm = spend / messages
    if cpm > CPM_EXPENSIVE:
        return ("cổng 1", "GIẢM 50%",
            f"Tin đắt {cpm:,.0f}đ + chưa có khách — giảm chờ thêm 1 mốc chi (không tắt vội)", empty_win)
    if cpm <= CPM_CHEAP:
        if spend >= GATE1_FLAG_DEADLINE:
            if ctype == "video":
                return ("cổng 1", "TẠM DỪNG",
                    f"ĐÁNH DẤU quá hạn: video tin rẻ {cpm:,.0f}đ, chi {spend:,.0f}đ vẫn 0 khách", empty_win)
            return ("cổng 1", "GIẢM 50%",
                f"ĐÁNH DẤU quá hạn: tin rẻ {cpm:,.0f}đ, chi {spend:,.0f}đ vẫn 0 khách", empty_win)
        return ("cổng 1", "ĐÁNH DẤU",
            f"Tin rẻ {cpm:,.0f}đ + chưa có khách — phải ra khách trước mốc {GATE1_FLAG_DEADLINE:,.0f}đ"
            + (" (video: áp nghiêm nhất)" if ctype == "video" else ""), empty_win)
    return "cổng 1", "THEO DÕI", f"Tin {cpm:,.0f}đ vùng giữa, chưa có khách — chờ", empty_win


# ─── Scan job ─────────────────────────────────────────────────────────────────

def run_shadow_scan(ads: list) -> dict:
    """Ghi snapshot + bắt hành động đội ngũ + chấm quyết định v3. KHÔNG gọi FB mutate."""
    started = datetime.now()
    init_db()
    today = date.today().isoformat()

    with _conn() as c:
        prev_date_row = c.execute(
            "SELECT MAX(snap_date) m FROM snapshots WHERE snap_date < ?", (today,)).fetchone()
        prev_date = prev_date_row["m"]

        prev_snap = {}
        if prev_date:
            for r in c.execute("SELECT * FROM snapshots WHERE snap_date = ?", (prev_date,)):
                prev_snap[r["ad_id"]] = dict(r)

        prev_dec = {}
        for r in c.execute(
                "SELECT ad_id, decision FROM decisions WHERE snap_date = "
                "(SELECT MAX(snap_date) FROM decisions WHERE snap_date < ?)", (today,)):
            prev_dec[r["ad_id"]] = r["decision"]

        n_dec = 0
        n_act = 0
        for a in ads:
            ad_id = a.get("ad_id")
            if not ad_id:
                continue
            full_name = " ".join([a.get("campaign_name", "") or "",
                                  a.get("adset_name", "") or "",
                                  a.get("ad_name", "") or ""])
            region = classify_region(a.get("campaign_name", ""))
            ctype = classify_ctype(full_name)
            spend = float(a.get("spend_raw") or 0)
            messages = int(a.get("messages") or 0)
            purchases = int(a.get("purchases") or 0)
            roas = float(a.get("roas_raw") or 0)
            status = a.get("effective_status", "UNKNOWN")
            camp_budget = int(a.get("campaign_daily_budget") or 0)
            adset_budget = int(a.get("adset_daily_budget") or 0)

            c.execute(
                "INSERT OR REPLACE INTO snapshots "
                "(snap_date, ad_id, campaign_id, campaign_name, account, region, ctype, "
                " effective_status, campaign_daily_budget, adset_daily_budget, "
                " spend_cum, messages, purchases, roas, account_id, bm) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                 a.get("account", ""), region, ctype, status, camp_budget, adset_budget,
                 spend, messages, purchases, roas,
                 a.get("account_id", ""), a.get("bm", "")))

            # Bắt hành động đội ngũ (so với snapshot gần nhất)
            p = prev_snap.get(ad_id)
            if p:
                if p["effective_status"] == "ACTIVE" and status != "ACTIVE":
                    c.execute("INSERT OR REPLACE INTO team_actions VALUES (?,?,?,?,?,?)",
                              (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                               "TẠM DỪNG", f"{p['effective_status']} → {status}"))
                    n_act += 1
                elif p["effective_status"] != "ACTIVE" and status == "ACTIVE":
                    c.execute("INSERT OR REPLACE INTO team_actions VALUES (?,?,?,?,?,?)",
                              (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                               "BẬT LẠI", f"{p['effective_status']} → ACTIVE"))
                    n_act += 1
                old_b = p["campaign_daily_budget"] or p["adset_daily_budget"]
                new_b = camp_budget or adset_budget
                if old_b and new_b and abs(new_b - old_b) / old_b >= 0.10:
                    pct = round((new_b - old_b) / old_b * 100)
                    c.execute("INSERT OR REPLACE INTO team_actions VALUES (?,?,?,?,?,?)",
                              (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                               "TĂNG NS" if pct > 0 else "GIẢM NS",
                               f"{old_b:,} → {new_b:,} ({pct:+d}%)"))
                    n_act += 1

            # Quyết định v3.1 (chỉ ad đang chạy — ad đã tắt thì khỏi khuyến nghị)
            if status == "ACTIVE":
                r3 = {                       # cửa sổ 7 ngày (ngày đã hoàn chỉnh)
                    "spend": float(a.get("r3_spend") or 0),
                    "messages": int(a.get("r3_messages") or 0),
                    "purchases": int(a.get("r3_purchases") or 0),
                    "roas": float(a.get("r3_roas") or 0),
                }
                gate, decision, reason, win = evaluate_v3(
                    spend, messages, purchases, region, ctype, prev_dec.get(ad_id), roas, r3,
                    age_days=int(a.get("age_days") or 0))
                if decision != "CHƯA XÉT":
                    cpm = int(spend / messages) if messages else 0
                    cpa = int(spend / purchases) if purchases else 0
                    bench = REGION_CPA_BENCHMARK.get(region, REGION_CPA_BENCHMARK["?"])
                    c.execute(
                        "INSERT OR REPLACE INTO decisions "
                        "(snap_date, ad_id, campaign_id, campaign_name, account, region, ctype, gate, "
                        " spend_cum, messages, purchases, cost_per_msg, cpa, benchmark, roas, "
                        " decision, reason, account_id, bm, "
                        " eval_window, win_spend, win_purchases, win_cpa, win_roas) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                         a.get("account", ""), region, ctype, gate,
                         spend, messages, purchases, cpm, cpa, bench, roas, decision, reason,
                         a.get("account_id", ""), a.get("bm", ""),
                         win.get("window", ""), win.get("spend", 0), win.get("purchases", 0),
                         win.get("cpa", 0), win.get("roas", 0)))
                    n_dec += 1

        dur = int((datetime.now() - started).total_seconds() * 1000)
        c.execute("INSERT OR REPLACE INTO scan_log VALUES (?,?,?,?,?,?)",
                  (started.isoformat(timespec="seconds"), len(ads), n_dec, n_act, dur, ""))

    print(f"[shadow] ✅ {len(ads)} ads → {n_dec} quyết định, {n_act} hành động đội ngũ ({dur}ms)")
    return {"ads": len(ads), "decisions": n_dec, "team_actions": n_act, "duration_ms": dur}


def last_scan_ts() -> str | None:
    if not DB_PATH.exists():
        return None
    with _conn() as c:
        r = c.execute("SELECT MAX(ts) m FROM scan_log").fetchone()
        return r["m"] if r else None


# ─── Dữ liệu cho UI /doichung ────────────────────────────────────────────────

def get_dashboard_data() -> dict:
    init_db()
    today = date.today().isoformat()
    with _conn() as c:
        latest_row = c.execute("SELECT MAX(snap_date) m FROM decisions").fetchone()
        latest = latest_row["m"] or today

        decisions = [dict(r) for r in c.execute(
            "SELECT * FROM decisions WHERE snap_date = ? "
            "ORDER BY CASE decision WHEN 'TẠM DỪNG' THEN 0 WHEN 'GIẢM 50%' THEN 1 "
            "WHEN 'ĐÁNH DẤU' THEN 2 WHEN 'TĂNG NS' THEN 3 WHEN 'THEO DÕI' THEN 4 ELSE 5 END, "
            "spend_cum DESC", (latest,))]

        # Ngân sách/ngày từ snapshot mới nhất (CBO ưu tiên, không thì adset)
        snap_latest = c.execute("SELECT MAX(snap_date) m FROM snapshots").fetchone()["m"]
        budget_by_ad = {}
        if snap_latest:
            for r in c.execute(
                "SELECT ad_id, campaign_daily_budget, adset_daily_budget "
                "FROM snapshots WHERE snap_date = ?", (snap_latest,)):
                budget_by_ad[r["ad_id"]] = (r["campaign_daily_budget"] or 0) \
                    or (r["adset_daily_budget"] or 0)
        for d_row in decisions:
            d_row["daily_budget"] = budget_by_ad.get(d_row["ad_id"], 0)

        # Chuỗi khuyến nghị xấu liên tiếp + tiền chi thêm kể từ lần khuyến nghị đầu.
        # Đây là phép so "quy tắc nói — đội ngũ chưa làm" ngay trên từng dòng.
        BAD = ("TẠM DỪNG", "GIẢM 50%")
        hist_by_ad = defaultdict(dict)
        for r in c.execute("SELECT ad_id, snap_date, decision, spend_cum FROM decisions"):
            hist_by_ad[r["ad_id"]][r["snap_date"]] = (r["decision"], r["spend_cum"])
        scan_dates = sorted({dt for ad in hist_by_ad.values() for dt in ad},
                            reverse=True)  # mới nhất trước
        for d_row in decisions:
            streak, first_spend = 0, None
            for dt in scan_dates:
                if dt > latest:
                    continue
                rec = hist_by_ad.get(d_row["ad_id"], {}).get(dt)
                if rec and rec[0] in BAD:
                    streak += 1
                    first_spend = rec[1]
                else:
                    break
            d_row["flag_streak"] = streak
            d_row["extra_spend"] = max(0, d_row["spend_cum"] - first_spend) \
                if (first_spend is not None and streak > 1) else 0

        # Đếm theo loại quyết định (hôm nay)
        counts = {}
        for d in decisions:
            counts[d["decision"]] = counts.get(d["decision"], 0) + 1

        actions = [dict(r) for r in c.execute(
            "SELECT * FROM team_actions ORDER BY action_date DESC LIMIT 200")]

        # Bất đồng tích lũy: quy tắc bảo DỪNG/GIẢM nhưng ad vẫn ACTIVE ở snapshot mới nhất
        disagreements = [dict(r) for r in c.execute("""
            SELECT d.*, s.effective_status
            FROM decisions d
            JOIN snapshots s ON s.ad_id = d.ad_id AND s.snap_date = ?
            WHERE d.snap_date < ?
              AND d.decision IN ('TẠM DỪNG', 'GIẢM 50%')
              AND s.effective_status = 'ACTIVE'
            GROUP BY d.ad_id HAVING d.snap_date = MAX(d.snap_date)
            ORDER BY d.spend_cum DESC LIMIT 100
        """, (latest, latest))]

        scans = [dict(r) for r in c.execute(
            "SELECT * FROM scan_log ORDER BY ts DESC LIMIT 30")]

        n_days = c.execute("SELECT COUNT(DISTINCT snap_date) n FROM snapshots").fetchone()["n"]

    return {
        "latest_date": latest,
        "n_days_running": n_days,
        "counts": counts,
        "decisions": decisions,
        "team_actions": actions,
        "disagreements": disagreements,
        "scans": scans,
        "benchmarks": REGION_CPA_BENCHMARK,
        "config": {
            "gate1": GATE1_SPEND, "gate2": GATE2_SPEND,
            "flag_deadline": GATE1_FLAG_DEADLINE,
            "cpm_cheap": CPM_CHEAP, "cpm_expensive": CPM_EXPENSIVE,
            "lookback_days": SHADOW_LOOKBACK_DAYS,
        },
        "review": get_latest_review(),
    }


# ─── REVIEW định kỳ (1h30 hàng ngày): đánh giá ngày qua + hướng điều chỉnh ────

def compute_daily_review() -> dict | None:
    """Đánh giá ngày HOÀN CHỈNH gần nhất so mục tiêu + hướng điều chỉnh. Lưu vào daily_review."""
    init_db()
    try:
        import psycopg2
    except ImportError:
        print("[review] thiếu psycopg2"); return None
    url = _warehouse_url()
    if not url:
        print("[review] thiếu ROLLUP_DATABASE_URL"); return None

    # Lấy số NGÀY QUA + hôm trước TRỰC TIẾP từ FB API (không phụ thuộc sync kho lúc 04:00).
    # fetcher.messages = messaging_conversation_started_7d = đúng cột messages_conv_7d.
    from datetime import date as _date, timedelta as _td
    yest = (_date.today() - _td(days=1)).isoformat()
    prev = (_date.today() - _td(days=2)).isoformat()

    def _day_totals(dstr):
        try:
            import fetcher
            ads = (fetcher.fetch_all_ads(dstr, dstr) or {}).get("ads") or []
            ads = [a for a in ads if (a.get("spend_raw") or 0) > 0]
            sp = sum(a.get("spend_raw") or 0 for a in ads)
            conv = sum(a.get("messages") or 0 for a in ads)
            pu = sum(a.get("purchases") or 0 for a in ads)
            rev = sum((a.get("roas_raw") or 0) * (a.get("spend_raw") or 0) for a in ads)
            return {"d": dstr, "sp": int(sp), "conv": int(conv), "pu": int(pu),
                    "rev": int(rev), "ncam": len(ads)}
        except Exception as e:
            print(f"[review] fetch {dstr} fail: {e}")
            return None

    cur_d = _day_totals(yest)
    prev_d = _day_totals(prev)
    if not cur_d or cur_d["sp"] == 0:
        # Fallback: kho (ngày hoàn chỉnh gần nhất) nếu FB API lỗi
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT date::text, sum(spend_raw), sum(messages_conv_7d),
                    sum(purchases), sum(purchase_value),
                    count(DISTINCT campaign_id) FILTER (WHERE spend_raw>0)
                    FROM fb_ads_daily WHERE date >= current_date - INTERVAL '9 days'
                    GROUP BY date ORDER BY date DESC""")
                wr = [{"d": d, "sp": int(s or 0), "conv": int(c or 0), "pu": int(p or 0),
                       "rev": int(rv or 0), "ncam": int(n or 0)}
                      for d, s, c, p, rv, n in cur.fetchall()]
        finally:
            conn.close()
        avg7 = sum(r["sp"] for r in wr[:7]) / max(1, len(wr[:7])) if wr else 0
        comp = [r for r in wr if r["sp"] >= 0.5 * avg7] or wr
        cur_d = comp[0] if comp else None
        prev_d = comp[1] if len(comp) > 1 else None
        if not cur_d:
            return None

    def metrics(r):
        return {
            "date": r["d"], "chi": r["sp"], "hoi_thoai": r["conv"], "khach": r["pu"],
            "gia_mess": round(r["sp"] / r["conv"]) if r["conv"] else 0,
            "roas": round(r["rev"] / r["sp"], 2) if r["sp"] else 0,
            "n_cam": r["ncam"],
        }
    m = metrics(cur_d)
    p = metrics(prev_d) if prev_d else None

    # So mục tiêu
    checks = [
        {"ten": "Chi/ngày", "val": m["chi"], "fmt": "đ", "ok": m["chi"] <= TARGET_SPEND_MAX,
         "muc_tieu": f"≤ {TARGET_SPEND_MAX:,}đ"},
        {"ten": "Hội thoại", "val": m["hoi_thoai"], "fmt": "", "ok": m["hoi_thoai"] >= TARGET_CONV_MIN,
         "muc_tieu": f"≥ {TARGET_CONV_MIN}",
         "luuy": "tin quy về 7 ngày — số ngày mới còn tăng tiếp"},
        {"ten": "Giá mess", "val": m["gia_mess"], "fmt": "đ", "ok": 0 < m["gia_mess"] <= TARGET_GIAMESS_MAX,
         "muc_tieu": f"≤ {TARGET_GIAMESS_MAX:,}đ"},
        {"ten": "ROAS", "val": m["roas"], "fmt": "x", "ok": m["roas"] >= TARGET_ROAS_MIN,
         "muc_tieu": f"≥ {TARGET_ROAS_MIN}", "luuy": "đơn còn quy về 7 ngày — ROAS ngày mới có thể thấp giả"},
    ]
    n_ok = sum(1 for c in checks if c["ok"])

    # Hướng điều chỉnh = quyết định v3.1 quét gần nhất
    with _conn() as c:
        latest = (c.execute("SELECT MAX(snap_date) m FROM decisions").fetchone()["m"]) or ""
        dec_counts = {}
        for r in c.execute("SELECT decision, count(*) n FROM decisions WHERE snap_date=? GROUP BY decision", (latest,)):
            dec_counts[r["decision"]] = r["n"]
        # tiền vùng đỏ đang chờ xử
        red = c.execute(
            "SELECT count(*) n, COALESCE(sum(spend_cum),0) s FROM decisions "
            "WHERE snap_date=? AND decision IN ('TẠM DỪNG','GIẢM 50%')", (latest,)).fetchone()

    payload = {
        "today": m, "prev": p, "checks": checks, "n_ok": n_ok,
        "decisions": dec_counts, "decisions_date": latest,
        "red_count": red["n"], "red_spend": int(red["s"]),
        "targets": {"spend_max": TARGET_SPEND_MAX, "conv_min": TARGET_CONV_MIN,
                    "giamess_max": TARGET_GIAMESS_MAX, "roas_min": TARGET_ROAS_MIN},
    }
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO daily_review VALUES (?,?,?)",
                  (m["date"], _now_iso(), json.dumps(payload, ensure_ascii=False)))
    print(f"[review] ✅ ngày {m['date']}: {n_ok}/4 mục tiêu · chi {m['chi']:,} · {m['hoi_thoai']} hội thoại · ROAS {m['roas']}")
    return payload


def get_latest_review() -> dict | None:
    init_db()
    with _conn() as c:
        r = c.execute("SELECT review_date, created_at, payload FROM daily_review "
                      "ORDER BY review_date DESC LIMIT 1").fetchone()
        if not r:
            return None
        out = json.loads(r["payload"])
        out["_review_date"] = r["review_date"]
        out["_created_at"] = r["created_at"]
        return out


def last_review_date() -> str | None:
    init_db()
    with _conn() as c:
        r = c.execute("SELECT MAX(review_date) m FROM daily_review").fetchone()
        return r["m"] if r else None


def _now_iso() -> str:
    from datetime import datetime as _dt
    return _dt.now().isoformat(timespec="seconds")
