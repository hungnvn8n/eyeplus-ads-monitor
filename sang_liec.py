"""Bảng "Sáng Liếc" — dàn chỉ số vận hành + 4 nét tích cực tự động.

Chỉ ĐỌC từ kho Postgres (daily_rollup, pancake_inbox_intents, nhanh_bills) +
shadow.db local (decisions). Không đụng pipeline/backfill. Nét tích cực Content
(TikTok ER) lắp ở app.py vì lấy từ cache TikTok.

Trạng thái mỗi ô: 🟢 tốt · 🟡 cần chú ý · 🔴 xấu, kèm mũi tên ▲▼ so hôm trước.
Pins (lựa chọn nét tích cực CEO ghim) lưu ở shadow.db — state riêng của app.
"""
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import requests

import inbox_db          # tái dùng pool Postgres (ROLLUP_DATABASE_URL)
import shadow            # _db_path() cho shadow.db + decisions

# ── Ngưỡng (để trong code cho dễ chỉnh; mốc gần nhất, tinh chỉnh theo vùng sau) ──
TH = {
    "sdt_pct":     {"good": 12.0, "warn": 8.0, "higher_better": True},  # % SĐT/hội thoại (🟢≥12 · 🟡8-12 · 🔴<8)
    "convert_pct": {"good": 8.0, "warn": 4.0, "higher_better": True},   # đơn/mess
    "cost_msg":    {"good": 60000, "warn": 90000, "higher_better": False},  # đ/mess (FB, chuẩn vùng ~50-90K)
    "ads_pct":     {"good": 13.5, "warn": 14.5, "higher_better": False},    # %ads/DT
}
# Cho phép dấu cách/chấm/gạch xen giữa chữ số (khách hay gõ "0908.208.365"
# hoặc "0962 051 895") — khớp với inbox_db.py, verify không mất/không bắt nhầm.
PHONE_RE = r"0[35789][ .\-]?[0-9]([ .\-]?[0-9]){7}"

# ── Ngưỡng CHẶN SÀN tỉ lệ SĐT theo page (dưới sàn → ô đỏ "DƯỚI SÀN") ────────────
# Tỉ lệ = SĐT mới / KH mới, LẤY THẲNG từ Pancake statistics/pages để KHỚP đúng
# bảng "Thống kê chi tiết" của Pancake (uniq_phone_number_count / new_customer_count).
# KHÔNG regex text tin nhắn (regex sót SĐT lấy qua hồ sơ/nút gọi + gộp cả khách cũ).
PAGE_SDT = [
    {"page_id": "821332004654252",  "token_env": "PANCAKE_TOKEN_CHINH",
     "label": "SĐT · Kính mắt",      "floor": 10.0},
    {"page_id": "1416611528598331", "token_env": "PANCAKE_TOKEN_HER",
     "label": "SĐT · Mắt Kính (Nữ)", "floor": 6.0},
]
_VN_TZ = timezone(timedelta(hours=7))
_PANCAKE_CACHE = {}   # (page_id, day_iso) -> (ts, (new_customer, uniq_phone))
_PANCAKE_TTL = 600    # 10 phút; ngày cũ số ổn định, hôm nay refresh sau 10'


def _pancake_sdt(page_id, token, day):
    """Trả (new_customer, uniq_phone) cho 1 ngày (giờ VN) từ Pancake statistics/pages.
    Khớp cột 'SĐT mới / KH mới' Pancake. Trả (None, None) nếu lỗi/thiếu token."""
    if not token:
        return (None, None)
    key = (page_id, day.isoformat())
    hit = _PANCAKE_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _PANCAKE_TTL:
        return hit[1]
    since = int(datetime(day.year, day.month, day.day, tzinfo=_VN_TZ).timestamp())
    until = since + 86400
    try:
        r = requests.get(
            f"https://pages.fm/api/public_api/v1/pages/{page_id}/statistics/pages"
            f"?page_access_token={token}&since={since}&until={until}", timeout=12)
        data = r.json().get("data", []) or []
        nc = sum((row.get("new_customer_count") or 0) for row in data)
        ph = sum((row.get("uniq_phone_number_count") or 0) for row in data)
        res = (int(nc), int(ph))
    except Exception:
        res = (None, None)
    _PANCAKE_CACHE[key] = (time.time(), res)
    return res


def _total_spend_by_day(since_d: date, until_d: date) -> dict:
    """Tổng chi phí FB ads theo NGÀY (mọi ad trong fb_ads_daily) → {date_iso: spend}.
    LƯU Ý: chỉ phần có trong kho ads (thiếu vài tài khoản FB → số thực có thể cao hơn).
    Không tách page (dùng cho CP/1 SĐT chung toàn hệ)."""
    out = {}
    try:
        with inbox_db._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, SUM(spend_raw) AS sp
                FROM fb_ads_daily
                WHERE date BETWEEN %s AND %s
                GROUP BY date
            """, (since_d, until_d))
            for d, spend in cur.fetchall():
                out[d.isoformat()] = float(spend or 0)
    except Exception:
        pass
    return out


def _tiktok_sdt_by_day(since_d: date, until_d: date) -> dict:
    """{ngày: (số hội thoại, số hội thoại có SĐT)} của inbox TikTok.

    Facebook lấy "khách mới" từ Pancake; TikTok không có chỉ số đó nên dùng SỐ
    HỘI THOẠI làm mẫu số — gần nhất về ý nghĩa (mỗi hội thoại = một người hỏi).
    Đếm theo HỘI THOẠI để cùng đơn vị với tab Inbox, không đếm theo tin nhắn.
    """
    out = {}
    try:
        with inbox_db._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT msg_ts::date AS d,
                       COUNT(DISTINCT conv_id) AS conv,
                       COUNT(DISTINCT conv_id)
                         FILTER (WHERE message ~ '0[35789][ .\-]?[0-9]([ .\-]?[0-9]){7}') AS ph
                FROM tiktok_inbox_intents
                WHERE msg_ts::date BETWEEN %s AND %s
                GROUP BY 1
            """, (since_d, until_d))
            for d, conv, ph in cur.fetchall():
                out[d.isoformat()] = (int(conv or 0), int(ph or 0))
    except Exception:
        pass
    return out


