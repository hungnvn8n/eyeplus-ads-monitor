"""vung_dia_ly — đọc vùng thật của chiến dịch từ phần nhắm địa điểm của Facebook.

Lý do file này tồn tại (sự cố 20/09/2026)
=========================================
Facebook để địa điểm nhắm ở BỐN chỗ khác nhau trong `geo_locations`:

    cities            chọn nguyên thành phố
    regions           chọn nguyên tỉnh
    places            ghim địa chỉ + bán kính (ví dụ "Nguyễn Cư Trinh, Quận 1
                      Tphcm" bán kính 5km) — Facebook gọi là "địa điểm đã lưu"
    custom_locations  ghim toạ độ + bán kính, không có tên

Code cũ chỉ đọc `cities` và `regions`. Các chiến dịch của HCM lại ghim đúng
7 địa chỉ cửa hàng theo bán kính 5km, tức nằm ở `places` — nên đọc không ra,
rơi xuống dò tên chiến dịch, mà tên cũng không có chữ HCM. Kết quả: bị dán
nhãn "Toàn quốc" và KHÔNG được cộng vào bất kỳ vùng nào trong báo cáo.
Tính riêng 01–19/09/2026 là 8,46 triệu tiền quảng cáo HCM biến mất khỏi
báo cáo vùng.

Cách nhận vùng, theo thứ tự tin cậy giảm dần:
  1. mã thành phố Facebook (primary_city_id) — chắc nhất
  2. toạ độ rơi vào khung của vùng nào — dùng cho điểm ghim không có mã
  3. tên địa điểm có chứa từ khoá vùng

Toạ độ lấy làm chuẩn vì không phụ thuộc người đặt tên. Khung của bốn vùng
không chồng lên nhau: HCM cách xa phía nam; Hà Nội cắt với Bắc Ninh ở kinh
độ 106,02; Hải Phòng bắt đầu từ kinh độ 106,45 nên không đụng Bắc Ninh.
"""

# Mã thành phố của Facebook. Chỉ điền mã đã tự tay xác minh trong dữ liệu thật.
MA_THANH_PHO = {
    2590531: "HCM",   # xác minh 20/09/2026 từ 7 điểm ghim cửa hàng HCM
}

# Khung toạ độ từng vùng: (vĩ độ nhỏ, vĩ độ lớn, kinh độ nhỏ, kinh độ lớn)
KHUNG_TOA_DO = {
    "HCM": (10.35, 11.20, 106.35, 107.05),
    "HN":  (20.85, 21.39, 105.28, 106.02),
    "BN":  (20.95, 21.42, 106.02, 106.35),
    "HP":  (20.55, 21.05, 106.45, 107.10),
}

TU_KHOA_TEN = {
    "HCM": ["TPHCM", "TP HCM", "HỒ CHÍ MINH", "HO CHI MINH", "SÀI GÒN", "SAI GON", "SAIGON"],
    "HN":  ["HÀ NỘI", "HA NOI", "HANOI"],
    "BN":  ["BẮC NINH", "BAC NINH"],
    "HP":  ["HẢI PHÒNG", "HAI PHONG", "HAIPHONG"],
}


def vung_tu_toa_do(lat, lng):
    """Toạ độ rơi vào khung của vùng nào. Không khớp khung nào thì trả None."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    for ma, (v_min, v_max, k_min, k_max) in KHUNG_TOA_DO.items():
        if v_min <= lat <= v_max and k_min <= lng <= k_max:
            return ma
    return None


def _vung_tu_ten(ten):
    ten = (ten or "").upper()
    if not ten:
        return None
    for ma, tu_khoa in TU_KHOA_TEN.items():
        if any(tk in ten for tk in tu_khoa):
            return ma
    return None


def _doc_diem_ghim(muc):
    """Một điểm ghim (places / custom_locations) → mã vùng, hoặc None."""
    if not isinstance(muc, dict):
        return None
    ma_tp = muc.get("primary_city_id")
    if ma_tp in MA_THANH_PHO:
        return MA_THANH_PHO[ma_tp]
    ma = vung_tu_toa_do(muc.get("latitude"), muc.get("longitude"))
    if ma:
        return ma
    return _vung_tu_ten(muc.get("name"))


def vung_tu_geo(geo_locations):
    """Trả tập mã vùng ('HN','HCM','BN','HP') đọc được từ geo_locations.

    Đọc đủ bốn chỗ: cities, regions, places, custom_locations.
    Không tìm thấy gì → tập rỗng (nghĩa là nhắm toàn quốc, hoặc nơi khác).
    """
    geo = geo_locations or {}
    if not isinstance(geo, dict):
        return set()
    tim_thay = set()

    for khoa in ("cities", "regions"):
        for muc in geo.get(khoa) or []:
            if not isinstance(muc, dict):
                continue
            ma = _vung_tu_ten(muc.get("name"))
            if ma:
                tim_thay.add(ma)

    for khoa in ("places", "custom_locations"):
        for muc in geo.get(khoa) or []:
            ma = _doc_diem_ghim(muc)
            if ma:
                tim_thay.add(ma)

    return tim_thay
