"""thoi_tiet_doc — ĐỌC lịch thời tiết: dải 3 ngày ở Tổng quan và trang Thời tiết.

Chỉ đọc, không ghi. Bảng `thoi_tiet_ngay` và `thoi_tiet_he_so` do app MKT
(ChienluocKD/fb_chatbot/thoi_tiet.py) kéo về và ghi theo nhịp 2 tiếng; hai dịch
vụ dùng chung một Postgres (ROLLUP_DATABASE_URL) nên bên này đọc thẳng bảng,
khỏi gọi Open-Meteo lần hai và khỏi dựng đường truyền chéo giữa hai dịch vụ.

Ngưỡng xếp nhóm và hệ số KHÔNG chép lại ở đây: nhóm đã được ghi sẵn vào bảng
lúc kéo dữ liệu, hệ số đọc từ bảng `thoi_tiet_he_so`. Phần đặt câu khuyến nghị
và chia mục tiêu tuần chỉ có MỘT bản, nằm ở đây — bên app MKT đã gỡ đi để không
có hai bản lệch nhau.

Số liệu nền (đo trên 864 ngày bán hàng): Hà Nội ngày nắng +5,4%, mưa rất to
−10,2%; TP.HCM chỉ mưa rất to mới ăn thua, −7,5%. Bắc Ninh và Hải Phòng đo riêng
từ dữ liệu của chính hai cơ sở, nhưng có gia cố bằng số Hà Nội vì mỗi ngày chỉ
bán 8–10 hoá đơn, số thô lắc quá mạnh. Chi tiết ở fb_chatbot/thoi_tiet.py.
"""
import logging
import os
from datetime import date, datetime, timedelta

log = logging.getLogger("thoi_tiet_doc")

TEN_VUNG = {"HN": "Hà Nội", "HCM": "TP.HCM", "BN": "Bắc Ninh", "HP": "Hải Phòng"}
# Độ tin của số đo riêng từng vùng — màn hình dùng để ghi chú.
DO_TIN = {"HN": "đầy", "HCM": "đầy", "BN": "vừa", "HP": "mỏng"}

TEN_NHOM = {
    "nang": "Nắng nhiều", "kho": "Khô, ít nắng", "mua_nhe": "Mưa nhẹ",
    "mua_to": "Mưa to", "mua_rat_to": "Mưa rất to",
}
ICON_NHOM = {
    "nang": "☀️", "kho": "🌤️", "mua_nhe": "🌦️", "mua_to": "🌧️", "mua_rat_to": "⛈️",
}
# Bản sao phòng khi bảng hệ số chưa có dòng nào (lần chạy đầu).
HE_SO_DU_PHONG = {
    "HN":  {"nang": 0.054, "kho": 0.0,   "mua_nhe": 0.0,   "mua_to": -0.039, "mua_rat_to": -0.102},
    "HCM": {"nang": 0.034, "kho": 0.0,   "mua_nhe": 0.030, "mua_to":  0.0,   "mua_rat_to": -0.075},
    "BN":  {"nang": 0.048, "kho": 0.015, "mua_nhe": 0.0,   "mua_to": -0.014, "mua_rat_to": -0.050},
    "HP":  {"nang": 0.059, "kho": 0.0,   "mua_nhe": 0.065, "mua_to": -0.047, "mua_rat_to": -0.106},
}

NGUONG_GIAM = -0.03     # dưới mức này mới đáng nhắc giảm chi


def _conn():
    import psycopg2
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    return psycopg2.connect(url, connect_timeout=15)


def _he_so_bang(cur) -> dict:
    ra = {v: dict(d) for v, d in HE_SO_DU_PHONG.items()}
    try:
        cur.execute("SELECT vung, nhom, he_so FROM thoi_tiet_he_so")
        for v, nh, hs in cur.fetchall():
            ra.setdefault(v, {})[nh] = float(hs)
    except Exception:
        pass          # bảng chưa có → dùng bản dự phòng
    return ra


def _he_so(bang: dict, vung: str, nhom: str) -> float:
    return float((bang.get(vung) or {}).get(nhom, 0.0))


