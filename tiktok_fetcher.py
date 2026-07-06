"""Fetch campaign/ad-level insights từ TikTok Ads cho Eye Plus.

Token đọc từ env: TIKTOK_ACCESS_TOKEN (hoặc TIKTOK_CAPI_ACCESS_TOKEN làm fallback).
Advertiser IDs đọc từ env: TIKTOK_ADVERTISER_IDS (comma-separated).
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

import requests

TT_BASE = "https://business-api.tiktok.com/open_api/v1.3"


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
    try:
        r = requests.get(
            f"{TT_BASE}{path}",
            headers={"Access-Token": token},
            params=params,
            timeout=30,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _report(advertiser_id: str, data_level: str, dimensions: list,
            metrics: list, date_from: str, date_to: str,
            page_size: int = 200, page: int = 1,
            filtering: Optional[list] = None) -> tuple[list, str]:
    """Gọi /report/integrated/get/, trả (rows, error)."""
    import json
    params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
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
]

CAMPAIGN_METRICS = [
    "campaign_name", "spend", "impressions", "reach",
    "clicks", "ctr", "cpm", "conversion", "cost_per_conversion",
    "complete_payment", "offline_shopping_events", "offline_shopping_events_value",
]

AD_METRICS = [
    "campaign_name", "adgroup_name", "ad_name",
    "spend", "impressions", "reach", "clicks", "ctr", "cpm",
    "conversion", "cost_per_conversion",
    "complete_payment", "offline_shopping_events", "offline_shopping_events_value",
]


def _purchase_fields(m: dict) -> tuple[int, float]:
    """(số đơn, giá trị VND) — gộp mua tại cửa hàng (offline) + mua web."""
    off_n = int(float(m.get("offline_shopping_events") or 0))
    off_val = float(m.get("offline_shopping_events_value") or 0)
    web_n = int(m.get("complete_payment") or 0)
    return off_n + web_n, off_val


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
    roas = _calc_roas(purchase_value, spend)
    return {
        "ad_id": d.get("ad_id", ""),
        "ad_name": m.get("ad_name", ""),
        "adgroup_name": m.get("adgroup_name", ""),
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

    total_spend = sum(c["spend"] for c in all_camps)
    total_impressions = sum(c["impressions"] for c in all_camps)
    total_clicks = sum(c["clicks"] for c in all_camps)
    total_conversions = sum(c["conversions"] for c in all_camps)
    total_purchase_value = sum(c["purchase_value"] for c in all_camps)
    total_purchases = sum(c["purchases"] for c in all_camps)
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

    total_spend = sum(a["spend"] for a in all_ads)
    total_impressions = sum(a["impressions"] for a in all_ads)
    total_clicks = sum(a["clicks"] for a in all_ads)
    total_conversions = sum(a["conversions"] for a in all_ads)
    total_purchase_value = sum(a["purchase_value"] for a in all_ads)
    total_purchases = sum(a["purchases"] for a in all_ads)
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
