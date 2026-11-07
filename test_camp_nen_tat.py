"""Test danh sách chiến dịch "Nên tắt" (nguồn cho thiệp sáng Lark).

Không gọi Facebook: thay _fb_price_map / inbox_db.query_all / _fb_campaign_statuses
bằng số giả, chỉ kiểm phần lọc — đúng ô "Nên tắt" của tab Inbox hay không.

Chạy: python3 test_camp_nen_tat.py
"""
import os
import sys

os.environ.setdefault("APP_PASSWORD", "test")
os.environ.setdefault("SKIP_BOOT_FETCH", "1")

import app as A
import inbox_db


# ─── Số giả ──────────────────────────────────────────────────────────────────
# 100 hội thoại: 10 SĐT (10%), 10 địa chỉ (10%), 60 mất tích/100 quét
#   điểm = (0,10 + 0,10 + 0,40) / 3 = 20  → DƯỚI 30
def _camp(total=100, phone=10, addr=10, ghost=60, scanned=100, ten="camp"):
    return {"campaign_name": ten, "total": total, "phone": phone,
            "by_label": {"addr": addr}, "ghost": ghost, "scanned": scanned}


def _gia(spend=2_000_000, roas=0.6, mess=40):
    return {"spend": spend, "roas": roas, "messages": mess,
            "price": round(spend / mess) if mess else None,
            "account_id": "123", "name": ""}


def _mau(aid="a1", chi=1_000_000, mess=10, roas=0.0, ten="mẫu"):
    return {"ad_id": aid, "ten": ten, "chi": chi, "mess": mess, "roas": roas}


def _chay(camps, gia, trang_thai, theo_mau=None, **kw):
    A._fb_price_map = lambda frm, to: gia
    A._fb_ad_map = lambda frm, to: (theo_mau or {})
    inbox_db.query_all = lambda **k: {"campaigns": camps}
    A._fb_campaign_statuses = lambda: (trang_thai, [])
    return A._camp_nen_tat(**kw)


def test_diem_dung_cong_thuc():
    """Điểm = trung bình 3 tỉ lệ, không trần — y hệt qualityScore() bên JS."""
    assert A._diem_chat_luong(_camp()) == 20
    # 0 hội thoại → 0 điểm, không chia cho 0
    assert A._diem_chat_luong({"total": 0}) == 0
    # chưa quét conv nào thì tỉ lệ mất tích = 0 (giống ghostRate() bên JS),
    # nên tỉ lệ "ở lại" tính tròn 1: (0,10 + 0,10 + 1) / 3 = 40
    assert A._diem_chat_luong(_camp(ghost=5, scanned=0)) == 40


def test_bat_dung_o_nen_tat():
    """Chỉ 'điểm <30 VÀ roas <2' mới vào danh sách — 3 ô kia phải lọt ra ngoài."""
    camps = {
        "yeu_ca_hai":   _camp(ten="yếu cả hai"),                       # điểm 20
        "diem_cao":     _camp(phone=40, addr=40, ghost=10, ten="điểm cao"),
        "roas_cao":     _camp(ten="roas cao"),
        "tot_ca_hai":   _camp(phone=40, addr=40, ghost=10, ten="tốt cả hai"),
    }
    gia = {
        "yeu_ca_hai": _gia(roas=0.6),
        "diem_cao":   _gia(roas=0.6),
        "roas_cao":   _gia(roas=3.1),
        "tot_ca_hai": _gia(roas=3.1),
    }
    on = {k: "on" for k in camps}
    r = _chay(camps, gia, on)
    assert [x["campaign_id"] for x in r["items"]] == ["yeu_ca_hai"], r["items"]
    assert r["tong_camp"] == 1


def test_bo_qua_camp_da_tat():
    camps = {"a": _camp(), "b": _camp()}
    gia = {"a": _gia(spend=5_000_000), "b": _gia(spend=1_000_000)}
    r = _chay(camps, gia, {"a": "off", "b": "on"})
    assert [x["campaign_id"] for x in r["items"]] == ["b"]


def test_chua_du_hoi_thoai_thi_khong_ket_luan():
    """Dưới 10 hội thoại — một hai người cũng đủ đẩy điểm, không chấm."""
    camps = {"it": _camp(total=9, phone=0, addr=0, ghost=9, scanned=9)}
    r = _chay(camps, {"it": _gia()}, {"it": "on"})
    assert r["items"] == []


