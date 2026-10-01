"""thoi_tiet_doc — ĐỌC lịch thời tiết để vẽ dải 3 ngày trên trang Tổng quan.

Chỉ đọc, không ghi. Bảng `thoi_tiet_ngay` và `thoi_tiet_he_so` do app MKT
(ChienluocKD/fb_chatbot/thoi_tiet.py) kéo về và ghi theo nhịp 2 tiếng; hai dịch
vụ dùng chung một Postgres (ROLLUP_DATABASE_URL) nên bên này đọc thẳng bảng,
khỏi gọi Open-Meteo lần hai và khỏi dựng đường truyền chéo giữa hai dịch vụ.

Ngưỡng xếp nhóm và hệ số KHÔNG chép lại ở đây: nhóm đã được ghi sẵn vào bảng
lúc kéo dữ liệu, hệ số đọc từ bảng `thoi_tiet_he_so`. Chỉ phần đặt câu khuyến
nghị là viết riêng bên này — nếu sửa chữ thì sửa cả thoi_tiet.khuyen_nghi bên
app MKT cho khớp.

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