def ba_ngay(so_ngay: int = 3) -> dict:
    """Dải dự báo cho trang Tổng quan: {vung: [...]} + danh sách khuyến nghị.

    Lỗi kho dữ liệu thì trả rỗng chứ KHÔNG ném — trang Tổng quan phải mở được
    bình thường kể cả khi phần thời tiết hỏng, chỉ là thiếu mất dải dự báo.
    """
    hom_nay = date.today()
    den = hom_nay + timedelta(days=so_ngay - 1)
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                bang = _he_so_bang(cur)
                cur.execute("""
                    SELECT vung, ngay::text, loai, mua_mm, nhiet_max, nhom
                    FROM thoi_tiet_ngay WHERE ngay BETWEEN %s AND %s
                    ORDER BY vung, ngay
                """, (hom_nay, den))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as ex:
        log.warning(f"Không đọc được lịch thời tiết: {ex}")
        return {"co": False, "vung": [], "khuyen_nghi": []}

    if not rows:
        return {"co": False, "vung": [], "khuyen_nghi": []}

    theo_vung = {}
    for v, ng, loai, mm, tx, nh in rows:
        theo_vung.setdefault(v, []).append({
            "ngay": ng, "nhan": _nhan_ngay(ng), "loai": loai,
            "mua_mm": round(mm or 0),
            "nhiet_max": round(tx) if tx is not None else None,
            "nhom": nh, "ten_nhom": TEN_NHOM.get(nh, nh),
            "icon": ICON_NHOM.get(nh, ""), "he_so": round(_he_so(bang, v, nh), 4),
        })

    ds_vung = [{"ma": v, "ten": TEN_VUNG.get(v, v), "ngay": theo_vung[v],
                "do_tin": DO_TIN.get(v, "đầy")}
               for v in ("HN", "HCM", "BN", "HP") if v in theo_vung]
    return {"co": True, "vung": ds_vung,
            "khuyen_nghi": _khuyen_nghi(ds_vung)}


