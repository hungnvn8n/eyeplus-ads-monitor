"""Sổ công thức MKT — lưu trữ + tự chấm điểm công thức rút ra từ họp tuần.

Vấn đề giải quyết: mỗi tuần team nêu ra "công thức hiệu quả" nhưng không có
kỳ hạn nghiệm thu và không có ngưỡng số để phán quyết → trôi từ tuần này sang
tuần khác với kết luận "cần test thêm".

Cách làm ở đây: mỗi công thức là 1 HỒ SƠ có vòng đời
    Đề xuất → Đang test (có hạn nghiệm thu) → Đạt / Không đạt / Không kết luận
Công thức Đạt vào Thư viện chuẩn, có ngày rà soát lại mỗi quý.

Phán quyết bằng NỀN CÙNG KỲ: nhóm ad của công thức được so với phần ads còn lại
cùng khu vực, cùng khoảng ngày (không so với con số cố định) — công bằng khi thị
trường biến động.

Nguồn số: warehouse Postgres bảng fb_ads_daily (theo ngày × ad).
Lưu trữ: SQLite congthuc.db, cạnh shadow.db (dùng /data khi chạy Railway).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from shadow import classify_region


def _warehouse_url() -> str:
    """URL kho dữ liệu. Tự đọc .env vì shadow._warehouse_url tham chiếu ROOT không có
    trong module đó (chỉ chạy được khi biến môi trường đã nạp sẵn)."""
    url = os.getenv("ROLLUP_DATABASE_URL", "")
    if url:
        return url
    for base in (Path("/data"), Path(__file__).resolve().parent):
        f = base / ".env"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("ROLLUP_DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    return ""

# ─── Tham số phán quyết (đổi được bằng env) ───────────────────────────────────

# Đạt khi chi phí/1 đơn TỐT HƠN NỀN ít nhất ngần này (%)
CPA_BETTER_PCT = float(os.getenv("CT_CPA_BETTER_PCT", "15"))
# … và ROAS không thấp hơn nền quá ngần này (%) — 0 = phải bằng hoặc hơn nền
ROAS_TOLERANCE_PCT = float(os.getenv("CT_ROAS_TOLERANCE_PCT", "0"))

# Chặn mẫu quá nhỏ: dưới ngưỡng này thì KHÔNG kết luận, cho gia hạn 1 lần
MIN_PURCHASES = int(os.getenv("CT_MIN_PURCHASES", "5"))
MIN_SPEND = int(os.getenv("CT_MIN_SPEND", "1000000"))
# Số ngày gia hạn khi chưa đủ dữ liệu (chỉ được 1 lần)
GIA_HAN_NGAY = int(os.getenv("CT_GIA_HAN_NGAY", "7"))

# Tối đa 2 vòng test cho 1 ý tưởng
MAX_VONG = 2
# Thư viện chuẩn rà soát lại mỗi quý
RA_SOAT_NGAY = int(os.getenv("CT_RA_SOAT_NGAY", "90"))

AD_VAT_RATE = 0.10  # khớp fetcher.py — chi phí hiển thị đã VAT (xem chú thích ở đó)

KHU_VUC = ["HN", "HCM", "HP", "BN", "TQ", "ALL"]
LOAI = ["Ads", "Nội dung", "Sản phẩm", "Giá", "Quy trình"]

# Nghiệm thu bằng gì — không phải công thức nào cũng đo được bằng quảng cáo
DO_BANG = {
    "ads":      "Số quảng cáo",       # app tự tính chi phí/đơn so nền
    "tay":      "Số nhập tay",        # đo được nhưng không nằm trong ads
    "xac_nhan": "Xác nhận đã làm",    # chuẩn làm việc — chỉ cần áp dụng, không chứng minh bằng tiền
}

TRANG_THAI = {
    "de_xuat": "Đề xuất",
    "dang_test": "Đang test",
    "dat": "Đạt — Thư viện chuẩn",
    "khong_dat": "Không đạt — Bài học",
    "khong_ket_luan": "Không kết luận — Bài học",
    "het_thoi": "Hết thời",
}


# ─── DB ───────────────────────────────────────────────────────────────────────

def _db_path() -> Path:
    try:
        vol = Path("/data")
        root = vol if vol.exists() and vol.is_dir() else Path(__file__).resolve().parent
    except Exception:
        root = Path(__file__).resolve().parent
    return root / "congthuc.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS cong_thuc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ma TEXT UNIQUE,
            ten TEXT NOT NULL,
            gia_thuyet TEXT DEFAULT '',
            nguoi_pt TEXT DEFAULT '',
            khu_vuc TEXT DEFAULT 'ALL',
            loai TEXT DEFAULT 'Ads',
            do_bang TEXT DEFAULT 'ads',          -- 'ads' (tự đo) | 'tay' (nhập số)
            ngay_bd TEXT,
            han TEXT,                            -- hạn nghiệm thu
            gia_han_den TEXT DEFAULT '',
            da_gia_han INTEGER DEFAULT 0,
            ngan_sach INTEGER DEFAULT 0,
            cpa_better_pct REAL DEFAULT 15,
            trang_thai TEXT DEFAULT 'de_xuat',
            vong INTEGER DEFAULT 1,
            goc_id INTEGER DEFAULT 0,            -- hồ sơ vòng 1 sinh ra hồ sơ này
            ket_luan TEXT DEFAULT '',
            ket_luan_ngay TEXT DEFAULT '',
            so_tay TEXT DEFAULT '',              -- JSON số nhập tay khi do_bang='tay'
            dieu_kien TEXT DEFAULT '',           -- điều kiện áp dụng (khi vào thư viện)
            ngay_ra_soat TEXT DEFAULT '',
            can_xem_lai INTEGER DEFAULT 0,
            viec_phai_lam TEXT DEFAULT '',        -- hành động tiếp theo (chữ tự do)
            han_lam TEXT DEFAULT '',              -- hạn phải xong VIỆC (khác hạn nghiệm thu)
            tuan_nguon TEXT DEFAULT '',          -- VD "T7 · Tuần 3"
            tao_luc TEXT,
            cap_nhat_luc TEXT
        );
        CREATE TABLE IF NOT EXISTS cong_thuc_ad (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ct_id INTEGER NOT NULL,
            kind TEXT NOT NULL,                  -- 'ad' | 'campaign'
            obj_id TEXT NOT NULL,
            name TEXT DEFAULT '',
            UNIQUE (ct_id, kind, obj_id)
        );
        CREATE TABLE IF NOT EXISTS cong_thuc_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ct_id INTEGER NOT NULL,
            ts TEXT,
            hanh_dong TEXT,
            chi_tiet TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_ct_ad ON cong_thuc_ad(ct_id);
        CREATE INDEX IF NOT EXISTS idx_ct_log ON cong_thuc_log(ct_id);
        """)
        # Migration cộng-thêm cho sổ đã có dữ liệu
        cols = [r[1] for r in c.execute("PRAGMA table_info(cong_thuc)")]
        for col in ("viec_phai_lam", "han_lam"):
            if col not in cols:
                c.execute(f"ALTER TABLE cong_thuc ADD COLUMN {col} TEXT DEFAULT ''")


