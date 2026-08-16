"""Fetch ad-level insights từ 6 FB Ad Accounts của Eye Plus (multi-BM).

Account IDs hardcode khớp với fb_chatbot/content_dashboard.py AD_ACCOUNTS.
Tokens đọc từ env: FB_TOKEN_BM1, FB_TOKEN_BM2, FB_TOKEN_BM3.
"""

import os
from datetime import date, datetime, timedelta
from typing import Optional

import requests

FB_API_VERSION = "v19.0"
FB_BASE_URL = f"https://graph.facebook.com/{FB_API_VERSION}"
# Thuế Meta thu tại VN: 5% VAT + 5% TNDN = 10%. Quy ước gốc ở
# ChienluocKD/fb_chatbot/ad_vat.py (app riêng nên không import chéo được).
# Chi phí HIỂN THỊ = đã VAT; ROAS + giá tin = số thô (khớp Trình quản lý QC FB).
AD_VAT_RATE = 0.10

AD_ACCOUNTS = [
    {"name": "Test cam",      "account_id": "act_549531036136206",  "token_env": "FB_TOKEN_BM3", "bm": "BM3"},
    {"name": "TK chính",      "account_id": "act_503539498015244",  "token_env": "FB_TOKEN_BM2", "bm": "BM2"},
    {"name": "Page her",      "account_id": "act_1077015690986400", "token_env": "FB_TOKEN_BM2", "bm": "BM2"},
    {"name": "Store mới",     "account_id": "act_3209088345923012", "token_env": "FB_TOKEN_BM2", "bm": "BM2"},
    {"name": "Reach",         "account_id": "act_1430491514665102", "token_env": "FB_TOKEN_BM1", "bm": "BM1"},
    {"name": "Ads chính EP1", "account_id": "act_1842061356575955", "token_env": "FB_TOKEN_BM1", "bm": "BM1"},
]

MSG_ACTION = "onsite_conversion.messaging_conversation_started_7d"
PURCHASE_TYPES = {"omni_purchase", "offsite_conversion.fb_pixel_purchase"}
# ER = (tim + bình luận + chia sẻ + theo dõi trang) / hiển thị × 100 — khớp công thức tab TikTok
ENGAGE_TYPES = {"post_reaction", "comment", "post", "like"}


def _parse_ad(row: dict, account: dict) -> dict:
    actions = row.get("actions") or []
    messages = next(
        (int(a.get("value", 0) or 0)
         for a in actions if a.get("action_type") == MSG_ACTION),
        0,
    )
    purchases = sum(
        int(a.get("value") or 0)
        for a in actions if a.get("action_type") in PURCHASE_TYPES
    )
    engagements = sum(
        int(a.get("value") or 0)
        for a in actions if a.get("action_type") in ENGAGE_TYPES
    )

    spend_raw = float(row.get("spend") or 0)
    spend_vat = spend_raw * (1 + AD_VAT_RATE)   # khớp dashboard chính (measurement.html)
    cost_per_msg = int(round(spend_vat / messages)) if messages > 0 else 0

    roas_data = row.get("purchase_roas") or []
    roas_raw = next(
        (float(r.get("value") or 0)
         for r in roas_data if r.get("action_type") == "omni_purchase"),
        0.0,
    )
    # ROAS từ FB tính trên spend pre-VAT. Quy chiếu VAT: roas_effective = roas_raw / 1.10
    roas = roas_raw / (1 + AD_VAT_RATE) if roas_raw > 0 else 0.0

    return {
        "ad_id": row.get("ad_id"),
        "ad_name": row.get("ad_name", ""),
        "adset_id": row.get("adset_id", ""),
        "adset_name": row.get("adset_name", ""),
        "campaign_id": row.get("campaign_id", ""),
        "campaign_name": row.get("campaign_name", ""),
        "spend": spend_vat,        # ĐÃ VAT — dùng cho hiển thị + so sánh dashboard chính
        "spend_raw": spend_raw,    # Pre-VAT — giữ cho debug / quy chiếu
        "impressions": int(row.get("impressions") or 0),
        "reach": int(row.get("reach") or 0),
        "clicks": int(row.get("clicks") or 0),
        "engagements": engagements,
        "er": round(engagements / int(row.get("impressions") or 1) * 100, 2) if int(row.get("impressions") or 0) > 0 else 0.0,
        "messages": messages,
        "cost_per_message": cost_per_msg,
        "purchases": purchases,
        "roas": roas,
        "roas_raw": roas_raw,
        "account": account["name"],
        "account_id": account["account_id"],  # "act_xxxxx"
        "bm": account["bm"],
        "thumbnail_url": "",      # filled by _fetch_ad_meta (low-res)
        "image_full_url": "",     # high-res for zoom
        "effective_status": "",   # filled by _fetch_ad_meta
        "is_paused": False,       # derived: effective_status != ACTIVE
        "is_advantage": False,    # filled by _fetch_adset_targeting
        "targeting_type": "",     # "advantage" or "manual"
        "targeting_reason": "",   # detail flags (expansion_all, lookalike+, ...)
    }


