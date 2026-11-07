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


def test_con_mau_dat_chuan_thi_khuyen_TAT_LE():
    """Số thật 19/10 - vid gc lẻ mix mk - hcm (07/11/2026): ROAS chung 0,67
    nhưng bên trong có một mẫu ROAS 3,85. Khuyên tắt cả là giết nhầm mẫu đó."""
    r = A._xu_ly_the_nao([
        _mau("gốc", 1_732_767, 44, 0.00),
        _mau("bs1", 627_248, 5, 0.00),
        _mau("bs3", 580_038, 24, 3.85),      # mẫu tốt
        _mau("bs2", 354_335, 5, 0.00),
        _mau("bs4", 35_705, 0, 0.00),
    ])
    assert r["cach"] == "tat_le"
    assert r["so_mau"] == 5 and r["so_mau_tot"] == 1
    assert r["mau_tot"]["roas"] == 3.85
    # tiền đang chảy vào 4 mẫu không ra đồng nào
    assert r["chi_mau_yeu"] == 1_732_767 + 627_248 + 354_335 + 35_705


def test_moi_mau_deu_yeu_thi_khuyen_TAT_CA():
    """Số thật 7/10 - vid gc lẻ mix - hn nữ: 4 mẫu, 91 tin, 0 đơn ở cả 4."""
    r = A._xu_ly_the_nao([
        _mau("a", 1_316_515, 68, 0.00), _mau("b", 248_879, 13, 0.00),
        _mau("c", 180_491, 5, 0.00), _mau("d", 106_471, 5, 0.00),
    ])
    assert r["cach"] == "tat_ca"
    assert r["so_mau_tot"] == 0 and r["mau_tot"] is None


def test_mau_tot_nhung_tieu_qua_it_thi_khong_tinh():
    """Mẫu mới tiêu 50k mà ROAS 9 là một đơn may mắn, không phải mẫu tốt —
    tin nó thì tắt nhầm cả 3 mẫu kia để giữ một cái ảo."""
    r = A._xu_ly_the_nao([
        _mau("to", 2_000_000, 60, 0.3),
        _mau("ti", 50_000, 1, 9.0),
    ])
    assert r["cach"] == "tat_ca", r


def test_khong_boc_duoc_mau_thi_coi_nhu_tat_ca():
    """Chiến dịch không có mẫu nào trong cache (lấy số dở dang) — không bịa."""
    r = A._xu_ly_the_nao([])
    assert r["cach"] == "tat_ca" and r["so_mau"] == 0


def test_cach_xu_ly_di_kem_tung_dong_va_tong_ket():
    camps = {"cuu": _camp(), "bo": _camp()}
    gia = {"cuu": _gia(spend=3_330_093), "bo": _gia(spend=1_852_356)}
    theo_mau = {
        "cuu": [_mau("x", 2_750_055, 54, 0.0), _mau("y", 580_038, 24, 3.85)],
        "bo":  [_mau("z", 1_852_356, 91, 0.0)],
    }
    r = _chay(camps, gia, {"cuu": "on", "bo": "on"}, theo_mau=theo_mau)
    cach = {x["campaign_id"]: x["cach"] for x in r["items"]}
    assert cach == {"cuu": "tat_le", "bo": "tat_ca"}
    assert r["so_tat_le"] == 1 and r["so_tat_ca"] == 1
    assert r["chi_tat_ca"] == 1_852_356          # tắt cả thì tiết kiệm được ngần này
    assert r["chi_mau_yeu_tat_le"] == 2_750_055  # tiền đang chảy sai chỗ


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
