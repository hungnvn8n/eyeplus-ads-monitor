"""Dòng phụ ô "Tỉ lệ SĐT xin được" trên màn hình TV — tách rõ Facebook và TikTok.

CEO yêu cầu 20/10/2026 vì bản cũ gộp "32/190 · 3 kênh", nhìn vào không biết kênh nào
đang kéo tỉ lệ xuống.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sang_liec as s


def test_du_hai_kenh_thi_ghi_ca_so_lan_ti_le():
    assert s._sub_sdt(28, 150, 4, 40) == "FB 28/150 (18.7%) · TikTok 4/40 (10.0%)"


def test_tiktok_rong_thi_noi_ro_chua_co_so():
    """KHÔNG được ghi 'TikTok 0/0 (0.0%)' — nhìn như TikTok bết, thật ra là thiếu số."""
    out = s._sub_sdt(28, 150, 0, 0)
    assert "0/0" not in out
    assert out == "FB 28/150 (18.7%) · TikTok chưa có số"


def test_khong_lay_duoc_tiktok_thi_im_lang():
    """Khác với 'lấy được nhưng rỗng' — chưa gọi được thì đừng hứa gì."""
    assert s._sub_sdt(28, 150, 0, 0, co_tiktok=False) == "FB 28/150 (18.7%)"


def test_chi_co_tiktok_thi_bo_han_phan_fb():
    assert s._sub_sdt(0, 0, 4, 40) == "TikTok 4/40 (10.0%)"


def test_khong_co_gi_thi_tra_chuoi_rong():
    """metrics() thấy chuỗi rỗng sẽ tự quay về cách ghi gộp cũ."""
    assert s._sub_sdt(0, 0, 0, 0, co_tiktok=False) == ""


def test_khong_chia_cho_khong():
    for a in [(0, 0, 0, 0), (5, 0, 0, 0), (0, 0, 5, 0)]:
        s._sub_sdt(*a)       # không được văng ZeroDivisionError


def test_ti_le_tren_100_van_ghi_duoc():
    """SĐT nhiều hơn khách mới là có thật (một khách nhắn nhiều lần vẫn cho số)."""
    assert s._sub_sdt(12, 10, 0, 0, co_tiktok=False) == "FB 12/10 (120.0%)"
