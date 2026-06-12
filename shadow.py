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
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "shadow.db"

# Cửa sổ cộng dồn dùng làm "đời ad" (ad trẻ quyết trong 14 ngày đầu là chính)
SHADOW_LOOKBACK_DAYS = int(os.getenv("SHADOW_LOOKBACK_DAYS", "14"))

# Mốc chi tiêu (đ)
GATE1_SPEND = int(os.getenv("SHADOW_GATE1_SPEND", "200000"))
GATE1_FLAG_DEADLINE = int(os.getenv("SHADOW_FLAG_DEADLINE", "400000"))
GATE2_SPEND = int(os.getenv("SHADOW_GATE2_SPEND", "500000"))

# Ngưỡng giá tin (đ/tin) cho Cổng 1
CPM_CHEAP = int(os.getenv("SHADOW_CPM_CHEAP", "30000"))
CPM_EXPENSIVE = int(os.getenv("SHADOW_CPM_EXPENSIVE", "60000"))

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
    conn = sqlite3.connect(DB_PATH)
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
            PRIMARY KEY (snap_date, ad_id)
        );
        CREATE TABLE IF NOT EXISTS decisions (
            snap_date TEXT, ad_id TEXT,
            campaign_id TEXT, campaign_name TEXT, account TEXT,
            region TEXT, ctype TEXT, gate TEXT,
            spend_cum REAL, messages INTEGER, purchases INTEGER,
            cost_per_msg INTEGER, cpa INTEGER, benchmark INTEGER, roas REAL,
            decision TEXT, reason TEXT,
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
        """)


# ─── Quy tắc v3 ───────────────────────────────────────────────────────────────

def evaluate_v3(spend: float, messages: int, purchases: int,
                region: str, ctype: str, prev_decision: str | None,
                roas: float = 0.0) -> tuple[str, str, str]:
    """Trả (gate, decision, reason).

    decision ∈ {GIỮ, THEO DÕI, ĐÁNH DẤU, GIẢM 50%, TẠM DỪNG, TĂNG NS}
    Thước đo chính theo audit T6/2026: CPA so chuẩn vùng + ROAS (giỏ hàng cao bù CPA).
    """
    if spend < GATE1_SPEND:
        return "chưa đủ", "CHƯA XÉT", f"Chi {spend:,.0f}đ < mốc {GATE1_SPEND:,.0f}đ"

    benchmark = REGION_CPA_BENCHMARK.get(region, REGION_CPA_BENCHMARK["?"])

    # ── Cổng 2: trưởng thành ──
    if spend >= GATE2_SPEND:
        if purchases == 0:
            return "cổng 2", "TẠM DỪNG", f"Chi {spend:,.0f}đ vẫn 0 khách mua"
        cpa = spend / purchases
        # ROAS cao = giỏ hàng cao bù CPA — không xử oan ad lãi lớn
        if cpa <= 0.8 * benchmark or roas >= 5.0:
            return "cổng 2", "TĂNG NS", (
                f"CPA {cpa:,.0f}đ / ROAS {roas:.1f} tốt hơn hẳn chuẩn {region} ({benchmark:,.0f}đ)")
        if cpa <= 1.5 * benchmark or roas >= 3.5:
            return "cổng 2", "GIỮ", (
                f"CPA {cpa:,.0f}đ / ROAS {roas:.1f} quanh chuẩn {region} ({benchmark:,.0f}đ)")
        if prev_decision == "GIẢM 50%":
            return "cổng 2", "TẠM DỪNG", (
                f"CPA {cpa:,.0f}đ kém >1.5× chuẩn {region}, ROAS {roas:.1f} thấp, lần thứ 2 liên tiếp")
        return "cổng 2", "GIẢM 50%", (
            f"CPA {cpa:,.0f}đ kém >1.5× chuẩn {region} ({benchmark:,.0f}đ), ROAS {roas:.1f} thấp")

    # ── Cổng 1: sàng sớm — ưu tiên ĐƠN trước (khách có thể đến từ đường xem/CAPI, không qua tin)
    if purchases >= 1:
        return "cổng 1", "GIỮ", f"Đã có {purchases} khách mua — tín hiệu mạnh nhất, giữ bất kể giá tin"
    if messages == 0:
        return "cổng 1", "TẠM DỪNG", f"Chi {spend:,.0f}đ nhưng 0 tin nhắn"

    cpm = spend / messages
    if cpm > CPM_EXPENSIVE:
        return "cổng 1", "GIẢM 50%", (
            f"Tin đắt {cpm:,.0f}đ + chưa có khách — giảm chờ thêm 1 mốc chi (không tắt vội)")
    if cpm <= CPM_CHEAP:
        if spend >= GATE1_FLAG_DEADLINE:
            if ctype == "video":
                return "cổng 1", "TẠM DỪNG", (
                    f"ĐÁNH DẤU quá hạn: video tin rẻ {cpm:,.0f}đ, chi {spend:,.0f}đ vẫn 0 khách")
            return "cổng 1", "GIẢM 50%", (
                f"ĐÁNH DẤU quá hạn: tin rẻ {cpm:,.0f}đ, chi {spend:,.0f}đ vẫn 0 khách")
        return "cổng 1", "ĐÁNH DẤU", (
            f"Tin rẻ {cpm:,.0f}đ + chưa có khách — phải ra khách trước mốc {GATE1_FLAG_DEADLINE:,.0f}đ"
            + (" (video: áp nghiêm nhất)" if ctype == "video" else ""))
    return "cổng 1", "THEO DÕI", f"Tin {cpm:,.0f}đ vùng giữa, chưa có khách — chờ"


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
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                 a.get("account", ""), region, ctype, status, camp_budget, adset_budget,
                 spend, messages, purchases, roas))

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

            # Quyết định v3 (chỉ ad đang chạy — ad đã tắt thì khỏi khuyến nghị)
            if status == "ACTIVE":
                gate, decision, reason = evaluate_v3(
                    spend, messages, purchases, region, ctype, prev_dec.get(ad_id), roas)
                if decision != "CHƯA XÉT":
                    cpm = int(spend / messages) if messages else 0
                    cpa = int(spend / purchases) if purchases else 0
                    bench = REGION_CPA_BENCHMARK.get(region, REGION_CPA_BENCHMARK["?"])
                    c.execute(
                        "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (today, ad_id, a.get("campaign_id", ""), a.get("campaign_name", ""),
                         a.get("account", ""), region, ctype, gate,
                         spend, messages, purchases, cpm, cpa, bench, roas, decision, reason))
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
    }