def fetch_account_ads(account: dict, date_from: str, date_to: Optional[str] = None) -> tuple[list, str]:
    """Trả (list ad dicts, error_message_or_empty). date_to mặc định = date_from."""
    if not date_to:
        date_to = date_from
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return [], f"Thiếu env {account['token_env']} cho TK {account['name']}"

    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,actions,purchase_roas,impressions,reach,clicks",
        "level": "ad",
        "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
        # Lấy CẢ ad đang ACTIVE lẫn đã PAUSE (paused vẫn còn spend trong range)
        "filtering": '[{"field":"ad.effective_status","operator":"IN",'
                     '"value":["ACTIVE","PAUSED","CAMPAIGN_PAUSED","ADSET_PAUSED",'
                     '"WITH_ISSUES","PENDING_REVIEW","IN_PROCESS"]}]',
        "limit": 500,
    }

    ads = []
    page_count = 0
    while url and page_count < 10:
        page_count += 1
        try:
            r = requests.get(url, params=params if page_count == 1 else None, timeout=60)
            data = r.json()
        except Exception as e:
            return ads, f"TK {account['name']} request fail: {e}"

        if "error" in data:
            return ads, f"TK {account['name']}: {data['error'].get('message', 'unknown error')}"

        for row in data.get("data", []):
            ads.append(_parse_ad(row, account))

        url = (data.get("paging") or {}).get("next") or ""

    # Fetch images + effective_status cho từng ad
    if ads:
        meta = _fetch_ad_meta(token, [a["ad_id"] for a in ads])
        for a in ads:
            d = meta.get(a["ad_id"], {})
            a["thumbnail_url"] = d.get("thumb", "")
            a["image_full_url"] = d.get("full", "") or d.get("thumb", "")
            a["effective_status"] = d.get("effective_status", "UNKNOWN")
            a["is_paused"] = a["effective_status"] != "ACTIVE"

        # Fetch adset targeting → detect Advantage+ và lấy luôn daily_budget per adset
        unique_adset_ids = list({a["adset_id"] for a in ads if a.get("adset_id")})
        targeting_map = _fetch_adset_targeting(token, unique_adset_ids)
        for a in ads:
            t = targeting_map.get(a.get("adset_id"), {})
            a["is_advantage"] = bool(t.get("is_advantage"))
            a["targeting_type"] = "advantage" if a["is_advantage"] else "manual"
            a["targeting_reason"] = t.get("reason", "")
            a["adset_daily_budget"] = int(t.get("daily_budget") or 0)

        # Fetch campaign-level daily_budget (CBO)
        unique_camp_ids = list({a["campaign_id"] for a in ads if a.get("campaign_id")})
        camp_budgets = _fetch_campaign_budgets(token, unique_camp_ids)
        for a in ads:
            a["campaign_daily_budget"] = int((camp_budgets.get(a.get("campaign_id")) or {}).get("daily_budget") or 0)

    return ads, ""


def _fetch_campaign_budgets(token: str, campaign_ids: list) -> dict:
    """Batch fetch daily_budget + lifetime_budget per campaign (CBO)."""
    out = {}
    BATCH = 50
    for i in range(0, len(campaign_ids), BATCH):
        batch = [cid for cid in campaign_ids[i:i + BATCH] if cid]
        if not batch:
            continue
        try:
            r = requests.get(
                FB_BASE_URL + "/",
                params={
                    "access_token": token,
                    "ids": ",".join(batch),
                    "fields": "daily_budget,lifetime_budget",
                },
                timeout=20,
            )
            d = r.json()
        except Exception:
            continue
        if not isinstance(d, dict) or "error" in d:
            continue
        for cid, info in d.items():
            if isinstance(info, dict):
                out[cid] = {
                    "daily_budget": int(info.get("daily_budget") or 0),
                    "lifetime_budget": int(info.get("lifetime_budget") or 0),
                }
    return out