def _tiktok_spend_by_day(since_d: date, until_d: date) -> dict:
    """{ngày: chi TikTok} lấy từ kho tổng hợp ngày (số đã gồm VAT)."""
    out = {}
    try:
        with inbox_db._conn() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT date, tiktok_ads_spend FROM daily_rollup
                           WHERE date BETWEEN %s AND %s""",
                        (since_d.isoformat(), until_d.isoformat()))
            for d, sp in cur.fetchall():
                out[str(d)] = float(sp or 0)
    except Exception:
        pass
    return out


def sdt_series(since_d: date, until_d: date, granularity: str = "day") -> dict:
    """Chuỗi thời gian tỉ lệ + số lượng SĐT mới/KH mới theo page cho biểu đồ.
    Lấy 1 lần/page cho cả khoảng từ Pancake statistics/pages (bucket theo giờ,
    field 'hour'), gom về từng NGÀY hoặc THÁNG (giờ VN). granularity='day'|'month'."""
    if until_d < since_d:
        since_d, until_d = until_d, since_d
    monthly = (granularity == "month")
    if monthly:
        # danh sách các tháng YYYY-MM từ since→until
        days = []
        y, m = since_d.year, since_d.month
        while (y, m) <= (until_d.year, until_d.month):
            days.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m, y = 1, y + 1
        # lấy dữ liệu cả tháng: mở rộng đến hết tháng until
        stat_since = date(since_d.year, since_d.month, 1)
    else:
        days = []
        cur = since_d
        while cur <= until_d:
            days.append(cur.isoformat())
            cur += timedelta(days=1)
        stat_since = since_d
    idx = {d: i for i, d in enumerate(days)}

    def _period(day_iso):
        return day_iso[:7] if monthly else day_iso

    since_ts = int(datetime(stat_since.year, stat_since.month, stat_since.day, tzinfo=_VN_TZ).timestamp())
    until_ts = int(datetime(until_d.year, until_d.month, until_d.day, tzinfo=_VN_TZ).timestamp()) + 86400

    pages_out = {}
    for pg in PAGE_SDT:
        key = "chinh" if pg["page_id"] == "821332004654252" else "her"
        tok = os.environ.get(pg["token_env"], "")
        nc = [0] * len(days)
        ph = [0] * len(days)
        ok = False
        if tok:
            try:
                r = requests.get(
                    f"https://pages.fm/api/public_api/v1/pages/{pg['page_id']}/statistics/pages"
                    f"?page_access_token={tok}&since={since_ts}&until={until_ts}", timeout=20)
                for row in (r.json().get("data", []) or []):
                    i = idx.get(_period(str(row.get("hour", ""))[:10]))
                    if i is None:
                        continue
                    nc[i] += row.get("new_customer_count") or 0
                    ph[i] += row.get("uniq_phone_number_count") or 0
                ok = True
            except Exception:
                ok = False
        rate = [round(100 * ph[i] / nc[i], 1) if nc[i] else None for i in range(len(days))]
        pages_out[key] = {
            "label": pg["label"].replace("SĐT · ", ""),
            "floor": pg["floor"],
            "phone": ph, "newcust": nc, "rate": rate, "ok": ok,
        }

    # CP/1 SĐT CHUNG toàn hệ = tổng chi phí FB ads (mọi ad) ÷ tổng SĐT mới (2 page).
    # KHÔNG tách page (không có nguồn gán ad→page đủ lịch sử) → tính được cho MỌI ngày.
    total_spend_map = _total_spend_by_day(stat_since, until_d)
    total_spend = [0] * len(days)
    for d_iso, sp in total_spend_map.items():
        i = idx.get(_period(d_iso))
        if i is not None:
            total_spend[i] += round(sp)
    # ── TikTok: cùng bộ tiêu chí (SĐT mới · tỉ lệ · CP/1 SĐT) ──────────────
    tt_raw = _tiktok_sdt_by_day(stat_since, until_d)
    tt_nc = [0] * len(days)
    tt_ph = [0] * len(days)
    for d_iso, (conv, ph) in tt_raw.items():
        i = idx.get(_period(d_iso))
        if i is not None:
            tt_nc[i] += conv
            tt_ph[i] += ph
    tt_spend_map = _tiktok_spend_by_day(stat_since, until_d)
    tt_spend = [0] * len(days)
    for d_iso, sp in tt_spend_map.items():
        i = idx.get(_period(d_iso))
        if i is not None:
            tt_spend[i] += round(sp)
    pages_out["tiktok"] = {
        "label": "TikTok",
        "floor": 6,
        "phone": tt_ph, "newcust": tt_nc,
        "rate": [round(100 * tt_ph[i] / tt_nc[i], 1) if tt_nc[i] else None
                 for i in range(len(days))],
        "spend": tt_spend,
        "cost": [round(tt_spend[i] / tt_ph[i]) if tt_ph[i] else None
                 for i in range(len(days))],
        "ok": bool(tt_raw),
    }

    ph_chinh = pages_out.get("chinh", {}).get("phone", [0] * len(days))
    ph_her = pages_out.get("her", {}).get("phone", [0] * len(days))
    total_phone = [ph_chinh[i] + ph_her[i] for i in range(len(days))]
    cost_all = [round(total_spend[i] / total_phone[i]) if total_phone[i] else None
                for i in range(len(days))]

    return {"days": days, "pages": pages_out, "granularity": granularity,
            "total_spend": total_spend, "total_phone": total_phone,
            "cost_per_sdt": cost_all}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _status(val, good, warn, higher_better):
    """Trả 'green' | 'yellow' | 'red'. good/warn là 2 mốc; higher_better đảo chiều."""
    if val is None:
        return "none"
    if higher_better:
        if val >= good:
            return "green"
        if val >= warn:
            return "yellow"
        return "red"
    else:
        if val <= good:
            return "green"
        if val <= warn:
            return "yellow"
        return "red"


def _arrow(cur_v, prev_v):
    """'up' | 'down' | 'flat' — chỉ hướng thay đổi, không phán tốt/xấu."""
    if cur_v is None or prev_v is None:
        return "flat"
    if cur_v > prev_v * 1.005:
        return "up"
    if cur_v < prev_v * 0.995:
        return "down"
    return "flat"


def _div(a, b):
    return (a / b) if (a and b) else 0


def _parse_stores(raw) -> dict:
    """retail_by_store là text JSON array [{name, rev, bills}] → dict {name: rev}."""
    import json
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    if isinstance(data, list):
        return {d.get("name"): (d.get("rev") or 0) for d in data if d.get("name")}
    if isinstance(data, dict):
        return data
    return {}


def _fmt_money(n):
    return f"{round(n):,}".replace(",", ".") + "đ"


def _fmt_pct(n):
    return f"{n:.1f}%"


# ── Nguồn dữ liệu ngày ─────────────────────────────────────────────────────────
def _rollup(cur, day):
    cur.execute("""
        SELECT retail_total, retail_bills, ads_total, ads_msg, pancake_leads,
               COALESCE(google_ads_spend,0), COALESCE(tiktok_ads_spend,0),
               retail_by_store
        FROM daily_rollup WHERE date = %s
    """, (str(day),))
    r = cur.fetchone()
    if not r:
        return None
    keys = ("retail_total", "retail_bills", "ads_total", "ads_msg", "pancake_leads",
            "google_spend", "tiktok_spend", "by_store")
    return dict(zip(keys, r))


# ── Tầng 1: dàn chỉ số ─────────────────────────────────────────────────────────
def metrics(day: date) -> list[dict]:
    """Trả list ô chỉ số cho ngày `day`, so với ngày trước."""
    prev = day - timedelta(days=1)
    out = []
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        r  = _rollup(cur, day)
        rp = _rollup(cur, prev)

    if not r:
        return [{"key": "empty", "label": "Chưa có dữ liệu ngày này", "value": "—",
                 "status": "none", "arrow": "flat", "sub": ""}]

    # 1) Tỉ lệ SĐT = SĐT mới / KH mới (lấy thẳng Pancake, khớp bảng Thống kê chi tiết)
    #    Gom số 2 page cho ô tổng + giữ lại per-page cho các ô chặn sàn bên dưới.
    pg_stats = []  # (pg, nc, ph, nc_prev, ph_prev)
    tot_nc = tot_ph = tot_nc_p = tot_ph_p = 0
    any_ok = False
    for pg in PAGE_SDT:
        tok = os.environ.get(pg["token_env"], "")
        nc, ph   = _pancake_sdt(pg["page_id"], tok, day)
        ncp, php = _pancake_sdt(pg["page_id"], tok, prev)
        pg_stats.append((pg, nc, ph, ncp, php))
        if nc is not None:
            any_ok = True
            tot_nc += nc; tot_ph += ph
        if ncp is not None:
            tot_nc_p += ncp; tot_ph_p += php

    if any_ok:
        sdt   = _div(tot_ph, tot_nc) * 100
        sdt_p = _div(tot_ph_p, tot_nc_p) * 100 if tot_nc_p else None
        out.append({"key": "sdt", "label": "Tỉ lệ SĐT xin được", "raw": sdt,
                    "value": _fmt_pct(sdt), "status": _status(sdt, **TH["sdt_pct"]),
                    "arrow": _arrow(sdt, sdt_p), "bar": min(100, round(sdt / 12 * 100)),
                    "sub": f"{tot_ph}/{tot_nc} KH mới (SĐT mới / KH mới)"})
    else:
        out.append({"key": "sdt", "label": "Tỉ lệ SĐT xin được", "value": "—",
                    "status": "none", "arrow": "flat",
                    "sub": "chưa lấy được số Pancake"})

    # 1b) Tỉ lệ SĐT theo từng page + CHẶN SÀN (dưới ngưỡng → đỏ "DƯỚI SÀN")
    for pg, nc, ph, ncp, php in pg_stats:
        floor_txt = f"sàn {pg['floor']:.0f}%"
        if nc is None:
            out.append({"key": f"sdt_page:{pg['page_id']}", "label": pg["label"],
                        "value": "—", "status": "none", "arrow": "flat",
                        "sub": f"chưa lấy được số Pancake · {floor_txt}"})
            continue
        pct   = _div(ph, nc) * 100
        pct_p = _div(php, ncp) * 100 if (ncp) else None
        below = nc > 0 and pct < pg["floor"]
        status = "none" if nc == 0 else ("red" if below else "green")
        sub = (f"🔴 DƯỚI SÀN · {ph}/{nc} KH mới · {floor_txt}"
               if below else f"{ph}/{nc} KH mới · {floor_txt}")
        out.append({"key": f"sdt_page:{pg['page_id']}", "label": pg["label"],
                    "value": _fmt_pct(pct) if nc else "—",
                    "status": status, "alert": below,
                    "arrow": _arrow(pct, pct_p),
                    "bar": min(100, round(pct / pg["floor"] * 100)) if nc else None,
                    "sub": sub})

    # 2) Tỉ lệ chuyển đổi (đơn bán lẻ / tổng mess)
    conv   = _div(r["retail_bills"], r["pancake_leads"]) * 100
    conv_p = _div(rp and rp["retail_bills"], rp and rp["pancake_leads"]) * 100 if rp else None
    out.append({"key": "convert", "label": "Tỉ lệ chuyển đổi (mess→đơn)", "raw": conv,
                "value": _fmt_pct(conv), "status": _status(conv, **TH["convert_pct"]),
                "arrow": _arrow(conv, conv_p), "bar": min(100, round(conv / 8 * 100)),
                "sub": f"{r['retail_bills']} đơn / {r['pancake_leads']} mess"})

    # 3) Số mess (QC + tự nhiên)
    mess    = r["pancake_leads"] or 0
    mess_qc = r["ads_msg"] or 0
    mess_tn = max(mess - mess_qc, 0)
    mess_p  = (rp["pancake_leads"] or 0) if rp else None
    out.append({"key": "mess", "label": "Số mess",
                "value": f"{mess:,}".replace(",", "."), "status": "green" if mess else "none",
                "arrow": _arrow(mess, mess_p),
                "sub": f"QC {mess_qc:,} · tự nhiên {mess_tn:,}".replace(",", ".")})

    # 4) Giá mess (FB)
    cpm    = _div(r["ads_total"], r["ads_msg"])
    cpm_p  = _div(rp and rp["ads_total"], rp and rp["ads_msg"]) if rp else None
    out.append({"key": "cost_msg", "label": "Giá mess (FB)", "raw": cpm,
                "value": _fmt_money(cpm) if cpm else "—",
                "status": _status(cpm if cpm else None, **TH["cost_msg"]),
                "arrow": _arrow(cpm, cpm_p),
                "bar": min(100, round(cpm / 90000 * 100)) if cpm else None,
                "sub": f"chi {_fmt_money(r['ads_total'])} / {r['ads_msg']:,} mess".replace(",", ".")})

    # 5) DT trung bình / đơn
    aov   = _div(r["retail_total"], r["retail_bills"])
    aov_p = _div(rp and rp["retail_total"], rp and rp["retail_bills"]) if rp else None
    out.append({"key": "aov", "label": "DT trung bình/đơn",
                "value": _fmt_money(aov) if aov else "—",
                "status": "green" if aov else "none", "arrow": _arrow(aov, aov_p),
                "sub": f"{_fmt_money(r['retail_total'])} / {r['retail_bills']} đơn"})

    # 6) Rủi ro (rule v3.2 — số cam cần xử lý)
    risk = _risk_counts()
    danger = risk["pause"] + risk["reduce"]
    rstatus = "red" if risk["pause"] else ("yellow" if risk["reduce"] else "green")
    out.append({"key": "risk", "label": "Quản trị rủi ro",
                "value": f"{danger} cam cần xử lý", "status": rstatus, "arrow": "flat",
                "sub": f"Tạm dừng {risk['pause']} · Giảm {risk['reduce']} · Giữ/Tăng {risk['keep']}"})

    # 7) Kiểm soát chi phí (%ads/DT + %digital)
    ads_pct = _div(r["ads_total"], r["retail_total"]) * 100
    dig_pct = _div((r["ads_total"] or 0) + r["google_spend"] + r["tiktok_spend"],
                   r["retail_total"]) * 100
    ads_pct_p = _div(rp and rp["ads_total"], rp and rp["retail_total"]) * 100 if rp else None
    out.append({"key": "cost", "label": "Kiểm soát chi phí", "raw": ads_pct,
                "value": f"Ads {_fmt_pct(ads_pct)}", "status": _status(ads_pct, **TH["ads_pct"]),
                "arrow": _arrow(ads_pct, ads_pct_p),
                "bar": min(100, round(ads_pct / 14.5 * 100)) if ads_pct else None,
                "sub": f"Digital {_fmt_pct(dig_pct)} (chuẩn: ads ≤13,5% · digital ≤14,5%)"})

    return out


# ── Rủi ro & nét tích cực Ads: đọc shadow.db (decisions của lần quét gần nhất) ──
# ── Tầng 1b: chỉ số theo vùng ─────────────────────────────────────────────────
# Nhận diện vùng từ tên chiến dịch — GIỮ ĐỒNG BỘ với db_fb_ads._REGION_CASE bên
# app MKT, lệch một chữ là hai nơi ra hai con số khác nhau.
_REGION_SQL = """
  CASE
    WHEN campaign_name ~* '[_ -]hcm[_ -]' OR campaign_name ~* '[_ -]sg[_ -]' OR campaign_name ~* 'q7' THEN 'HCM'
    WHEN campaign_name ~* '[_ -]hn[_ -]' THEN 'HN'
    WHEN campaign_name ~* '[_ -]bn[_ -]' OR campaign_name ~* 'bắc ninh' THEN 'BN'
    WHEN campaign_name ~* '[_ -]hp[_ -]' OR campaign_name ~* 'hải phòng' OR campaign_name ~* 'lạch tray' THEN 'HP'
    ELSE 'other'
  END
