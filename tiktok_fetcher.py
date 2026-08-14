"""Fetch campaign/ad-level insights từ TikTok Ads cho Eye Plus.

Token đọc từ env: TIKTOK_ACCESS_TOKEN (hoặc TIKTOK_CAPI_ACCESS_TOKEN làm fallback).
Advertiser IDs đọc từ env: TIKTOK_ADVERTISER_IDS (comma-separated).
"""

import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

import requests

TT_BASE = "https://business-api.tiktok.com/open_api/v1.3"

# TikTok giới hạn 10 request/giây/app — van tiết lưu toàn cục giữ dưới ngưỡng
_MAX_QPS = 8
_rl_lock = threading.Lock()
_rl_times: deque = deque()


def _throttle() -> None:
    while True:
        with _rl_lock:
            now = time.time()
            while _rl_times and now - _rl_times[0] > 1.0:
                _rl_times.popleft()
            if len(_rl_times) < _MAX_QPS:
                _rl_times.append(now)
                return
            wait = 1.0 - (now - _rl_times[0]) + 0.02
        time.sleep(max(wait, 0.02))


def _token() -> str:
    return (
        os.environ.get("TIKTOK_ACCESS_TOKEN", "")
        or os.environ.get("TIKTOK_CAPI_ACCESS_TOKEN", "")
    ).strip()