def _fetch_ad_meta(token: str, ad_ids: list) -> dict:
    """Batch fetch creative images + effective_status. Trả {ad_id: {thumb, full, effective_status}}."""
    out = {}
    BATCH = 50
    fields = (
        "effective_status,"
        "creative{thumbnail_url,image_url,"
        "object_story_spec{link_data{picture},photo_data{url},video_data{image_url}}}"
    )
    for i in range(0, len(ad_ids), BATCH):
        batch = [aid for aid in ad_ids[i:i + BATCH] if aid]
        if not batch:
            continue
        try:
            r = requests.get(
                FB_BASE_URL + "/",
                params={
                    "access_token": token,
                    "ids": ",".join(batch),
                    "fields": fields,
                },
                timeout=30,
            )
            data = r.json()
        except Exception:
            continue
        if not isinstance(data, dict) or "error" in data:
            continue
        for ad_id, ad in data.items():
            if not isinstance(ad, dict):
                continue
            cr = ad.get("creative") or {}
            oss = (cr.get("object_story_spec") or {})
            link = (oss.get("link_data") or {})
            photo = (oss.get("photo_data") or {})
            video = (oss.get("video_data") or {})
            # Prefer high-res sources for zoom popup
            full = (
                cr.get("image_url")
                or link.get("picture")
                or photo.get("url")
                or video.get("image_url")
                or cr.get("thumbnail_url")
                or ""
            )
            thumb = cr.get("thumbnail_url") or full
            out[ad_id] = {
                "thumb": thumb,
                "full": full,
                "effective_status": ad.get("effective_status", "UNKNOWN"),
            }
    return out


# Backward-compat alias (kept for any external callers)
_fetch_thumbnails = _fetch_ad_meta


def _fetch_adset_targeting(token: str, adset_ids: list) -> dict:
    """Batch fetch adset.targeting → detect Advantage+ Audience + daily_budget.

    Trả {adset_id: {is_advantage: bool, reason: str, daily_budget: int}}.

    Logic Advantage+:
    - targeting.targeting_optimization == "expansion_all"  → Advantage detailed targeting
    - targeting.targeting_relaxation_types.lookalike == 1   → Advantage lookalike expansion
    - targeting.targeting_relaxation_types.custom_audience == 1 → Advantage custom aud expansion
    - targeting.is_advantage_audience == true               → Full Advantage+ Audience
    """
    out = {}
    BATCH = 50
    fields = "targeting,daily_budget"  # whole targeting + adset daily_budget
    for i in range(0, len(adset_ids), BATCH):
        batch = [aid for aid in adset_ids[i:i + BATCH] if aid]
        if not batch:
            continue
        try:
            r = requests.get(
                FB_BASE_URL + "/",
                params={
                    "access_token": token,
                    "ids": ",".join(batch),
                    "fields": fields,
                },
                timeout=30,
            )
            data = r.json()
        except Exception:
            continue
        if not isinstance(data, dict) or "error" in data:
            continue
        for adset_id, info in data.items():
            if not isinstance(info, dict):
                continue
            t = info.get("targeting") or {}
            reasons = []
            if t.get("targeting_optimization") == "expansion_all":
                reasons.append("expansion_all")
            relax = t.get("targeting_relaxation_types") or {}
            if relax.get("lookalike") == 1:
                reasons.append("lookalike+")
            if relax.get("custom_audience") == 1:
                reasons.append("CA+")
            # Advantage+ Audience: 2 cách FB encode (cũ + mới, từ ~2025)
            if t.get("is_advantage_audience") is True:
                reasons.append("Adv+ Audience")
            auto = t.get("targeting_automation") or {}
            if auto.get("advantage_audience") == 1 and "Adv+ Audience" not in reasons:
                reasons.append("Adv+ Audience")
            out[adset_id] = {
                "is_advantage": bool(reasons),
                "reason": ",".join(reasons) if reasons else "manual",
                "daily_budget": int(info.get("daily_budget") or 0),
            }
    return out


def _fetch_ad_post_ids(token: str, ad_ids: list) -> dict:
    """Batch fetch effective_object_story_id cho từng ad. Trả {ad_id: post_id}."""
    out = {}
    BATCH = 50
    for i in range(0, len(ad_ids), BATCH):
        batch = [aid for aid in ad_ids[i:i + BATCH] if aid]
        if not batch:
            continue
        try:
            r = requests.get(
                FB_BASE_URL + "/",
                params={
                    "access_token": token,
                    "ids": ",".join(batch),
                    "fields": "creative{effective_object_story_id}",
                },
                timeout=30,
            )
            data = r.json()
        except Exception:
            continue
        if not isinstance(data, dict) or "error" in data:
            continue
        for ad_id, ad in data.items():
            cr = ad.get("creative") or {}
            post_id = cr.get("effective_object_story_id")
            if post_id:
                out[ad_id] = post_id
    return out