def _nhan_ngay(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    cach = (d - date.today()).days
    if cach == 0:
        return "Hôm nay"
    if cach == 1:
        return "Mai"
    if cach == 2:
        return "Kia"
    thu = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
    return f"{thu[d.weekday()]} {d:%d/%m}"


def _khuyen_nghi(ds_vung: list) -> list:
    """Vài dòng gợi ý. Rỗng = không có gì đáng làm, khỏi bày thêm chữ cho nhiễu.

    CHỈ gợi ý, KHÔNG có nút tự chỉnh ngân sách: đo theo tuần thì dự báo chỉ khớp
    thực tế ở mức 0,26 trên thang 0–1, nên người vẫn phải nhìn bối cảnh khác.
    """
    ra = []
    for v in ds_vung:
        ngay = v["ngay"]
        nang_ne = [r for r in ngay if r["he_so"] <= NGUONG_GIAM]
        if nang_ne:
            r = min(nang_ne, key=lambda x: x["he_so"])
            dep = [x for x in ngay if x["nhom"] == "nang" and x["ngay"] > r["ngay"]]
            them = f" Có thể dồn sang {dep[0]['nhan'].lower()} trời nắng." if dep else ""
            ra.append({
                "muc": "giam", "icon": r["icon"],
                "chu": (f"{v['ten']} — {r['nhan'].lower()} {r['ten_nhom'].lower()} "
                        f"({r['mua_mm']}mm). Ngày như vậy thường hụt khoảng "
                        f"{abs(round(r['he_so'] * 100))}% lượt khách. "
                        f"Cân nhắc giảm chi quảng cáo {v['ten']} ngày đó.{them}"),
            })
        elif ngay and all(r["nhom"] == "nang" for r in ngay):
            ra.append({
                "muc": "tang", "icon": "☀️",
                "chu": (f"{v['ten']} — ba ngày tới đều nắng, thuận cho đẩy mạnh. "
                        f"Mức thường tăng khoảng {round(ngay[0]['he_so'] * 100)}%."),
            })
    return ra


# ─── Chia mục tiêu tuần ──────────────────────────────────────────────────────
# Tỉ trọng doanh thu bán lẻ từng vùng. Đọc từ doanh thu thật 90 ngày gần nhất
# chứ không chôn số trong mã nguồn — mở thêm cửa hàng là cơ cấu đổi ngay.
# Bộ dưới chỉ là đường lui khi không đọc được kho dữ liệu.
TI_TRONG = {"HN": 0.52, "HCM": 0.39, "BN": 0.045, "HP": 0.045}
_NHO_TT = {"luc": 0.0, "gia_tri": None}
TTL_TI_TRONG = 3600


def ti_trong_vung() -> dict:
    """{vung: tỉ trọng doanh thu} từ 90 ngày gần nhất, cộng lại bằng 1."""
    import time
    if _NHO_TT["gia_tri"] and time.time() - _NHO_TT["luc"] < TTL_TI_TRONG:
        return _NHO_TT["gia_tri"]
    ra = dict(TI_TRONG)
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    WITH x AS (
                      SELECT (j->>'name') AS ten, (j->>'rev')::bigint AS rev
                      FROM daily_rollup d,
                           LATERAL jsonb_array_elements(d.retail_by_store::jsonb) j
                      WHERE d.date >= to_char(current_date - 90, 'YYYY-MM-DD')
                    )
                    SELECT CASE WHEN ten LIKE 'HN-%' THEN 'HN'
                                WHEN ten LIKE 'SG-%' THEN 'HCM'
                                WHEN ten LIKE 'BN-%' THEN 'BN'
                                WHEN ten LIKE 'HP-%' THEN 'HP' END AS vung,
                           sum(rev)
                    FROM x GROUP BY 1
                """)
                dt = {v: float(r) for v, r in cur.fetchall() if v}
        finally:
            conn.close()
        tong = sum(dt.values())
        if tong > 0 and len(dt) >= 2:
            ra = {v: dt.get(v, 0) / tong for v in TEN_VUNG}
    except Exception as ex:
        log.warning(f"Không đọc được tỉ trọng vùng, dùng bộ dựng sẵn: {ex}")
    _NHO_TT.update(luc=__import__("time").time(), gia_tri=ra)
    return ra

# Chặn cứng mức điều chỉnh mục tiêu tuần. Đo theo tuần thì dự báo chỉ khớp thực
# tế ở mức 0,26 trên thang 0–1 — chỉnh mạnh hơn là tin vào thứ số liệu không đỡ
# nổi, và để hở thì sớm muộn sẽ có người kéo lên ±20%.
BIEN_DO = 0.05
NGAY_DU_BAO = 16      # từ ngày 17 trở đi chỉ còn trung bình nhiều năm


def chan_bien_do(x, bien=BIEN_DO) -> float:
    return max(-bien, min(bien, float(x or 0)))


def _tuan_cua(ngay: date, dau_thang: date) -> int:
    """Tuần thứ mấy trong tháng, cắt theo thứ Hai (tuần 1 = tuần chứa ngày 1)."""
    dau_tuan_1 = dau_thang - timedelta(days=dau_thang.weekday())
    return ((ngay - dau_tuan_1).days // 7) + 1


def gop_tuan(ngay_list: list, muc_tieu_thang: int = 0, ti_trong: dict = None) -> list:
    """Gộp các ngày thành dòng theo tuần, chia mục tiêu RIÊNG CHO TỪNG VÙNG.

    Mỗi vùng chịu thời tiết khác nhau — Hà Nội mưa rất to hụt 10% trong khi
    TP.HCM hôm đó có thể đang nắng. Gộp chung cả hệ thống thì hai chiều ngược
    nhau triệt tiêu lẫn nhau và không ai biết phải điều chỉnh ở đâu. Nên mỗi
    vùng được chia riêng rồi mới cộng lại thành số toàn hệ.

    Khung tuần dựng theo LỊCH của cả tháng, không theo những ngày đang có dữ
    liệu. Nếu không thì hôm nào kho mới chỉ có nửa tháng, cả mục tiêu tháng sẽ
    bị dồn vào mấy tuần đầu — người đọc tưởng tuần này phải làm gấp rưỡi.
    Tuần chưa có dự báo vẫn hiện, mức điều chỉnh để 0.
    """
    if not ngay_list:
        return []
    # Chuẩn hoá tỉ trọng về đúng 1 trước khi chia, nếu không cộng các vùng lại
    # sẽ thiếu hoặc thừa so với mục tiêu tháng.
    tho = ti_trong or TI_TRONG
    _t = sum(tho.get(v, 0) for v in TEN_VUNG) or 1
    tt = {v: tho.get(v, 0) / _t for v in TEN_VUNG}
    ngays = sorted({r["ngay"] for r in ngay_list})
    dau_thang = datetime.strptime(ngays[0], "%Y-%m-%d").date().replace(day=1)
    sang_thang = (dau_thang + timedelta(days=32)).replace(day=1)
    so_ngay_thang = (sang_thang - dau_thang).days

    khung = {}
    for i in range(so_ngay_thang):
        d = dau_thang + timedelta(days=i)
        khung.setdefault(_tuan_cua(d, dau_thang), {"ngay": set(), "hs": {}})["ngay"].add(d.isoformat())
    for r in ngay_list:
        d = datetime.strptime(r["ngay"], "%Y-%m-%d").date()
        if not (dau_thang <= d < sang_thang):
            continue
        khung[_tuan_cua(d, dau_thang)]["hs"].setdefault(r["vung"], []).append(
            float(r.get("he_so") or 0))

    so_tuans = sorted(khung)
    # Hệ số tuần của từng vùng = bình quân các ngày trong tuần, ghì về trong biên độ.
    hs = {v: {t: chan_bien_do(sum(khung[t]["hs"].get(v, [0])) / len(khung[t]["hs"].get(v, [0])))
              for t in so_tuans} for v in TEN_VUNG}

    # Chuẩn hoá TRONG TỪNG VÙNG: cộng các tuần của một vùng phải đúng bằng mục
    # tiêu tháng của vùng đó, nếu không là âm thầm nâng/hạ mục tiêu cả tháng.
    tt_thuong = {t: len(khung[t]["ngay"]) / so_ngay_thang for t in so_tuans}
    goi_y = {}
    for v in TEN_VUNG:
        mau = sum(tt_thuong[t] * (1 + hs[v][t]) for t in so_tuans) or 1
        goi_y[v] = {t: tt_thuong[t] * (1 + hs[v][t]) / mau for t in so_tuans}

    ra = []
    for t in so_tuans:
        ngay_sx = sorted(khung[t]["ngay"])
        ds_vung = []
        for v in ("HN", "HCM", "BN", "HP"):
            mt_vung = (muc_tieu_thang or 0) * tt.get(v, 0)
            ds_vung.append({
                "ma": v, "ten": TEN_VUNG[v], "he_so": round(hs[v][t], 4),
                "ti_trong_goi_y": round(goi_y[v][t], 4),
                "muc_tieu": int(round(mt_vung * goi_y[v][t])),
                "co_du_bao": bool(khung[t]["hs"].get(v)),
            })
        tong_mt = sum(x["muc_tieu"] for x in ds_vung)
        # Hệ số toàn hệ = bình quân hệ số các vùng có trọng số doanh thu.
        hs_chung = sum(tt.get(x["ma"], 0) * x["he_so"] for x in ds_vung) / (sum(tt.values()) or 1)
        ra.append({
            "tuan": t, "tu": ngay_sx[0], "den": ngay_sx[-1], "so_ngay": len(ngay_sx),
            "co_du_bao": bool(khung[t]["hs"]),
            "he_so": round(hs_chung, 4),
            "ti_trong_thuong": round(tt_thuong[t], 4),
            "ti_trong_goi_y": round(tong_mt / (muc_tieu_thang or 1), 4) if muc_tieu_thang
                              else round(sum(tt.get(x["ma"], 0) * x["ti_trong_goi_y"]
                                             for x in ds_vung) / (sum(tt.values()) or 1), 4),
            "muc_tieu": tong_mt,
            "vung": ds_vung,
        })

    # Làm tròn từng ô khiến tổng lệch vài đồng so với mục tiêu tháng. Bù phần
    # lệch vào ô lớn nhất — bảng phải cộng ra đúng con số CEO đã duyệt.
    if muc_tieu_thang:
        du = muc_tieu_thang - sum(r["muc_tieu"] for r in ra)
        if du:
            to_nhat = max((x for r in ra for x in r["vung"]), key=lambda x: x["muc_tieu"])
            to_nhat["muc_tieu"] += du
            for r in ra:
                r["muc_tieu"] = sum(x["muc_tieu"] for x in r["vung"])
    return ra


def muc_tieu_thang(thang: str) -> int:
    """Mục tiêu doanh thu bán lẻ của một tháng ('2026-10'), đặt ở Cài đặt app MKT."""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT amount FROM mkt_tv_target "
                            "WHERE month_key=%s AND kind='revenue'", (thang,))
                r = cur.fetchone()
                return int(r[0]) if r else 0
        finally:
            conn.close()
    except Exception as ex:
        log.warning(f"Không đọc được mục tiêu tháng {thang}: {ex}")
        return 0


def _ds_vung(ti_trong: dict = None) -> list:
    """Danh sách vùng theo đúng thứ tự doanh thu — jsonify sắp xếp khoá của dict
    theo bảng chữ cái nên không thể gửi dict nếu muốn giữ thứ tự."""
    tt = ti_trong or TI_TRONG
    return [{"ma": v, "ten": TEN_VUNG[v], "do_tin": DO_TIN.get(v, "đầy"),
             "ti_trong": round(tt.get(v, 0), 4)}
            for v in ("HN", "HCM", "BN", "HP")]


def lich_thang(so_ngay: int = 30) -> dict:
    """Lịch 30 ngày 4 vùng + dòng tuần cho trang Thời tiết.

    Lỗi kho dữ liệu thì trả khung rỗng chứ không ném — trang vẫn mở được.
    """
    hom_nay = date.today()
    dau_thang = hom_nay.replace(day=1)
    den = hom_nay + timedelta(days=so_ngay + 10)
    thang = hom_nay.strftime("%Y-%m")
    rong = {"co": False, "hom_nay": hom_nay.isoformat(), "thang": thang,
            "ngay": [], "tuan": [], "muc_tieu_thang": 0,
            "bien_do": BIEN_DO, "vung": _ds_vung(), "cap_nhat": None}
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                bang = _he_so_bang(cur)
                cur.execute("""
                    SELECT vung, ngay::text, loai, mua_mm, nhiet_max, nhiet_min, nhom
                    FROM thoi_tiet_ngay WHERE ngay BETWEEN %s AND %s
                    ORDER BY ngay, vung
                """, (dau_thang, den))
                rows = cur.fetchall()
                cur.execute("SELECT max(updated_at) FROM thoi_tiet_ngay")
                cn = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as ex:
        log.warning(f"Không đọc được lịch thời tiết: {ex}")
        return rong
    if not rows:
        return rong

    ngay = [{
        "vung": v, "ngay": ng, "loai": loai, "mua_mm": round(mm or 0, 1),
        "nhiet_max": round(tx) if tx is not None else None,
        "nhiet_min": round(tn) if tn is not None else None,
        "nhom": nh, "ten_nhom": TEN_NHOM.get(nh, nh), "icon": ICON_NHOM.get(nh, ""),
        "he_so": round(_he_so(bang, v, nh), 4), "do_tin": DO_TIN.get(v, "đầy"),
    } for v, ng, loai, mm, tx, tn, nh in rows]

    mt = muc_tieu_thang(thang)
    tt = ti_trong_vung()
    return {**rong, "co": True, "ngay": ngay, "vung": _ds_vung(tt), "ti_trong": tt,
            "tuan": gop_tuan([r for r in ngay if r["ngay"][:7] == thang], mt, tt),
            "muc_tieu_thang": mt,
            "cap_nhat": cn.isoformat() if cn else None}