"""
BANK_FEE_RATE = 0.011   # phí ngân hàng trên tiền chuyển đi (CEO chốt 10/08/2026)
_VUNG_LABEL = {"HN": "Hà Nội", "HCM": "TP HCM", "BN": "Bắc Ninh", "HP": "Hải Phòng"}
# Tiền tố cửa hàng trong retail_by_store → vùng ("SG-..." là TP HCM)
_STORE_PREFIX = {"HN": "HN", "SG": "HCM", "BN": "BN", "HP": "HP"}


def _tien_thuc_tra(spend_raw: float) -> float:
    """Tiền quảng cáo thực rời khỏi tài khoản: thô → +VAT 10% → +phí ngân hàng 1,1%."""
    from fetcher import AD_VAT_RATE
    return spend_raw * (1 + AD_VAT_RATE) * (1 + BANK_FEE_RATE)


def _smooth_path(pts: list) -> str:
    """Catmull-Rom → Bézier — vẽ đường cong mềm mại thay vì nét gãy khúc nối
    thẳng từng điểm. Toạ độ tĩnh, tính 1 lần ở server, không cần JS."""
    if not pts:
        return ""
    d = f"M{pts[0][0]:.2f},{pts[0][1]:.2f}"
    n = len(pts)
    for i in range(n - 1):
        a = pts[i - 1] if i - 1 >= 0 else pts[i]
        b, c = pts[i], pts[i + 1]
        e = pts[i + 2] if i + 2 < n else c
        c1x, c1y = b[0] + (c[0] - a[0]) / 6, b[1] + (c[1] - a[1]) / 6
        c2x, c2y = c[0] - (e[0] - b[0]) / 6, c[1] - (e[1] - b[1]) / 6
        d += f"C{c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {c[0]:.2f},{c[1]:.2f}"
    return d


def _svg_chart(day_series: list, target_per_day: int) -> dict:
    """Dựng sẵn toạ độ SVG cho biểu đồ doanh thu theo ngày — vẽ ở server bằng
    Python/Jinja thuần, KHÔNG dùng Chart.js/JS gì cả (trình duyệt TV đời cũ có
    thể không chạy được thư viện JS nặng, còn <svg> tĩnh thì trình duyệt nào
    cũng vẽ được, kể cả tắt JavaScript hoàn toàn).

    day_series: [{"ngay": "17/08", "dt": 111519000}, ...] theo thứ tự ngày tăng dần.
    Trả {points, area, target_y, labels, max_txt} — Jinja chỉ việc in ra, không
    tính toán gì thêm.
    """
    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 1000, 300, 10, 10, 14, 20
    n = len(day_series)
    if n == 0:
        return {"path_d": "", "area_d": "", "target_y": None, "labels": [], "grid": [],
                "missed": [], "last_pt": None, "max_txt": "0đ"}
    vals = [d["dt"] for d in day_series]
    max_val = max(vals + [target_per_day or 0, 1])
    chart_w, chart_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def _xy(i, v):
        x = PAD_L + (i / (n - 1) if n > 1 else 0) * chart_w
        y = PAD_T + (1 - v / max_val) * chart_h
        return x, y

    pts = [_xy(i, d["dt"]) for i, d in enumerate(day_series)]
    path_d = _smooth_path(pts)  # nét mềm, không còn gãy khúc thẳng từng điểm
    area_d = (path_d + f" L{pts[-1][0]:.1f},{H-PAD_B:.1f} L{pts[0][0]:.1f},{H-PAD_B:.1f} Z") if path_d else ""
    target_y = None
    if target_per_day:
        _, ty = _xy(0, target_per_day)
        target_y = round(ty, 1)
    # Nhãn trục ngày — chỉ ngày LẺ (1, 3, 5, 7...) như bảng mẫu, không chia
    # thưa theo tổng số điểm nữa (trước bị lệch nhịp khi tháng đổi độ dài).
    labels = [{"x": round(pts[i][0], 1), "txt": d["ngay"].split("/")[0]}
              for i, d in enumerate(day_series) if int(d["ngay"].split("/")[0]) % 2 == 1]
    last_day_txt = day_series[-1]["ngay"].split("/")[0]
    if not labels or labels[-1]["txt"] != last_day_txt:
        labels.append({"x": round(pts[-1][0], 1), "txt": last_day_txt})
    # Trục Y — 4 vạch mốc (0 → max, chia đều), giống bảng mẫu tham khảo
    # (0 / 100 tr / 200 tr .../ trần) thay vì biểu đồ trơn không có thang đo.
    grid = []
    for k in range(4):
        v = max_val * k / 3
        _, gy = _xy(0, v)
        txt = "0" if k == 0 else (f"{round(v/1_000_000)} tr" if v >= 1_000_000 else f"{round(v/1000)} k")
        grid.append({"y": round(gy, 1), "txt": txt})
    # Ngày KHÔNG đạt mục tiêu → chấm rỗng nhạt màu trên đường (giống bảng mẫu
    # tham khảo) để phân biệt trực quan với ngày đạt, không cần hover chuột.
    missed = ([{"x": round(x, 1), "y": round(y, 1)} for (x, y), d in zip(pts, day_series) if d["dt"] < target_per_day]
              if target_per_day else [])
    last_pt = {"x": round(pts[-1][0], 1), "y": round(pts[-1][1], 1)}
    return {"path_d": path_d, "area_d": area_d, "target_y": target_y,
            "labels": labels, "grid": grid, "missed": missed, "last_pt": last_pt,
            "max_txt": _fmt_money(max_val)}


def _svg_spark(vals: list) -> str:
    """Đường sparkline nhỏ (không trục, không nhãn) cho 1 ô KPI — viewBox cố
    định 0 0 240 56, Jinja chỉ in polyline, không tính toán gì thêm."""
    n = len(vals)
    if n < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = [(i * 240 / (n - 1), 6 + (1 - (v - lo) / rng) * 44) for i, v in enumerate(vals)]
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


_VUNG_COLOR = {"HN": "#C6321A", "HCM": "#12305C", "HP": "#8A6A34", "BN": "#16A34A"}


def _svg_donut(vung: list) -> dict:
    """Toạ độ sẵn cho biểu đồ tròn (donut) doanh thu theo vùng — kỹ thuật
    stroke-dasharray trên <circle>, KHÔNG cần JS, Jinja in thẳng ra <svg>."""
    total = sum(r["dt"] for r in vung) or 1
    C = 2 * 3.14159265 * 72   # chu vi đường tròn bán kính 72 (khớp donut trong template)
    off = 0.0
    arcs = []
    for r in vung:
        length = max((r["dt"] / total) * C - 3, 2 if r["dt"] > 0 else 0)
        arcs.append({"vung": r["vung"], "label": r["label"], "color": _VUNG_COLOR.get(r["vung"], "#999"),
                    "dasharray": f"{length:.2f} {C - length:.2f}", "dashoffset": round(-off, 2),
                    "dt": r["dt"], "dt_txt": r["dt_txt"],
                    "pct_of_total": round(r["dt"] / total * 100, 1) if total else 0})
        off += (r["dt"] / total) * C
    return {"arcs": arcs, "total_txt": _fmt_money(total)}


# ── Bảng TV: mục tiêu doanh thu tháng, sửa qua /tv/settings ────────────────
# Lưu ở Postgres (kho chung) chứ KHÔNG lưu file trên đĩa máy chủ — máy chủ
# Railway chạy app này không có ổ đĩa cố định (không mount volume), mỗi lần
# đẩy code mới (`railway up`) là container dựng lại từ đầu, file trên đĩa
# mất sạch. Bảng riêng mkt_tv_target, không đụng các bảng kho có sẵn.
_TV_TARGET_TABLE_READY = False


def _ensure_tv_target_table() -> None:
    global _TV_TARGET_TABLE_READY
    if _TV_TARGET_TABLE_READY:
        return
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS mkt_tv_target (
            month_key TEXT PRIMARY KEY, amount BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT now())""")
        conn.commit()
    _TV_TARGET_TABLE_READY = True