def _fetch_post_comments(page_token: str, post_id: str,
                         limit: int = 200, since_ts: int = None) -> tuple[list, str]:
    """Fetch comments của 1 post FB dùng Page Access Token.

    since_ts: Unix timestamp — chỉ lấy comments SAU thời điểm này (incremental sync).
    Trả (comments_list, error_message).
    """
    params = {
        "access_token": page_token,
        "fields": "id,message,from,created_time,like_count",
        "limit": limit,
        "filter": "stream",
    }
    if since_ts:
        params["since"] = since_ts + 1  # +1s tránh lấy trùng comment cuối

    all_comments: list = []
    url = f"{FB_BASE_URL}/{post_id}/comments"
    page_count = 0

    while url and page_count < 5:
        page_count += 1
        try:
            r = requests.get(url, params=params if page_count == 1 else None, timeout=30)
            data = r.json()
        except Exception as e:
            return all_comments, str(e)
        if "error" in data:
            return all_comments, data["error"].get("message", "unknown error")
        all_comments.extend(data.get("data", []))
        url = (data.get("paging") or {}).get("next") or ""

    return all_comments, ""


def _get_page_tokens(user_token: str) -> dict:
    """Đổi user token → {page_id: page_access_token} cho tất cả pages."""
    try:
        r = requests.get(
            f"{FB_BASE_URL}/me/accounts",
            params={"access_token": user_token, "limit": 50},
            timeout=15,
        )
        data = r.json()
        if "error" in data:
            return {}
        return {p["id"]: p["access_token"] for p in data.get("data", []) if p.get("access_token")}
    except Exception:
        return {}


def fetch_comments_for_accounts(ads_by_bm: dict) -> dict:
    """Fetch comments grouped by campaign/ad.

    Input: {bm: [(ad_id, campaign_id, campaign_name, ad_name), ...]}
    Returns: {campaigns, total_comments, fetched_at, errors}

    Dùng FB_PAGE_TOKEN (user token) để đổi lấy page-specific tokens,
    sau đó dùng page token đúng page để đọc comments.
    """
    user_token = os.environ.get("FB_PAGE_TOKEN", "").strip()

    campaigns: dict = {}
    errors: list = []
    total = 0

    if not user_token:
        return {
            "campaigns": {},
            "total_comments": 0,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "errors": ["Chưa cấu hình FB_PAGE_TOKEN. Xem hướng dẫn trong tab Bình luận."],
            "need_setup": True,
        }

    # Đổi user token → page tokens (mỗi page có token riêng)
    page_tokens = _get_page_tokens(user_token)
    if not page_tokens:
        errors.append("Không lấy được page token. Token hết hạn hoặc thiếu quyền pages_show_list.")

    # Gom tất cả post_ids từ các BM (dùng BM token để lấy creative)
    all_post_to_ads: dict = {}
    for bm, ads in ads_by_bm.items():
        bm_token = os.environ.get(f"FB_TOKEN_{bm}", "").strip()
        if not bm_token or not ads:
            continue
        ad_ids = [a[0] for a in ads]
        post_id_map = _fetch_ad_post_ids(bm_token, ad_ids)
        for ad_id, campaign_id, campaign_name, ad_name in ads:
            post_id = post_id_map.get(ad_id)
            if not post_id:
                continue
            all_post_to_ads.setdefault(post_id, []).append(
                (ad_id, campaign_id, campaign_name, ad_name)
            )

    if not all_post_to_ads:
        errors.append("Không lấy được post_id từ ads (cache chưa tải hoặc không có ad ACTIVE hôm nay)")

    # Fetch comments — dùng page token khớp với page_id trong post_id
    comment_errors: list = []
    for post_id, post_ads in all_post_to_ads.items():
        # post_id format: "page_id_post_id" hoặc "page_id_video_id"
        page_id = post_id.split("_")[0]
        token = page_tokens.get(page_id, user_token)  # fallback user token
        comments, err = _fetch_post_comments(token, post_id)
        if err and err not in comment_errors:
            comment_errors.append(err)
        for ad_id, campaign_id, campaign_name, ad_name in post_ads:
            camp = campaigns.setdefault(campaign_id, {
                "campaign_name": campaign_name,
                "ads": {},
            })
            camp["ads"][ad_id] = {
                "ad_name": ad_name,
                "post_id": post_id,
                "comments": comments,
                "comment_count": len(comments),
            }
            total += len(comments)

    if comment_errors:
        errors.append("Lỗi đọc comment: " + comment_errors[0])

    return {
        "campaigns": campaigns,
        "total_comments": total,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "errors": errors,
    }