def _advertiser_ids() -> list[str]:
    raw = os.environ.get("TIKTOK_ADVERTISER_IDS", "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []


def _get(path: str, params: dict) -> dict:
    token = _token()
    if not token:
        return {"error": "Thiếu TIKTOK_ACCESS_TOKEN"}
    d: dict = {}
    for attempt in range(3):
        _throttle()
        try:
            r = requests.get(
                f"{TT_BASE}{path}",
                headers={"Access-Token": token},
                params=params,
                timeout=30,
            )
            d = r.json()
        except Exception as e:
            return {"error": str(e)}
        # Dính QPS limit → chờ rồi thử lại (tối đa 2 lần)
        if "QPS" not in str(d.get("message", "")):
            return d
        time.sleep(1.2 * (attempt + 1))
    return d


def _post(path: str, payload: dict) -> dict:
    """POST lệnh ghi lên TikTok (bật/tắt). Body là JSON, không phải form."""
    token = _token()
    if not token:
        return {"code": -1, "message": "Thiếu TIKTOK_ACCESS_TOKEN"}
    for attempt in range(3):
        _throttle()
        try:
            r = requests.post(
                f"{TT_BASE}{path}",
                headers={"Access-Token": token, "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            d = r.json()
        except Exception as e:
            return {"code": -1, "message": str(e)}
        if "QPS" not in str(d.get("message", "")):
            return d
        time.sleep(1.2 * (attempt + 1))
    return d


# 3 cấp bật/tắt: chiến dịch → nhóm quảng cáo → quảng cáo.
# Mỗi cấp một đường dẫn + một tên trường ID riêng, còn cách gọi thì giống hệt nhau.
_LEVELS = {
    "campaign": ("/campaign/status/update/", "campaign_ids", "chiến dịch"),
    "adgroup":  ("/adgroup/status/update/",  "adgroup_ids",  "nhóm quảng cáo"),
    "ad":       ("/ad/status/update/",       "ad_ids",       "quảng cáo"),
}


def set_status(level: str, advertiser_id: str, ids: list,
               operation_status: str) -> dict:
    """Bật/Tắt THẬT trên TikTok ở cấp chiến dịch / nhóm quảng cáo / quảng cáo.

    operation_status: ENABLE | DISABLE. Trả {"ok": bool, "error": str}.
    TikTok cho tối đa 20 đối tượng mỗi lệnh.

    KHÔNG hỗ trợ DELETE — xoá là không hoàn tác được; ai thực sự cần thì vào
    TikTok Ads Manager tự làm.
    """
    if level not in _LEVELS:
        return {"ok": False, "error": f"Cấp không hợp lệ: {level}"}
    if operation_status not in ("ENABLE", "DISABLE"):
        return {"ok": False, "error": "Trạng thái phải là ENABLE hoặc DISABLE"}
    path, id_field, label = _LEVELS[level]
    clean = [str(x) for x in ids if x][:20]
    if not clean or not advertiser_id:
        return {"ok": False, "error": f"Thiếu ID {label} hoặc advertiser_id"}
    d = _post(path, {
        "advertiser_id": str(advertiser_id),
        id_field: clean,
        "operation_status": operation_status,
    })
    if d.get("code") != 0:
        return {"ok": False, "error": d.get("message") or f"TikTok trả mã {d.get('code')}"}
    return {"ok": True}


def set_campaign_status(advertiser_id: str, campaign_ids: list,
                        operation_status: str) -> dict:
    return set_status("campaign", advertiser_id, campaign_ids, operation_status)


def _report(advertiser_id: str, data_level: str, dimensions: list,
            metrics: list, date_from: str, date_to: str,
            page_size: int = 200, page: int = 1,
            filtering: Optional[list] = None,
            report_type: str = "BASIC") -> tuple[list, str]:
    """Gọi /report/integrated/get/, trả (rows, error)."""
    import json
    params = {
        "advertiser_id": advertiser_id,
        "report_type": report_type,
        "data_level": data_level,
        "dimensions": json.dumps(dimensions),
        "metrics": json.dumps(metrics),
        "start_date": date_from,
        "end_date": date_to,
        "page_size": page_size,
        "page": page,
    }
    if filtering:
        params["filtering"] = json.dumps(filtering)
    d = _get("/report/integrated/get/", params)
    if "error" in d:
        return [], d["error"]
    if d.get("code") != 0:
        return [], d.get("message", "TikTok API error")
    rows = d.get("data", {}).get("list", [])
    return rows, ""


# Mua tại cửa hàng = offline_shopping_events (đẩy từ CSKH qua Events API,
# event CompletePayment với event_source=offline). complete_payment chỉ đếm mua trên web.
DAILY_METRICS = [
    "spend", "impressions", "reach", "clicks", "ctr", "cpm",
    "conversion", "cost_per_conversion",
    "complete_payment", "offline_shopping_events", "offline_shopping_events_value",
    # 4 chỉ số mua tại cửa hàng — khớp đúng cột "(offline)" trên TikTok Ads Manager.
    # cost_per_ và value_per_ là số TikTok tự tính, không tự chia lại để tránh lệch.
    "cost_per_offline_shopping_event", "value_per_offline_shopping_event",
    "likes", "comments", "shares", "follows",
]

CAMPAIGN_METRICS = [
    "campaign_name", "spend", "impressions", "reach",
    "clicks", "ctr", "cpm", "conversion", "cost_per_conversion",
    "complete_payment", "offline_shopping_events", "offline_shopping_events_value",
    # 4 chỉ số mua tại cửa hàng — khớp đúng cột "(offline)" trên TikTok Ads Manager.
    # cost_per_ và value_per_ là số TikTok tự tính, không tự chia lại để tránh lệch.
    "cost_per_offline_shopping_event", "value_per_offline_shopping_event",
    "likes", "comments", "shares", "follows",
]

AD_METRICS = [
    # adgroup_id để bật/tắt được cấp nhóm quảng cáo ngay trong tool
    "campaign_id", "campaign_name", "adgroup_id", "adgroup_name", "ad_name",
    "spend", "impressions", "reach", "clicks", "ctr", "cpm",
    "conversion", "cost_per_conversion",
    "complete_payment", "offline_shopping_events", "offline_shopping_events_value",
    # 4 chỉ số mua tại cửa hàng — khớp đúng cột "(offline)" trên TikTok Ads Manager.
    # cost_per_ và value_per_ là số TikTok tự tính, không tự chia lại để tránh lệch.
    "cost_per_offline_shopping_event", "value_per_offline_shopping_event",
    "likes", "comments", "shares", "follows",
]


def _engagement_fields(m: dict) -> tuple[int, float]:
    """(tổng tương tác, ER%) — ER = (tim + bình luận + chia sẻ + theo dõi) / hiển thị × 100."""
    eng = sum(int(float(m.get(k) or 0)) for k in ("likes", "comments", "shares", "follows"))
    imp = int(m.get("impressions") or 0)
    er = round(eng / imp * 100, 2) if imp > 0 else 0.0
    return eng, er


def _purchase_fields(m: dict) -> tuple[int, float]:
    """(số đơn, giá trị VND) — gộp mua tại cửa hàng (offline) + mua web."""
    off_n = int(float(m.get("offline_shopping_events") or 0))
    off_val = float(m.get("offline_shopping_events_value") or 0)
    web_n = int(m.get("complete_payment") or 0)
    return off_n + web_n, off_val


def _offline_fields(m: dict) -> dict:
    """4 chỉ số mua TẠI CỬA HÀNG — khớp đúng nhóm cột "(offline)" trên TikTok Ads
    Manager (đã đối chiếu 13/08/2026, lệch <0,05% do làm tròn).

    Khác cột "Đơn" hiện có: cột đó gộp cả mua web (complete_payment), còn nhóm này
    CHỈ tính mua tại cửa hàng — đúng thứ cần để đo hiệu quả kéo khách tới cửa hàng.
    cost_per / value_per lấy thẳng số TikTok tính, không tự chia lại.
    Riêng "tỉ lệ mua" TikTok không có sẵn → tự tính đơn ÷ nhấp (đã kiểm chứng:
    2/36 = 5,56% và 12/358 = 3,35%, khớp Ads Manager).
    """
    don = int(float(m.get("offline_shopping_events") or 0))
    nhap = int(float(m.get("clicks") or 0))
    return {
        "don_offline": don,
        "chiphi_don_offline": round(float(m.get("cost_per_offline_shopping_event") or 0)),
        "giatri_don_offline": round(float(m.get("value_per_offline_shopping_event") or 0)),
        "ty_le_mua_offline": round(don / nhap * 100, 2) if nhap else 0.0,
    }


def _status_key(op: str, sec: str) -> str:
    """Gom trạng thái TikTok về 3 nhóm: on=Đang bật, off=Đã tắt (người tắt),
    paused=Đã dừng (hết ngân sách/hết lịch/chưa chạy/cấp trên đang tắt).

    Dùng chung cho cả 3 cấp nên chỉ xét ĐUÔI của secondary_status — TikTok đặt
    tên khác nhau theo cấp (CAMPAIGN_STATUS_*, ADGROUP_STATUS_*, AD_STATUS_*).
    """
    if sec.endswith("_DELETE") or op == "DELETE":
        return "off"
    if op == "DISABLE":
        return "off"
    if op == "ENABLE" and (sec.endswith("_ENABLE") or sec.endswith("_DELIVERY_OK")):
        return "on"
    return "paused"


# Mục tiêu quảng cáo — dịch mã của TikTok sang đúng chữ trên giao diện TikTok
OBJECTIVE_VI = {
    "REACH": "Phạm vi tiếp cận",
    "TRAFFIC": "Lưu lượng",
    "VIDEO_VIEWS": "Số lượt xem video",
    "ENGAGEMENT": "Tương tác với cộng đồng",
    "BRAND_CONSIDERATION": "Cân nhắc thương hiệu",
    "BRAND_MISSION": "Sứ mệnh thương hiệu",
    "LEAD_GENERATION": "Tạo khách hàng tiềm năng",
    "WEB_CONVERSIONS": "Lượt chuyển đổi web",
    "PRODUCT_SALES": "Doanh số bán hàng",
    "APP_PROMOTION": "Quảng cáo ứng dụng",
    "CATALOG_SALES": "Doanh số danh mục",
    "SHOP_PURCHASES": "Mua hàng trên shop",
}


_AGE_NUM = {
    "AGE_13_17": (13, 17), "AGE_18_24": (18, 24), "AGE_25_34": (25, 34),
    "AGE_35_44": (35, 44), "AGE_45_54": (45, 54), "AGE_55_100": (55, 100),
}


def _targeting_label(genders: set, ages: set) -> str:
    """Gộp tệp nhắm của các nhóm quảng cáo thành 1 nhãn ngắn: 'Nữ · 18-34'."""
    if "GENDER_UNLIMITED" in genders or len(genders) > 1:
        g = "Mọi giới"
    elif "GENDER_FEMALE" in genders:
        g = "Nữ"
    elif "GENDER_MALE" in genders:
        g = "Nam"
    else:
        g = ""
    lo = min((_AGE_NUM[a][0] for a in ages if a in _AGE_NUM), default=None)
    hi = max((_AGE_NUM[a][1] for a in ages if a in _AGE_NUM), default=None)
    if lo is None:
        return g
    age = f"{lo}+" if hi and hi >= 100 else f"{lo}-{hi}"
    return f"{g} · {age}" if g else age


def fetch_campaign_targeting(advertiser_id: str, campaign_ids: list) -> dict:
    """{campaign_id: 'Mọi giới · 18-34'} — gộp giới tính + tuổi từ các nhóm QC.

    Nhắm chọn nằm ở CẤP NHÓM quảng cáo chứ không phải cấp chiến dịch, nên phải
    đọc từng nhóm rồi gộp lại theo chiến dịch mẹ.
    """
    import json
    clean = sorted({str(c) for c in campaign_ids if c})
    acc: dict = {}
    for i in range(0, len(clean), 100):
        chunk = clean[i:i + 100]
        page = 1
        while True:
            d = _get("/adgroup/get/", {
                "advertiser_id": str(advertiser_id), "page": page, "page_size": 100,
                "filtering": json.dumps({"campaign_ids": chunk}),
                "fields": json.dumps(["adgroup_id", "campaign_id", "gender", "age_groups"]),
            })
            if d.get("code") != 0:
                break
            data = d.get("data") or {}
            for g in data.get("list") or []:
                cid = str(g.get("campaign_id", ""))
                slot = acc.setdefault(cid, {"g": set(), "a": set()})
                if g.get("gender"):
                    slot["g"].add(str(g["gender"]))
                for a in g.get("age_groups") or []:
                    slot["a"].add(str(a))
            total_page = int((data.get("page_info") or {}).get("total_page") or 1)
            if page >= total_page:
                break
            page += 1
    return {cid: _targeting_label(v["g"], v["a"]) for cid, v in acc.items()}


_adv_name_cache: dict = {}


def fetch_advertiser_names() -> dict:
    """{advertiser_id: tên tài khoản quảng cáo}. Tên không đổi nên nhớ luôn."""
    import json
    global _adv_name_cache
    ids = _advertiser_ids()
    if _adv_name_cache and all(i in _adv_name_cache for i in ids):
        return _adv_name_cache
    d = _get("/advertiser/info/", {"advertiser_ids": json.dumps(ids)})
    if d.get("code") != 0:
        return _adv_name_cache
    out = {}
    for a in (d.get("data") or {}).get("list") or []:
        name = str(a.get("name") or "").strip()
        # Bỏ phần "CÔNG TY TNHH THƯƠNG MẠI " để chip trên giao diện đọc được
        short = name.replace("CÔNG TY TNHH THƯƠNG MẠI", "").strip() or name
        out[str(a.get("advertiser_id"))] = short
    if out:
        _adv_name_cache = out
    return _adv_name_cache


def fetch_campaign_meta() -> dict:
    """{campaign_id: {status, automation, objective}} từ /campaign/get/.

    automation lấy THẲNG từ campaign_automation_type của TikTok:
      MANUAL → 'man' (Chiến dịch thủ công) · UPGRADED_SMART_PLUS → 'adv' (Smart+).
    Trước đây đoán theo TÊN campaign nên chiến dịch thủ công mà tên có chữ
    "smart"/"advantage" lại bị xếp nhầm thành tự động.
    """
    import json
    out: dict[str, dict] = {}
    fields = ["campaign_id", "operation_status", "secondary_status",
              "campaign_automation_type", "is_smart_performance_campaign",
              "objective_type"]
    for adv in _advertiser_ids():
        page = 1
        while True:
            d = _get("/campaign/get/", {
                "advertiser_id": adv, "page": page, "page_size": 100,
                "fields": json.dumps(fields),
            })
            if d.get("code") != 0:
                break
            data = d.get("data") or {}
            for c in data.get("list") or []:
                auto = str(c.get("campaign_automation_type") or "")
                smart = bool(c.get("is_smart_performance_campaign"))
                obj = str(c.get("objective_type") or "")
                out[str(c.get("campaign_id", ""))] = {
                    "status": _status_key(c.get("operation_status", ""),
                                          c.get("secondary_status", "")),
                    "automation": "adv" if (smart or "SMART" in auto) else
                                  ("man" if auto == "MANUAL" else ""),
                    "objective": OBJECTIVE_VI.get(obj, obj),
                }
            total_page = int((data.get("page_info") or {}).get("total_page") or 1)
            if page >= total_page:
                break
            page += 1
    return out


def fetch_campaign_statuses() -> dict:
    """{campaign_id: 'on'|'off'|'paused'} — giữ lại cho chỗ nào chỉ cần trạng thái."""
    return {k: v["status"] for k, v in fetch_campaign_meta().items()}


def _statuses_by_ids(level: str, advertiser_id: str, ids: list) -> dict:
    """{id: 'on'|'off'|'paused'} cho nhóm quảng cáo hoặc quảng cáo.

    Hỏi đúng những ID đang hiển thị (mỗi lần 100 cái) thay vì quét cả tài khoản
    — tài khoản có hàng nghìn quảng cáo cũ, quét hết thì trang tải rất lâu.
    """
    import json
    cfg = {
        "adgroup": ("/adgroup/get/", "adgroup_id", "adgroup_ids"),
        "ad":      ("/ad/get/",      "ad_id",      "ad_ids"),
    }
    if level not in cfg or not advertiser_id:
        return {}
    path, id_field, filter_key = cfg[level]
    clean = sorted({str(x) for x in ids if x})
    out: dict[str, str] = {}
    for i in range(0, len(clean), 100):
        chunk = clean[i:i + 100]
        d = _get(path, {
            "advertiser_id": str(advertiser_id), "page": 1, "page_size": 100,
            "filtering": json.dumps({filter_key: chunk}),
            "fields": json.dumps([id_field, "operation_status", "secondary_status"]),
        })
        if d.get("code") != 0:
            continue
        for o in (d.get("data") or {}).get("list") or []:
            out[str(o.get(id_field, ""))] = _status_key(
                o.get("operation_status", ""), o.get("secondary_status", ""))
    return out


AUDIENCE_METRICS = ["spend", "conversion", "impressions", "clicks"]


def fetch_tiktok_audience(date_from: str, date_to: str) -> dict:
    """Breakdown giới tính + độ tuổi theo từng campaign (report AUDIENCE).
    Trả {"gender": [...], "age": [...], "errors": [...]} — mỗi row:
    {campaign_id, key, spend, conversions, impressions, clicks}."""
    out = {"gender": [], "age": [], "errors": []}
    for adv in _advertiser_ids():
        for dim in ("gender", "age"):
            page = 1
            while True:
                rows, err = _report(
                    adv, "AUCTION_CAMPAIGN", ["campaign_id", dim],
                    AUDIENCE_METRICS, date_from, date_to,
                    page_size=200, page=page, report_type="AUDIENCE",
                )
                if err:
                    out["errors"].append(f"ADV {adv} {dim}: {err}")
                    break
                for r in rows:
                    m = r.get("metrics", {})
                    spend = float(m.get("spend") or 0)
                    if spend <= 0:
                        continue
                    out[dim].append({
                        "campaign_id": str(r.get("dimensions", {}).get("campaign_id", "")),
                        "key": r.get("dimensions", {}).get(dim, ""),
                        "spend": round(spend),
                        "conversions": int(m.get("conversion") or 0),
                        "impressions": int(m.get("impressions") or 0),
                        "clicks": int(m.get("clicks") or 0),
                    })
                if len(rows) < 200:
                    break
                page += 1
    return out


def _calc_roas(purchase_value: float, spend: float) -> float:
    if spend <= 0 or purchase_value <= 0:
        return 0.0
    return round(purchase_value / spend, 2)


def _parse_campaign(row: dict, advertiser_id: str) -> dict:
    m = row.get("metrics", {})
    d = row.get("dimensions", {})
    spend = float(m.get("spend") or 0)
    impressions = int(m.get("impressions") or 0)
    clicks = int(m.get("clicks") or 0)
    conversions = int(m.get("conversion") or 0)
    ctr = float(m.get("ctr") or 0)
    cpm = float(m.get("cpm") or 0)
    cpa = float(m.get("cost_per_conversion") or 0)
    reach = int(m.get("reach") or 0)
    purchases, purchase_value = _purchase_fields(m)
    engagements, er = _engagement_fields(m)
    roas = _calc_roas(purchase_value, spend)
    return {
        "campaign_id": d.get("campaign_id", ""),
        "campaign_name": m.get("campaign_name", ""),
        "spend": round(spend),
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "ctr": round(ctr, 2),
        "cpm": round(cpm),
        "conversions": conversions,
        "cpa": round(cpa),
        "purchases": purchases,
        "purchase_value": round(purchase_value),
        "roas": roas,
        "engagements": engagements,
        "er": er,
        **_offline_fields(m),
        "advertiser_id": advertiser_id,
    }


def _parse_ad(row: dict, advertiser_id: str) -> dict:
    m = row.get("metrics", {})
    d = row.get("dimensions", {})
    spend = float(m.get("spend") or 0)
    impressions = int(m.get("impressions") or 0)
    clicks = int(m.get("clicks") or 0)
    conversions = int(m.get("conversion") or 0)
    ctr = float(m.get("ctr") or 0)
    cpm = float(m.get("cpm") or 0)
    cpa = float(m.get("cost_per_conversion") or 0)
    reach = int(m.get("reach") or 0)
    purchases, purchase_value = _purchase_fields(m)
    engagements, er = _engagement_fields(m)
    roas = _calc_roas(purchase_value, spend)
    return {
        "ad_id": d.get("ad_id", ""),
        "ad_name": m.get("ad_name", ""),
        "adgroup_id": str(m.get("adgroup_id") or ""),
        "adgroup_name": m.get("adgroup_name", ""),
        "campaign_id": str(m.get("campaign_id") or ""),
        "campaign_name": m.get("campaign_name", ""),
        "spend": round(spend),
        "impressions": impressions,
        "reach": reach,
        "clicks": clicks,
        "ctr": round(ctr, 2),
        "cpm": round(cpm),
        "conversions": conversions,
        "cpa": round(cpa),
        "purchases": purchases,
        "purchase_value": round(purchase_value),
        "roas": roas,
        "engagements": engagements,
        "er": er,
        **_offline_fields(m),
        "advertiser_id": advertiser_id,
    }


def _fetch_campaigns_one(advertiser_id: str, date_from: str, date_to: str) -> tuple[list, list]:
    all_rows, errors = [], []
    page = 1
    while True:
        rows, err = _report(
            advertiser_id, "AUCTION_CAMPAIGN",
            ["campaign_id"], CAMPAIGN_METRICS,
            date_from, date_to, page_size=200, page=page,
        )
        if err:
            errors.append(f"ADV {advertiser_id}: {err}")
            break
        all_rows.extend([_parse_campaign(r, advertiser_id) for r in rows])
        if len(rows) < 200:
            break
        page += 1
    return all_rows, errors


def _fetch_ads_one(advertiser_id: str, date_from: str, date_to: str) -> tuple[list, list]:
    all_rows, errors = [], []
    page = 1
    while True:
        rows, err = _report(
            advertiser_id, "AUCTION_AD",
            ["ad_id"], AD_METRICS,
            date_from, date_to, page_size=200, page=page,
        )
        if err:
            errors.append(f"ADV {advertiser_id}: {err}")
            break
        all_rows.extend([_parse_ad(r, advertiser_id) for r in rows])
        if len(rows) < 200:
            break
        page += 1
    return all_rows, errors


def fetch_tiktok_campaigns(date_from: Optional[str] = None,
                           date_to: Optional[str] = None) -> dict:
    """Fetch campaign-level data từ tất cả advertiser accounts."""
    if not date_from:
        date_from = date.today().isoformat()
    if not date_to:
        date_to = date_from

    advertiser_ids = _advertiser_ids()
    if not advertiser_ids:
        return {"campaigns": [], "errors": ["Thiếu TIKTOK_ADVERTISER_IDS"],
                "date_from": date_from, "date_to": date_to,
                "fetched_at": datetime.now().isoformat(timespec="seconds")}

    all_camps, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_campaigns_one, adv, date_from, date_to): adv
                   for adv in advertiser_ids}
        for fut, adv in futures.items():
            try:
                camps, errs = fut.result()
                all_camps.extend(camps)
                errors.extend(errs)
            except Exception as e:
                errors.append(f"ADV {adv}: {e}")

    # Lọc bỏ campaign không có spend + sort theo spend desc
    all_camps = [c for c in all_camps if c["spend"] > 0]
    all_camps.sort(key=lambda x: x["spend"], reverse=True)

    # Trạng thái + kiểu chạy (thủ công/Smart+) + mục tiêu — 1 call /campaign/get/
    meta = fetch_campaign_meta()
    adv_names = fetch_advertiser_names()
    # Tệp nhắm (giới tính + tuổi) — hỏi theo từng tài khoản, chỉ những camp đang hiện
    targeting: dict = {}
    _by_adv: dict = {}
    for c in all_camps:
        _by_adv.setdefault(str(c["advertiser_id"]), []).append(str(c["campaign_id"]))
    for _adv, _cids in _by_adv.items():
        targeting.update(fetch_campaign_targeting(_adv, _cids))
    for c in all_camps:
        m = meta.get(str(c["campaign_id"])) or {}
        c["status"] = m.get("status", "")
        c["automation"] = m.get("automation", "")
        c["objective"] = m.get("objective", "")
        c["advertiser_name"] = adv_names.get(str(c["advertiser_id"]), "")
        c["target_label"] = targeting.get(str(c["campaign_id"]), "")

    total_spend = sum(c["spend"] for c in all_camps)
    total_impressions = sum(c["impressions"] for c in all_camps)
    total_clicks = sum(c["clicks"] for c in all_camps)
    total_conversions = sum(c["conversions"] for c in all_camps)
    total_purchase_value = sum(c["purchase_value"] for c in all_camps)
    total_purchases = sum(c["purchases"] for c in all_camps)
    total_engagements = sum(c["engagements"] for c in all_camps)
    avg_ctr = round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0
    avg_cpa = round(total_spend / total_conversions) if total_conversions > 0 else 0
    total_roas = round(total_purchase_value / total_spend, 2) if total_spend > 0 and total_purchase_value > 0 else 0

    return {
        "campaigns": all_camps,
        "errors": errors,
        "date_from": date_from,
        "date_to": date_to,
        "totals": {
            "spend": round(total_spend),
            "impressions": total_impressions,
            "clicks": total_clicks,
            "conversions": total_conversions,
            "purchases": total_purchases,
            "engagements": total_engagements,
            "er": round(total_engagements / total_impressions * 100, 2) if total_impressions > 0 else 0,
            "purchase_value": round(total_purchase_value),
            "ctr": avg_ctr,
            "cpa": avg_cpa,
            "roas": total_roas,
        },
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_tiktok_ads(date_from: Optional[str] = None,
                     date_to: Optional[str] = None) -> dict:
    """Fetch ad-level data từ tất cả advertiser accounts."""
    if not date_from:
        date_from = date.today().isoformat()
    if not date_to:
        date_to = date_from

    advertiser_ids = _advertiser_ids()
    if not advertiser_ids:
        return {"ads": [], "errors": ["Thiếu TIKTOK_ADVERTISER_IDS"],
                "date_from": date_from, "date_to": date_to,
                "fetched_at": datetime.now().isoformat(timespec="seconds")}

    all_ads, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_ads_one, adv, date_from, date_to): adv
                   for adv in advertiser_ids}
        for fut, adv in futures.items():
            try:
                ads, errs = fut.result()
                all_ads.extend(ads)
                errors.extend(errs)
            except Exception as e:
                errors.append(f"ADV {adv}: {e}")

    all_ads = [a for a in all_ads if a["spend"] > 0]
    all_ads.sort(key=lambda x: x["spend"], reverse=True)

    # Trạng thái nhóm quảng cáo + quảng cáo — để bật/tắt được ngay trong tool
    by_adv: dict[str, list] = {}
    for a in all_ads:
        by_adv.setdefault(str(a["advertiser_id"]), []).append(a)
    adv_names = fetch_advertiser_names()
    for adv, ads in by_adv.items():
        for a in ads:
            a["advertiser_name"] = adv_names.get(adv, "")
        ad_st = _statuses_by_ids("ad", adv, [a["ad_id"] for a in ads])
        grp_st = _statuses_by_ids("adgroup", adv, [a["adgroup_id"] for a in ads])
        for a in ads:
            a["status"] = ad_st.get(str(a["ad_id"]), "")
            a["adgroup_status"] = grp_st.get(str(a["adgroup_id"]), "")

    total_spend = sum(a["spend"] for a in all_ads)
    total_impressions = sum(a["impressions"] for a in all_ads)
    total_clicks = sum(a["clicks"] for a in all_ads)
    total_conversions = sum(a["conversions"] for a in all_ads)
    total_purchase_value = sum(a["purchase_value"] for a in all_ads)
    total_purchases = sum(a["purchases"] for a in all_ads)
    total_engagements = sum(a["engagements"] for a in all_ads)
    avg_ctr = round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0
    avg_cpa = round(total_spend / total_conversions) if total_conversions > 0 else 0
    total_roas = round(total_purchase_value / total_spend, 2) if total_spend > 0 and total_purchase_value > 0 else 0

    return {
        "ads": all_ads,
        "errors": errors,
        "date_from": date_from,
        "date_to": date_to,
        "totals": {
            "spend": round(total_spend),
            "impressions": total_impressions,
            "clicks": total_clicks,
            "conversions": total_conversions,
            "purchases": total_purchases,
            "engagements": total_engagements,
            "er": round(total_engagements / total_impressions * 100, 2) if total_impressions > 0 else 0,
            "purchase_value": round(total_purchase_value),
            "ctr": avg_ctr,
            "cpa": avg_cpa,
            "roas": total_roas,
        },
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _parse_daily_row(row: dict) -> dict:
    m = row.get("metrics", {})
    d = row.get("dimensions", {})
    spend = float(m.get("spend") or 0)
    purchases, purchase_value = _purchase_fields(m)
    engagements, er = _engagement_fields(m)
    roas = _calc_roas(purchase_value, spend)
    return {
        "date": (d.get("stat_time_day") or "")[:10],
        "campaign_id": str(d.get("campaign_id", "")),
        "spend": round(spend),
        "impressions": int(m.get("impressions") or 0),
        "clicks": int(m.get("clicks") or 0),
        "ctr": round(float(m.get("ctr") or 0), 2),
        "cpm": round(float(m.get("cpm") or 0)),
        "conversions": int(m.get("conversion") or 0),
        "cpa": round(float(m.get("cost_per_conversion") or 0)),
        "purchases": purchases,
        "purchase_value": round(purchase_value),
        "roas": roas,
        "engagements": engagements,
        "er": er,
    }


def fetch_tiktok_campaign_daily(advertiser_id: str, campaign_id: str,
                                 date_from: str, date_to: str) -> dict:
    """Daily breakdown (CPM + ROAS) cho một campaign cụ thể."""
    filtering = [{"field_name": "campaign_id", "filter_type": "IN",
                  "filter_value": f'["{campaign_id}"]'}]
    all_rows, page = [], 1
    while True:
        rows, err = _report(
            advertiser_id, "AUCTION_CAMPAIGN",
            ["campaign_id", "stat_time_day"], DAILY_METRICS,
            date_from, date_to, page_size=200, page=page, filtering=filtering,
        )
        if err:
            return {"error": err, "daily": []}
        all_rows.extend([_parse_daily_row(r) for r in rows])
        if len(rows) < 200:
            break
        page += 1
    all_rows.sort(key=lambda x: x["date"])
    return {"daily": all_rows, "campaign_id": campaign_id}


def fetch_tiktok_campaign_ads(advertiser_id: str, campaign_id: str,
                               date_from: str, date_to: str) -> dict:
    """Fetch tất cả ads thuộc một campaign cụ thể."""
    filtering = [{"field_name": "campaign_id", "filter_type": "IN",
                  "filter_value": f'["{campaign_id}"]'}]
    all_ads, page = [], 1
    while True:
        rows, err = _report(
            advertiser_id, "AUCTION_AD",
            ["ad_id"], AD_METRICS,
            date_from, date_to, page_size=200, page=page, filtering=filtering,
        )
        if err:
            return {"error": err, "ads": []}
        all_ads.extend([_parse_ad(r, advertiser_id) for r in rows])
        if len(rows) < 200:
            break
        page += 1
    all_ads = [a for a in all_ads if a["spend"] > 0]
    all_ads.sort(key=lambda x: x["spend"], reverse=True)
    return {"ads": all_ads, "campaign_id": campaign_id}


def _fetch_all_daily_one(advertiser_id: str, date_from: str, date_to: str) -> tuple[list, list]:
    all_rows, errors = [], []
    page = 1
    while True:
        rows, err = _report(
            advertiser_id, "AUCTION_CAMPAIGN",
            ["campaign_id", "stat_time_day"], DAILY_METRICS,
            date_from, date_to, page_size=200, page=page,
        )
        if err:
            errors.append(f"ADV {advertiser_id}: {err}")
            break
        all_rows.extend([_parse_daily_row(r) for r in rows])
        if len(rows) < 200:
            break
        page += 1
    return all_rows, errors


def fetch_ad_thumbnails(advertiser_id: str, ad_ids: list) -> dict:
    """Lấy thumbnail URL cho list ad_ids. Return {ad_id: url}."""
    import json
    if not ad_ids:
        return {}

    # Step 1: /ad/get/ — lấy image_ids & video_id
    params = {
        "advertiser_id": advertiser_id,
        "filtering": json.dumps({"ad_ids": [str(a) for a in ad_ids[:100]]}),
        "fields": json.dumps(["ad_id", "image_ids", "video_id"]),
        "page_size": min(len(ad_ids), 100),
    }
    d = _get("/ad/get/", params)
    if d.get("code") != 0:
        return {}

    ads_list = (d.get("data") or {}).get("list") or []
    image_id_to_ad: dict[str, str] = {}
    video_id_to_ad: dict[str, str] = {}

    for ad in ads_list:
        ad_id = str(ad.get("ad_id", ""))
        imgs = ad.get("image_ids") or []
        vid = str(ad.get("video_id") or "")
        if imgs:
            image_id_to_ad[imgs[0]] = ad_id
        elif vid and vid not in ("0", ""):
            video_id_to_ad[vid] = ad_id

    result: dict[str, str] = {}

    # Step 2a: video cover URL (hầu hết TikTok ads là video)
    if video_id_to_ad:
        vp = {
            "advertiser_id": advertiser_id,
            "video_ids": json.dumps(list(video_id_to_ad.keys())[:50]),
        }
        vd = _get("/file/video/ad/get/", vp)
        for item in ((vd.get("data") or {}).get("list") or []):
            vid = str(item.get("video_id", ""))
            cover = item.get("video_cover_url") or item.get("poster_url") or ""
            if vid in video_id_to_ad and cover:
                result[video_id_to_ad[vid]] = cover

    # Step 2b: image URL (image-only ads)
    if image_id_to_ad:
        ip = {
            "advertiser_id": advertiser_id,
            "image_ids": json.dumps(list(image_id_to_ad.keys())[:50]),
        }
        id_ = _get("/file/image/ad/get/", ip)
        for item in ((id_.get("data") or {}).get("list") or []):
            img_id = item.get("image_id", "")
            url = item.get("image_url") or item.get("url") or ""
            if img_id in image_id_to_ad and url:
                result[image_id_to_ad[img_id]] = url

    return result


def fetch_tiktok_all_daily(date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> dict:
    """Fetch daily breakdown TẤT CẢ campaigns — dùng để client-side filter khi expand."""
    if not date_from:
        date_from = date.today().isoformat()
    if not date_to:
        date_to = date_from

    advertiser_ids = _advertiser_ids()
    if not advertiser_ids:
        return {"daily": [], "errors": ["Thiếu TIKTOK_ADVERTISER_IDS"]}

    all_rows, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_all_daily_one, adv, date_from, date_to): adv
                   for adv in advertiser_ids}
        for fut, adv in futures.items():
            try:
                rows, errs = fut.result()
                all_rows.extend(rows)
                errors.extend(errs)
            except Exception as e:
                errors.append(f"ADV {adv}: {e}")

    all_rows.sort(key=lambda x: (x["campaign_id"], x["date"]))
    return {"daily": all_rows, "errors": errors}


