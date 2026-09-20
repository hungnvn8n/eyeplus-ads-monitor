"""vung_camp — sổ tra vùng của từng chiến dịch, dùng chung cho cả app.

Vì sao cần (sự cố 20/09/2026)
=============================
Trước đây MỖI trang tự đoán vùng bằng cách dò chữ trong TÊN chiến dịch, mỗi
nơi một kiểu: trang Chiến dịch, trang Tổng quan, trang Hộp thư, và cả
`_parse_region` bên app.py. Chiến dịch HCM ghim quanh 7 cửa hàng theo bán
kính 5km mà tên không có chữ HCM thì cả bốn nơi đều dán nhãn "Toàn quốc" —
tiền HCM rơi khỏi mọi thẻ vùng.

Nay vùng lấy từ ĐỊA ĐIỂM NHẮM thật của Facebook (`vung_dia_ly`), ghi vào sổ
này một lần lúc kéo dữ liệu, rồi mọi trang cùng tra một chỗ.

Sổ nằm trong SQLite nên nhớ được qua các lần khởi động lại — trang Hộp thư
và nhật ký chỉnh sửa chỉ có tên chiến dịch trong tay, không gọi lại Facebook
để hỏi địa điểm nhắm được.
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

MA_HOP_LE = ("HN", "HCM", "BN", "HP")


def _duong_dan() -> Path:
    """Dùng ổ /data khi có (Railway) để sổ bền qua mỗi lần deploy."""
    try:
        vol = Path("/data")
        goc = vol if vol.exists() and vol.is_dir() else Path(__file__).resolve().parent
    except Exception:
        goc = Path(__file__).resolve().parent
    return goc / "shadow.db"


def _mo():
    conn = sqlite3.connect(_duong_dan(), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vung_chien_dich (
            campaign_id TEXT PRIMARY KEY,
            vung        TEXT NOT NULL,
            cap_nhat    TEXT DEFAULT (datetime('now'))
        )
    """)
    return conn


def ghi_nho_tu_ads(ads: list) -> int:
    """Nhặt vùng thật từ list ad (fetcher đã điền `vung`) rồi lưu vào sổ.

    Một chiến dịch có nhiều nhóm quảng cáo; nếu các nhóm nhắm khác vùng nhau
    thì KHÔNG lưu — để trang tra ra rỗng và hiển thị "Toàn quốc", đúng hơn là
    gán bừa một vùng.
    """
    theo_camp: dict = {}
    for a in ads or []:
        cid = str(a.get("campaign_id") or "")
        v = (a.get("vung") or "").strip()
        if not cid or v not in MA_HOP_LE:
            continue
        theo_camp.setdefault(cid, set()).add(v)

    can_ghi = [(cid, next(iter(vs))) for cid, vs in theo_camp.items() if len(vs) == 1]
    if not can_ghi:
        return 0
    try:
        conn = _mo()
        with conn:
            conn.executemany(
                "INSERT INTO vung_chien_dich (campaign_id, vung) VALUES (?,?) "
                "ON CONFLICT(campaign_id) DO UPDATE SET "
                "vung=excluded.vung, cap_nhat=datetime('now')",
                can_ghi,
            )
        conn.close()
        return len(can_ghi)
    except Exception as e:
        logger.warning(f"vung_camp.ghi_nho_tu_ads bỏ qua lỗi: {e}")
        return 0


def so_tra() -> dict:
    """Toàn bộ sổ {campaign_id: vùng}."""
    try:
        conn = _mo()
        rows = conn.execute("SELECT campaign_id, vung FROM vung_chien_dich").fetchall()
        conn.close()
        return {str(c): v for c, v in rows}
    except Exception as e:
        logger.warning(f"vung_camp.so_tra lỗi: {e}")
        return {}


def tra(campaign_id: str, ten_du_phong: str = "") -> str:
    """Vùng của một chiến dịch. Chưa có trong sổ thì mới dò tên (lối lui)."""
    cid = str(campaign_id or "")
    if cid:
        try:
            conn = _mo()
            r = conn.execute(
                "SELECT vung FROM vung_chien_dich WHERE campaign_id = ?", (cid,)
            ).fetchone()
            conn.close()
            if r and r[0]:
                return r[0]
        except Exception:
            pass
    return do_ten(ten_du_phong)


def do_ten(ten: str) -> str:
    """LỐI LUI — dò chữ trong tên chiến dịch. Chỉ dùng khi sổ chưa có.

    Giữ lại vì nhật ký chỉnh sửa và hộp thư đôi khi gặp chiến dịch quá cũ,
    đã xoá khỏi Facebook nên không hỏi được địa điểm nhắm nữa.
    """
    import re
    u = (ten or "").upper()
    if not u:
        return ""

    def co(*mau):
        return any(re.search(r"(?:^|[^A-Za-z])(?:" + m + r")(?:[^A-Za-z]|$)", u)
                   for m in mau)

    if co("HCM", "SG", "SAIGON") or re.search(r"SÀI GÒN|SAI GON|TP[ _]*HCM", u):
        return "HCM"
    if co("HP") or re.search(r"HẢI PHÒNG|HAI PHONG", u):
        return "HP"
    if co("BN") or re.search(r"BẮC NINH|BAC NINH", u):
        return "BN"
    if co("HN", "HANOI") or re.search(r"HÀ NỘI|HA NOI", u):
        return "HN"
    return ""