def sync_comments_to_db(ads_by_bm: dict, db) -> dict:
    """Incremental sync: chỉ fetch comments mới hơn MAX(created_time) per post_id.

    ads_by_bm: {bm: [(ad_id, campaign_id, campaign_name, ad_name), ...]}
    db: module comments_db (passed in để tránh circular import).
    Trả {synced, skipped, errors}.
    """
    user_token = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not user_token:
        return {"synced": 0, "skipped": 0, "errors": ["FB_PAGE_TOKEN chưa cấu hình"]}

    page_tokens = _get_page_tokens(user_token)
    if not page_tokens:
        return {"synced": 0, "skipped": 0, "errors": ["Không lấy được page token — token hết hạn?"]}

    # Lấy latest timestamp từ DB cho từng post (1 query duy nhất)
    latest_ts_map = db.get_latest_ts_by_post()

    # Build post_id → meta map từ BM tokens
    post_to_meta: dict = {}
    for bm, ads in ads_by_bm.items():
        bm_token = os.environ.get(f"FB_TOKEN_{bm}", "").strip()
        if not bm_token or not ads:
            continue
        ad_ids = [a[0] for a in ads]
        post_id_map = _fetch_ad_post_ids(bm_token, ad_ids)
        for ad_id, campaign_id, campaign_name, ad_name in ads:
            post_id = post_id_map.get(ad_id)
            if not post_id:
                continue
            # Ưu tiên ad đầu tiên gắn với post này
            if post_id not in post_to_meta:
                post_to_meta[post_id] = {
                    "ad_id": ad_id,
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_name,
                    "ad_name": ad_name,
                }

    synced = 0
    skipped = 0
    errors: list = []

    for post_id, meta in post_to_meta.items():
        page_id = post_id.split("_")[0]
        token = page_tokens.get(page_id, user_token)
        since_ts = latest_ts_map.get(post_id)  # None = backfill toàn bộ

        comments, err = _fetch_post_comments(token, post_id, since_ts=since_ts)
        if err:
            if err not in errors:
                errors.append(err)
            continue
        if not comments:
            skipped += 1
            continue

        rows = []
        for c in comments:
            cid = c.get("id")
            if not cid:
                continue
            rows.append({
                "comment_id": cid,
                "post_id": post_id,
                "page_id": page_id,
                "ad_id": meta["ad_id"],
                "campaign_id": meta["campaign_id"],
                "campaign_name": meta["campaign_name"],
                "ad_name": meta["ad_name"],
                "commenter_name": (c.get("from") or {}).get("name", ""),
                "message": c.get("message", ""),
                "created_time": c.get("created_time"),
                "like_count": c.get("like_count", 0),
                "label": db.classify(c.get("message", "")),
                "label_source": "rule",
            })

        synced += db.upsert_comments(rows)

    return {"synced": synced, "skipped": skipped, "errors": errors}