# ═══════════════════════════════════════════════════════════════════
# CONTENT TIKTOK — gộp ads theo VIDEO/CREATIVE (hiệu quả của content,
# không phải của từng ad). Mirror trang Bài đăng Facebook.
# ═══════════════════════════════════════════════════════════════════

def _fetch_ad_media(advertiser_id: str, ad_ids: list) -> dict:
    """Map ad_id → {media_id, thumb, create_time, status} cho list ad
    (chunk 100 id / request; video cover + image url chunk 50)."""
    import json
    out: dict = {}
    if not ad_ids:
        return out
    video_to_ads: dict = {}
    image_to_ads: dict = {}
    for i in range(0, len(ad_ids), 100):
        chunk = [str(a) for a in ad_ids[i:i + 100]]
        d = _get("/ad/get/", {
            "advertiser_id": advertiser_id,
            "filtering": json.dumps({"ad_ids": chunk}),
            "fields": json.dumps(["ad_id", "image_ids", "video_id", "tiktok_item_id",
                                   "create_time", "operation_status", "secondary_status"]),
            "page_size": len(chunk),
        })
        if d.get("code") != 0:
            continue
        for ad in (d.get("data") or {}).get("list") or []:
            aid = str(ad.get("ad_id", ""))
            vid = str(ad.get("video_id") or "")
            imgs = ad.get("image_ids") or []
            item = str(ad.get("tiktok_item_id") or "")
            media = ""
            if item and item not in ("0", ""):
                media = f"t:{item}"     # Spark Ads — bài TikTok gốc (content thật)
            elif vid and vid not in ("0", ""):
                media = f"v:{vid}"
                video_to_ads.setdefault(vid, []).append(aid)
            elif imgs:
                media = f"i:{imgs[0]}"
                image_to_ads.setdefault(imgs[0], []).append(aid)
            out[aid] = {
                "media_id": media,
                "thumb": "",
                "create_time": (ad.get("create_time") or "")[:10],
                "status": "on" if ad.get("operation_status") == "ENABLE" else "off",
            }
    # Cover video
    vids = list(video_to_ads.keys())
    for i in range(0, len(vids), 50):
        d = _get("/file/video/ad/get/", {
            "advertiser_id": advertiser_id,
            "video_ids": json.dumps(vids[i:i + 50]),
        })
        for item in ((d.get("data") or {}).get("list") or []):
            cover = item.get("video_cover_url") or item.get("poster_url") or ""
            for aid in video_to_ads.get(str(item.get("video_id", "")), []):
                if cover:
                    out[aid]["thumb"] = cover
    # Ảnh
    imgs = list(image_to_ads.keys())
    for i in range(0, len(imgs), 50):
        d = _get("/file/image/ad/get/", {
            "advertiser_id": advertiser_id,
            "image_ids": json.dumps(imgs[i:i + 50]),
        })
        for item in ((d.get("data") or {}).get("list") or []):
            url = item.get("image_url") or item.get("url") or ""
            for aid in image_to_ads.get(item.get("image_id", ""), []):
                if url:
                    out[aid]["thumb"] = url
    return out


