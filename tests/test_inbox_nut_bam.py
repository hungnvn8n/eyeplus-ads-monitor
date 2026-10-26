"""Lọc câu bấm sẵn khỏi các chỉ số inbox."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inbox_db


def test_nut_bam_tra_list():
    """Thiếu DB thì trả [] chứ không nổ — chỉ số vẫn chạy, chỉ là không lọc."""
    inbox_db._NUT_BAM_CACHE = (0.0, [])
    assert isinstance(inbox_db.nut_bam(), list)


def test_cache_giu_gia_tri(monkeypatch):
    """Đã có cache còn hạn thì không hỏi DB lại."""
    import time
    inbox_db._NUT_BAM_CACHE = (time.time(), ["Bắt đầu"])
    monkeypatch.setattr(inbox_db, "_conn", lambda: (_ for _ in ()).throw(AssertionError("không được hỏi DB")))
    assert inbox_db.nut_bam() == ["Bắt đầu"]


def test_cac_cau_dem_nhan_deu_loc_nut_bam():
    """3 chỗ đếm nhãn phải cùng loại câu bấm sẵn, nếu không số sẽ lệch nhau."""
    import inspect
    for fn in (inbox_db.campaign_quality, inbox_db.query_all):
        src = inspect.getsource(fn)
        assert "nut_bam()" in src, f"{fn.__name__} chưa lọc câu bấm sẵn"


def test_dia_chi_loai_dung_cau():
    """Câu bấm sẵn phải là tin MỞ ĐẦU — 'Hcm' khách tự gõ không được lọc."""
    import inspect
    src = inspect.getsource(inbox_db.nut_bam)
    assert "inbox_nut_bam" in src          # đọc từ Cài đặt, không gõ cứng