def get_tv_target(month_key: str, conn=None) -> int:
    """Mục tiêu doanh thu tháng `month_key` (YYYY-MM). 0 nếu chưa đặt.

    Nhận `conn` có sẵn để dùng chung 1 kết nối khi gọi từ tv_kpi() — mở 3
    kết nối riêng cho 3 truy vấn nhỏ từng đo mất ~4,4s/lần, chủ yếu là thời
    gian MỞ kết nối chứ không phải chạy truy vấn (proxy Postgres công khai
    của Railway chậm lúc bắt tay, ~1,5-1,7s mỗi lần mở).
    """
    try:
        if conn is not None:
            _ensure_tv_target_table()
            cur = conn.cursor()
            cur.execute("SELECT amount FROM mkt_tv_target WHERE month_key = %s", (month_key,))
            r = cur.fetchone()
            return int(r[0]) if r else 0
        _ensure_tv_target_table()
        with inbox_db._conn() as c:
            cur = c.cursor()
            cur.execute("SELECT amount FROM mkt_tv_target WHERE month_key = %s", (month_key,))
            r = cur.fetchone()
            return int(r[0]) if r else 0
    except Exception:
        return 0


def set_tv_target(month_key: str, amount: int) -> None:
    _ensure_tv_target_table()
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO mkt_tv_target (month_key, amount, updated_at)
                       VALUES (%s, %s, now())
                       ON CONFLICT (month_key) DO UPDATE SET amount = EXCLUDED.amount,
                       updated_at = now()""", (month_key, int(amount)))
        conn.commit()


def tv_kpi(day: date) -> dict:
    """Gộp mọi số cho màn hình TV: DT tháng vs mục tiêu, %chi ads/DT toàn hệ,
    ROAS, và 4 ô vùng (dùng lại vung_metrics — cùng công thức, không tính lại).
    """
    month_key = day.strftime("%Y-%m")
    month_start = day.replace(day=1)
    days_in_month = (date(day.year + (day.month == 12), day.month % 12 + 1, 1)
                     - month_start).days
    days_elapsed = (day - month_start).days + 1

    dt_hom_nay = 0
    dt_thang = 0
    ads_raw_thang = google_thang = tiktok_thang = 0
    ads_raw_hom_nay = google_hom_nay = tiktok_hom_nay = 0
    # DÙNG CHUNG 1 kết nối cho cả 3 phần (tháng · mục tiêu · vùng) — trước đây
    # mỗi phần tự mở kết nối riêng, đo được ~4,4s/lần gọi TV vì phần lớn là
    # THỜI GIAN MỞ kết nối (proxy Postgres công khai của Railway chậm lúc bắt
    # tay, ~1,5-1,7s/lần), không phải thời gian chạy truy vấn.
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT date, retail_total, COALESCE(ads_total,0),
                              COALESCE(google_ads_spend,0), COALESCE(tiktok_ads_spend,0),
                              COALESCE(tmdt_total,0), COALESCE(tmdt_orders,0)
                       FROM daily_rollup WHERE date BETWEEN %s AND %s ORDER BY date""",
                    (month_start.isoformat(), day.isoformat()))
        rows = cur.fetchall()
        muc_tieu = get_tv_target(month_key, conn=conn)
        vung_full = vung_metrics(day, conn=conn)
    day_series = []
    # TMĐT — CHỈ hiện để tham khảo, KHÔNG gộp vào doanh thu/mục tiêu/%đạt của
    # MKT (chốt trước đó: KPI phòng MKT chỉ tính bán lẻ). Cộng riêng hẳn.
    tmdt_thang = tmdt_hom_nay = 0
    tmdt_don_thang = tmdt_don_hom_nay = 0
    for d, rt, ads, gg, tt, tm, tmdon in rows:
        rt = rt or 0
        dt_thang += rt
        ads_raw_thang += float(ads or 0)
        google_thang += float(gg or 0)
        tiktok_thang += float(tt or 0)
        tmdt_thang += float(tm or 0)
        tmdt_don_thang += int(tmdon or 0)
        d_iso = str(d)  # cột date đôi lúc về str thay vì datetime.date tuỳ driver
        day_series.append({"ngay": f"{d_iso[8:10]}/{d_iso[5:7]}", "dt": round(rt)})
        if str(d) == day.isoformat():
            dt_hom_nay = rt
            ads_raw_hom_nay, google_hom_nay, tiktok_hom_nay = float(ads or 0), float(gg or 0), float(tt or 0)
            tmdt_hom_nay, tmdt_don_hom_nay = float(tm or 0), int(tmdon or 0)

    def _chi_3_kenh(fb_raw, gg_vat, tt_vat):
        # FB kho lưu RAW → cộng VAT+phí; Google/TikTok kho đã có VAT sẵn → chỉ cộng phí.
        return _tien_thuc_tra(fb_raw) + gg_vat * (1 + BANK_FEE_RATE) + tt_vat * (1 + BANK_FEE_RATE)

    chi_hom_nay = _chi_3_kenh(ads_raw_hom_nay, google_hom_nay, tiktok_hom_nay)
    chi_thang = _chi_3_kenh(ads_raw_thang, google_thang, tiktok_thang)
    pct_hom_nay = _div(chi_hom_nay * 100, dt_hom_nay) if dt_hom_nay else None
    pct_thang = _div(chi_thang * 100, dt_thang) if dt_thang else None

    # Tỉ lệ ads theo KÊNH (FB/Google/TikTok) — trong tổng tiền quảng cáo lũy
    # kế tháng, KHÔNG phải %chi/DT (đó là chỉ số khác đã có ở trên).
    _fb_cost_thang = _tien_thuc_tra(ads_raw_thang)
    _gg_cost_thang = google_thang * (1 + BANK_FEE_RATE)
    _tt_cost_thang = tiktok_thang * (1 + BANK_FEE_RATE)
    ads_by_channel = [
        {"ten": "Facebook", "mau": "#1877F2", "chi": _fb_cost_thang,
         "chi_txt": _fmt_money(_fb_cost_thang), "pct": round(_div(_fb_cost_thang * 100, chi_thang), 1)},
        {"ten": "Google", "mau": "#EA4335", "chi": _gg_cost_thang,
         "chi_txt": _fmt_money(_gg_cost_thang), "pct": round(_div(_gg_cost_thang * 100, chi_thang), 1)},
        {"ten": "TikTok", "mau": "#000000", "chi": _tt_cost_thang,
         "chi_txt": _fmt_money(_tt_cost_thang), "pct": round(_div(_tt_cost_thang * 100, chi_thang), 1)},
    ]

    pct_dat = _div(dt_thang * 100, muc_tieu) if muc_tieu else None
    pct_ngay_qua = round(days_elapsed / days_in_month * 100, 1)

    # Mục tiêu HÔM NAY = chia đều mục tiêu tháng cho số ngày trong tháng —
    # cách chia đơn giản nhất khi chưa có kế hoạch riêng từng ngày/tuần.
    muc_tieu_ngay = round(muc_tieu / days_in_month) if muc_tieu else 0
    pct_dat_ngay = _div(dt_hom_nay * 100, muc_tieu_ngay) if muc_tieu_ngay else None

    # 7 chỉ số của trang "Chỉ số tracking" (mkt.kinhmateyeplus.com/app/tracking
    # nhúng thẳng trang này) — nguồn daily_rollup TỔNG + Pancake trực tiếp, cập
    # nhật intraday (5 lần/ngày), KHÔNG bị khoá tới 04:00 sáng như bảng vùng ở
    # trên (bảng vùng dùng fb_ads_daily — chi tiết theo TỪNG quảng cáo, nặng
    # hơn nên chỉ đồng bộ 1 lần/ngày).
    try:
        chi_so = metrics(day)
    except Exception:
        chi_so = []

    vung = vung_full["rows"]
    # ROAS + tổng đơn toàn hệ = gộp lại từ 4 ô vùng (đã tính theo tiền RAW,
    # khớp Trình quản lý QC Facebook — không nhân hệ số ×0,51).
    # Chỉ gộp vùng nào ĐÃ có ROAS (roas is not None) — vùng còn "—" (dữ liệu
    # chưa đủ) mà cứ coi như 0 thì gộp lại sẽ kéo ROAS toàn hệ tụt giả.
    vung_du = [r for r in vung if r["roas"] is not None]
    sp_fb_vung = sum(r["chi_tho"] for r in vung_du)
    dt_ads_vung = sum(r["roas"] * r["chi_tho"] for r in vung_du)
    don_vung = sum(r["don"] for r in vung)
    mess_vung = sum(r["mess"] for r in vung)
    roas_toan_he = round(_div(dt_ads_vung, sp_fb_vung), 2) if sp_fb_vung else None

    chart = _svg_chart(day_series, muc_tieu_ngay)
    vung_ranked = sorted(vung, key=lambda r: -r["dt"])
    # 6 ô cho lưới "Tổng quan" (kiểu Shopee 2x3) — bỏ 2 ô SĐT riêng từng page
    # và ô rủi ro (đã có cảnh báo riêng), giữ đúng 1 hàng chữ mỗi ô cho gọn.
    _by_key = {x["key"]: x for x in chi_so}
    chi_so_tv = [_by_key[k] for k in
                ("sdt", "convert", "mess", "cost_msg", "aov", "cost") if k in _by_key]

    # 3 chỉ số nhỏ cạnh biểu đồ: tổng tháng (đã có), TB/ngày, ngày cao nhất.
    tb_ngay = _div(dt_thang, days_elapsed)
    ngay_cao_nhat = max(day_series, key=lambda d: d["dt"]) if day_series else None
    so_ngay_dat = sum(1 for d in day_series if muc_tieu_ngay and d["dt"] >= muc_tieu_ngay)
    chart_stats = {
        "tb_ngay_txt": _fmt_money(tb_ngay),
        "ngay_cao_nhat_txt": (f"{ngay_cao_nhat['ngay']} · {_fmt_money(ngay_cao_nhat['dt'])}"
                              if ngay_cao_nhat and ngay_cao_nhat["dt"] > 0 else "—"),
        "so_ngay_dat_txt": f"{so_ngay_dat} / {days_elapsed} ngày" if muc_tieu_ngay else "—",
    }

    # Sparkline nhỏ cho ô KPI doanh thu hôm nay — 10 ngày gần nhất
    spark_dt = _svg_spark([d["dt"] for d in day_series[-10:]])

    # Donut doanh thu theo vùng (hôm nay)
    # Phễu chuyển đổi — thanh trước đây bị CHẶN TRẦN ở 100% ngay khi vừa chạm
    # ngưỡng (bar = value/ngưỡng×100, cap 100), nên đạt 12% hay 20% nhìn thanh
    # dài NHƯ NHAU — không phân biệt được, nhìn "vô nghĩa". Nay giãn thang ra
    # 1,4 lần trị đạt được (hoặc ngưỡng, cái nào lớn hơn) và có VẠCH MỐC ngay
    # tại vị trí ngưỡng — thanh dài/ngắn thật sự phản ánh cách xa ngưỡng bao nhiêu.
    def _funnel_item(key, threshold, goal_label, higher_better=True, bad_label="Dưới mục tiêu",
                      over_label="Vượt chuẩn", good_label="Đạt"):
        x = dict(_by_key[key])
        raw = x.get("raw")
        scale = max(abs(raw or 0), threshold) * 1.4 if threshold else None
        x["bar2"] = min(100, round((raw or 0) / scale * 100)) if scale else 0
        x["mark2"] = round(threshold / scale * 100, 1) if scale else None
        x["goal_label"] = goal_label
        if raw is None:
            x["pill_class"], x["pill_txt"] = "", "—"
        elif (raw >= threshold) if higher_better else (raw <= threshold):
            x["pill_class"], x["pill_txt"] = "ok", good_label
        else:
            x["pill_class"], x["pill_txt"] = "bad", (bad_label if higher_better else over_label)
        return x
    funnel = [
        _funnel_item("sdt", 12.0, "mục tiêu 12%"),
        _funnel_item("convert", 8.0, "mục tiêu 8%"),
        _funnel_item("cost_msg", 90000, "trần 90.000đ", higher_better=False),
        _funnel_item("cost", 13.5, "chuẩn ≤13,5%", higher_better=False),
    ]

    # Hiệu quả chi phí theo vùng — badge Trong ngưỡng / Vượt ngưỡng (13,5%),
    # kèm SỐ TIỀN chi phí tuyệt đối (trước chỉ có %, thiếu số tiền thật).
    cost_rows = sorted(
        [{"label": r["label"], "dt_txt": r["dt_txt"], "chi_txt": r["chi_txt"], "pct_txt": r["pct_txt"],
          "ok": (r["pct"] is not None and r["pct"] <= 13.5)} for r in vung],
        key=lambda x: -1 if x["ok"] else 1)

    # Tiến độ tháng — TĨNH, không đếm giờ (bỏ đồng hồ theo yêu cầu, gây mất
    # tập trung). Lấp khoảng trống trống trải trong thẻ bằng đủ số cần biết:
    # còn thiếu bao nhiêu, trung bình mỗi ngày ĐÃ đạt và CẦN đạt, %chi ads/DT.
    ngay_con_lai = max(days_in_month - days_elapsed, 0)
    con_thieu = max(muc_tieu - dt_thang, 0) if muc_tieu else None
    can_moi_ngay = _div(con_thieu, ngay_con_lai) if muc_tieu and ngay_con_lai else None
    du_bao_cuoi_thang = tb_ngay * days_in_month
    thang_pace = {
        "con_thieu_txt": (_fmt_money(con_thieu) if con_thieu else "đã đạt") if muc_tieu else "—",
        "tb_ngay_txt": _fmt_money(tb_ngay),
        "can_moi_ngay_txt": _fmt_money(can_moi_ngay) if can_moi_ngay is not None else "—",
        "ngay_con_lai": ngay_con_lai,
        "du_bao_txt": _fmt_money(du_bao_cuoi_thang),
        "du_bao_pct": round(_div(du_bao_cuoi_thang * 100, muc_tieu), 1) if muc_tieu else None,
        "pct_chi_thang": round(pct_thang, 1) if pct_thang is not None else None,
    }

    # Khối TMĐT — CHỈ để tham khảo, không gộp vào DT/mục tiêu/%đạt của MKT
    # (chốt trước: KPI phòng MKT chỉ tính bán lẻ). Ghi rõ ngay trong khối.
    tmdt = {
        "hom_nay_txt": _fmt_money(tmdt_hom_nay),
        "thang_txt": _fmt_money(tmdt_thang),
        "don_hom_nay": tmdt_don_hom_nay,
        "don_thang": tmdt_don_thang,
        "aov_txt": _fmt_money(_div(tmdt_thang, tmdt_don_thang)) if tmdt_don_thang else "—",
        "ty_trong_hom_nay": round(_div(tmdt_hom_nay * 100, tmdt_hom_nay + dt_hom_nay), 1)
                           if (tmdt_hom_nay + dt_hom_nay) else None,
        "ty_trong_thang": round(_div(tmdt_thang * 100, tmdt_thang + dt_thang), 1)
                          if (tmdt_thang + dt_thang) else None,
    }

    return {
        "ngay": day.isoformat(),
        "thang_nhan": f"Tháng {day.month}/{day.year}",
        "dt_hom_nay": round(dt_hom_nay), "dt_hom_nay_txt": _fmt_money(dt_hom_nay),
        "dt_thang": round(dt_thang), "dt_thang_txt": _fmt_money(dt_thang),
        "muc_tieu": muc_tieu, "muc_tieu_txt": _fmt_money(muc_tieu) if muc_tieu else "chưa đặt",
        "pct_dat": round(pct_dat, 1) if pct_dat is not None else None,
        "pct_ngay_qua": pct_ngay_qua,
        "muc_tieu_ngay": muc_tieu_ngay,
        "muc_tieu_ngay_txt": _fmt_money(muc_tieu_ngay) if muc_tieu_ngay else "chưa đặt",
        "pct_dat_ngay": round(pct_dat_ngay, 1) if pct_dat_ngay is not None else None,
        "chi_so": chi_so,
        "chi_so_tv": chi_so_tv,
        "chi_hom_nay_txt": _fmt_money(chi_hom_nay),
        "chi_thang_txt": _fmt_money(chi_thang),
        "pct_hom_nay": round(pct_hom_nay, 1) if pct_hom_nay is not None else None,
        "pct_thang": round(pct_thang, 1) if pct_thang is not None else None,
        "pct_status": _status(pct_thang, 13.5, 15.0, higher_better=False) if pct_thang else "none",
        "roas_toan_he": roas_toan_he,
        "roas_status": _status(roas_toan_he, 2.0, 1.5, higher_better=True) if roas_toan_he is not None else "none",
        "don_thang": don_vung, "mess_thang": mess_vung,
        "vung": vung,
        "vung_ranked": vung_ranked,
        "chart": chart,
        "chart_stats": chart_stats,
        "spark_dt": spark_dt,
        "funnel": funnel,
        "cost_rows": cost_rows,
        "thang_pace": thang_pace,
        "tmdt": tmdt,
        "ads_by_channel": ads_by_channel,
        # fb_ads_daily (Mess/Đơn/ROAS chi tiết) chỉ đồng bộ 04:00 mỗi sáng — chưa
        # đủ thì vung_metrics tự trả cảnh báo, TV hiện lại nguyên văn thay vì
        # im lặng cho ROAS = 0x (nhìn như "đốt tiền không ra gì" trong khi thực
        # ra là SỐ CHƯA VỀ ĐỦ).
        "canh_bao": vung_full.get("canh_bao", ""),
        "cap_nhat_luc": datetime.now(_VN_TZ).strftime("%H:%M %d/%m"),
    }