_oembed_cache: dict = {}


def _fetch_oembed(item_id: str) -> dict:
    """Caption + thumbnail + kênh đăng của bài TikTok qua oEmbed công khai (cache)."""
    if item_id in _oembed_cache:
        return _oembed_cache[item_id]
    out = {}
    try:
        r = requests.get("https://www.tiktok.com/oembed", params={
            "url": f"https://www.tiktok.com/@tiktok/video/{item_id}"}, timeout=12)
        if r.status_code == 200:
            j = r.json()
            out = {
                "title": j.get("title") or "",
                "thumb": j.get("thumbnail_url") or "",
                "author": j.get("author_name") or "",
                "url": (j.get("author_url") or "") + f"/video/{item_id}",
            }
    except Exception:
        pass
    _oembed_cache[item_id] = out
    return out


def fetch_tiktok_content(date_from: Optional[str] = None,
                          date_to: Optional[str] = None) -> dict:
    """Content TikTok = gộp ads theo video/creative: cộng chi phí, mess,
    tương tác... của MỌI ad dùng chung media. Ad không xác định được media
    thì đứng thành content riêng."""
    ads_res = fetch_tiktok_ads(date_from, date_to)
    ads = ads_res.get("ads") or []
    errors = list(ads_res.get("errors") or [])

    # Media map theo advertiser
    by_adv: dict = {}
    for a in ads:
        by_adv.setdefault(a["advertiser_id"], []).append(a["ad_id"])
    media_map: dict = {}
    for adv, ids in by_adv.items():
        media_map.update(_fetch_ad_media(adv, ids))

    contents: dict = {}
    for a in ads:
        meta = media_map.get(str(a["ad_id"]), {})
        key = meta.get("media_id") or f"ad:{a['ad_id']}"
        c = contents.setdefault(key, {
            "media_id": key,
            "name": a.get("ad_name") or a.get("adgroup_name") or "",
            "thumb": "",
            "created": "",
            "status": "off",
            "ads_count": 0,
            "campaigns": set(),
            "spend": 0, "impressions": 0, "reach": 0, "clicks": 0,
            "conversions": 0, "engagements": 0,
            "purchases": 0, "purchase_value": 0,
            "advertiser_id": a["advertiser_id"],
        })
        c["ads_count"] += 1
        if a.get("campaign_name"):
            c["campaigns"].add(a["campaign_name"])
        for k in ("spend", "impressions", "reach", "clicks",
                  "conversions", "engagements", "purchases", "purchase_value"):
            c[k] += a.get(k) or 0
        if meta.get("thumb") and not c["thumb"]:
            c["thumb"] = meta["thumb"]
        ct = meta.get("create_time") or ""
        if ct and (not c["created"] or ct < c["created"]):
            c["created"] = ct
        if meta.get("status") == "on":
            c["status"] = "on"
        # Tên: ưu tiên tên ngắn gọn có nghĩa (tên dài nhất thường mô tả rõ nhất)
        nm = a.get("ad_name") or ""
        if len(nm) > len(c["name"]):
            c["name"] = nm

    # Làm giàu Spark Ads: caption thật + thumbnail + kênh/KOC đăng bài
    spark_items = [k[2:] for k in contents if k.startswith("t:")]
    if spark_items:
        with ThreadPoolExecutor(max_workers=8) as ex:
            oembeds = dict(zip(spark_items, ex.map(_fetch_oembed, spark_items)))
        for k, c in contents.items():
            if k.startswith("t:"):
                o = oembeds.get(k[2:]) or {}
                if o.get("title"):
                    c["name"] = o["title"]
                if o.get("thumb"):
                    c["thumb"] = o["thumb"]
                c["author"] = o.get("author") or ""
                c["url"] = o.get("url") or ""

    out = []
    for c in contents.values():
        c.setdefault("author", "")
        c.setdefault("url", "")
        c["campaigns"] = sorted(c["campaigns"])
        imp = c["impressions"]
        c["ctr"] = round(c["clicks"] / imp * 100, 2) if imp else 0
        c["er"] = round(c["engagements"] / imp * 100, 2) if imp else 0
        c["cpa"] = round(c["spend"] / c["conversions"]) if c["conversions"] else 0
        c["roas"] = _calc_roas(c["purchase_value"], c["spend"])
        c["spend"] = round(c["spend"])
        out.append(c)
    out.sort(key=lambda x: -x["spend"])

    t_spend = sum(c["spend"] for c in out)
    t_imp = sum(c["impressions"] for c in out)
    t_conv = sum(c["conversions"] for c in out)
    t_eng = sum(c["engagements"] for c in out)
    return {
        "contents": out,
        "errors": errors,
        "date_from": ads_res.get("date_from"),
        "date_to": ads_res.get("date_to"),
        "totals": {
            "count": len(out),
            "spend": t_spend,
            "impressions": t_imp,
            "conversions": t_conv,
            "engagements": t_eng,
            "er": round(t_eng / t_imp * 100, 2) if t_imp else 0,
            "cpa": round(t_spend / t_conv) if t_conv else 0,
            "purchases": sum(c["purchases"] for c in out),
        },
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