def fetch_all_ads(date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
    """Fetch ad-level từ tất cả 6 TK trong range [date_from, date_to].

    Mặc định date_from = HÔM NAY (data có sẵn từ FB Marketing API real-time).
    """
    if not date_from:
        date_from = date.today().isoformat()
    if not date_to:
        date_to = date_from

    all_ads = []
    errors = []
    for acc in AD_ACCOUNTS:
        ads, err = fetch_account_ads(acc, date_from, date_to)
        all_ads.extend(ads)
        if err:
            errors.append(err)

    return {
        "ads": all_ads,
        "errors": errors,
        "date_from": date_from,
        "date_to": date_to,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _sum_purchases_revenue(row: dict) -> tuple[int, float]:
    """purchases + revenue thật (omni_purchase) từ 1 row insights có actions/action_values."""
    actions = row.get("actions") or []
    purchases = sum(
        int(a.get("value", 0) or 0) for a in actions if a.get("action_type") in PURCHASE_TYPES
    )
    action_values = row.get("action_values") or []
    revenue = sum(
        float(av.get("value") or 0) for av in action_values if av.get("action_type") in PURCHASE_TYPES
    )
    return purchases, revenue


def _fetch_age_breakdown_one_account(account: dict, date_from: str, date_to: str) -> dict:
    """Returns {ad_id: {age_bucket: {spend, purchases, revenue}}} cho 1 account — số THẬT
    từ FB Insights (breakdowns=age), không phải gán cả ad vào 1 nhóm ăn spend nhiều nhất."""
    out = {}
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return out
    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "ad_id,spend,actions,action_values",
        "level": "ad",
        "breakdowns": "age",
        "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
        "filtering": '[{"field":"ad.effective_status","operator":"IN",'
                     '"value":["ACTIVE","PAUSED","CAMPAIGN_PAUSED","ADSET_PAUSED",'
                     '"WITH_ISSUES","PENDING_REVIEW","IN_PROCESS"]}]',
        "limit": 500,
    }
    next_url = url
    page = 0
    while next_url and page < 30:
        page += 1
        try:
            r = requests.get(next_url, params=params if page == 1 else None, timeout=45)
            data = r.json()
        except Exception:
            break
        if "error" in data:
            break
        for row in data.get("data", []):
            aid = row.get("ad_id")
            age = row.get("age") or "?"
            spend = float(row.get("spend") or 0) * (1 + AD_VAT_RATE)
            purchases, revenue = _sum_purchases_revenue(row)
            if not aid:
                continue
            bucket = out.setdefault(aid, {}).setdefault(age, {"spend": 0.0, "purchases": 0, "revenue": 0.0})
            bucket["spend"] += spend
            bucket["purchases"] += purchases
            bucket["revenue"] += revenue
        next_url = (data.get("paging") or {}).get("next") or ""
    return out


def _merge_bucket_detail(merged: dict, d: dict) -> None:
    """Gộp {ad_id: {bucket: {spend, purchases, revenue}}} — cộng dồn nếu trùng ad_id+bucket
    (không nên xảy ra vì 1 ad_id chỉ thuộc 1 account, nhưng an toàn khi có)."""
    for aid, by_bucket in d.items():
        dest = merged.setdefault(aid, {})
        for bucket, vals in by_bucket.items():
            b = dest.setdefault(bucket, {"spend": 0.0, "purchases": 0, "revenue": 0.0})
            b["spend"] += vals.get("spend", 0)
            b["purchases"] += vals.get("purchases", 0)
            b["revenue"] += vals.get("revenue", 0)


def fetch_age_breakdown(date_from: str, date_to: str) -> dict:
    """Tổng hợp 6 account, trả {ad_id: {age: {spend, purchases, revenue}}}. Parallel."""
    from concurrent.futures import ThreadPoolExecutor
    merged = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_age_breakdown_one_account, acc, date_from, date_to)
                   for acc in AD_ACCOUNTS]
        for fut in futures:
            try:
                d = fut.result()
            except Exception:
                continue
            _merge_bucket_detail(merged, d)
    return merged


def _fetch_gender_breakdown_one_account(account: dict, date_from: str, date_to: str) -> dict:
    """Returns {ad_id: {gender: {spend, purchases, revenue}}} cho 1 account — số THẬT từ FB."""
    out = {}
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return out
    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "ad_id,spend,actions,action_values",
        "level": "ad",
        "breakdowns": "gender",
        "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
        "filtering": '[{"field":"ad.effective_status","operator":"IN",'
                     '"value":["ACTIVE","PAUSED","CAMPAIGN_PAUSED","ADSET_PAUSED",'
                     '"WITH_ISSUES","PENDING_REVIEW","IN_PROCESS"]}]',
        "limit": 500,
    }
    next_url = url
    page = 0
    while next_url and page < 30:
        page += 1
        try:
            r = requests.get(next_url, params=params if page == 1 else None, timeout=45)
            data = r.json()
        except Exception:
            break
        if "error" in data:
            break
        for row in data.get("data", []):
            aid = row.get("ad_id")
            gender = row.get("gender") or "unknown"
            spend = float(row.get("spend") or 0) * (1 + AD_VAT_RATE)
            purchases, revenue = _sum_purchases_revenue(row)
            if not aid:
                continue
            bucket = out.setdefault(aid, {}).setdefault(gender, {"spend": 0.0, "purchases": 0, "revenue": 0.0})
            bucket["spend"] += spend
            bucket["purchases"] += purchases
            bucket["revenue"] += revenue
        next_url = (data.get("paging") or {}).get("next") or ""
    return out