def _log(c: sqlite3.Connection, ct_id: int, hanh_dong: str, chi_tiet: str = "") -> None:
    c.execute("INSERT INTO cong_thuc_log (ct_id, ts, hanh_dong, chi_tiet) VALUES (?,?,?,?)",
              (ct_id, datetime.now().isoformat(timespec="seconds"), hanh_dong, chi_tiet))


def _next_ma(c: sqlite3.Connection) -> str:
    row = c.execute("SELECT ma FROM cong_thuc WHERE ma LIKE 'CT%' ORDER BY id DESC LIMIT 1").fetchone()
    n = 0
    if row and row["ma"]:
        try:
            n = int(str(row["ma"])[2:])
        except ValueError:
            n = 0
    return f"CT{n + 1:02d}"


# ─── CRUD ─────────────────────────────────────────────────────────────────────

def _today() -> date:
    return date.today()


def create(data: dict) -> dict:
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    ngay_bd = (data.get("ngay_bd") or "").strip() or _today().isoformat()
    han = (data.get("han") or "").strip()
    if not han:
        han = (date.fromisoformat(ngay_bd) + timedelta(days=14)).isoformat()
    with _conn() as c:
        ma = _next_ma(c)
        cur = c.execute("""
            INSERT INTO cong_thuc
              (ma, ten, gia_thuyet, nguoi_pt, khu_vuc, loai, do_bang, ngay_bd, han,
               ngan_sach, cpa_better_pct, trang_thai, vong, goc_id, tuan_nguon,
               viec_phai_lam, han_lam, tao_luc, cap_nhat_luc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ma, (data.get("ten") or "").strip(), (data.get("gia_thuyet") or "").strip(),
              (data.get("nguoi_pt") or "").strip(), data.get("khu_vuc") or "ALL",
              data.get("loai") or "Ads", data.get("do_bang") or "ads", ngay_bd, han,
              int(data.get("ngan_sach") or 0), float(data.get("cpa_better_pct") or CPA_BETTER_PCT),
              data.get("trang_thai") or "dang_test", int(data.get("vong") or 1),
              int(data.get("goc_id") or 0), (data.get("tuan_nguon") or "").strip(),
              (data.get("viec_phai_lam") or "").strip(), (data.get("han_lam") or "").strip(),
              now, now))
        ct_id = cur.lastrowid
        _log(c, ct_id, "Mở hồ sơ", f"{ma} · hạn nghiệm thu {han}")
        for lk in (data.get("links") or []):
            _add_link(c, ct_id, lk)
    return get(ct_id)


EDITABLE = ("ten", "gia_thuyet", "nguoi_pt", "khu_vuc", "loai", "do_bang", "ngay_bd",
            "han", "ngan_sach", "cpa_better_pct", "dieu_kien", "tuan_nguon", "trang_thai",
            "viec_phai_lam", "han_lam")


def update(ct_id: int, data: dict) -> dict:
    init_db()
    sets, vals = [], []
    for k in EDITABLE:
        if k in data:
            sets.append(f"{k} = ?")
            vals.append(data[k])
    if "so_tay" in data:
        sets.append("so_tay = ?")
        vals.append(json.dumps(data["so_tay"], ensure_ascii=False))
    if not sets:
        return get(ct_id)
    sets.append("cap_nhat_luc = ?")
    vals.append(datetime.now().isoformat(timespec="seconds"))
    vals.append(ct_id)
    with _conn() as c:
        c.execute(f"UPDATE cong_thuc SET {', '.join(sets)} WHERE id = ?", vals)
        _log(c, ct_id, "Sửa hồ sơ", ", ".join(k for k in data if k in EDITABLE or k == "so_tay"))
    return get(ct_id)


def _add_link(c: sqlite3.Connection, ct_id: int, lk: dict) -> None:
    c.execute("INSERT OR IGNORE INTO cong_thuc_ad (ct_id, kind, obj_id, name) VALUES (?,?,?,?)",
              (ct_id, lk.get("kind") or "ad", str(lk.get("obj_id") or ""), lk.get("name") or ""))


def set_links(ct_id: int, links: list) -> dict:
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM cong_thuc_ad WHERE ct_id = ?", (ct_id,))
        for lk in links:
            if lk.get("obj_id"):
                _add_link(c, ct_id, lk)
        _log(c, ct_id, "Gắn quảng cáo", f"{len(links)} mục")
    return get(ct_id)


def delete(ct_id: int) -> None:
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM cong_thuc WHERE id = ?", (ct_id,))
        c.execute("DELETE FROM cong_thuc_ad WHERE ct_id = ?", (ct_id,))
        c.execute("DELETE FROM cong_thuc_log WHERE ct_id = ?", (ct_id,))


def get(ct_id: int) -> dict:
    init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM cong_thuc WHERE id = ?", (ct_id,)).fetchone()
        if not row:
            return {}
        d = dict(row)
        d["links"] = [dict(r) for r in c.execute(
            "SELECT kind, obj_id, name FROM cong_thuc_ad WHERE ct_id = ?", (ct_id,))]
        d["log"] = [dict(r) for r in c.execute(
            "SELECT ts, hanh_dong, chi_tiet FROM cong_thuc_log WHERE ct_id = ? ORDER BY id DESC LIMIT 30",
            (ct_id,))]
        return d


def list_all() -> list:
    init_db()
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM cong_thuc ORDER BY id DESC")]
        links = {}
        for r in c.execute("SELECT ct_id, kind, obj_id, name FROM cong_thuc_ad"):
            links.setdefault(r["ct_id"], []).append(dict(r))
    for r in rows:
        r["links"] = links.get(r["id"], [])
    return rows


# ─── Đo số từ warehouse ───────────────────────────────────────────────────────

CACHE_TTL = 300  # giây
_CACHE_MAX = 6   # giữ tối đa ngần này khoảng ngày trong bộ nhớ

# Mỗi mục: "tu|den" -> {"ts": lúc tải, "rows": [...]}
# Nhiều mục chứ không phải một, vì mỗi công thức có khoảng ngày riêng — bản cũ chỉ
# nhớ được 1 khoảng nên mở trang có bao nhiêu công thức là bấy nhiêu lần truy kho.
_cache: dict = {}


def _fetch_rows(d_from: str, d_to: str) -> list:
    """Kéo fb_ads_daily trong khoảng ngày. Nhớ 5 phút.

    Nếu đã tải một khoảng RỘNG HƠN và còn hạn thì cắt lại theo ngày ngay trong bộ nhớ,
    không truy kho lần nữa — nhờ vậy cả trang chỉ tốn 1 lần đọc kho.
    """
    now = time.time()
    for k in list(_cache):
        if now - _cache[k]["ts"] >= CACHE_TTL:
            _cache.pop(k, None)
    for k, v in _cache.items():
        f, t = k.split("|")
        if f <= d_from and t >= d_to:
            if f == d_from and t == d_to:
                return v["rows"]
            return [r for r in v["rows"] if d_from <= r["date"] <= d_to]

    import psycopg2
    url = _warehouse_url()
    if not url:
        return []
    rows = []
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, ad_id, campaign_id, campaign_name, adset_name, ad_name,
                       COALESCE(spend_raw,0), COALESCE(messages_conv_7d,0),
                       COALESCE(purchases,0), COALESCE(purchase_value,0)
                FROM fb_ads_daily
                WHERE date >= %s AND date <= %s
            """, (d_from, d_to))
            for r in cur.fetchall():
                rows.append({
                    "date": str(r[0] or ""),
                    "ad_id": str(r[1] or ""), "campaign_id": str(r[2] or ""),
                    "campaign_name": r[3] or "", "adset_name": r[4] or "", "ad_name": r[5] or "",
                    "spend_raw": float(r[6]), "messages": int(r[7]),
                    "purchases": int(r[8]), "revenue": float(r[9]),
                })
    finally:
        conn.close()
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k]["ts"]), None)
    _cache[f"{d_from}|{d_to}"] = {"ts": now, "rows": rows}
    return rows