def vung_metrics(day: date, conn=None) -> list[dict]:
    """Chỉ số quảng cáo theo vùng cho ngày `day`.

    Doanh thu vùng: từ daily_rollup.retail_by_store (đo được).
    Chi/mess/đơn/ROAS: từ fb_ads_daily, lọc vùng bằng tên chiến dịch — CHỈ Facebook.
    Google/TikTok kho không tách vùng nên KHÔNG gộp vào đây; nói rõ ở ghi chú để
    không nhìn số này rồi tưởng còn dư ngưỡng (%chi đủ 3 kênh cao hơn ~1,2 lần).

    ROAS và giá tin cố ý tính trên tiền THÔ để khớp Trình quản lý QC Facebook;
    riêng cột chi và %chi/DT dùng tiền thực trả (đã VAT + phí ngân hàng).

    Nhận `conn` có sẵn để dùng chung 1 kết nối với nơi gọi (vd tv_kpi) — mở
    kết nối mới tốn ~1,5-1,7s/lần (proxy Postgres công khai chậm lúc bắt tay),
    gọi liên tiếp nhiều hàm mà mỗi hàm tự mở kết nối riêng sẽ cộng dồn rất phí.
    """
    rows = []

    def _query(c):
        cur = c.cursor()
        cur.execute("SELECT retail_by_store, COALESCE(ads_total,0) FROM daily_rollup WHERE date = %s",
                    (str(day),))
        r = cur.fetchone()
        chi_du_kien = float(r[1]) if r else 0.0     # tổng chi FB thật của ngày
        dt_vung = {}
        for ten, rev in _parse_stores(r[0] if r else None).items():
            pre = str(ten).split("-")[0].strip()
            v = _STORE_PREFIX.get(pre)
            if v:
                dt_vung[v] = dt_vung.get(v, 0) + (rev or 0)

        # CHI theo vùng: lấy từ fb_age_gender_daily — bảng này do run_daily_report ghi,
        # chạy 2 TIẾNG/LẦN nên luôn gần thời gian thực. (fb_ads_daily chỉ đồng bộ 04:00
        # mỗi sáng nên tới tận trưa vẫn thiếu số của hôm trước.)
        cur.execute("""SELECT region, COALESCE(SUM(cp),0) FROM fb_age_gender_daily
                       WHERE date = %s GROUP BY region""", (str(day),))
        chi_vung = {v: float(cp) for v, cp in cur.fetchall()}

        # MESS / ĐƠN / ROAS: chỉ fb_ads_daily mới có (theo từng quảng cáo). Bảng này
        # có thể chưa đủ → tính độ đầy đủ để quyết định có hiện mấy cột đó không.
        cur.execute(f"""
            SELECT {_REGION_SQL} AS vung,
                   COALESCE(SUM(spend_raw),0), COALESCE(SUM(messages_conv_7d),0),
                   COALESCE(SUM(purchases),0), COALESCE(SUM(purchase_value),0)
            FROM fb_ads_daily WHERE date = %s GROUP BY 1
        """, (str(day),))
        ads_vung = {v: (sp, ms, dn, rev) for v, sp, ms, dn, rev in cur.fetchall()}
        return chi_du_kien, dt_vung, chi_vung, ads_vung

    if conn is not None:
        chi_du_kien, dt_vung, chi_vung, ads_vung = _query(conn)
    else:
        with inbox_db._conn() as c:
            chi_du_kien, dt_vung, chi_vung, ads_vung = _query(c)

    # Độ đầy đủ của fb_ads_daily — quyết định có hiện Mess/Giá tin/Đơn/ROAS không.
    chi_ads_daily = sum(float(t[0]) for t in ads_vung.values())
    du_chi_tiet = chi_du_kien <= 0 or _div(chi_ads_daily * 100, chi_du_kien) >= 90

    for v in ("HN", "HCM", "BN", "HP"):
        sp, ms, dn, dt_ads = ads_vung.get(v, (0, 0, 0, 0))
        if not du_chi_tiet:
            ms = dn = dt_ads = 0          # thà để trống còn hơn hiện số thiếu
        dt = dt_vung.get(v, 0)
        # Ưu tiên số chi từ fb_age_gender_daily (tươi hơn); chưa có thì dùng tạm
        # fb_ads_daily. HP chỉ có từ 12/08/2026 — trước đó bảng kia bỏ sót vùng này.
        sp_chi = chi_vung.get(v)
        chi = _tien_thuc_tra(float(sp_chi if sp_chi is not None else sp))
        pct = _div(chi * 100, dt)
        rows.append({
            "vung": v, "label": _VUNG_LABEL[v],
            "dt": dt, "dt_txt": _fmt_money(dt),
            "chi": round(chi), "chi_txt": _fmt_money(chi),
            "chi_tho": float(sp_chi if sp_chi is not None else sp),
            "pct": round(pct, 1) if pct else None,
            "pct_txt": _fmt_pct(pct) if pct else "—",
            "pct_status": _status(pct, 13.5, 15.0, higher_better=False) if pct else "none",
            "mess": int(ms),
            "gia_tin": round(_div(float(sp), ms)) if ms else None,
            "gia_tin_txt": _fmt_money(_div(float(sp), ms)) if ms else "—",
            "gia_tin_status": _status(_div(float(sp), ms), 50000, 60000, higher_better=False) if ms else "none",
            "don": int(dn),
            # Trước đây ROAS chỉ xét `sp` (số chi THÔ, không bị ép về 0 khi
            # dữ liệu chưa đủ) nên lúc chưa đủ: dt_ads bị ép về 0 nhưng sp>0
            # → ROAS hiện "0x" — đọc thành "đốt tiền không ra gì" trong khi
            # thực ra chỉ là SỐ CHƯA VỀ ĐỦ. Nay xét thêm `du_chi_tiet` để
            # hiện "—" đúng lúc, không đè lên trường hợp thật sự 0 đơn.
            "roas": round(_div(float(dt_ads), float(sp)), 2) if sp and du_chi_tiet else None,
            "roas_status": _status(_div(float(dt_ads), float(sp)), 2.0, 1.5, higher_better=True) if sp and du_chi_tiet else "none",
        })

    # Cảnh báo dữ liệu chưa đủ. fb_ads_daily chỉ được đồng bộ lúc 04:00 mỗi ngày
    # (sync_recent 7 ngày), nên trong khoảng 00:00–04:00 thì "hôm qua" mới chỉ có
    # ~4 tiếng đầu — hiện ra thành chi phí bé tí, dễ tưởng hôm qua tiêu rất ít.
    # daily_rollup cập nhật 2h/lần nên dùng làm mốc đối chiếu.
    chi_da_co = sum(r["chi_tho"] for r in rows)
    pct_db = _div(chi_da_co * 100, chi_du_kien)
    canh_bao = ""
    if not du_chi_tiet:
        canh_bao = ("Mess · Giá tin · Đơn · ROAS chưa hiện được: bảng chi tiết từng quảng cáo "
                    "chỉ đồng bộ lúc 04:00 mỗi sáng nên ngày này chưa đủ. "
                    "Cột Doanh thu, Chi FB và %Chi/DT vẫn ĐÚNG (nguồn cập nhật 2 tiếng/lần).")
    elif chi_du_kien > 0 and pct_db < 90:
        canh_bao = (f"Chi quảng cáo mới ghi nhận {pct_db:.0f}% "
                    f"({_fmt_money(chi_da_co)} / {_fmt_money(chi_du_kien)}) — số dưới đây chưa đủ.")
    return {"rows": rows, "dong_bo_pct": round(pct_db, 1), "canh_bao": canh_bao}


