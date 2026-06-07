"""Classifier 2 tầng + evaluator.

Rule chốt 2026-06-07:
- BoFu: tên campaign/adset/ad có CT3, CT4, CT5, hoặc CT6 → Mess ≤ 100K VÀ ROAS ≥ 3.0 → GIỮ
- ToFu: phần còn lại → Mess ≤ 50K → GIỮ
"""

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
    """Trả (action, reason). action ∈ {'GIỮ', 'TẮT', 'SKIP'}."""
    spend = float(ad.get("spend") or 0)
    messages = int(ad.get("messages") or 0)
    cost_per_msg = int(ad.get("cost_per_message") or 0)
    roas = float(ad.get("roas") or 0)

    if tier == "tofu":
        min_spend = cfg["tofu_min_spend"]
        mess_max = cfg["tofu_mess_max"]
        if spend < min_spend:
            return "SKIP", f"Chi {spend:,.0f}đ < tối thiểu {min_spend:,.0f}đ"
        if messages == 0:
            return "TẮT", f"0 Mess sau khi chi {spend:,.0f}đ"
        if cost_per_msg <= mess_max:
            return "GIỮ", f"Mess {cost_per_msg:,.0f}đ ≤ {mess_max:,.0f}đ"
        return "TẮT", f"Mess {cost_per_msg:,.0f}đ > {mess_max:,.0f}đ"

    # bofu — cần CẢ Mess OK lẫn ROAS đạt
    min_spend = cfg["bofu_min_spend"]
    mess_max = cfg["bofu_mess_max"]
    roas_min = cfg["bofu_roas_min"]

    if spend < min_spend:
        return "SKIP", f"Chi {spend:,.0f}đ < tối thiểu {min_spend:,.0f}đ"
    if messages == 0:
        return "TẮT", f"0 Mess sau khi chi {spend:,.0f}đ"
    if cost_per_msg > mess_max:
        return "TẮT", f"Mess {cost_per_msg:,.0f}đ > {mess_max:,.0f}đ"
    if roas < roas_min:
        return "TẮT", f"Mess OK ({cost_per_msg:,.0f}đ) nhưng ROAS {roas:.2f} < {roas_min}"
    return "GIỮ", f"Mess {cost_per_msg:,.0f}đ ≤ {mess_max:,.0f}đ VÀ ROAS {roas:.2f} ≥ {roas_min}"


def grade(ad: dict, tier: str, action: str, cfg: dict) -> str:
    """Phân loại hiệu quả: 'special' / 'good' / 'bad' / 'skip'.

    - special: ad đạt rule VÀ vượt xa ngưỡng (champion)
    - good:    đạt rule, ở mức bình thường
    - bad:     vi phạm rule
    - skip:    chưa đủ data
    """
    if action == "TẮT":
        return "bad"
    if action == "SKIP":
        return "skip"

    messages = int(ad.get("messages") or 0)
    cost_per_msg = int(ad.get("cost_per_message") or 0)
    roas = float(ad.get("roas") or 0)

    if tier == "tofu":
        # Đặc biệt: chi phí mess CỰC THẤP (≤ 50% ngưỡng) + volume đủ
        half_max = cfg["tofu_mess_max"] * 0.5
        if cost_per_msg > 0 and cost_per_msg <= half_max and messages >= 3:
            return "special"
        return "good"

    # bofu: đặc biệt nếu ROAS gấp ~1.67x ngưỡng (≥5.0 mặc định)
    roas_excellent = cfg["bofu_roas_min"] * 5.0 / 3.0  # 5.0 nếu ngưỡng = 3.0
    if roas >= roas_excellent:
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