def _nap_truoc(cts: list) -> None:
    """Tải MỘT lần khoảng ngày bao trọn mọi công thức, để từng công thức chỉ việc cắt lại."""
    tu, den = [], []
    for ct in cts:
        if (ct.get("do_bang") or "ads") != "ads":
            continue
        try:
            f, t = _window(ct)
            tu.append(f)
            den.append(t)
        except Exception:
            continue
    if not tu:
        return
    try:
        _fetch_rows(min(tu), max(den))
    except Exception:
        pass  # để từng công thức tự báo lỗi ở measure()


def _agg(rows: list) -> dict:
    spend_raw = sum(r["spend_raw"] for r in rows)
    spend = spend_raw * (1 + AD_VAT_RATE)
    msgs = sum(r["messages"] for r in rows)
    buys = sum(r["purchases"] for r in rows)
    rev = sum(r["revenue"] for r in rows)
    return {
        "chi": round(spend),
        "tin": msgs,
        "don": buys,
        "doanh_thu": round(rev),
        "gia_tin": round(spend / msgs) if msgs else 0,
        "cpa": round(spend / buys) if buys else 0,
        "roas": round(rev / spend_raw, 2) if spend_raw else 0.0,
        "so_ad": len({r["ad_id"] for r in rows}),
    }