def test_chua_co_so_chi_thi_khong_ket_luan():
    camps = {"x": _camp()}
    r = _chay(camps, {}, {"x": "on"})                       # không có trong price map
    assert r["items"] == []
    r = _chay(camps, {"x": _gia(spend=0, roas=None)}, {"x": "on"})
    assert r["items"] == []


def test_chan_duoi_theo_tien():
    camps = {"to": _camp(), "nho": _camp()}
    gia = {"to": _gia(spend=2_000_000), "nho": _gia(spend=120_000)}
    r = _chay(camps, gia, {"to": "on", "nho": "on"}, chi_toi_thieu=300_000)
    assert [x["campaign_id"] for x in r["items"]] == ["to"]


def test_sap_theo_tien_va_cat_ngon():
    camps = {str(i): _camp(ten=f"c{i}") for i in range(8)}
    gia = {str(i): _gia(spend=(i + 1) * 1_000_000) for i in range(8)}
    r = _chay(camps, gia, {str(i): "on" for i in range(8)}, gioi_han=5)
    assert r["tong_camp"] == 8                               # tổng đếm ĐỦ
    assert len(r["items"]) == 5                              # chỉ hiện 5
    assert [x["campaign_id"] for x in r["items"]] == ["7", "6", "5", "4", "3"]
    assert r["tong_chi"] == sum((i + 1) * 1_000_000 for i in range(8))


def test_camp_chua_doc_duoc_trang_thai_thi_GIU_lai():
    """Facebook chặn 1 tài khoản vì gọi quá nhiều → chiến dịch của tài khoản đó
    không có trong bản đồ trạng thái. Đó là CHƯA BIẾT, không phải ĐÃ TẮT.

    Loại nó đi là lặp lại lỗi 26/10/2026 (bảng còn 23 dòng trong khi Facebook
    có 86 cái đang chạy) — và thiệp sáng sẽ im lặng đúng hôm tiêu tiền nhiều nhất.
    """
    camps = {"biet_bat": _camp(), "chua_biet": _camp(), "biet_tat": _camp()}
    gia = {k: _gia(spend=1_000_000) for k in camps}
    A._fb_price_map = lambda frm, to: gia
    inbox_db.query_all = lambda **k: {"campaigns": camps}
    # "chua_biet" KHÔNG có trong bản đồ — tài khoản của nó bị Facebook chặn
    A._fb_campaign_statuses = lambda: (
        {"biet_bat": "on", "biet_tat": "off"}, ["TK chính: too many calls"])
    r = A._camp_nen_tat()
    ids = sorted(x["campaign_id"] for x in r["items"])
    assert ids == ["biet_bat", "chua_biet"], ids
    assert r["du_trang_thai"] is False


def test_khong_doc_duoc_trang_thai_thi_van_tra_va_bao():
    """Lấy trạng thái hỏng → không lọc (thà thừa còn hơn giấu), nhưng phải báo."""
    camps = {"a": _camp()}
    A._fb_price_map = lambda frm, to: {"a": _gia()}
    inbox_db.query_all = lambda **k: {"campaigns": camps}

    def _no():
        raise RuntimeError("token hỏng")
    A._fb_campaign_statuses = _no
    r = A._camp_nen_tat()
    assert len(r["items"]) == 1
    assert r["du_trang_thai"] is False


def test_bo_qua_hoi_thoai_chua_gan_quang_cao():
    camps = {"__no_ad__": _camp(ten="chưa link"), "a": _camp()}
    gia = {"__no_ad__": _gia(), "a": _gia()}
    r = _chay(camps, gia, {"__no_ad__": "on", "a": "on"})
    assert [x["campaign_id"] for x in r["items"]] == ["a"]


def test_loc_dung_mau_dang_tat():
    """Số thật 19/10 - vid gc lẻ mix mk - hcm (07/11/2026): 5 mẫu, 1 cái ROAS
    3,50 phải GIỮ, 4 cái còn lại vào danh sách tắt."""
    xau = A._mau_nen_tat([
        _mau("gốc", 1_732_767, 44, 0.00),
        _mau("bs1", 627_248, 5, 0.00),
        _mau("bs3", 580_038, 24, 3.50),      # đạt chuẩn → giữ
        _mau("bs2", 354_335, 5, 0.00),
        _mau("bs4", 35_705, 0, 0.00),        # tiêu quá ít → không xét
    ])
    assert [m["ad_id"] for m in xau] == ["gốc", "bs1", "bs2"]


