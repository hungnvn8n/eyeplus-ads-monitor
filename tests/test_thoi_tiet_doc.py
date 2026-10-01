"""Dải dự báo 3 ngày trên trang Tổng quan — chỉ đọc, không ghi."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import thoi_tiet_doc as td


def _ngay(i: int) -> str:
    return (date.today() + timedelta(days=i)).isoformat()


def _vung(ma, nhoms, he_so):
    return {"ma": ma, "ten": td.TEN_VUNG[ma], "do_tin": td.DO_TIN[ma],
            "ngay": [{"ngay": _ngay(i), "nhan": td._nhan_ngay(_ngay(i)),
                      "loai": "du_bao", "mua_mm": 30 if nh.startswith("mua") else 0,
                      "nhiet_max": 30, "nhom": nh, "ten_nhom": td.TEN_NHOM[nh],
                      "icon": td.ICON_NHOM[nh], "he_so": he_so[i]}
                     for i, nh in enumerate(nhoms)]}


def test_bon_vung_deu_co_he_so_rieng():
    """BN và HP đo riêng từ dữ liệu của chính nó, không dùng lại số Hà Nội."""
    b = td.HE_SO_DU_PHONG
    assert set(b) == {"HN", "HCM", "BN", "HP"}
    assert td._he_so(b, "BN", "mua_rat_to") != td._he_so(b, "HN", "mua_rat_to")


def test_bn_hp_da_gia_co_nen_khong_ra_so_vo_ly():
    """Số thô của BN ra 'mưa rất to +10,9%'. Sau gia cố phải âm trở lại —
    không bao giờ được bảo đội Digital tăng chi vào ngày bão."""
    b = td.HE_SO_DU_PHONG
    for v in ("BN", "HP"):
        assert td._he_so(b, v, "mua_rat_to") < 0
        assert td._he_so(b, v, "nang") > 0


def test_nhan_ngay_doc_duoc():
    assert td._nhan_ngay(_ngay(0)) == "Hôm nay"
    assert td._nhan_ngay(_ngay(1)) == "Mai"
    assert td._nhan_ngay(_ngay(2)) == "Kia"


def test_ngay_binh_thuong_khong_ra_khuyen_nghi():
    v = _vung("HN", ["kho", "kho", "kho"], [0, 0, 0])
    assert td._khuyen_nghi([v]) == []


def test_mua_rat_to_ra_canh_bao_giam():
    v = _vung("HN", ["kho", "mua_rat_to", "kho"], [0, -0.102, 0])
    kn = td._khuyen_nghi([v])
    assert len(kn) == 1 and kn[0]["muc"] == "giam"
    assert "10%" in kn[0]["chu"] and "mai" in kn[0]["chu"]


def test_chi_sang_ngay_nang_phia_sau():
    v = _vung("HN", ["mua_rat_to", "nang", "kho"], [-0.102, 0.054, 0])
    assert "trời nắng" in td._khuyen_nghi([v])[0]["chu"]


def test_ba_ngay_nang_ra_khuyen_nghi_tang():
    v = _vung("HCM", ["nang", "nang", "nang"], [0.034] * 3)
    kn = td._khuyen_nghi([v])
    assert len(kn) == 1 and kn[0]["muc"] == "tang"


def test_hcm_mua_to_he_so_0_khong_bao_dong_nham():
    v = _vung("HCM", ["mua_to"] * 3, [0, 0, 0])
    assert td._khuyen_nghi([v]) == []


def test_kho_du_lieu_hong_thi_tra_rong_khong_nem(monkeypatch):
    """Trang Tổng quan phải mở được kể cả khi phần thời tiết hỏng."""
    def no():
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    monkeypatch.setattr(td, "_conn", no)
    assert td.ba_ngay() == {"co": False, "vung": [], "khuyen_nghi": []}


# ─── Chia mục tiêu tuần ──────────────────────────────────────────────────────
# Chuyển từ app MKT sang đây 01/10/2026 cùng với mã nguồn, khi trang Thời tiết
# dọn vào menu Quản lý Ads.

import pytest


def test_chan_bien_do_5_phan_tram():
    assert td.chan_bien_do(0.30) == pytest.approx(0.05)
    assert td.chan_bien_do(-0.30) == pytest.approx(-0.05)
    assert td.chan_bien_do(0.02) == pytest.approx(0.02)


def _ngay_thang(thang: str, so_ngay: int, nhom="kho", hs=0.0):
    return [{"ngay": f"{thang}-{i:02d}", "vung": v, "nhom": nhom, "he_so": hs}
            for i in range(1, so_ngay + 1) for v in td.TEN_VUNG]


def test_gop_tuan_rong():
    assert td.gop_tuan([]) == []


def test_tong_muc_tieu_cac_tuan_bang_muc_tieu_thang():
    """Chia lại tỉ trọng KHÔNG được âm thầm nâng hay hạ mục tiêu cả tháng."""
    ds = _ngay_thang("2026-10", 31, "mua_rat_to", -0.102)
    for r in ds:
        if r["ngay"] <= "2026-10-05":
            r["nhom"], r["he_so"] = "nang", 0.054
    tuan = td.gop_tuan(ds, muc_tieu_thang=8_000_000_000)
    assert sum(t["muc_tieu"] for t in tuan) == pytest.approx(8_000_000_000, abs=10)
    assert sum(t["ti_trong_goi_y"] for t in tuan) == pytest.approx(1.0, abs=0.001)


def test_tuan_nang_duoc_giao_nhieu_hon_tuan_mua():
    ds = _ngay_thang("2026-10", 14)
    for r in ds:
        if r["ngay"] <= "2026-10-04":
            r["nhom"], r["he_so"] = "nang", 0.054
        else:
            r["nhom"], r["he_so"] = "mua_rat_to", -0.102
    # 01/10/2026 rơi vào thứ Năm nên 14 ngày đầu tháng trải ra 3 tuần lịch.
    t1, t2 = td.gop_tuan(ds, 1_000_000_000)[:2]
    assert t1["he_so"] > 0 > t2["he_so"]
    assert t1["ti_trong_goi_y"] / t1["so_ngay"] > t2["ti_trong_goi_y"] / t2["so_ngay"]


def test_he_so_tuan_khong_vuot_bien_do():
    ds = _ngay_thang("2026-10", 7, "mua_rat_to", -0.5)      # cố tình nhập quá tay
    assert td.gop_tuan(ds)[0]["he_so"] == pytest.approx(-0.05)


def test_vung_doanh_thu_lon_keo_he_so_manh_hon():
    """HN chiếm 55% doanh thu — HN mưa phải nặng hơn HP mưa."""
    ngay = "2026-10-05"
    def tuan(nang_ne, con_lai):
        ds = [{"ngay": ngay, "vung": v, "nhom": "kho", "he_so": 0.0} for v in con_lai]
        ds.append({"ngay": ngay, "vung": nang_ne, "nhom": "mua_rat_to", "he_so": -0.102})
        return next(t for t in td.gop_tuan(ds) if t["co_du_bao"])
    assert tuan("HN", ("HCM", "BN", "HP"))["he_so"] < tuan("HP", ("HCM", "BN", "HN"))["he_so"]


def test_khung_tuan_phu_du_thang_du_chi_co_nua_thang_du_lieu():
    """Kho mới có nửa tháng thì KHÔNG được dồn cả mục tiêu tháng vào mấy tuần đầu."""
    ds = _ngay_thang("2026-10", 16, "nang", 0.054)          # chỉ 16/31 ngày
    tuan = td.gop_tuan(ds, muc_tieu_thang=8_000_000_000)
    assert sum(t["so_ngay"] for t in tuan) == 31
    assert sum(t["muc_tieu"] for t in tuan) == pytest.approx(8_000_000_000, abs=10)
    cuoi = [t for t in tuan if not t["co_du_bao"]]
    assert cuoi and all(t["he_so"] == 0 for t in cuoi)


def test_lich_thang_hong_kho_du_lieu_van_tra_khung_rong(monkeypatch):
    def no():
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    monkeypatch.setattr(td, "_conn", no)
    r = td.lich_thang()
    assert r["co"] is False and r["ngay"] == [] and r["tuan"] == []


# ─── Chia mục tiêu RIÊNG TỪNG VÙNG ───────────────────────────────────────────
# CEO chốt 01/10/2026: không gộp chung cả hệ thống, vì Hà Nội mưa rất to trong
# khi TP.HCM đang nắng thì hai chiều triệt tiêu nhau.

TT = {"HN": 0.52, "HCM": 0.39, "BN": 0.045, "HP": 0.045}


def _thang_du(nhom_theo_vung):
    """31 ngày tháng 10, mỗi vùng một kiểu thời tiết cố định."""
    return [{"ngay": f"2026-10-{d:02d}", "vung": v, "nhom": nh,
             "he_so": td._he_so(td.HE_SO_DU_PHONG, v, nh)}
            for d in range(1, 32) for v, nh in nhom_theo_vung.items()]


def test_moi_tuan_co_du_bon_vung():
    t = td.gop_tuan(_thang_du({v: "nang" for v in td.TEN_VUNG}), 8_010_000_000, TT)
    assert [x["ma"] for x in t[0]["vung"]] == ["HN", "HCM", "BN", "HP"]


def test_hai_vung_nguoc_chieu_khong_triet_tieu_nhau():
    """Hà Nội mưa rất to, TP.HCM nắng — mỗi vùng phải giữ chiều của mình."""
    ds = _thang_du({"HN": "mua_rat_to", "HCM": "nang", "BN": "kho", "HP": "kho"})
    v = {x["ma"]: x for x in td.gop_tuan(ds, 8_010_000_000, TT)[1]["vung"]}
    assert v["HN"]["he_so"] < 0 < v["HCM"]["he_so"]


def test_tong_cac_vung_cac_tuan_bang_dung_muc_tieu_thang():
    """Làm tròn từng ô không được làm lệch con số CEO đã duyệt."""
    ds = _thang_du({"HN": "mua_rat_to", "HCM": "nang", "BN": "mua_to", "HP": "nang"})
    t = td.gop_tuan(ds, 8_010_000_000, TT)
    assert sum(x["muc_tieu"] for x in t) == 8_010_000_000
    assert sum(y["muc_tieu"] for x in t for y in x["vung"]) == 8_010_000_000


def test_ti_trong_lech_1_van_chia_dung():
    """Tỉ trọng đọc từ kho có thể không cộng tròn 1 — phải tự chuẩn hoá."""
    ds = _thang_du({v: "nang" for v in td.TEN_VUNG})
    t = td.gop_tuan(ds, 1_000_000_000, {"HN": 5, "HCM": 4, "BN": 0.5, "HP": 0.5})
    assert sum(x["muc_tieu"] for x in t) == 1_000_000_000


def test_vung_doanh_thu_lon_duoc_giao_nhieu_hon():
    ds = _thang_du({v: "nang" for v in td.TEN_VUNG})
    v = {x["ma"]: x for x in td.gop_tuan(ds, 8_010_000_000, TT)[1]["vung"]}
    assert v["HN"]["muc_tieu"] > v["HCM"]["muc_tieu"] > v["BN"]["muc_tieu"]


def test_he_so_tung_vung_khong_vuot_bien_do():
    ds = [{"ngay": f"2026-10-{d:02d}", "vung": v, "nhom": "mua_rat_to", "he_so": -0.5}
          for d in range(1, 32) for v in td.TEN_VUNG]
    assert all(x["he_so"] == pytest.approx(-0.05)
               for t in td.gop_tuan(ds, 0, TT) for x in t["vung"])


def test_ti_trong_vung_hong_kho_du_lieu_van_tra_bo_dung_san(monkeypatch):
    def no():
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    monkeypatch.setattr(td, "_conn", no)
    td._NHO_TT.update(luc=0, gia_tri=None)
    assert td.ti_trong_vung() == td.TI_TRONG