def _window(ct: dict) -> tuple:
    """Cửa sổ đo: ngày bắt đầu → min(hạn (+gia hạn), hôm qua). Loại hôm nay vì chưa đủ ngày."""
    yest = _today() - timedelta(days=1)
    d_from = ct.get("ngay_bd") or yest.isoformat()
    end = ct.get("gia_han_den") or ct.get("han") or yest.isoformat()
    d_to = min(date.fromisoformat(end), yest)
    if d_to < date.fromisoformat(d_from):
        d_to = date.fromisoformat(d_from)
    return d_from, d_to.isoformat()


def _match(ct: dict, r: dict) -> bool:
    """Ad thuộc công thức khi: được tick tay, HOẶC tên có mã công thức."""
    for lk in ct.get("links") or []:
        if lk["kind"] == "ad" and lk["obj_id"] == r["ad_id"]:
            return True
        if lk["kind"] == "campaign" and lk["obj_id"] == r["campaign_id"]:
            return True
    ma = (ct.get("ma") or "").upper()
    if ma:
        blob = f"{r['ad_name']} {r['adset_name']} {r['campaign_name']}".upper()
        if ma in blob:
            return True
    return False


def measure(ct: dict) -> dict:
    """Trả số của công thức + nền cùng kỳ + phán quyết đề nghị."""
    if ct.get("do_bang") == "xac_nhan":
        d_from, d_to = _window(ct)
        return {"ok": True, "do_bang": "xac_nhan", "tu": d_from, "den": d_to,
                "ct": {}, "nen": {}, "du_lieu_du": True,
                "phan_quyet": "", "chenh_cpa": None, "chenh_roas": None,
                "ly_do": "Chuẩn làm việc — chỉ cần xác nhận đã áp dụng hay chưa, "
                         "không chứng minh bằng chi phí/đơn"}

    if ct.get("do_bang") == "tay":
        so = {}
        try:
            so = json.loads(ct.get("so_tay") or "{}")
        except Exception:
            so = {}
        d_from, d_to = _window(ct)
        return {"ok": True, "do_bang": "tay", "tu": d_from, "den": d_to,
                "ct": so, "nen": {}, "du_lieu_du": bool(so),
                "phan_quyet": so.get("phan_quyet") or "", "ly_do": so.get("ghi_chu") or "",
                "chenh_cpa": None, "chenh_roas": None}

    d_from, d_to = _window(ct)
    try:
        rows = _fetch_rows(d_from, d_to)
    except Exception as e:
        return {"ok": False, "error": f"Không đọc được kho dữ liệu: {e}",
                "tu": d_from, "den": d_to}

    vung = (ct.get("khu_vuc") or "ALL").upper()
    mine, base = [], []
    for r in rows:
        if _match(ct, r):
            mine.append(r)
        elif vung in ("ALL", "") or classify_region(r["campaign_name"]) == vung:
            base.append(r)

    ct_agg, nen_agg = _agg(mine), _agg(base)
    du = ct_agg["don"] >= MIN_PURCHASES or ct_agg["chi"] >= MIN_SPEND

    chenh_cpa = chenh_roas = None
    if nen_agg["cpa"] and ct_agg["cpa"]:
        chenh_cpa = round((nen_agg["cpa"] - ct_agg["cpa"]) / nen_agg["cpa"] * 100, 1)
    if nen_agg["roas"] and ct_agg["roas"]:
        chenh_roas = round((ct_agg["roas"] - nen_agg["roas"]) / nen_agg["roas"] * 100, 1)

    nguong = float(ct.get("cpa_better_pct") or CPA_BETTER_PCT)
    if not du:
        pq, ly_do = "chua_du", (
            f"Mới {ct_agg['don']} đơn / {ct_agg['chi']:,.0f}đ — chưa đủ để kết luận "
            f"(cần ≥{MIN_PURCHASES} đơn hoặc ≥{MIN_SPEND:,.0f}đ)")
    elif not ct_agg["don"] or not nen_agg["cpa"]:
        pq, ly_do = "khong_dat", "Không có đơn nào trong kỳ test"
    elif chenh_cpa is not None and chenh_cpa >= nguong and \
            (chenh_roas is None or chenh_roas >= -ROAS_TOLERANCE_PCT):
        pq, ly_do = "dat", (
            f"Chi phí/1 đơn {ct_agg['cpa']:,.0f}đ — tốt hơn nền {chenh_cpa:.1f}% "
            f"(nền {nen_agg['cpa']:,.0f}đ), ROAS {ct_agg['roas']} so nền {nen_agg['roas']}")
    elif chenh_cpa is not None and chenh_cpa <= 0:
        pq, ly_do = "khong_dat", (
            f"Chi phí/1 đơn {ct_agg['cpa']:,.0f}đ — xấu hơn nền {abs(chenh_cpa):.1f}% "
            f"(nền {nen_agg['cpa']:,.0f}đ)")
    else:
        pq, ly_do = "mot_phan", (
            f"Chi phí/1 đơn tốt hơn nền {chenh_cpa:.1f}% — chưa tới ngưỡng {nguong:.0f}%"
            + ("" if chenh_roas is None or chenh_roas >= -ROAS_TOLERANCE_PCT
               else f"; ROAS thấp hơn nền {abs(chenh_roas):.1f}%"))

    return {"ok": True, "do_bang": "ads", "tu": d_from, "den": d_to,
            "ct": ct_agg, "nen": nen_agg, "du_lieu_du": du,
            "phan_quyet": pq, "ly_do": ly_do,
            "chenh_cpa": chenh_cpa, "chenh_roas": chenh_roas,
            "nguong": nguong}


