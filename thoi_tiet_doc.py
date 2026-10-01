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
−10,2%; TP.HCM chỉ mưa rất to mới ăn thua, −7,5%. Bắc Ninh và Hải Phòng mượn hệ
số Hà Nội vì chưa đủ ngày để đo riêng.
"""
import logging
import os
from datetime import date, datetime, timedelta

log = logging.getLogger("thoi_tiet_doc")

TEN_VUNG = {"HN": "Hà Nội", "HCM": "TP.HCM", "BN": "Bắc Ninh", "HP": "Hải Phòng"}
MUON_HE_SO = {"BN": "HN", "HP": "HN"}

TEN_NHOM = {
    "nang": "Nắng nhiều", "kho": "Khô, ít nắng", "mua_nhe": "Mưa nhẹ",
    "mua_to": "Mưa to", "mua_rat_to": "Mưa rất to",
}
ICON_NHOM = {
    "nang": "☀️", "kho": "🌤️", "mua_nhe": "🌦️", "mua_to": "🌧️", "mua_rat_to": "⛈️",
}
# Bản sao phòng khi bảng hệ số chưa có dòng nào (lần chạy đầu).
HE_SO_DU_PHONG = {
    "HN":  {"nang": 0.054, "kho": 0.0, "mua_nhe": 0.0,   "mua_to": -0.039, "mua_rat_to": -0.102},
    "HCM": {"nang": 0.034, "kho": 0.0, "mua_nhe": 0.030, "mua_to":  0.0,   "mua_rat_to": -0.075},
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
    goc = MUON_HE_SO.get(vung, vung)
    return float((bang.get(goc) or {}).get(nhom, 0.0))


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
                "muon_he_so": v in MUON_HE_SO}
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
# Tỉ trọng doanh thu bán lẻ từng vùng — gộp hệ số 4 vùng thành một con số chung.
TI_TRONG = {"HN": 0.55, "HCM": 0.37, "BN": 0.04, "HP": 0.04}

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


def gop_tuan(ngay_list: list, muc_tieu_thang: int = 0) -> list:
    """Gộp các ngày thành dòng theo tuần, kèm mục tiêu tuần đã hiệu chỉnh.

    Khung tuần dựng theo LỊCH của cả tháng, không theo những ngày đang có dữ
    liệu. Nếu không thì hôm nào kho mới chỉ có nửa tháng, cả mục tiêu tháng sẽ
    bị dồn vào mấy tuần đầu — người đọc tưởng tuần này phải làm gấp rưỡi.
    Tuần chưa có dự báo vẫn hiện, mức điều chỉnh để 0.
    """
    if not ngay_list:
        return []
    ngays = sorted({r["ngay"] for r in ngay_list})
    dau_thang = datetime.strptime(ngays[0], "%Y-%m-%d").date().replace(day=1)
    sang_thang = (dau_thang + timedelta(days=32)).replace(day=1)
    so_ngay_thang = (sang_thang - dau_thang).days

    theo_tuan = {}
    for i in range(so_ngay_thang):
        d = dau_thang + timedelta(days=i)
        theo_tuan.setdefault(_tuan_cua(d, dau_thang),
                             {"ngay": set(), "diem": []})["ngay"].add(d.isoformat())
    for r in ngay_list:
        d = datetime.strptime(r["ngay"], "%Y-%m-%d").date()
        if not (dau_thang <= d < sang_thang):
            continue
        theo_tuan[_tuan_cua(d, dau_thang)]["diem"].append(
            (r["ngay"], r["vung"], float(r.get("he_so") or 0)))

    ra = []
    for so_tuan in sorted(theo_tuan):
        t = theo_tuan[so_tuan]
        # Gộp theo ngày trước (bình quân có trọng số vùng), rồi mới bình quân các
        # ngày — làm ngược lại thì vùng nào thiếu dữ liệu sẽ kéo lệch cả tuần.
        theo_ngay = {}
        for ng, v, hs in t["diem"]:
            theo_ngay.setdefault(ng, []).append((v, hs))
        hs_ngay = []
        for ds in theo_ngay.values():
            tong_tt = sum(TI_TRONG.get(v, 0) for v, _ in ds) or 1
            hs_ngay.append(sum(TI_TRONG.get(v, 0) * h for v, h in ds) / tong_tt)
        hs_tuan = chan_bien_do(sum(hs_ngay) / len(hs_ngay)) if hs_ngay else 0.0

        ngay_sx = sorted(t["ngay"])
        ra.append({"tuan": so_tuan, "tu": ngay_sx[0], "den": ngay_sx[-1],
                   "so_ngay": len(ngay_sx), "he_so": round(hs_tuan, 4),
                   "ti_trong_thuong": len(ngay_sx) / so_ngay_thang,
                   "co_du_bao": bool(theo_ngay)})

    # Hiệu chỉnh tỉ trọng rồi CHUẨN HOÁ về đúng 100%: cộng các tuần phải bằng mục
    # tiêu tháng, nếu không là âm thầm nâng/hạ mục tiêu của cả tháng.
    tong = sum(r["ti_trong_thuong"] * (1 + r["he_so"]) for r in ra) or 1
    for r in ra:
        r["ti_trong_goi_y"] = r["ti_trong_thuong"] * (1 + r["he_so"]) / tong
        r["muc_tieu"] = int(round((muc_tieu_thang or 0) * r["ti_trong_goi_y"]))
        r["ti_trong_thuong"] = round(r["ti_trong_thuong"], 4)
        r["ti_trong_goi_y"] = round(r["ti_trong_goi_y"], 4)
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
            "bien_do": BIEN_DO, "ten_vung": TEN_VUNG, "cap_nhat": None}
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
        "he_so": round(_he_so(bang, v, nh), 4), "muon_he_so": v in MUON_HE_SO,
    } for v, ng, loai, mm, tx, tn, nh in rows]

    mt = muc_tieu_thang(thang)
    return {**rong, "co": True, "ngay": ngay,
            "tuan": gop_tuan([r for r in ngay if r["ngay"][:7] == thang], mt),
            "muc_tieu_thang": mt,
            "cap_nhat": cn.isoformat() if cn else None}
