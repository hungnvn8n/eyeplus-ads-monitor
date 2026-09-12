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


def chi_so_doi_thu() -> list[dict]:
    """Bảng so sánh chỉ số theo đối thủ — suy ra từ chính dữ liệu quét, không
    cần thêm nguồn nào:

    - so_ad        : tổng quảng cáo bắt gặp
    - so_mau       : số MẪU nội dung khác nhau (ad/mẫu cao = nhân bản nhiều)
    - nhan_ban_tb  : trung bình mỗi mẫu được nhân thành bao nhiêu quảng cáo
    - moi_7ngay    : quảng cáo mới phát hiện trong 7 ngày (nhịp ra mẫu)
    - ty_le_video  : % mẫu dạng video (nội dung có mốc thời lượng "0:00")
    - tuoi_tb      : số ngày trung bình 1 quảng cáo được chạy (càng lâu càng
                     có thể là mẫu hiệu quả — họ không tắt)
    """
    return _rows("""
        SELECT doi_thu,
               COUNT(*)                                        AS so_ad,
               COUNT(DISTINCT noi_dung)                        AS so_mau,
               ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT noi_dung),0), 1) AS nhan_ban_tb,
               COUNT(*) FILTER (WHERE ngay_phat_hien >= now() - interval '7 days') AS moi_7ngay,
               ROUND(100.0 * COUNT(*) FILTER (WHERE noi_dung LIKE '%%0:00%%')
                     / NULLIF(COUNT(*),0))                     AS ty_le_video,
               ROUND(AVG(COALESCE(lan_cuoi_con_thay, CURRENT_DATE) - ngay_bat_dau_chay::date)) AS tuoi_tb
        FROM ci_quang_cao_fb
        GROUP BY doi_thu
        ORDER BY so_ad DESC
    """)


# Bộ từ khoá CHƯƠNG TRÌNH KHUYẾN MÃI của ngành kính mắt — gom các cách viết
# khác nhau về cùng 1 nhãn (đối thủ viết rất nhiều biến thể cho cùng 1 chiêu).
# Mỗi nhãn: (tên hiển thị, danh sách mẫu tìm — so khớp không phân biệt hoa/thường)
_TU_KHOA_KM: list[tuple[str, list[str]]] = [
    ("Tặng gọng 0Đ",        ["gọng 0đ", "gọng 0 đ", "tặng gọng", "free gọng", "free toàn bộ gọng", "gọng miễn phí"]),
    ("Thu cũ đổi mới",      ["thu cũ đổi mới", "đổi cũ lấy mới", "kính cũ", "lên đời", "trợ giá"]),
    # KHÔNG dùng mẫu trơ "%" — nó khớp cả "100% tia UV", "99% ánh sáng xanh"
    # (đặc tính sản phẩm, không phải khuyến mãi) → thổi phồng nhãn này.
    ("Giảm giá",             ["giảm giá", "sale ", "giảm đến", "giảm tới", "giảm ngay",
                              "ưu đãi đến", "ưu đãi tới", "deal ", "khuyến mãi"]),
    ("Combo / Mua kèm",     ["combo", "mua kèm", "3in1", "2in1", "trọn bộ"]),
    ("Back to school",      ["back to school", "năm học", "tựu trường", "campus", "học sinh", "sinh viên"]),
    ("Tròng đổi màu",       ["đổi màu", "photochromic", "kochi", "chuyển màu"]),
    ("Chống ánh sáng xanh", ["ánh sáng xanh", "asx", "blue light", "lọc ánh sáng"]),
    ("Kính râm / phân cực", ["kính râm", "kính mát", "sunglasses", "phân cực", "polarized"]),
    ("Gọng Titan",          ["titan", "titanium"]),
    ("Đo mắt miễn phí",     ["đo mắt miễn phí", "đo mắt free", "khám mắt miễn phí", "kiểm tra mắt miễn phí"]),
    # "0%" trơ cũng khớp "100%"/"90%" → chỉ nhận cụm nói rõ lãi suất
    ("Trả góp",             ["trả góp", "0% lãi", "lãi suất 0", "không lãi"]),
    ("Miễn phí vận chuyển", ["freeship", "miễn phí vận chuyển", "free ship"]),
    ("Bảo hành",            ["bảo hành", "1 đổi 1"]),
    ("KOL / Người nổi tiếng", ["meichan", "kol", "đại sứ", "x anna", "collab"]),
    ("Cắt kính cận",        ["cắt kính", "cắt tròng", "đo độ", "chuẩn độ"]),
]


def tu_khoa_km(doi_thu: str = "") -> list[dict]:
    """Đếm số MẪU quảng cáo có nhắc tới từng chương trình khuyến mãi/chủ đề.

    Đếm theo MẪU (nội dung khác nhau) chứ không theo ad_id — nếu đếm ad_id thì
    1 mẫu nhân bản 20 lần sẽ thổi phồng chủ đề đó lên 20 điểm, méo hoàn toàn
    bức tranh "đối thủ đang đánh chiêu gì".

    Trả list {nhan, so_mau, so_ad, doi_thu_chinh} — sắp nhiều nhất lên đầu,
    dùng dựng tag cloud (cỡ chữ theo so_mau).
    """
    where = "WHERE noi_dung IS NOT NULL AND noi_dung <> ''"
    params: list = []
    if doi_thu:
        where += " AND doi_thu = %s"
        params.append(doi_thu)
    rows = _rows(f"SELECT doi_thu, noi_dung, COUNT(*) AS so_ad FROM ci_quang_cao_fb "
                 f"{where} GROUP BY doi_thu, noi_dung", tuple(params))

    out = []
    for nhan, mau_list in _TU_KHOA_KM:
        so_mau = 0
        so_ad = 0
        theo_doi_thu: dict = {}
        for r in rows:
            low = (r["noi_dung"] or "").lower()
            if any(m in low for m in mau_list):
                so_mau += 1
                so_ad += int(r["so_ad"])
                theo_doi_thu[r["doi_thu"]] = theo_doi_thu.get(r["doi_thu"], 0) + 1
        if so_mau:
            chinh = max(theo_doi_thu.items(), key=lambda x: x[1])
            out.append({
                "nhan": nhan, "so_mau": so_mau, "so_ad": so_ad,
                "doi_thu_chinh": chinh[0], "doi_thu_chinh_so": chinh[1],
                "theo_doi_thu": sorted(theo_doi_thu.items(), key=lambda x: -x[1]),
            })
    out.sort(key=lambda x: -x["so_mau"])
    return out


def mxh() -> list[dict]:
    """Chỉ số mạng xã hội (TikTok) — tách follower/lượt thích từ mô tả kênh."""
    import re
    def _so(s: str) -> float | None:
        """'61.4k' → 61400 · '10.5m' → 10500000"""
        if not s:
            return None
        s = s.strip().lower().replace(",", "")
        mul = 1
        if s.endswith("k"):
            mul, s = 1_000, s[:-1]
        elif s.endswith("m"):
            mul, s = 1_000_000, s[:-1]
        try:
            return float(s) * mul
        except ValueError:
            return None

    out = []
    for r in _rows("SELECT * FROM ci_bai_dang_mxh ORDER BY doi_thu"):
        txt = r.get("noi_dung") or ""
        m_fol = re.search(r"([\d.,]+[km]?)\s*Follower", txt, re.I)
        m_like = re.search(r"([\d.,]+[km]?)\s*L[ưu]ợt th[íi]ch", txt, re.I)
        r["follower"] = _so(m_fol.group(1)) if m_fol else None
        r["luot_thich"] = _so(m_like.group(1)) if m_like else None
        out.append(r)
    return out


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