# ─── Vòng đời ─────────────────────────────────────────────────────────────────

def _han_hieu_luc(ct: dict) -> date:
    end = ct.get("gia_han_den") or ct.get("han")
    return date.fromisoformat(end) if end else _today()


def den_han(ct: dict) -> bool:
    return ct.get("trang_thai") == "dang_test" and _han_hieu_luc(ct) < _today()


def sweep() -> dict:
    """Chạy mỗi lần mở trang: gia hạn 1 lần khi thiếu dữ liệu, quá hạn thì tự đóng,
    thư viện chuẩn tới ngày rà soát thì đánh dấu cần xem lại."""
    init_db()
    n_gia_han = n_dong = n_ra_soat = 0
    ds = list_all()
    _nap_truoc(ds)
    for ct in ds:
        if den_han(ct):
            if ct.get("do_bang") != "ads":
                continue  # đo tay / xác nhận đã làm → chờ người kết luận, không tự xử lý
            m = measure(ct)
            if not m.get("ok"):
                continue
            if m.get("du_lieu_du"):
                continue  # chờ người phán quyết ở khối "Đến hạn tuần này"
            if not ct.get("da_gia_han"):
                den = (_today() + timedelta(days=GIA_HAN_NGAY)).isoformat()
                with _conn() as c:
                    c.execute("UPDATE cong_thuc SET gia_han_den=?, da_gia_han=1 WHERE id=?",
                              (den, ct["id"]))
                    _log(c, ct["id"], "Gia hạn tự động",
                         f"Chưa đủ dữ liệu → gia hạn tới {den} (chỉ 1 lần)")
                n_gia_han += 1
            else:
                with _conn() as c:
                    c.execute("""UPDATE cong_thuc SET trang_thai='khong_ket_luan',
                                 ket_luan=?, ket_luan_ngay=? WHERE id=?""",
                              ("Hết hạn gia hạn mà vẫn không đủ dữ liệu — tự đóng. "
                               "Muốn làm lại phải mở hồ sơ mới.",
                               _today().isoformat(), ct["id"]))
                    _log(c, ct["id"], "Tự đóng", "Không kết luận — hết hạn gia hạn")
                n_dong += 1
        elif ct.get("trang_thai") == "dat" and ct.get("ngay_ra_soat") and not ct.get("can_xem_lai"):
            if date.fromisoformat(ct["ngay_ra_soat"]) <= _today():
                with _conn() as c:
                    c.execute("UPDATE cong_thuc SET can_xem_lai=1 WHERE id=?", (ct["id"],))
                    _log(c, ct["id"], "Tới hạn rà soát", "Đánh dấu cần xem lại")
                n_ra_soat += 1
    return {"gia_han": n_gia_han, "tu_dong": n_dong, "ra_soat": n_ra_soat}


