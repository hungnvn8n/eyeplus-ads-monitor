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
PHONE_RE = "0[35789][0-9]{8}"

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
                    "arrow": _arrow(sdt, sdt_p),
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
                    "sub": sub})

    # 2) Tỉ lệ chuyển đổi (đơn bán lẻ / tổng mess)
    conv   = _div(r["retail_bills"], r["pancake_leads"]) * 100
    conv_p = _div(rp and rp["retail_bills"], rp and rp["pancake_leads"]) * 100 if rp else None
    out.append({"key": "convert", "label": "Tỉ lệ chuyển đổi (mess→đơn)",
                "value": _fmt_pct(conv), "status": _status(conv, **TH["convert_pct"]),
                "arrow": _arrow(conv, conv_p),
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
                "sub": f"Digital {_fmt_pct(dig_pct)} (chuẩn: ads ≤13,5% · digital ≤14,5%)"})

    return out


# ── Rủi ro & nét tích cực Ads: đọc shadow.db (decisions của lần quét gần nhất) ──
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
    """Top cam ROAS thực (lần quét shadow gần nhất, chỉ cam GIỮ/TĂNG có mua)."""
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
            "metric": f"ROAS thực {r['win_roas']:.1f}",
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