def fetch_gender_breakdown(date_from: str, date_to: str) -> dict:
    """Tổng hợp 6 account, trả {ad_id: {gender: {spend, purchases, revenue}}}. Parallel."""
    from concurrent.futures import ThreadPoolExecutor
    merged = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_gender_breakdown_one_account, acc, date_from, date_to)
                   for acc in AD_ACCOUNTS]
        for fut in futures:
            try:
                d = fut.result()
            except Exception:
                continue
            _merge_bucket_detail(merged, d)
    return merged


def _fetch_one_account_daily(account: dict, date_from: str, date_to: str) -> tuple[list, list]:
    """Fetch daily campaign-level insights cho 1 account. Trả (rows, errors).
    Mỗi row: (date_str, campaign_name, spend_vat, roas_pixel).
    """
    rows = []
    errors = []
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return rows, errors

    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "campaign_name,spend,purchase_roas",
        "level": "campaign",
        "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
        "time_increment": "1",
        "filtering": '[{"field":"ad.effective_status","operator":"IN",'
                     '"value":["ACTIVE","PAUSED","CAMPAIGN_PAUSED","ADSET_PAUSED",'
                     '"WITH_ISSUES","PENDING_REVIEW","IN_PROCESS"]}]',
        "limit": 500,
    }
    next_url = url
    page = 0
    while next_url and page < 20:
        page += 1
        try:
            r = requests.get(next_url, params=params if page == 1 else None, timeout=45)
            data = r.json()
        except Exception as e:
            errors.append(f"TK {account['name']}: {e}")
            break
        if "error" in data:
            errors.append(f"TK {account['name']}: {data['error'].get('message', '?')}")
            break
        for row in data.get("data", []):
            d = row.get("date_start") or ""
            if not d:
                continue
            cname = row.get("campaign_name") or ""
            spend = float(row.get("spend") or 0) * (1 + AD_VAT_RATE)
            roas_arr = row.get("purchase_roas") or []
            roas_pixel = next(
                (float(r["value"]) for r in roas_arr if r.get("action_type") == "omni_purchase"),
                0.0,
            ) / (1 + AD_VAT_RATE)
            rows.append((d, cname, spend, roas_pixel))
        next_url = (data.get("paging") or {}).get("next") or ""
    return rows, errors


def fetch_daily_retail_revenue(date_from: str, date_to: str) -> dict:
    """Query Postgres daily_rollup → retail_total per day.
    Trả {dates: [], retail: []}. Trống nếu DB chưa setup.
    """
    db_url = os.environ.get("ROLLUP_DATABASE_URL", "").strip()
    if not db_url:
        return {"dates": [], "retail": [], "total_retail": 0}
    try:
        import psycopg2
    except ImportError:
        return {"dates": [], "retail": [], "total_retail": 0}
    daily = {}
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT date, retail_total FROM daily_rollup "
                    "WHERE date >= %s AND date <= %s ORDER BY date",
                    (date_from, date_to),
                )
                for row in cur.fetchall():
                    d, retail = row[0], int(row[1] or 0)
                    daily[d] = retail
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️  daily_rollup query fail: {e}")
        return {"dates": [], "retail": [], "total_retail": 0}
    dates = sorted(daily.keys())
    retail = [daily[d] for d in dates]
    return {"dates": dates, "retail": retail, "total_retail": sum(retail)}


def _fetch_one_account_daily_mess(account: dict, date_from: str, date_to: str) -> list:
    """Fetch daily spend + messages + revenue. Trả list (date_str, spend_vat, mess, revenue)."""
    rows = []
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return rows
    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "spend,actions,action_values",
        "level": "account",
        "time_range": f'{{"since":"{date_from}","until":"{date_to}"}}',
        "time_increment": "1",
        "limit": 500,
    }
    next_url = url
    page = 0
    while next_url and page < 20:
        page += 1
        try:
            r = requests.get(next_url, params=params if page == 1 else None, timeout=45)
            data = r.json()
        except Exception:
            break
        if "error" in data:
            break
        for row in data.get("data", []):
            d = row.get("date_start") or ""
            if not d:
                continue
            spend = float(row.get("spend") or 0) * (1 + AD_VAT_RATE)
            actions = row.get("actions") or []
            mess = next((int(a.get("value", 0) or 0)
                         for a in actions if a.get("action_type") == MSG_ACTION), 0)
            action_values = row.get("action_values") or []
            revenue = sum(float(av.get("value") or 0) for av in action_values
                           if av.get("action_type") in PURCHASE_TYPES)
            rows.append((d, spend, mess, revenue))
        next_url = (data.get("paging") or {}).get("next") or ""
    return rows