def doi_trang_thai(ct_id: int, moi: str, ghi_chu: str = "", so_ngay: int = 14) -> dict:
    """Chuyển hồ sơ sang trạng thái bất kỳ (đi tới hoặc quay lại), luôn ghi nhật ký.

    Mở lại test từ Bài học / Thư viện: đồng hồ chạy lại từ hôm nay, xoá cờ gia hạn cũ
    để hồ sơ được hưởng trọn một chu kỳ mới thay vì bị đóng ngay.
    """
    ct = get(ct_id)
    if not ct:
        return {}
    if moi not in TRANG_THAI:
        raise ValueError(f"Trạng thái không hợp lệ: {moi}")
    cu = ct.get("trang_thai")
    hom_nay = _today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")

    with _conn() as c:
        if moi == "dang_test":
            han = (_today() + timedelta(days=max(1, int(so_ngay or 14)))).isoformat()
            c.execute("""UPDATE cong_thuc SET trang_thai='dang_test', ngay_bd=?, han=?,
                         gia_han_den='', da_gia_han=0, ket_luan='', ket_luan_ngay='',
                         ngay_ra_soat='', can_xem_lai=0, cap_nhat_luc=? WHERE id=?""",
                      (hom_nay, han, now, ct_id))
            them = f"đồng hồ chạy lại {hom_nay} → {han}"
        elif moi == "de_xuat":
            c.execute("""UPDATE cong_thuc SET trang_thai='de_xuat', gia_han_den='', da_gia_han=0,
                         ket_luan='', ket_luan_ngay='', can_xem_lai=0, cap_nhat_luc=? WHERE id=?""",
                      (now, ct_id))
            them = "đưa về chờ, chưa chạy đồng hồ"
        elif moi == "dat":
            ra_soat = (_today() + timedelta(days=RA_SOAT_NGAY)).isoformat()
            c.execute("""UPDATE cong_thuc SET trang_thai='dat', ket_luan=COALESCE(NULLIF(?,''), ket_luan),
                         ket_luan_ngay=?, ngay_ra_soat=?, can_xem_lai=0, cap_nhat_luc=? WHERE id=?""",
                      (ghi_chu, hom_nay, ra_soat, now, ct_id))
            them = f"rà soát lại {ra_soat}"
        else:  # khong_dat | khong_ket_luan | het_thoi
            c.execute("""UPDATE cong_thuc SET trang_thai=?, ket_luan=COALESCE(NULLIF(?,''), ket_luan),
                         ket_luan_ngay=?, can_xem_lai=0, cap_nhat_luc=? WHERE id=?""",
                      (moi, ghi_chu, hom_nay, now, ct_id))
            them = ""
        chi_tiet = f"{TRANG_THAI.get(cu, cu)} → {TRANG_THAI.get(moi, moi)}"
        if ghi_chu:
            chi_tiet += f" · {ghi_chu}"
        if them:
            chi_tiet += f" ({them})"
        _log(c, ct_id, "Chuyển trạng thái", chi_tiet)
    return {"ok": True, "ct": get(ct_id)}


def xuat_toan_bo() -> dict:
    """Sao lưu cả sổ: hồ sơ + quảng cáo đã gắn + nhật ký."""
    init_db()
    with _conn() as c:
        return {
            "phien_ban": 1,
            "xuat_luc": datetime.now().isoformat(timespec="seconds"),
            "cong_thuc": [dict(r) for r in c.execute("SELECT * FROM cong_thuc ORDER BY id")],
            "cong_thuc_ad": [dict(r) for r in c.execute("SELECT * FROM cong_thuc_ad ORDER BY id")],
            "cong_thuc_log": [dict(r) for r in c.execute("SELECT * FROM cong_thuc_log ORDER BY id")],
        }


