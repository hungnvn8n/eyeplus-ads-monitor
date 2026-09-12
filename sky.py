"""Sky — Competitor Intelligence (theo dõi đối thủ chuỗi kính mắt).

Đọc 6 bảng ci_* trong kho Postgres dùng chung (nạp từ file SQL export
"competitor-intel-postgres.sql", 12/09/2026). Đây là bảng READ-ONLY —
dữ liệu được nạp/cập nhật bằng cách chạy lại file SQL export đó (chỉ
CREATE IF NOT EXISTS + INSERT ON CONFLICT, an toàn không mất dữ liệu cũ).

Đối thủ theo dõi: Anna, HMK (khớp bộ theo dõi đã chốt — xem
project_eyeplus_competitors_chains).
"""
import inbox_db


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with inbox_db._conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def overview() -> dict:
    """Tổng quan: số ad đang chạy theo đối thủ, tin tức gần đây, biến động."""
    by_doi_thu = _rows("""
        SELECT doi_thu, COUNT(*) AS so_ad,
               MAX(ngay_phat_hien) AS lan_phat_hien_gan_nhat
        FROM ci_quang_cao_fb GROUP BY doi_thu ORDER BY so_ad DESC
    """)
    so_ad_theo_ngay = _rows("""
        SELECT ngay, thuong_hieu, so_ad FROM ci_so_ad_theo_ngay
        ORDER BY ngay ASC
    """)
    return {"by_doi_thu": by_doi_thu, "so_ad_theo_ngay": so_ad_theo_ngay}


def mau_lap_lai(doi_thu: str = "", limit: int = 100) -> list[dict]:
    """Gom quảng cáo theo MẪU NỘI DUNG giống hệt nhau (đối thủ nhân bản 1 mẫu
    thành nhiều ad_id khác nhau — thường để test target/placement khác nhau
    mà FB Ad Library liệt kê thành từng dòng riêng). Trả về số lượng quảng
    cáo đang trỏ vào cùng 1 mẫu, sắp xếp nhiều nhất lên đầu.
    """
    sql = """
        SELECT doi_thu, noi_dung, COUNT(*) AS so_luong,
               MIN(ngay_bat_dau_chay) AS chay_som_nhat,
               MAX(lan_cuoi_con_thay) AS con_thay_gan_nhat,
               (array_agg(anh_video ORDER BY ngay_bat_dau_chay ASC NULLS LAST))[1] AS anh,
               (array_agg(link_ad_library ORDER BY ngay_bat_dau_chay ASC NULLS LAST))[1] AS link_dau_tien
        FROM ci_quang_cao_fb
        WHERE noi_dung IS NOT NULL AND noi_dung <> ''
    """
    params: list = []
    if doi_thu:
        sql += " AND doi_thu = %s"
        params.append(doi_thu)
    sql += """
        GROUP BY doi_thu, noi_dung
        HAVING COUNT(*) >= 2
        ORDER BY so_luong DESC
        LIMIT %s
    """
    params.append(limit)
    return _rows(sql, tuple(params))


def quang_cao_fb(doi_thu: str = "", q: str = "", limit: int = 200) -> list[dict]:
    """Danh sách quảng cáo FB đối thủ — lọc theo đối thủ + từ khoá nội dung."""
    sql = "SELECT * FROM ci_quang_cao_fb WHERE 1=1"
    params: list = []
    if doi_thu:
        sql += " AND doi_thu = %s"
        params.append(doi_thu)
    if q:
        sql += " AND noi_dung ILIKE %s"
        params.append(f"%{q}%")
    sql += " ORDER BY ngay_bat_dau_chay DESC NULLS LAST LIMIT %s"
    params.append(limit)
    return _rows(sql, tuple(params))


def tin_tuc(limit: int = 100) -> list[dict]:
    return _rows("SELECT * FROM ci_tin_tuc ORDER BY ngay_dang DESC NULLS LAST LIMIT %s", (limit,))


def nhac_den_ben_ngoai(limit: int = 100) -> list[dict]:
    return _rows("SELECT * FROM ci_nhac_den_ben_ngoai ORDER BY ngay_phat_hien DESC NULLS LAST LIMIT %s", (limit,))


def bai_dang_mxh(limit: int = 100) -> list[dict]:
    return _rows("SELECT * FROM ci_bai_dang_mxh ORDER BY ngay_dang DESC NULLS LAST LIMIT %s", (limit,))


def thay_doi_website(limit: int = 100) -> list[dict]:
    return _rows("SELECT * FROM ci_thay_doi_website ORDER BY ngay DESC NULLS LAST LIMIT %s", (limit,))
