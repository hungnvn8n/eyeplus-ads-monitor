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
AD_VAT_RATE = 0.10  # khớp dashboard chính

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
        "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,actions,purchase_roas",
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


def _fetch_age_breakdown_one_account(account: dict, date_from: str, date_to: str) -> dict:
    """Returns {ad_id: {age_bucket: spend_vat}} cho 1 account."""
    out = {}
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return out
    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "ad_id,spend",
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
            if not aid:
                continue
            bucket = out.setdefault(aid, {})
            bucket[age] = bucket.get(age, 0) + spend
        next_url = (data.get("paging") or {}).get("next") or ""
    return out


def fetch_age_breakdown(date_from: str, date_to: str) -> dict:
    """Tổng hợp 6 account, trả {ad_id: {age: spend_vat}}. Parallel."""
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
            for aid, by_age in d.items():
                if aid not in merged:
                    merged[aid] = by_age
                else:
                    for age, sp in by_age.items():
                        merged[aid][age] = merged[aid].get(age, 0) + sp
    return merged


def _fetch_gender_breakdown_one_account(account: dict, date_from: str, date_to: str) -> dict:
    """Returns {ad_id: {gender: spend_vat}} cho 1 account."""
    out = {}
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return out
    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "ad_id,spend",
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
            if not aid:
                continue
            bucket = out.setdefault(aid, {})
            bucket[gender] = bucket.get(gender, 0) + spend
        next_url = (data.get("paging") or {}).get("next") or ""
    return out


def fetch_gender_breakdown(date_from: str, date_to: str) -> dict:
    """Tổng hợp 6 account, trả {ad_id: {gender: spend_vat}}. Parallel."""
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
            for aid, by_gender in d.items():
                if aid not in merged:
                    merged[aid] = by_gender
                else:
                    for g, sp in by_gender.items():
                        merged[aid][g] = merged[aid].get(g, 0) + sp
    return merged


def _fetch_one_account_daily(account: dict, date_from: str, date_to: str) -> tuple[list, list]:
    """Fetch daily campaign-level insights cho 1 account. Trả (rows, errors).
    Mỗi row: (date_str, campaign_name, spend_vat). Classify ở caller (giảm import overhead per-thread).
    """
    rows = []
    errors = []
    token = os.environ.get(account["token_env"], "").strip()
    if not token:
        return rows, errors

    url = f"{FB_BASE_URL}/{account['account_id']}/insights"
    params = {
        "access_token": token,
        "fields": "campaign_name,spend",
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
            rows.append((d, cname, spend))
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
    """Fetch chi tiêu mỗi ngày, group TOFU/BOFU. 6 account chạy song song qua ThreadPool.

    Trả {dates, tofu, bofu, errors, fetched_at}. Tận dụng FB time_increment=1.
    """
    from concurrent.futures import ThreadPoolExecutor
    from rules import classify

    daily = {}  # {date_str: {tofu, bofu}}
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
            for d, cname, spend in rows:
                tier = classify({"campaign_name": cname})
                bucket = daily.setdefault(d, {"tofu": 0.0, "bofu": 0.0})
                bucket[tier] += spend

    dates = sorted(daily.keys())
    return {
        "dates": dates,
        "tofu": [round(daily[d]["tofu"]) for d in dates],
        "bofu": [round(daily[d]["bofu"]) for d in dates],
        "errors": errors,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