def nhap_toan_bo(data: dict, de_len: bool = False) -> dict:
    """Nhập sổ từ bản sao lưu. Khớp theo MÃ công thức:
    - mã chưa có  → thêm mới, giữ nguyên ngày tháng và nhật ký
    - mã đã có    → bỏ qua, trừ khi de_len=True thì ghi đè hồ sơ đó
    """
    init_db()
    them = de = bo_qua = 0
    with _conn() as c:
        co_san = {r["ma"]: r["id"] for r in c.execute("SELECT ma, id FROM cong_thuc")}
        cols = [r[1] for r in c.execute("PRAGMA table_info(cong_thuc)")]
        for ho_so in data.get("cong_thuc") or []:
            ma = ho_so.get("ma")
            if not ma:
                continue
            cu_id = ho_so.get("id")
            if ma in co_san:
                if not de_len:
                    bo_qua += 1
                    continue
                c.execute("DELETE FROM cong_thuc WHERE id=?", (co_san[ma],))
                c.execute("DELETE FROM cong_thuc_ad WHERE ct_id=?", (co_san[ma],))
                c.execute("DELETE FROM cong_thuc_log WHERE ct_id=?", (co_san[ma],))
                de += 1
            else:
                them += 1
            dung = {k: v for k, v in ho_so.items() if k in cols and k != "id"}
            c.execute(f"INSERT INTO cong_thuc ({','.join(dung)}) "
                      f"VALUES ({','.join('?' * len(dung))})", list(dung.values()))
            moi_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            for lk in data.get("cong_thuc_ad") or []:
                if lk.get("ct_id") == cu_id:
                    c.execute("INSERT OR IGNORE INTO cong_thuc_ad (ct_id, kind, obj_id, name) "
                              "VALUES (?,?,?,?)",
                              (moi_id, lk.get("kind"), lk.get("obj_id"), lk.get("name") or ""))
            for lg in data.get("cong_thuc_log") or []:
                if lg.get("ct_id") == cu_id:
                    c.execute("INSERT INTO cong_thuc_log (ct_id, ts, hanh_dong, chi_tiet) "
                              "VALUES (?,?,?,?)",
                              (moi_id, lg.get("ts"), lg.get("hanh_dong"), lg.get("chi_tiet") or ""))
    return {"them": them, "de_len": de, "bo_qua": bo_qua}


def nhat_ky(ct_id: int) -> list:
    init_db()
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts, hanh_dong, chi_tiet FROM cong_thuc_log WHERE ct_id=? ORDER BY id DESC",
            (ct_id,))]


def phan_quyet(ct_id: int, quyet_dinh: str, ghi_chu: str = "", dieu_kien: str = "") -> dict:
    """quyet_dinh: 'dat' | 'khong_dat' | 'mot_phan' (→ mở vòng 2) | 'khong_ket_luan' | 'het_thoi'"""
    ct = get(ct_id)
    if not ct:
        return {}
    hom_nay = _today().isoformat()

    if quyet_dinh == "mot_phan":
        if int(ct.get("vong") or 1) >= MAX_VONG:
            quyet_dinh = "khong_dat"
            ghi_chu = (ghi_chu + " · Đã hết 2 vòng test, đóng lại.").strip(" ·")
        else:
            with _conn() as c:
                c.execute("""UPDATE cong_thuc SET trang_thai='khong_ket_luan',
                             ket_luan=?, ket_luan_ngay=? WHERE id=?""",
                          (f"Đạt một phần → chuyển sang vòng 2. {ghi_chu}".strip(),
                           hom_nay, ct_id))
                _log(c, ct_id, "Phán quyết", "Đạt một phần → mở vòng 2")
            moi = create({
                "ten": ct["ten"], "gia_thuyet": ct["gia_thuyet"], "nguoi_pt": ct["nguoi_pt"],
                "khu_vuc": ct["khu_vuc"], "loai": ct["loai"], "do_bang": ct["do_bang"],
                "ngay_bd": hom_nay,
                "han": (_today() + timedelta(days=14)).isoformat(),
                "ngan_sach": ct["ngan_sach"], "cpa_better_pct": ct["cpa_better_pct"],
                "trang_thai": "dang_test", "vong": int(ct.get("vong") or 1) + 1,
                "goc_id": ct_id, "tuan_nguon": ct.get("tuan_nguon") or "",
                "links": ct.get("links") or [],
            })
            return {"ok": True, "vong_moi": moi}

    ra_soat = ""
    if quyet_dinh == "dat":
        ra_soat = (_today() + timedelta(days=RA_SOAT_NGAY)).isoformat()
    with _conn() as c:
        c.execute("""UPDATE cong_thuc SET trang_thai=?, ket_luan=?, ket_luan_ngay=?,
                     ngay_ra_soat=?, dieu_kien=COALESCE(NULLIF(?,''), dieu_kien),
                     can_xem_lai=0, cap_nhat_luc=? WHERE id=?""",
                  (quyet_dinh, ghi_chu, hom_nay, ra_soat, dieu_kien,
                   datetime.now().isoformat(timespec="seconds"), ct_id))
        _log(c, ct_id, "Phán quyết", f"{TRANG_THAI.get(quyet_dinh, quyet_dinh)} · {ghi_chu}")
    return {"ok": True, "ct": get(ct_id)}