def test_mau_tieu_qua_it_thi_khong_xet():
    """Dưới 200k thì tắt hay không cũng không đổi gì, mà ROAS lại rất nhiễu."""
    assert A._mau_nen_tat([_mau("ti", 50_000, 1, 0.0)]) == []
    assert A._mau_nen_tat([_mau("ti", 50_000, 1, 9.0)]) == []


def test_danh_sach_mau_gom_moi_chien_dich_va_xep_theo_tien():
    camps = {"c1": _camp(), "c2": _camp()}
    gia = {"c1": _gia(spend=3_330_093), "c2": _gia(spend=1_852_356)}
    theo_mau = {
        "c1": [_mau("to", 2_750_055, 54, 0.0), _mau("tot", 580_038, 24, 3.5)],
        "c2": [_mau("vua", 1_316_515, 68, 0.0), _mau("nho", 248_879, 13, 0.0)],
    }
    r = _chay(camps, gia, {"c1": "on", "c2": "on"}, theo_mau=theo_mau, gioi_han=10)
    assert [m["ad_id"] for m in r["maus"]] == ["to", "vua", "nho"]
    assert r["so_mau"] == 3
    assert r["chi_mau"] == 2_750_055 + 1_316_515 + 248_879


def test_danh_dau_khi_ca_chien_dich_deu_phai_tat():
    """Ngân sách ở cấp chiến dịch: tắt hết mẫu mà không tắt chiến dịch thì
    Facebook dồn tiền sang chỗ khác, không cắt được đồng nào."""
    camps = {"het": _camp(), "con": _camp()}
    gia = {"het": _gia(spend=1_500_000), "con": _gia(spend=1_500_000)}
    theo_mau = {
        "het": [_mau("a", 900_000, 40, 0.0), _mau("b", 600_000, 20, 0.0)],
        "con": [_mau("c", 900_000, 40, 0.0), _mau("d", 600_000, 20, 2.4)],
    }
    r = _chay(camps, gia, {"het": "on", "con": "on"}, theo_mau=theo_mau, gioi_han=10)
    ca = {m["ad_id"]: m["ca_camp"] for m in r["maus"]}
    assert ca == {"a": True, "b": True, "c": False}


def test_ma_duoi_de_tim_trong_trinh_quan_ly():
    """4 mẫu trong cùng chiến dịch trùng tên nhau, phải có mã mới phân biệt."""
    camps = {"c": _camp()}
    theo_mau = {"c": [_mau("120251611290650060", 900_000, 40, 0.0)]}
    r = _chay(camps, {"c": _gia()}, {"c": "on"}, theo_mau=theo_mau)
    assert r["maus"][0]["ma"] == "650060"


def test_cong_khong_bi_chot_chan_chung_nuot():
    """Chốt chặn đăng nhập ở đầu mọi lối vào phải CHỪA cổng này ra.

    Quên chừa là 401 ngay, mà 401 đó bắn trước cả phần kiểm khoá nên nhìn y
    hệt 'sai khoá' — đã mất một lượt dò 07/11/2026 vì đúng chỗ này.
    """
    os.environ["SANG_LIEC_TOKEN"] = "khoa-test"
    that = A._camp_nen_tat          # trả lại sau, kẻo test sau dùng nhầm bản giả
    A._camp_nen_tat = lambda *a, **k: {"items": [], "tong_camp": 0, "tong_chi": 0}
    try:
        c = A.app.test_client()

        r = c.get("/api/camp-nen-tat?token=khoa-test")
        assert r.status_code == 200, f"bị chặn: {r.status_code} {r.get_data(as_text=True)[:80]}"
        assert r.get_json().get("ok") is True

        r = c.get("/api/camp-nen-tat?token=sai")
        assert r.status_code == 401
        assert r.get_json().get("error") == "unauthorized"

        r = c.get("/api/camp-nen-tat")
        assert r.status_code == 401
    finally:
        A._camp_nen_tat = that


if __name__ == "__main__":
    loi = 0
    for ten, ham in sorted(globals().items()):
        if ten.startswith("test_") and callable(ham):
            try:
                ham()
                print(f"  ✓ {ten}")
            except AssertionError as e:
                loi += 1
                print(f"  ✗ {ten}: {e}")
            except Exception as e:
                loi += 1
                print(f"  ✗ {ten}: {type(e).__name__}: {e}")
    print("ĐẠT" if not loi else f"HỎNG {loi} test")
    sys.exit(1 if loi else 0)
