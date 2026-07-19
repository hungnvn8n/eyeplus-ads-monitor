"""Classifier theo Rule v4 (2026-07-19).

Dùng ROAS CHÍNH THỨC của Facebook (số trên Ads Manager), KHÔNG nhân hệ số
nội bộ nào — để một con số duy nhất, ai cũng tự kiểm tra được, dễ quản lý.

Mức ĐẠT = 2,0 (rút từ dữ liệu thật: trung vị ROAS FB 49 ngày 01/6–19/7 = 1,92,
đúng lúc %chi ads nằm chuẩn 13,4%). Mức ưu tiên tăng = 2,3 (nhóm 25% cao nhất).

Rule chốt:
- Trạm 1: spend ≥ 200K → đánh giá
- Trạm 2: spend ≥ 500K → đánh giá chắc hơn
- TĂNG  : ROAS FB ≥ 2.3
- GIỮ   : 2.0 ≤ ROAS FB < 2.3 (đạt)
- TẮT   : ROAS FB < 2.0 (chưa đạt)
- SKIP  : spend < 200K (chưa đủ ngưỡng)
"""

TRAM1_SPEND = 200_000    # ngưỡng Trạm 1
TRAM2_SPEND = 500_000    # ngưỡng Trạm 2
PASS_ROAS = 2.0          # mức ĐẠT (ROAS Facebook chính thức)
SCALE_ROAS = 2.3         # mức ưu tiên tăng (nhóm 25% cao nhất lịch sử)
KEEP_ROAS = PASS_ROAS    # alias giữ tương thích các module import cũ

# Giữ BOFU_TAGS để backward-compat (dùng cho labeling, không ảnh hưởng grade)
BOFU_TAGS = ("CT3", "CT4", "CT5", "CT6")


def classify(ad: dict) -> str:
    """Trả 'bofu' nếu tên có CT3/CT4/CT5/CT6, else 'tofu'.

    Match nếu không bị ráp vào chữ/số ở 2 đầu (vd "CT30" KHÔNG match "CT3",
    nhưng "_CT3_" hoặc " CT3 " hoặc "CT3" ở cuối ĐỀU match).
    """
    import re
    text = " ".join([
        ad.get("campaign_name", "") or "",
        ad.get("adset_name", "") or "",
        ad.get("ad_name", "") or "",
    ]).upper()
    for tag in BOFU_TAGS:
        # (?<![A-Z0-9]) = không có chữ/số ngay trước; (?![A-Z0-9]) = không có chữ/số ngay sau
        if re.search(rf"(?<![A-Z0-9]){re.escape(tag)}(?![A-Z0-9])", text):
            return "bofu"
    return "tofu"


def evaluate(ad: dict, tier: str, cfg: dict) -> tuple[str, str]:
    """Trả (action, reason). action ∈ {'GIỮ', 'TẮT', 'SKIP'}.

    Rule v4: dùng ROAS Facebook chính thức, mức đạt 2,0 · ưu tiên tăng 2,3.
    Tham số cfg vẫn nhận để backward-compat nhưng không dùng cho thresholds.
    """
    spend = float(ad.get("spend") or 0)
    roas = float(ad.get("roas") or 0)   # ROAS Facebook chính thức

    if spend < TRAM1_SPEND:
        return "SKIP", f"Chi {spend:,.0f}đ < Trạm 1 ({TRAM1_SPEND:,}đ)"

    tram = "Trạm 2" if spend >= TRAM2_SPEND else "Trạm 1"

    if roas < PASS_ROAS:
        return "TẮT", f"{tram} · ROAS FB {roas:.2f} < {PASS_ROAS} (chưa đạt)"
    if roas < SCALE_ROAS:
        return "GIỮ", f"{tram} · ROAS FB {roas:.2f} ≥ {PASS_ROAS} (đạt)"
    return "GIỮ", f"{tram} · ROAS FB {roas:.2f} ≥ {SCALE_ROAS} → ưu tiên tăng"


def grade(ad: dict, tier: str, action: str, cfg: dict) -> str:
    """Phân loại hiệu quả: 'special' / 'good' / 'bad' / 'skip'.

    Rule v4 (ROAS Facebook chính thức):
    - special: ROAS FB ≥ SCALE_ROAS (2.3) → ưu tiên tăng
    - good:    PASS_ROAS ≤ ROAS FB < SCALE_ROAS → đạt, giữ
    - bad:     ROAS FB < PASS_ROAS → chưa đạt, tắt
    - skip:    spend < Trạm 1 (200K)
    """
    if action == "TẮT":
        return "bad"
    if action == "SKIP":
        return "skip"

    roas = float(ad.get("roas") or 0)
    if roas >= SCALE_ROAS:
        return "special"
    return "good"


# ─── Auto-pause rules (app tự động gọi FB API tắt ad) ─────────────────────────
# Chỉ rule SIÊU rõ ràng mới được auto-pause. Mọi case khác → user tự xử lý.

def evaluate_rule(rule: dict, ad: dict, tier: str) -> bool:
    """Check 1 rule có match ad không. Mọi điều kiện non-null phải pass."""
    if not rule.get("enabled", True):
        return False
    tier_filter = rule.get("tier_filter")
    if tier_filter and tier_filter != "any" and tier_filter != tier:
        return False

    spend = float(ad.get("spend") or 0)
    cpm = float(ad.get("cost_per_message") or 0)
    roas = float(ad.get("roas") or 0)
    purchases = int(ad.get("purchases") or 0)

    # spend > X
    sv = rule.get("spend_gt")
    if sv is not None and not (spend > float(sv)):
        return False
    # cost_per_message > X
    cv = rule.get("cpm_gt")
    if cv is not None and not (cpm > float(cv)):
        return False
    # roas < X
    rv = rule.get("roas_lt")
    if rv is not None and not (roas < float(rv)):
        return False
    # purchases <= X (0 = không đơn nào)
    pv = rule.get("purchases_max")
    if pv is not None and not (purchases <= int(pv)):
        return False
    return True


def matching_rule(rules: list, ad: dict, tier: str) -> dict | None:
    """Trả rule đầu tiên match ad (theo thứ tự rules list)."""
    for rule in rules or []:
        if evaluate_rule(rule, ad, tier):
            return rule
    return None


# Legacy single-bool API — fallback to default rules nếu không pass rules vào
DEFAULT_AUTO_PAUSE_RULES = [
    {
        "id": "tofu_no_roas",
        "name": "TOFU — Chi nhiều nhưng không có ROAS",
        "enabled": True,
        "tier_filter": "tofu",
        "spend_gt": 200_000,
        "cpm_gt": 60_000,
        "roas_lt": None,
        "purchases_max": 0,
    },
    {
        "id": "bofu_low_roas",
        "name": "BOFU — Chi nhiều, mess đắt, ROAS thấp",
        "enabled": True,
        "tier_filter": "bofu",
        "spend_gt": 200_000,
        "cpm_gt": 100_000,
        "roas_lt": 2.0,
        "purchases_max": None,
    },
]


def auto_pause_decision(ad: dict, tier: str, cfg: dict, rules: list | None = None) -> bool:
    """Trả True nếu ad match BẤT KỲ enabled rule nào."""
    if rules is None:
        rules = DEFAULT_AUTO_PAUSE_RULES
    return matching_rule(rules, ad, tier) is not None