def vung_daily(date_from: str, date_to: str) -> dict:
    """Chi quảng cáo FB + %chi/doanh thu theo NGÀY, tách theo VÙNG.

    Nguồn: fb_age_gender_daily (chi FB theo vùng, do run_daily_report ghi 2 TIẾNG/LẦN
    nên luôn gần thời gian thực) + daily_rollup.retail_by_store (doanh thu theo vùng).
    KHÔNG gọi Facebook API — nhanh và không tốn hạn mức.

    Chi đã quy về TIỀN THỰC TRẢ (VAT 10% + phí ngân hàng 1,1%) để %chi so đúng
    với ngưỡng 13,5%. CHỈ Facebook — Google/TikTok kho không tách được theo vùng.
    """
    out_dates, chi, dt = [], {}, {}
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT date::text, region, COALESCE(SUM(cp),0)
                       FROM fb_age_gender_daily WHERE date >= %s AND date <= %s
                       GROUP BY date, region""", (date_from, date_to))
        for d, v, cp in cur.fetchall():
            chi.setdefault(d, {})[v] = float(cp or 0)

        # Vá lịch sử: fb_demo_db.save_day bỏ sót Hải Phòng tới 12/08/2026, nên bảng
        # trên không có HP cho ngày cũ → đường HP cụt hẳn nửa biểu đồ. Lấy bù từ
        # fb_ads_daily (lọc vùng bằng tên chiến dịch) cho ĐÚNG những ô còn trống.
        # Chỉ điền chỗ thiếu, không đè số đã có — hai nguồn lệch nhau vài phần trăm.
        cur.execute(f"""SELECT date::text, {_REGION_SQL} AS vung, COALESCE(SUM(spend_raw),0)
                        FROM fb_ads_daily WHERE date >= %s AND date <= %s
                        GROUP BY 1, 2""", (date_from, date_to))
        for d, v, sp in cur.fetchall():
            if v in ("HN", "HCM", "BN", "HP") and v not in chi.get(d, {}):
                chi.setdefault(d, {})[v] = float(sp or 0)

        cur.execute("""SELECT date, retail_by_store FROM daily_rollup
                       WHERE date >= %s AND date <= %s ORDER BY date""",
                    (date_from, date_to))
        for d, by_store in cur.fetchall():
            d = str(d)[:10]
            out_dates.append(d)
            for ten, rev in _parse_stores(by_store).items():
                v = _STORE_PREFIX.get(str(ten).split("-")[0].strip())
                if v:
                    dt.setdefault(d, {})[v] = dt.get(d, {}).get(v, 0) + (rev or 0)

    vung = {}
    for v in ("HN", "HCM", "BN", "HP"):
        chi_ngay, pct_ngay = [], []
        for d in out_dates:
            raw = chi.get(d, {}).get(v)
            if raw is None:
                # Không có bản ghi ≠ chi 0đ. Vẽ 0 sẽ thành đường phẳng đáy, nhìn như
                # vùng đó không tiêu đồng nào. Hải Phòng chỉ có số từ 12/08/2026 —
                # trước đó fb_demo_db.save_day bỏ sót vùng này. Để trống cho đúng.
                chi_ngay.append(None)
                pct_ngay.append(None)
                continue
            c = _tien_thuc_tra(raw)
            r = dt.get(d, {}).get(v, 0)
            chi_ngay.append(round(c))
            pct_ngay.append(round(c / r * 100, 2) if r else None)
        vung[v] = {"label": _VUNG_LABEL[v], "chi": chi_ngay, "pct": pct_ngay}
    return {"dates": out_dates, "vung": vung, "nguong_pct": 13.5}


def _shadow_conn():
    conn = sqlite3.connect(shadow._db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _risk_counts() -> dict:
    try:
        with _shadow_conn() as c:
            row = c.execute("SELECT MAX(snap_date) FROM decisions").fetchone()
            snap = row[0] if row else None
            if not snap:
                return {"pause": 0, "reduce": 0, "keep": 0}
            rows = c.execute(
                "SELECT decision, COUNT(*) n FROM decisions WHERE snap_date=? GROUP BY decision",
                (snap,)).fetchall()
        by = {r["decision"]: r["n"] for r in rows}
        return {
            "pause":  by.get("TẠM DỪNG", 0),
            "reduce": by.get("GIẢM 50%", 0),
            "keep":   by.get("GIỮ", 0) + by.get("TĂNG NS", 0),
        }
    except Exception:
        return {"pause": 0, "reduce": 0, "keep": 0}


def hl_ads(day: date) -> list[dict]:
    """Top cam ROAS FB (lần quét shadow gần nhất, chỉ cam GIỮ/TĂNG có mua)."""
    try:
        with _shadow_conn() as c:
            snap = (c.execute("SELECT MAX(snap_date) FROM decisions").fetchone() or [None])[0]
            if not snap:
                return []
            rows = c.execute("""
                SELECT campaign_name, region, win_roas, win_cpa
                FROM decisions
                WHERE snap_date=? AND win_purchases > 0 AND win_roas > 0
                ORDER BY win_roas DESC LIMIT 3
            """, (snap,)).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        name = r["campaign_name"] or "(không tên)"
        out.append({
            "id": name,
            "title": name[:60],
            "metric": f"ROAS FB {r['win_roas']:.1f}",
            "sub": f"{r['region'] or ''} · CPA {_fmt_money(r['win_cpa']) if r['win_cpa'] else '—'}",
        })
    return out


# ── Nét tích cực Hàng hóa: top mã bán chạy từ nhanh_bills.products ──────────────
def hl_hang_hoa(day: date) -> list[dict]:
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.value->>'code'  AS code,
                   p.value->>'name'  AS name,
                   SUM((p.value->>'quantity')::int)                  AS qty,
                   SUM(COALESCE((p.value->>'money')::bigint,0))      AS money
            FROM nhanh_bills b, jsonb_each(b.products) p
            WHERE b.bill_date = %s
              AND jsonb_typeof(b.products) = 'object'
              AND COALESCE((p.value->>'money')::bigint, 0) > 0
            GROUP BY 1, 2
            ORDER BY money DESC, qty DESC
            LIMIT 3
        """, (day,))
        rows = cur.fetchall()
    out = []
    for code, name, qty, money in rows:
        out.append({
            "id": code or name,
            "title": (name or code or "")[:60],
            "metric": f"bán {qty} chiếc",
            "sub": f"doanh thu {_fmt_money(money)} · {code or ''}",
        })
    return out