def fetch_daily_cost_per_mess(date_from: str, date_to: str) -> dict:
    """Daily cost per mess + ROAS aggregated theo ngày, 6 TK song song."""
    from concurrent.futures import ThreadPoolExecutor
    daily = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_one_account_daily_mess, acc, date_from, date_to)
                   for acc in AD_ACCOUNTS]
        for fut in futures:
            try:
                for d, spend, mess, revenue in fut.result():
                    bucket = daily.setdefault(d, {"spend": 0.0, "mess": 0, "revenue": 0.0})
                    bucket["spend"] += spend
                    bucket["mess"] += mess
                    bucket["revenue"] += revenue
            except Exception:
                continue
    dates = sorted(daily.keys())
    spends = [round(daily[d]["spend"]) for d in dates]
    messages = [daily[d]["mess"] for d in dates]
    revenues = [round(daily[d]["revenue"]) for d in dates]
    cost_per_mess = [round(s / m) if m > 0 else 0 for s, m in zip(spends, messages)]
    # ROAS post-VAT: revenue / spend_vat
    roas = [round(r / s, 2) if s > 0 else 0.0 for s, r in zip(spends, revenues)]
    total_spend = sum(spends)
    total_mess = sum(messages)
    total_rev = sum(revenues)
    avg_cpm = round(total_spend / total_mess) if total_mess > 0 else 0
    avg_roas = round(total_rev / total_spend, 2) if total_spend > 0 else 0.0
    return {
        "dates": dates,
        "spend": spends,
        "messages": messages,
        "revenue": revenues,
        "cost_per_mess": cost_per_mess,
        "roas": roas,
        "avg_cost_per_mess": avg_cpm,
        "avg_roas": avg_roas,
        "total_spend": total_spend,
        "total_mess": total_mess,
        "total_revenue": total_rev,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_daily_spend_by_tier(date_from: str, date_to: str) -> dict:
    """Fetch chi tiêu mỗi ngày, group theo Rule v3.2: SCALE / GIỮ / TẮT.

    Trả {dates, scale, giu, tat, errors, fetched_at}.
    Phân loại dựa trên ROAS Facebook chính thức tích lũy toàn kỳ của từng campaign.
    """
    from concurrent.futures import ThreadPoolExecutor
    from rules import TRAM1_SPEND, SCALE_ROAS, PASS_ROAS

    all_rows = []  # [(date, cname, spend, roas_pixel)]
    errors = []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_one_account_daily, acc, date_from, date_to)
                   for acc in AD_ACCOUNTS]
        for fut in futures:
            try:
                rows, errs = fut.result()
            except Exception as e:
                errors.append(str(e))
                continue
            errors.extend(errs)
            all_rows.extend(rows)

    # Tính tổng spend + revenue từng campaign để xác định ROAS tích lũy
    camp_totals = {}  # {cname: {spend, revenue}}
    for d, cname, spend, roas_pixel in all_rows:
        t = camp_totals.setdefault(cname, {"spend": 0.0, "revenue": 0.0})
        t["spend"] += spend
        # revenue = spend × roas_pixel (vì roas = revenue/spend)
        t["revenue"] += spend * roas_pixel

    # Phân loại từng campaign
    def _camp_grade(cname: str) -> str:
        t = camp_totals.get(cname, {})
        s = t.get("spend", 0.0)
        if s < TRAM1_SPEND:
            return "tat"  # chưa đủ ngưỡng — gộp vào TẮT cho chart
        roas = t.get("revenue", 0.0) / s   # ROAS Facebook chính thức
        if roas >= SCALE_ROAS:
            return "scale"
        if roas >= PASS_ROAS:
            return "giu"
        return "tat"

    daily = {}  # {date: {scale, giu, tat}}
    for d, cname, spend, _ in all_rows:
        bucket = daily.setdefault(d, {"scale": 0.0, "giu": 0.0, "tat": 0.0})
        bucket[_camp_grade(cname)] += spend

    dates = sorted(daily.keys())
    return {
        "dates": dates,
        "scale": [round(daily[d]["scale"]) for d in dates],
        "giu": [round(daily[d]["giu"]) for d in dates],
        "tat": [round(daily[d]["tat"]) for d in dates],
        # backward-compat aliases (tofu=scale+giu, bofu=tat) để cũ không crash
        "tofu": [round(daily[d]["scale"] + daily[d]["giu"]) for d in dates],
        "bofu": [round(daily[d]["tat"]) for d in dates],
        "errors": errors,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
