"""Dải dự báo 3 ngày trên trang Tổng quan — chỉ đọc, không ghi."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import thoi_tiet_doc as td


def _ngay(i: int) -> str:
    return (date.today() + timedelta(days=i)).isoformat()


def _vung(ma, nhoms, he_so):
    return {"ma": ma, "ten": td.TEN_VUNG[ma], "muon_he_so": ma in td.MUON_HE_SO,
            "ngay": [{"ngay": _ngay(i), "nhan": td._nhan_ngay(_ngay(i)),
                      "loai": "du_bao", "mua_mm": 30 if nh.startswith("mua") else 0,
                      "nhiet_max": 30, "nhom": nh, "ten_nhom": td.TEN_NHOM[nh],
                      "icon": td.ICON_NHOM[nh], "he_so": he_so[i]}
                     for i, nh in enumerate(nhoms)]}


def test_he_so_bn_hp_muon_ha_noi():
    b = td.HE_SO_DU_PHONG
    for v in ("BN", "HP"):
        assert td._he_so(b, v, "mua_rat_to") == td._he_so(b, "HN", "mua_rat_to")


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
