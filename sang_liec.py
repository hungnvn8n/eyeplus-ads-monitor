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
        out.append({"key": "sdt", "label": "Tỉ lệ SĐT xin được",
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
    out.append({"key": "convert", "label": "Tỉ lệ chuyển đổi (mess→đơn)",
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
    out.append({"key": "cost_msg", "label": "Giá mess (FB)",
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
    out.append({"key": "cost", "label": "Kiểm soát chi phí",
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


def vung_metrics(day: date) -> list[dict]:
    """Chỉ số quảng cáo theo vùng cho ngày `day`.

    Doanh thu vùng: từ daily_rollup.retail_by_store (đo được).
    Chi/mess/đơn/ROAS: từ fb_ads_daily, lọc vùng bằng tên chiến dịch — CHỈ Facebook.
    Google/TikTok kho không tách vùng nên KHÔNG gộp vào đây; nói rõ ở ghi chú để
    không nhìn số này rồi tưởng còn dư ngưỡng (%chi đủ 3 kênh cao hơn ~1,2 lần).

    ROAS và giá tin cố ý tính trên tiền THÔ để khớp Trình quản lý QC Facebook;
    riêng cột chi và %chi/DT dùng tiền thực trả (đã VAT + phí ngân hàng).
    """
    rows = []
    with inbox_db._conn() as conn:
        cur = conn.cursor()
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
            "roas": round(_div(float(dt_ads), float(sp)), 2) if sp else None,
            "roas_status": _status(_div(float(dt_ads), float(sp)), 2.0, 1.5, higher_better=True) if sp else "none",
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
