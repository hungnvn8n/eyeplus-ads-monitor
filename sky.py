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