# ─── Dữ liệu cho trang ────────────────────────────────────────────────────────

def dashboard() -> dict:
    init_db()
    sw = sweep()
    den_han_l, dang_test, thu_vien, bai_hoc, de_xuat = [], [], [], [], []
    ds = list_all()
    _nap_truoc(ds)
    for ct in ds:
        ct["han_hieu_luc"] = _han_hieu_luc(ct).isoformat()
        ct["con_lai"] = (_han_hieu_luc(ct) - _today()).days
        st = ct.get("trang_thai")
        if st in ("dang_test", "de_xuat"):
            ct["do"] = measure(ct)
        if st == "de_xuat":
            de_xuat.append(ct)
        elif st == "dang_test":
            (den_han_l if den_han(ct) else dang_test).append(ct)
        elif st == "dat":
            thu_vien.append(ct)
        elif st in ("khong_dat", "khong_ket_luan", "het_thoi"):
            bai_hoc.append(ct)
    return {
        "de_xuat": de_xuat, "den_han": den_han_l, "dang_test": dang_test,
        "thu_vien": thu_vien, "bai_hoc": bai_hoc, "sweep": sw,
        "tham_so": {"cpa_better_pct": CPA_BETTER_PCT, "min_don": MIN_PURCHASES,
                    "min_chi": MIN_SPEND, "gia_han_ngay": GIA_HAN_NGAY,
                    "max_vong": MAX_VONG, "ra_soat_ngay": RA_SOAT_NGAY},
        "khu_vuc": KHU_VUC, "loai": LOAI, "do_bang": DO_BANG,
    }


def bang_hop_tuan() -> list:
    """Bảng dán vào slide họp tuần: 3 khối theo đúng nghi thức tuần."""
    d = dashboard()
    out = []
    for ct in d["den_han"]:
        m = ct.get("do") or {}
        out.append({"khoi": "Đến hạn phán quyết", "ma": ct["ma"], "ten": ct["ten"],
                    "ly_do": ct["gia_thuyet"], "viec": ct.get("viec_phai_lam", ""),
                    "so": m.get("ly_do", ""), "nguoi": ct["nguoi_pt"]})
    for ct in d["dang_test"]:
        m = ct.get("do") or {}
        out.append({"khoi": "Đang test", "ma": ct["ma"], "ten": ct["ten"],
                    "ly_do": ct["gia_thuyet"], "viec": ct.get("viec_phai_lam", ""),
                    "so": f"còn {ct['con_lai']} ngày · {m.get('ly_do','')}",
                    "nguoi": ct["nguoi_pt"]})
    for ct in d["thu_vien"]:
        out.append({"khoi": "Thư viện chuẩn", "ma": ct["ma"], "ten": ct["ten"],
                    "ly_do": ct["dieu_kien"] or ct["gia_thuyet"],
                    "viec": ct.get("viec_phai_lam", ""),
                    "so": ct["ket_luan"], "nguoi": ct["nguoi_pt"]})
    return out


def ads_chon(ngay: int = 14, ct_id: int = 0) -> dict:
    """Danh sách quảng cáo để tick gắn vào công thức.

    Trả về ĐỦ, không cắt bớt: bản cũ chỉ trả 400 quảng cáo tiêu tiền nhiều nhất nên
    quá nửa số quảng cáo không bao giờ hiện ra để tick.
    Nếu có ct_id thì lấy đúng khoảng ngày của công thức đó (công thức chạy 20 ngày
    trước sẽ không mất quảng cáo vì cửa sổ 14 ngày cố định).
    """
    d_to = (_today() - timedelta(days=1)).isoformat()
    d_from = (_today() - timedelta(days=max(1, ngay))).isoformat()
    if ct_id:
        ct = get(ct_id)
        if ct:
            f, t = _window(ct)
            d_from, d_to = min(d_from, f), max(d_to, t)
    try:
        rows = _fetch_rows(d_from, d_to)
    except Exception as e:
        return {"ads": [], "tu": d_from, "den": d_to, "error": str(e)}
    by_ad = {}
    for r in rows:
        a = by_ad.setdefault(r["ad_id"], {
            "ad_id": r["ad_id"], "ad_name": r["ad_name"], "campaign_id": r["campaign_id"],
            "campaign_name": r["campaign_name"], "adset_name": r["adset_name"],
            "chi": 0.0, "don": 0,
            "khu_vuc": classify_region(r["campaign_name"])})
        a["chi"] += r["spend_raw"] * (1 + AD_VAT_RATE)
        a["don"] += r["purchases"]
    out = sorted(by_ad.values(), key=lambda x: -x["chi"])
    for a in out:
        a["chi"] = round(a["chi"])
    return {"ads": out, "tu": d_from, "den": d_to}