# ── Nét tích cực Khách hàng: 3 góc điểm sáng (cơ sở + SĐT theo cam) ─────────────
def hl_khach_hang(day: date) -> list[dict]:
    prev = day - timedelta(days=1)
    out = []
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        r  = _rollup(cur, day)
        rp = _rollup(cur, prev)
        # cơ sở doanh thu cao nhất hôm qua (retail_by_store: text JSON [{name,rev,bills}])
        by  = _parse_stores((r or {}).get("by_store"))
        byp = _parse_stores((rp or {}).get("by_store"))
        if by:
            top = max(by.items(), key=lambda kv: kv[1] or 0)
            out.append({"id": f"store_top:{top[0]}",
                        "title": f"Cơ sở dẫn đầu: {top[0]}",
                        "metric": _fmt_money(top[1]),
                        "sub": "doanh thu cao nhất hôm qua"})
            grow = [(s, (v - byp[s]) / byp[s] * 100, v)
                    for s, v in by.items() if byp.get(s)]
            if grow:
                g = max(grow, key=lambda x: x[1])
                if g[1] > 0:
                    out.append({"id": f"store_grow:{g[0]}",
                                "title": f"Cơ sở tăng tốt: {g[0]}",
                                "metric": f"+{g[1]:.0f}% vs hôm trước",
                                "sub": _fmt_money(g[2])})
        # tỉ lệ SĐT tốt nhất theo chiến dịch
        cur.execute(f"""
            SELECT campaign_name,
                   COUNT(DISTINCT conv_id)                                        AS total,
                   COUNT(DISTINCT conv_id) FILTER (WHERE message ~ '{PHONE_RE}')  AS phone
            FROM pancake_inbox_intents
            WHERE (msg_ts AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = %s
              AND campaign_name IS NOT NULL
            GROUP BY campaign_name
            HAVING COUNT(DISTINCT conv_id) >= 10
            ORDER BY (COUNT(DISTINCT conv_id) FILTER (WHERE message ~ '{PHONE_RE}'))::float
                     / NULLIF(COUNT(DISTINCT conv_id),0) DESC
            LIMIT 1
        """, (day,))
        row = cur.fetchone()
    if row and row[1]:
        pct = row[2] / row[1] * 100
        out.append({"id": f"camp_phone:{row[0]}",
                    "title": f"Chiến dịch xin SĐT tốt: {row[0][:40]}",
                    "metric": f"{pct:.1f}% để lại SĐT",
                    "sub": f"{row[2]}/{row[1]} hội thoại"})
    return out[:3]


# ── Pins (lưu shadow.db) ────────────────────────────────────────────────────────
def ensure_pins_table():
    with _shadow_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS sang_liec_pins (
                pin_date  TEXT NOT NULL,
                area      TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                label     TEXT DEFAULT '',
                pinned_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (pin_date, area)
            )
        """)


def get_pins(day: date) -> dict:
    ensure_pins_table()
    with _shadow_conn() as c:
        rows = c.execute(
            "SELECT area, entity_id, label FROM sang_liec_pins WHERE pin_date=?",
            (day.isoformat(),)).fetchall()
    return {r["area"]: {"entity_id": r["entity_id"], "label": r["label"]} for r in rows}


def set_pin(day: date, area: str, entity_id: str, label: str = ""):
    ensure_pins_table()
    with _shadow_conn() as c:
        c.execute("""
            INSERT INTO sang_liec_pins (pin_date, area, entity_id, label)
            VALUES (?,?,?,?)
            ON CONFLICT(pin_date, area) DO UPDATE SET
                entity_id=excluded.entity_id, label=excluded.label,
                pinned_at=datetime('now')
        """, (day.isoformat(), area, entity_id, label))
