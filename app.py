"""fb_ad_local — Local Flask dashboard cho FB Ads phễu Eye Plus.

Chạy: python app.py
Dashboard: http://localhost:5050
"""

import calendar
import json
import os
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
import reports as rpt

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from fetcher import (fetch_all_ads, fetch_daily_spend_by_tier,
                     fetch_daily_cost_per_mess, fetch_age_breakdown, fetch_gender_breakdown,
                     fetch_daily_retail_revenue, FB_BASE_URL)
from rules import (
    DEFAULT_AUTO_PAUSE_RULES, auto_pause_decision, classify,
    evaluate, grade, matching_rule,
)
from tiktok_fetcher import (fetch_tiktok_campaigns, fetch_tiktok_ads,
                            fetch_tiktok_campaign_daily, fetch_tiktok_campaign_ads)

# Detect runtime: railway | frozen (desktop binary) | dev
# Railway: persistent files vào /data (mount volume) nếu có, fallback CWD
# Frozen: launcher.py đã chdir tới user data dir (~/Library/.../EyePlusAds)
# Dev: cạnh file app.py
IS_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RUN_MODE") == "railway")

if IS_RAILWAY:
    # Volume mount tại /data nếu đã setup; nếu chưa có thì fallback CWD (ephemeral)
    _vol = Path("/data")
    ROOT = _vol if _vol.exists() and _vol.is_dir() else Path(os.getcwd())
elif getattr(sys, "frozen", False):
    ROOT = Path(os.getcwd())
else:
    ROOT = Path(__file__).resolve().parent

# Load .env: trên Railway env vars set ở dashboard nên .env không bắt buộc
load_dotenv(ROOT / ".env")
if IS_RAILWAY:
    # Cũng load .env ở source code dir nếu có (cho local Railway test)
    load_dotenv(Path(__file__).resolve().parent / ".env")

# ─── Bundled secrets (FB tokens) ──────────────────────────────────────────
# CI workflow tạo file _bundled_secrets.py với FB tokens trước khi PyInstaller.
# Module này được include qua hiddenimports. Khi user mở app không cần điền token.
# Env .env vẫn ưu tiên cao hơn — nếu user muốn override (token mới hơn) thì đặt vào .env.
try:
    import _bundled_secrets as _bs
    for _k, _v in getattr(_bs, "BUNDLED_TOKENS", {}).items():
        if _v and not os.environ.get(_k):
            os.environ[_k] = _v
except ImportError:
    pass  # dev mode hoặc CI chưa inject — fallback đọc .env

# ─── License / Remote kill switch ─────────────────────────────────────────
import uuid as _uuid

# Default trỏ tới Railway endpoint của Eye Plus. Override qua env nếu cần.
_DEFAULT_LICENSE_URL = "https://eyeplus-fb-ads-bot-production.up.railway.app/app/api/eyeplus-ads-license"
LICENSE_CHECK_URL = (os.environ.get("LICENSE_CHECK_URL") or _DEFAULT_LICENSE_URL).strip()
LICENSE_KEY = os.environ.get("LICENSE_KEY", "").strip()
LICENSE_CHECK_INTERVAL_HOURS = int(os.environ.get("LICENSE_CHECK_INTERVAL_HOURS", "6"))
_INSTALL_ID_FILE = ROOT / ".install_id"
_LICENSE_STATE = {"ok": True, "last_check": None, "message": "", "checked": False}


def _get_install_id() -> str:
    """ID duy nhất per install. Lưu vào .install_id."""
    if _INSTALL_ID_FILE.exists():
        try:
            iid = _INSTALL_ID_FILE.read_text().strip()
            if iid:
                return iid
        except Exception:
            pass
    iid = _uuid.uuid4().hex
    try:
        _INSTALL_ID_FILE.write_text(iid)
    except Exception:
        pass
    return iid


def check_license_once(key: str = None) -> dict:
    """Ping license server. Trả {ok, message}.

    Args:
        key: license key override. Nếu None → dùng LICENSE_KEY từ env.

    Logic:
    - Nếu LICENSE_CHECK_URL trống → bypass (dev mode, return ok=True)
    - Nếu key trống → fail "Cần License Key"
    - HTTP fail (network) → fallback OK (đừng lock user vì network)
    - Server trả 403/ok=false → block
    """
    use_key = (key or LICENSE_KEY).strip()
    if not LICENSE_CHECK_URL:
        return {"ok": True, "message": "no license server configured", "skipped": True}
    if not use_key:
        return {"ok": False, "message": "Cần License Key (xin từ admin)"}
    import requests as _rq
    try:
        r = _rq.get(
            LICENSE_CHECK_URL,
            params={"key": use_key, "install_id": _get_install_id()},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json() if r.content else {}
            return {"ok": bool(data.get("ok", True)),
                    "message": data.get("message", "active"),
                    "status": data.get("status", "active")}
        elif r.status_code == 403:
            data = r.json() if r.content else {}
            return {"ok": False,
                    "message": data.get("message", "License đã bị tắt bởi admin"),
                    "status": "disabled"}
        else:
            # 5xx, timeout → fallback OK (giữ user app chạy được khi server lỗi)
            return {"ok": True, "message": f"server error {r.status_code}, fallback OK",
                    "fallback": True}
    except Exception as e:
        return {"ok": True, "message": f"network fail: {e}, fallback OK", "fallback": True}


def check_for_update() -> dict:
    """Fetch latest release từ GitHub. So sánh tag với APP_VERSION.

    Trả dict update state + cập nhật _UPDATE_STATE global.
    """
    import requests as _rq
    try:
        r = _rq.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=10,
            headers={"Accept": "application/vnd.github+json"},
        )
        if r.status_code != 200:
            _UPDATE_STATE["checked_at"] = datetime.now().isoformat(timespec="seconds")
            return _UPDATE_STATE
        data = r.json()
        latest = (data.get("tag_name") or "").lstrip("v").strip()
        if not latest:
            return _UPDATE_STATE

        # So sánh version đơn giản (string compare cho semver X.Y.Z)
        def _vtuple(v):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0, 0, 0)
        is_newer = _vtuple(latest) > _vtuple(APP_VERSION)

        # Chọn asset theo OS
        assets = data.get("assets") or []
        download_url = None
        if sys.platform == "darwin":
            asset = next((a for a in assets if a["name"].endswith(".dmg")), None)
            download_url = asset["browser_download_url"] if asset else None
        elif sys.platform == "win32":
            asset = next((a for a in assets if a["name"].endswith(".exe")), None)
            download_url = asset["browser_download_url"] if asset else None

        _UPDATE_STATE.update({
            "available": is_newer,
            "current": APP_VERSION,
            "latest": latest,
            "download_url": download_url,
            "release_url": data.get("html_url"),
            "notes": (data.get("body") or "")[:500],
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        })
        if is_newer:
            print(f"🆕 Bản mới: v{latest} (đang dùng v{APP_VERSION}) → {download_url}")
        return _UPDATE_STATE
    except Exception as e:
        _UPDATE_STATE["checked_at"] = datetime.now().isoformat(timespec="seconds")
        _UPDATE_STATE["error"] = str(e)
        return _UPDATE_STATE


def check_license_and_update() -> dict:
    """Run check + update _LICENSE_STATE."""
    result = check_license_once()
    _LICENSE_STATE["ok"] = result["ok"]
    _LICENSE_STATE["last_check"] = datetime.now().isoformat(timespec="seconds")
    _LICENSE_STATE["message"] = result.get("message", "")
    _LICENSE_STATE["checked"] = True
    if not result["ok"]:
        print(f"⛔ License invalid: {result.get('message')}")
    elif result.get("skipped"):
        print(f"ℹ️  License check skipped (no URL configured)")
    elif result.get("fallback"):
        print(f"⚠️  License check fallback: {result.get('message')}")
    else:
        print(f"✅ License OK")
    return result

CACHE_FILE = ROOT / "cache.json"
AUTO_PAUSE_LOG = ROOT / "auto_pause_log.jsonl"
BUDGET_LOG = ROOT / "budget_log.jsonl"
RULES_FILE = ROOT / "rules.json"

# Version + GitHub repo cho auto-update check
APP_VERSION = "1.0.25"
GITHUB_REPO = "hungnvn8n/eyeplus-ads-monitor"
UPDATE_CHECK_INTERVAL_HOURS = int(os.getenv("UPDATE_CHECK_INTERVAL_HOURS", "24"))
_UPDATE_STATE = {"available": False, "current": APP_VERSION,
                 "latest": None, "download_url": None, "release_url": None,
                 "checked_at": None}
REFRESH_INTERVAL_HOURS = int(os.getenv("REFRESH_INTERVAL_HOURS", "3"))
AUTO_PAUSE_INTERVAL_HOURS = int(os.getenv("AUTO_PAUSE_INTERVAL_HOURS", "8"))
AUTO_PAUSE_LOOKBACK_DAYS = int(os.getenv("AUTO_PAUSE_LOOKBACK_DAYS", "7"))
# Chế độ ĐỐI CHỨNG (quy tắc v3 chạy ngầm, chỉ ghi nhận — xem shadow.py).
# Mặc định TẮT; chỉ máy admin bật SHADOW_MODE=true trong .env. Không hiện trên nav.
SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"
CACHE_TTL_SEC = REFRESH_INTERVAL_HOURS * 3600

_lock = threading.Lock()
# Cache theo (date_from, date_to) key. Mỗi entry: {data, fetched_at, errors, date_from, date_to}
_state_by_range: dict = {}
# Lock cho mỗi range đang fetch (để tránh fetch trùng song song)
_fetching: set = set()
# Campaign IDs đã pause (qua app này) — override is_paused across mọi cache entry
_paused_campaign_ids: set = set()
# Cache daily-spend-by-tier (cho stacked bar chart) — key giống state_by_range
_daily_spend_by_range: dict = {}
# Cache daily cost-per-mess (line chart)
_daily_cpm_by_range: dict = {}
# Cache budget log per (cid, frm, to) — TTL 30 min, FB Activity Log chậm
_budget_log_cache: dict = {}
BUDGET_LOG_CACHE_TTL_SEC = 30 * 60


def load_rules() -> list:
    """Load auto-pause rules từ rules.json. Tạo mới với default nếu chưa có."""
    if not RULES_FILE.exists():
        save_rules(DEFAULT_AUTO_PAUSE_RULES)
        return [dict(r) for r in DEFAULT_AUTO_PAUSE_RULES]
    try:
        with RULES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "rules" in data:
            return data["rules"]
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"⚠️  rules.json load failed: {e} — dùng default")
    return [dict(r) for r in DEFAULT_AUTO_PAUSE_RULES]


def save_rules(rules: list) -> None:
    try:
        with RULES_FILE.open("w", encoding="utf-8") as f:
            json.dump({"rules": rules}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️  rules.json save failed: {e}")


def get_config() -> dict:
    return {
        "tofu_mess_max": int(os.getenv("TOFU_MESS_MAX", "60000")),
        "tofu_min_spend": int(os.getenv("TOFU_MIN_SPEND", "20000")),
        "bofu_mess_max": int(os.getenv("BOFU_MESS_MAX", "100000")),
        "bofu_roas_min": float(os.getenv("BOFU_ROAS_MIN", "2.5")),
        "bofu_min_spend": int(os.getenv("BOFU_MIN_SPEND", "50000")),
    }


def resolve_preset(preset: str) -> tuple[str, str]:
    """Trả (date_from, date_to) cho preset."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    if preset == "today":
        return today.isoformat(), today.isoformat()
    if preset == "yesterday":
        return yesterday.isoformat(), yesterday.isoformat()
    if preset == "3d":
        return (today - timedelta(days=3)).isoformat(), yesterday.isoformat()
    if preset == "7d":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    if preset == "30d":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    if preset == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if preset == "last_month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start.isoformat(), last_month_end.isoformat()
    # fallback today
    return today.isoformat(), today.isoformat()


def range_key(frm: str, to: str) -> str:
    return f"{frm}|{to}"


def process_ads(raw_ads: list, cfg: dict, rules: list | None = None) -> list:
    if rules is None:
        rules = load_rules()
    out = []
    for ad in raw_ads:
        tier = classify(ad)
        action, reason = evaluate(ad, tier, cfg)
        g = grade(ad, tier, action, cfg)
        matched = matching_rule(rules, ad, tier)
        out.append({**ad, "tier": tier, "action": action, "reason": reason,
                    "grade": g,
                    "auto_pause": matched is not None,
                    "auto_pause_rule_id": matched["id"] if matched else None,
                    "auto_pause_rule_name": matched["name"] if matched else None})
    return out


def _token_for_bm(bm: str) -> str:
    return os.environ.get(f"FB_TOKEN_{bm}", "").strip()


def _fb_error_summary(err: dict) -> dict:
    """Extract đầy đủ field từ FB API error object."""
    return {
        "message": err.get("message", "FB API error"),
        "code": err.get("code"),
        "subcode": err.get("error_subcode"),
        "user_title": err.get("error_user_title"),
        "user_msg": err.get("error_user_msg"),
    }


def _set_campaign_status(campaign_id: str, token: str, new_status: str) -> dict:
    """Set status campaign (PAUSED/ACTIVE). Trả {ok, error/error_detail}.

    Khi thành công: track campaign_id trong _paused_campaign_ids để
    override is_paused trên mọi cache entry → UI không bị stale.
    """
    try:
        r = requests.post(
            f"{FB_BASE_URL}/{campaign_id}",
            params={"access_token": token},
            data={"status": new_status},
            timeout=15,
        )
        res = r.json() if r.content else {}
    except Exception as e:
        return {"ok": False, "error": str(e), "error_detail": None}
    if "error" in res:
        det = _fb_error_summary(res["error"])
        return {
            "ok": False,
            "error": det["user_msg"] or det["user_title"] or det["message"],
            "error_detail": det,
        }
    # Update global set + propagate vào mọi cache entry
    with _lock:
        if new_status == "PAUSED":
            _paused_campaign_ids.add(campaign_id)
        else:
            _paused_campaign_ids.discard(campaign_id)
        _propagate_paused_state_locked()
    save_cache_to_disk()
    return {"ok": True, "error": None, "error_detail": None}


def _propagate_paused_state_locked() -> None:
    """Override is_paused cho mọi ad có campaign_id trong _paused_campaign_ids.
    Phải được gọi trong context của _lock.
    """
    for entry in _state_by_range.values():
        for ad in entry.get("data") or []:
            cid = ad.get("campaign_id")
            if cid in _paused_campaign_ids:
                ad["is_paused"] = True
                if ad.get("effective_status") == "ACTIVE":
                    ad["effective_status"] = "CAMPAIGN_PAUSED"


def _run_auto_pause(processed: list) -> list:
    """Scan processed ads → pause CAMPAIGN (không pause từng ad).

    Pause campaign-level đơn giản, bypass ad-level policy check.
    Dedupe theo campaign_id — 1 campaign có nhiều ad đạt rule chỉ pause 1 lần.
    """
    if os.environ.get("ENABLE_AUTO_PAUSE", "true").lower() != "true":
        return []
    actions = []
    seen_campaigns = set()  # dedupe

    # Group ads by campaign trước, tổng hợp chi tiêu campaign
    by_camp = {}
    for ad in processed:
        if not ad.get("auto_pause"):
            continue
        if ad.get("is_paused"):
            continue
        cid = ad.get("campaign_id")
        if not cid:
            continue
        if cid not in by_camp:
            by_camp[cid] = {
                "campaign_id": cid,
                "campaign_name": ad.get("campaign_name", ""),
                "bm": ad.get("bm", ""),
                "ad_ids": [],
                "total_spend": 0.0,
                "trigger_ads": [],
                "rule_id": ad.get("auto_pause_rule_id"),
                "rule_name": ad.get("auto_pause_rule_name"),
            }
        by_camp[cid]["ad_ids"].append(ad.get("ad_id"))
        by_camp[cid]["total_spend"] += float(ad.get("spend") or 0)
        by_camp[cid]["trigger_ads"].append({
            "ad_id": ad.get("ad_id"),
            "spend": ad.get("spend"),
            "cost_per_message": ad.get("cost_per_message"),
        })

    # Pause từng campaign
    for cid, info in by_camp.items():
        if cid in seen_campaigns:
            continue
        seen_campaigns.add(cid)
        bm = info["bm"]
        token = _token_for_bm(bm)
        if not token:
            actions.append({
                "campaign_id": cid,
                "campaign_name": info["campaign_name"],
                "spend": info["total_spend"],
                "ad_count": len(info["ad_ids"]),
                "ok": False,
                "error": f"Thiếu FB_TOKEN_{bm}",
                "error_detail": None,
            })
            continue
        result = _set_campaign_status(cid, token, "PAUSED")
        # Trigger info: ad cụ thể trigger rule (để debug)
        trigger = info["trigger_ads"][0] if info["trigger_ads"] else {}
        actions.append({
            "campaign_id": cid,
            "campaign_name": info["campaign_name"],
            "ad_id": trigger.get("ad_id"),
            "ad_count": len(info["ad_ids"]),
            "spend": info["total_spend"],
            "cost_per_message": trigger.get("cost_per_message"),
            "rule_id": info.get("rule_id"),
            "rule_name": info.get("rule_name"),
            "ok": result["ok"],
            "error": result["error"],
            "error_detail": result["error_detail"],
        })
        if result["ok"]:
            print(f"  🤖 Auto-paused CAMPAIGN: {info['campaign_name'][:50]} "
                  f"(chi {info['total_spend']:,.0f}đ, {len(info['ad_ids'])} ad trigger)")
        else:
            short = (result["error"] or "")[:100]
            print(f"  ❌ Pause campaign fail {cid}: {short}")
    return actions


def refresh_data(date_from: str | None = None, date_to: str | None = None) -> None:
    """Fetch FB API cho range cụ thể + apply rules + persist."""
    if not date_from:
        date_from = date.today().isoformat()
    if not date_to:
        date_to = date_from
    key = range_key(date_from, date_to)

    with _lock:
        if key in _fetching:
            print(f"  ⏳ Đang fetch sẵn {key}, bỏ qua trùng")
            return
        _fetching.add(key)

    now = datetime.now().isoformat(timespec="seconds")
    print(f"[{now}] 🔄 Refreshing FB Ads {date_from}→{date_to}...")
    try:
        cfg = get_config()
        result = fetch_all_ads(date_from, date_to)

        # Age + gender breakdown từ FB Insights — merge per ad (parallel fetch)
        try:
            age_map = fetch_age_breakdown(date_from, date_to)
            for ad in result["ads"]:
                buckets = age_map.get(ad.get("ad_id"))
                if buckets:
                    ad["age_breakdown"] = buckets
                    ad["dominant_age"] = max(buckets.items(), key=lambda kv: kv[1])[0]
                else:
                    ad["age_breakdown"] = {}
                    ad["dominant_age"] = ""
        except Exception as e:
            print(f"  ⚠️  age breakdown fail: {e}")

        try:
            gender_map = fetch_gender_breakdown(date_from, date_to)
            for ad in result["ads"]:
                ad["gender_breakdown"] = gender_map.get(ad.get("ad_id"), {})
        except Exception as e:
            print(f"  ⚠️  gender breakdown fail: {e}")
            for ad in result["ads"]:
                ad.setdefault("gender_breakdown", {})

        processed = process_ads(result["ads"], cfg)

        with _lock:
            _state_by_range[key] = {
                "data": processed,
                "fetched_at": result["fetched_at"],
                "errors": result.get("errors", []),
                "date_from": result["date_from"],
                "date_to": result["date_to"],
            }
            # Override is_paused cho ads thuộc campaigns đã pause trước đó
            _propagate_paused_state_locked()

        save_cache_to_disk()

        keep = sum(1 for a in processed if a["action"] == "GIỮ")
        pause = sum(1 for a in processed if a["action"] == "TẮT")
        skip = sum(1 for a in processed if a["action"] == "SKIP")
        special = sum(1 for a in processed if a.get("grade") == "special")
        print(f"  ✅ Done — Tổng {len(processed)} ads | "
              f"Đặc biệt: {special} | GIỮ: {keep} | TẮT: {pause} | SKIP: {skip}")
        if result.get("errors"):
            print(f"  ⚠️  Errors: {len(result['errors'])}")
            for e in result["errors"]:
                print(f"     · {e}")
    except Exception as e:
        print(f"  ❌ Refresh failed: {e}")
    finally:
        with _lock:
            _fetching.discard(key)


def save_cache_to_disk() -> None:
    try:
        with _lock:
            payload = {
                "state_by_range": _state_by_range,
                "paused_campaign_ids": sorted(_paused_campaign_ids),
            }
        with CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str, indent=2)
    except Exception as e:
        print(f"⚠️  Cache save failed: {e}")


def load_cache_from_disk() -> None:
    if not CACHE_FILE.exists():
        return
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            saved = json.load(f)
        with _lock:
            # Format mới có wrapper; format cũ là dict raw state
            if isinstance(saved, dict) and "state_by_range" in saved:
                _state_by_range.update(saved.get("state_by_range") or {})
                _paused_campaign_ids.update(saved.get("paused_campaign_ids") or [])
            else:
                # legacy: dict raw state, no paused set
                _state_by_range.update(saved or {})
            _propagate_paused_state_locked()
        total = sum(len(v.get("data") or []) for v in _state_by_range.values())
        print(f"📂 Loaded cache: {len(_state_by_range)} ranges, {total} ads · "
              f"{len(_paused_campaign_ids)} campaigns đã pause")
        # Re-classify tier sau khi load — đảm bảo rules mới áp dụng ngay
        _reprocess_cached_tiers()
    except Exception as e:
        print(f"⚠️  Cache load failed: {e}")


def _reprocess_cached_tiers() -> None:
    """Re-chạy classify/evaluate/grade trên data đã cache — áp dụng rules mới sau deploy."""
    try:
        cfg = get_config()
        rules_list = load_rules()
        count = 0
        with _lock:
            for entry in _state_by_range.values():
                if not entry.get("data"):
                    continue
                entry["data"] = process_ads(entry["data"], cfg, rules_list)
                count += len(entry["data"])
        if count:
            print(f"🔄 Re-classified {count} ads với rules hiện tại")
    except Exception as e:
        print(f"⚠️  Re-classify cache failed: {e}")


# ── Flask app ─────────────────────────────────────────────────────────────────
from functools import wraps
from flask import session, redirect, url_for

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
# Tab "Đối chứng" chỉ hiện trên máy bật SHADOW_MODE (admin) — bản team không thấy
app.jinja_env.globals["SHADOW_MODE"] = SHADOW_MODE
rpt.init_db()


@app.errorhandler(Exception)
def _show_full_error(e):
    """Show traceback trực tiếp lên browser thay vì generic 500 page (giúp debug)."""
    import traceback as _tb
    from werkzeug.exceptions import HTTPException as _HTTPEx
    if isinstance(e, _HTTPEx):
        return e
    tb_str = _tb.format_exc()
    print(f"\n❌ Exception:\n{tb_str}", file=sys.stderr, flush=True)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Lỗi</title>
<style>body{{font-family:ui-monospace,monospace;background:#0a0e1a;color:#e2e8f0;padding:20px;}}
h1{{color:#fb7185;}} pre{{background:#1e293b;padding:16px;border-radius:8px;overflow:auto;
font-size:12px;line-height:1.5;border:1px solid #334155;}}</style></head><body>
<h1>⚠️ Lỗi ứng dụng</h1>
<p>Chụp màn hình toàn bộ trang này gửi admin để fix.</p>
<pre>{tb_str.replace('<', '&lt;').replace('>', '&gt;')}</pre>
<p style="margin-top:20px;"><a href="/" style="color:#60a5fa;">← Thử lại</a></p>
</body></html>""", 500
# Secret key cho session — random per install nếu chưa có
_SECRET_KEY_FILE = ROOT / ".session_key"
if _SECRET_KEY_FILE.exists():
    app.secret_key = _SECRET_KEY_FILE.read_bytes()
else:
    app.secret_key = os.urandom(32)
    try:
        _SECRET_KEY_FILE.write_bytes(app.secret_key)
    except Exception:
        pass

# Session timeout 30 ngày
from datetime import timedelta as _td
app.permanent_session_lifetime = _td(days=30)
# Cho phép session cookie gửi trong cross-site iframe (Railway embed)
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True  # bắt buộc kèm SameSite=None

APP_PASSWORD = os.getenv("APP_PASSWORD", "Eyeplus123@@")
# Token dùng để auto-login khi embed trong iframe cross-site (dashboard fb_chatbot)
# Nếu không set riêng thì dùng APP_PASSWORD làm token mặc định
FBADS_EMBED_TOKEN = os.getenv("FBADS_EMBED_TOKEN", "").strip() or APP_PASSWORD


@app.before_request
def require_auth_globally():
    """Mọi route trừ /login, /logout, static đều cần auth."""
    public = ("login_page", "logout_page", "static", "refresh_endpoint")
    if request.endpoint in public:
        return None
    # Auto-login qua URL token khi embed trong iframe cross-site
    ep_token = request.args.get("ep_token", "").strip()
    if ep_token and ep_token == FBADS_EMBED_TOKEN:
        session["authed"] = True
        session.permanent = True
    if session.get("authed"):
        return None
    # API request → 401 JSON
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Chưa đăng nhập"}), 401
    return redirect(url_for("login_page", next=request.path))


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def _save_license_to_env(key: str) -> None:
    """Append LICENSE_KEY=... vào .env (hoặc update nếu đã có)."""
    env_path = ROOT / ".env"
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("LICENSE_KEY="):
                lines.append(f"LICENSE_KEY={key}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"LICENSE_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """Login bằng MẬT KHẨU + LICENSE KEY.

    - Password: APP_PASSWORD (default Eyeplus123@@)
    - License key: server check single-use binding với install_id
    - Cả 2 đều phải pass → session.authed = True
    """
    global LICENSE_KEY
    error = None
    has_saved_key = bool(LICENSE_KEY.strip())  # nếu đã lưu key trước đó

    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        key = (request.form.get("license_key") or "").strip()

        if pw != APP_PASSWORD:
            error = "Mật khẩu sai."
        elif IS_RAILWAY:
            # Railway: chỉ check password, không cần license key (multi-user shared)
            session["authed"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("overview_page")
            return redirect(next_url)
        elif not key and not has_saved_key:
            error = "Cần nhập License Key (xin từ admin)."
        else:
            # Dùng key user vừa nhập, hoặc key đã lưu trong .env
            check_key = key or LICENSE_KEY
            result = check_license_once(check_key)
            if result["ok"]:
                # Save key vào .env (nếu user vừa nhập mới)
                if key and key != LICENSE_KEY:
                    try:
                        _save_license_to_env(key)
                        LICENSE_KEY = key
                        os.environ["LICENSE_KEY"] = key
                    except Exception as e:
                        print(f"⚠️  Save license fail: {e}")
                session["authed"] = True
                session["license_key"] = check_key
                session.permanent = True
                next_url = request.args.get("next") or url_for("overview_page")
                return redirect(next_url)
            else:
                error = f"License không hợp lệ: {result.get('message', 'unknown')}"

    return render_template("login.html",
                           error=error,
                           has_saved_key=has_saved_key,
                           license_check_url=bool(LICENSE_CHECK_URL),
                           is_railway=IS_RAILWAY)


@app.route("/logout", methods=["GET", "POST"])
def logout_page():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
def overview_page():
    return render_template("overview.html", page="overview",
                           refresh_hours=REFRESH_INTERVAL_HOURS)


@app.route("/accounts")
def accounts_page():
    return render_template("accounts.html", page="accounts",
                           refresh_hours=REFRESH_INTERVAL_HOURS)


@app.route("/campaigns")
def campaigns_page():
    return render_template("campaigns.html", page="campaigns",
                           refresh_hours=REFRESH_INTERVAL_HOURS)


@app.route("/insights")
def insights_page():
    return render_template("insights.html", page="insights",
                           refresh_hours=REFRESH_INTERVAL_HOURS)


@app.route("/auto-log")
def auto_log_page():
    return render_template("auto_log.html", page="auto_log",
                           refresh_hours=REFRESH_INTERVAL_HOURS,
                           auto_pause_hours=AUTO_PAUSE_INTERVAL_HOURS)


@app.route("/tiktok")
@login_required
def tiktok_page():
    return render_template("tiktok.html", page="tiktok")


@app.route("/tiktok/campaigns")
@login_required
def tiktok_campaigns_api():
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    return jsonify(fetch_tiktok_campaigns(date_from, date_to))


@app.route("/tiktok/ads")
@login_required
def tiktok_ads_api():
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    return jsonify(fetch_tiktok_ads(date_from, date_to))


@app.route("/tiktok/campaign/<campaign_id>/daily")
@login_required
def tiktok_campaign_daily_api(campaign_id):
    date_from = request.args.get("date_from", date.today().isoformat())
    date_to = request.args.get("date_to", date_from)
    advertiser_id = request.args.get("advertiser_id", "").strip()
    if not advertiser_id:
        adv_ids = os.environ.get("TIKTOK_ADVERTISER_IDS", "").split(",")
        advertiser_id = adv_ids[0].strip() if adv_ids else ""
    return jsonify(fetch_tiktok_campaign_daily(advertiser_id, campaign_id, date_from, date_to))


@app.route("/tiktok/campaign/<campaign_id>/ads")
@login_required
def tiktok_campaign_ads_api(campaign_id):
    date_from = request.args.get("date_from", date.today().isoformat())
    date_to = request.args.get("date_to", date_from)
    advertiser_id = request.args.get("advertiser_id", "").strip()
    if not advertiser_id:
        adv_ids = os.environ.get("TIKTOK_ADVERTISER_IDS", "").split(",")
        advertiser_id = adv_ids[0].strip() if adv_ids else ""
    return jsonify(fetch_tiktok_campaign_ads(advertiser_id, campaign_id, date_from, date_to))


@app.route("/doichung")
def doichung_page():
    """Trang ĐỐI CHỨNG quy tắc v3 — không có link trên nav, chỉ truy cập trực tiếp URL."""
    if not SHADOW_MODE:
        return redirect(url_for("overview_page"))
    return render_template("doichung.html", page="doichung",
                           refresh_hours=REFRESH_INTERVAL_HOURS)


@app.route("/api/shadow/summary")
def shadow_summary_api():
    if not SHADOW_MODE:
        return jsonify({"ok": False, "error": "SHADOW_MODE chưa bật trong .env"}), 404
    import shadow
    return jsonify({"ok": True, **shadow.get_dashboard_data()})


@app.route("/api/shadow/scan-now", methods=["POST"])
def shadow_scan_now_api():
    if not SHADOW_MODE:
        return jsonify({"ok": False, "error": "SHADOW_MODE chưa bật trong .env"}), 404
    threading.Thread(target=lambda: shadow_scan_job(trigger="manual"), daemon=True).start()
    return jsonify({"ok": True, "message": "Đang quét — F5 sau ~1-2 phút"})


@app.route("/api/shadow/review-now", methods=["POST"])
def shadow_review_now_api():
    if not SHADOW_MODE:
        return jsonify({"ok": False, "error": "SHADOW_MODE chưa bật trong .env"}), 404
    import shadow
    try:
        r = shadow.compute_daily_review()
        return jsonify({"ok": True, "review": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/data")
def api_data():
    preset = request.args.get("preset", "").strip()
    frm = request.args.get("from", "").strip()
    to = request.args.get("to", "").strip()

    if preset and not (frm and to):
        frm, to = resolve_preset(preset)
    if not frm or not to:
        frm, to = resolve_preset("today")
    if not preset:
        preset = "custom"

    key = range_key(frm, to)
    with _lock:
        entry = _state_by_range.get(key)
        is_fresh = bool(entry and (datetime.now() - datetime.fromisoformat(entry["fetched_at"])).total_seconds() < CACHE_TTL_SEC)

    # Fetch nếu chưa có hoặc stale
    if not entry:
        # Synchronous fetch (block ~5-8s cho 6 TK)
        refresh_data(frm, to)
        with _lock:
            entry = _state_by_range.get(key)
    elif not is_fresh:
        # Stale-while-revalidate: trả cache cũ ngay, refresh ngầm
        threading.Thread(target=refresh_data, args=(frm, to), daemon=True).start()

    if not entry:
        return jsonify({
            "preset": preset, "date_from": frm, "date_to": to,
            "fetched_at": None, "errors": [],
            "summary": {"total": 0, "special": 0, "good": 0, "bad": 0, "skip": 0,
                        "keep": 0, "pause": 0, "spend_being_wasted": 0,
                        "spend_total": 0, "spend_good": 0},
            "special": [], "good": [], "bad": [], "skip": [],
        })

    data = list(entry.get("data") or [])
    # Tách paused ads ra section riêng
    active = [a for a in data if not a.get("is_paused")]
    paused = [a for a in data if a.get("is_paused")]

    # Phân loại ACTIVE ads theo grade
    special = [a for a in active if a.get("grade") == "special"]
    good = [a for a in active if a.get("grade") == "good"]
    bad = [a for a in active if a.get("grade") == "bad"]
    skip = [a for a in active if a.get("grade") == "skip"]

    spend_total = sum(float(a.get("spend") or 0) for a in data)
    spend_good = sum(float(a.get("spend") or 0) for a in special + good)
    spend_wasted = sum(float(a.get("spend") or 0) for a in bad)
    spend_paused = sum(float(a.get("spend") or 0) for a in paused)

    for lst in (special, good, bad, skip, paused):
        lst.sort(key=lambda a: -float(a.get("spend") or 0))

    return jsonify({
        "preset": preset,
        "date_from": entry["date_from"],
        "date_to": entry["date_to"],
        "fetched_at": entry["fetched_at"],
        "errors": entry.get("errors") or [],
        "summary": {
            "total": len(data),
            "active": len(active),
            "paused": len(paused),
            "special": len(special),
            "good": len(good),
            "bad": len(bad),
            "skip": len(skip),
            "keep": len(special) + len(good),
            "pause": len(bad),
            "spend_being_wasted": spend_wasted,
            "spend_total": spend_total,
            "spend_good": spend_good,
            "spend_paused": spend_paused,
        },
        "special": special,
        "good": good,
        "bad": bad,
        "skip": skip,
        "paused": paused,
    })


@app.route("/api/campaigns/<campaign_id>/status", methods=["POST"])
def api_campaign_status(campaign_id):
    """Pause hoặc Resume 1 CAMPAIGN (không phải ad).

    Đơn giản hơn pause ad-level — bypass policy check vì campaign-level
    không trigger ad creative revalidation.

    Body JSON: { "status": "PAUSED" | "ACTIVE", "bm": "BM1" | "BM2" | "BM3" }
    """
    body = request.json or {}
    new_status = (body.get("status") or "").strip().upper()
    bm = (body.get("bm") or "").strip().upper()
    if new_status not in ("PAUSED", "ACTIVE"):
        return jsonify({"ok": False, "error": "status phải là PAUSED hoặc ACTIVE"}), 400
    token = _token_for_bm(bm)
    if not token:
        return jsonify({"ok": False, "error": f"Thiếu FB_TOKEN_{bm}"}), 400
    result = _set_campaign_status(campaign_id, token, new_status)
    if not result["ok"]:
        return jsonify({
            "ok": False,
            "error": result["error"],
            "error_detail": result["error_detail"],
        }), 400
    return jsonify({"ok": True, "status": new_status, "campaign_id": campaign_id})


@app.route("/api/campaigns/<campaign_id>/duplicate", methods=["POST"])
def api_campaign_duplicate(campaign_id):
    """Nhân bản 1 campaign (deep copy cả adset + ad). Bản sao tạo ở trạng thái TẠM DỪNG.

    Body JSON: { "bm": "BM1" | "BM2" | "BM3" }
    """
    body = request.json or {}
    bm = (body.get("bm") or "").strip().upper()
    token = _token_for_bm(bm)
    if not token:
        return jsonify({"ok": False, "error": f"Thiếu FB_TOKEN_{bm}"}), 400
    try:
        r = requests.post(
            f"{FB_BASE_URL}/{campaign_id}/copies",
            data={"access_token": token, "deep_copy": "true", "status_option": "PAUSED"},
            timeout=90,
        )
        data = r.json()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Request fail: {e}"}), 500
    if isinstance(data, dict) and "error" in data:
        return jsonify({"ok": False,
                        "error": data["error"].get("error_user_msg")
                        or data["error"].get("message", "unknown")}), 400
    new_id = (data.get("copied_campaign_id") or data.get("id") or "") if isinstance(data, dict) else ""
    return jsonify({"ok": True, "copied_campaign_id": new_id,
                    "note": "Bản sao tạo ở trạng thái TẠM DỪNG — vào FB Ads Manager chỉnh rồi bật"})


@app.route("/api/campaigns/<campaign_id>/budget", methods=["POST"])
def api_campaign_budget(campaign_id):
    """Tăng/giảm daily_budget của 1 CAMPAIGN (CBO) theo % hoặc set tuyệt đối.

    Body JSON: { "bm": "BM1", "pct": 20 } hoặc { "bm": "BM1", "set": 150000 }

    Chỉ work khi campaign dùng CBO (Campaign Budget Optimization).
    Nếu campaign không có daily_budget (budget set ở adset), trả lỗi rõ.
    """
    body = request.json or {}
    bm = (body.get("bm") or "").strip().upper()
    pct = body.get("pct")
    set_value = body.get("set")
    token = _token_for_bm(bm)
    if not token:
        return jsonify({"ok": False, "error": f"Thiếu FB_TOKEN_{bm}"}), 400

    try:
        rg = requests.get(
            f"{FB_BASE_URL}/{campaign_id}",
            params={"access_token": token, "fields": "daily_budget,lifetime_budget,name"},
            timeout=15,
        )
        meta = rg.json()
    except Exception as e:
        return jsonify({"ok": False, "error": f"GET fail: {e}"}), 500
    if "error" in meta:
        return jsonify({"ok": False, "error": meta["error"].get("message")}), 400

    current = int(meta.get("daily_budget") or 0)
    if not current:
        return jsonify({
            "ok": False,
            "error": "Campaign không có daily_budget (CBO chưa bật, hoặc dùng lifetime_budget). "
                     "Bật CBO trong FB Ads Manager rồi thử lại.",
        }), 400

    if set_value is not None:
        try:
            new_val = int(float(set_value))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "set value không hợp lệ"}), 400
    elif pct is not None:
        try:
            new_val = int(round(current * (1 + float(pct) / 100)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "pct không hợp lệ"}), 400
    else:
        return jsonify({"ok": False, "error": "phải cung cấp pct hoặc set"}), 400

    if new_val <= 0:
        return jsonify({"ok": False, "error": "Budget mới phải > 0"}), 400

    try:
        ru = requests.post(
            f"{FB_BASE_URL}/{campaign_id}",
            params={"access_token": token},
            data={"daily_budget": new_val},
            timeout=15,
        )
        res = ru.json() if ru.content else {}
    except Exception as e:
        return jsonify({"ok": False, "error": f"POST fail: {e}"}), 500
    if "error" in res:
        return jsonify({"ok": False, "error": res["error"].get("message")}), 400

    _log_budget_change(campaign_id, meta.get("name", ""), bm, current, new_val,
                        source="user_button", pct=pct if pct is not None else None)

    return jsonify({
        "ok": True,
        "campaign_id": campaign_id,
        "campaign_name": meta.get("name", ""),
        "old_budget": current,
        "new_budget": new_val,
        "pct_change": pct,
    })


@app.route("/api/rules", methods=["GET"])
def api_rules_get():
    return jsonify({"rules": load_rules()})


@app.route("/api/rules", methods=["PUT"])
def api_rules_update():
    body = request.json or {}
    rules = body.get("rules")
    if not isinstance(rules, list):
        return jsonify({"ok": False, "error": "Body cần field 'rules' là list"}), 400
    # Validate basic schema
    for r in rules:
        if not isinstance(r, dict) or not r.get("id") or not r.get("name"):
            return jsonify({"ok": False, "error": "Mỗi rule cần id + name"}), 400
    save_rules(rules)
    return jsonify({"ok": True, "rules": load_rules()})


@app.route("/api/rules/reset", methods=["POST"])
def api_rules_reset():
    save_rules([dict(r) for r in DEFAULT_AUTO_PAUSE_RULES])
    return jsonify({"ok": True, "rules": load_rules()})


_daily_fetching: set = set()


def _refresh_daily_spend(frm: str, to: str) -> None:
    """Background refresh — single-flight per range."""
    key = range_key(frm, to)
    with _lock:
        if key in _daily_fetching:
            return
        _daily_fetching.add(key)
    try:
        res = fetch_daily_spend_by_tier(frm, to)
        with _lock:
            _daily_spend_by_range[key] = res
    except Exception as e:
        print(f"[spend-daily] refresh fail {key}: {e}")
    finally:
        with _lock:
            _daily_fetching.discard(key)


def _log_budget_change(campaign_id: str, campaign_name: str, bm: str,
                        old_budget: int, new_budget: int, source: str,
                        pct: float = None) -> None:
    """Append 1 entry vào budget_log.jsonl khi user/bot đổi budget."""
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "bm": bm,
            "old_budget": int(old_budget),
            "new_budget": int(new_budget),
            "delta": int(new_budget) - int(old_budget),
            "pct": round(float(pct), 1) if pct is not None else None,
            "source": source,
        }
        with BUDGET_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️  budget log fail: {e}")


def _fetch_fb_budget_activities(campaign_id: str, account_id: str, token: str,
                                  frm: str, to: str) -> list:
    """Fetch lịch sử update_campaign_budget từ FB Activity Log.

    Trả list entries cùng shape với local log:
    {ts, campaign_id, old_budget, new_budget, delta, pct, source='fb_log'}.
    """
    if not (campaign_id and account_id and token):
        return []
    url = f"{FB_BASE_URL}/{account_id}/activities"
    params = {
        "access_token": token,
        "fields": "event_type,event_time,object_id,extra_data",
        "since": frm or "2026-01-01",
        "until": to or date.today().isoformat(),
        "limit": 500,
    }
    out = []
    next_url = url
    page = 0
    while next_url and page < 5:
        page += 1
        try:
            r = requests.get(next_url, params=params if page == 1 else None, timeout=20)
            data = r.json()
        except Exception:
            break
        if "error" in data:
            break
        for ev in data.get("data", []):
            if ev.get("event_type") != "update_campaign_budget":
                continue
            if ev.get("object_id") != campaign_id:
                continue
            try:
                extra = json.loads(ev.get("extra_data") or "{}")
                ov_blob = extra.get("old_value") or {}
                nv_blob = extra.get("new_value") or {}
                ov = int(ov_blob.get("old_value") or 0)
                nv = int(nv_blob.get("new_value") or 0)
                if not ov and not nv:
                    continue
                pct = round((nv - ov) / ov * 100, 1) if ov > 0 else None
                out.append({
                    "ts": ev.get("event_time") or "",
                    "campaign_id": campaign_id,
                    "old_budget": ov,
                    "new_budget": nv,
                    "delta": nv - ov,
                    "pct": pct,
                    "source": "fb_log",
                })
            except Exception:
                continue
        next_url = (data.get("paging") or {}).get("next") or ""
    return out


@app.route("/api/campaigns/<campaign_id>/budget-log")
def api_campaign_budget_log(campaign_id):
    """Lịch sử đổi budget: merge local app log + FB Activity Log.
    Cache 30min per (cid, range) để expand lần 2 cùng cam tức thì."""
    frm = request.args.get("from", "").strip()
    to = request.args.get("to", "").strip()

    cache_key = (campaign_id, frm, to)
    with _lock:
        cached = _budget_log_cache.get(cache_key)
    if cached and (datetime.now() - datetime.fromisoformat(cached["fetched_at"])).total_seconds() < BUDGET_LOG_CACHE_TTL_SEC:
        return jsonify({"ok": True, "campaign_id": campaign_id,
                        "entries": cached["entries"], "from_cache": True})

    # Local log
    entries = []
    if BUDGET_LOG.exists():
        try:
            for line in BUDGET_LOG.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("campaign_id") != campaign_id:
                    continue
                ts_date = (e.get("ts") or "")[:10]
                if frm and ts_date < frm:
                    continue
                if to and ts_date > to:
                    continue
                entries.append(e)
        except Exception as e:
            print(f"⚠️  budget log read fail: {e}")

    # FB Activity Log — tìm BM/account của cam từ cache
    account_id = None
    bm = None
    for entry in _state_by_range.values():
        for a in entry.get("data") or []:
            if a.get("campaign_id") == campaign_id:
                account_id = a.get("account_id")
                bm = a.get("bm")
                break
        if account_id:
            break
    if account_id and bm:
        token = _token_for_bm(bm)
        try:
            fb_entries = _fetch_fb_budget_activities(campaign_id, account_id, token, frm, to)
            # Dedupe: bỏ FB entries có ts (đến phút) trùng với local entries
            local_ts_min = {(e.get("ts") or "")[:16] for e in entries}
            for fe in fb_entries:
                if fe["ts"][:16] in local_ts_min:
                    continue
                entries.append(fe)
        except Exception as e:
            print(f"⚠️  FB activities fail {campaign_id}: {e}")

    entries.sort(key=lambda x: x.get("ts", ""))

    # Save cache
    with _lock:
        _budget_log_cache[cache_key] = {
            "entries": entries,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }

    return jsonify({"ok": True, "campaign_id": campaign_id, "entries": entries})


@app.route("/api/campaigns/<campaign_id>/daily")
def api_campaign_daily(campaign_id):
    """Daily breakdown 1 campaign: spend, mess, cost/mess, ROAS, purchases."""
    preset = request.args.get("preset", "").strip()
    frm = request.args.get("from", "").strip()
    to = request.args.get("to", "").strip()
    bm = (request.args.get("bm") or "").strip().upper()
    if preset and not (frm and to):
        frm, to = resolve_preset(preset)
    if not frm or not to:
        frm, to = resolve_preset("7d")

    token = _token_for_bm(bm) if bm else ""
    if not token:
        for entry in _state_by_range.values():
            for a in entry.get("data") or []:
                if a.get("campaign_id") == campaign_id and a.get("bm"):
                    token = _token_for_bm(a["bm"])
                    if token:
                        break
            if token:
                break
    if not token:
        return jsonify({"ok": False, "error": "Không tìm được BM/token cho campaign"}), 400

    from fetcher import MSG_ACTION, PURCHASE_TYPES, AD_VAT_RATE
    try:
        r = requests.get(
            f"{FB_BASE_URL}/{campaign_id}/insights",
            params={
                "access_token": token,
                "fields": "spend,actions,purchase_roas",
                "time_range": f'{{"since":"{frm}","until":"{to}"}}',
                "time_increment": "1",
                "limit": 500,
            }, timeout=30)
        data = r.json()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if "error" in data:
        return jsonify({"ok": False, "error": data["error"].get("message", "?")}), 400

    rows = []
    for row in data.get("data", []):
        d = row.get("date_start")
        if not d:
            continue
        spend_vat = float(row.get("spend") or 0) * (1 + AD_VAT_RATE)
        actions = row.get("actions") or []
        mess = next((int(a.get("value", 0) or 0)
                     for a in actions if a.get("action_type") == MSG_ACTION), 0)
        purchases = sum(int(a.get("value") or 0) for a in actions
                        if a.get("action_type") in PURCHASE_TYPES)
        roas_data = row.get("purchase_roas") or []
        roas_raw = next((float(rr.get("value") or 0)
                         for rr in roas_data if rr.get("action_type") == "omni_purchase"), 0.0)
        roas = roas_raw / (1 + AD_VAT_RATE) if roas_raw > 0 else 0.0
        rows.append({
            "date": d,
            "spend": round(spend_vat),
            "mess": mess,
            "cost_per_mess": round(spend_vat / mess) if mess > 0 else 0,
            "roas": round(roas, 2),
            "purchases": purchases,
        })
    rows.sort(key=lambda x: x["date"])
    return jsonify({
        "ok": True,
        "campaign_id": campaign_id,
        "date_from": frm, "date_to": to,
        "dates": [r["date"] for r in rows],
        "spend": [r["spend"] for r in rows],
        "messages": [r["mess"] for r in rows],
        "cost_per_mess": [r["cost_per_mess"] for r in rows],
        "roas": [r["roas"] for r in rows],
        "purchases": [r["purchases"] for r in rows],
    })


@app.route("/api/cost-per-mess-daily")
def api_cost_per_mess_daily():
    """Line chart: giá/mess theo ngày trong range. SWR + parallel 6 TK."""
    preset = request.args.get("preset", "").strip()
    frm = request.args.get("from", "").strip()
    to = request.args.get("to", "").strip()
    if preset and not (frm and to):
        frm, to = resolve_preset(preset)
    if not frm or not to:
        frm, to = resolve_preset("7d")

    key = range_key(frm, to)
    with _lock:
        entry = _daily_cpm_by_range.get(key)
        is_fresh = bool(entry and (datetime.now() - datetime.fromisoformat(entry["fetched_at"])).total_seconds() < CACHE_TTL_SEC)

    def _fetch_with_retail(frm_, to_):
        """Fetch FB cpm + retail revenue song song."""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            cpm_fut = ex.submit(fetch_daily_cost_per_mess, frm_, to_)
            retail_fut = ex.submit(fetch_daily_retail_revenue, frm_, to_)
            cpm_res = cpm_fut.result()
            retail_res = retail_fut.result()
        retail_map = dict(zip(retail_res.get("dates", []), retail_res.get("retail", [])))
        cpm_res["retail"] = [retail_map.get(d, 0) for d in cpm_res.get("dates", [])]
        cpm_res["total_retail"] = retail_res.get("total_retail", 0)
        return cpm_res

    if not entry:
        try:
            res = _fetch_with_retail(frm, to)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "dates": [], "cost_per_mess": []}), 200
        with _lock:
            _daily_cpm_by_range[key] = res
        entry = res
    elif not is_fresh:
        def _bg():
            try:
                res = _fetch_with_retail(frm, to)
                with _lock:
                    _daily_cpm_by_range[key] = res
            except Exception as e:
                print(f"[cpm-daily] bg fail: {e}")
        threading.Thread(target=_bg, daemon=True).start()

    return jsonify({
        "ok": True,
        "date_from": frm, "date_to": to,
        **{k: entry.get(k) for k in ("dates", "spend", "messages", "revenue",
                                       "cost_per_mess", "roas", "retail",
                                       "avg_cost_per_mess", "avg_roas",
                                       "total_spend", "total_mess", "total_revenue",
                                       "total_retail")},
        "fetched_at": entry["fetched_at"],
    })


@app.route("/api/spend-daily")
def api_spend_daily():
    """Stacked bar: chi TOFU/BOFU theo ngày. Stale-while-revalidate."""
    preset = request.args.get("preset", "").strip()
    frm = request.args.get("from", "").strip()
    to = request.args.get("to", "").strip()
    if preset and not (frm and to):
        frm, to = resolve_preset(preset)
    if not frm or not to:
        frm, to = resolve_preset("7d")

    key = range_key(frm, to)
    with _lock:
        entry = _daily_spend_by_range.get(key)
        is_fresh = bool(entry and (datetime.now() - datetime.fromisoformat(entry["fetched_at"])).total_seconds() < CACHE_TTL_SEC)

    if not entry:
        # Lần đầu — phải block fetch
        try:
            res = fetch_daily_spend_by_tier(frm, to)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e),
                            "dates": [], "tofu": [], "bofu": []}), 200
        with _lock:
            _daily_spend_by_range[key] = res
        entry = res
    elif not is_fresh:
        # Stale — trả cache cũ + refresh ngầm
        threading.Thread(target=_refresh_daily_spend, args=(frm, to), daemon=True).start()

    return jsonify({
        "ok": True,
        "date_from": frm,
        "date_to": to,
        "dates": entry["dates"],
        "tofu": entry["tofu"],
        "bofu": entry["bofu"],
        "errors": entry.get("errors") or [],
        "fetched_at": entry["fetched_at"],
        "stale": not is_fresh,
    })


# ─── AI Chatbot ───────────────────────────────────────────────────────────
_anthropic_client = None


def _get_anthropic_client():
    """Lazy-init Anthropic client. None nếu chưa setup ANTHROPIC_API_KEY."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=key)
        return _anthropic_client
    except Exception as e:
        print(f"⚠️  Anthropic client init failed: {e}")
        return None


def _build_chat_context(preset: str = "", frm: str = "", to: str = "") -> str:
    """Trả snapshot dữ liệu dashboard theo range user đang xem.
    Ưu tiên: (frm,to) → preset → latest cached range → today.
    Nếu cache miss và phải fetch sync (block ~10-15s).
    """
    if preset and not (frm and to):
        frm, to = resolve_preset(preset)

    # Nếu client không gửi range info, fallback dùng range MỚI NHẤT đã fetch
    # (thường là range user đang xem trên UI — vì frontend tự fetch lúc load).
    if not frm or not to:
        with _lock:
            entries = [(k, v) for k, v in _state_by_range.items() if v.get("fetched_at")]
        if entries:
            entries.sort(key=lambda kv: kv[1]["fetched_at"], reverse=True)
            latest = entries[0][1]
            frm, to = latest["date_from"], latest["date_to"]
            print(f"[chat] No range from client → fallback latest cache {frm}→{to}")
        else:
            frm, to = resolve_preset("today")

    key = range_key(frm, to)
    entry = _state_by_range.get(key)
    if not entry:
        try:
            refresh_data(frm, to)
            entry = _state_by_range.get(key)
        except Exception as e:
            return f"Cache miss + fetch fail cho range {frm}→{to}: {e}"
    if not entry:
        return f"Không có data cho range {frm}→{to}."

    ads = entry.get("data") or []
    cfg = get_config()

    total = len(ads)
    active = [a for a in ads if not a.get("is_paused")]
    paused = [a for a in ads if a.get("is_paused")]
    bad = [a for a in ads if a.get("grade") == "bad" and not a.get("is_paused")]
    good = [a for a in ads if a.get("grade") in ("good", "special") and not a.get("is_paused")]

    spend_total = sum(float(a.get("spend") or 0) for a in ads)
    spend_bad = sum(float(a.get("spend") or 0) for a in bad)
    spend_good = sum(float(a.get("spend") or 0) for a in good)

    # Top 10 ads đốt nhiều
    bad_sorted = sorted(bad, key=lambda a: -float(a.get("spend") or 0))[:10]
    good_sorted = sorted(good, key=lambda a: -float(a.get("spend") or 0))[:5]

    def _ad_line(a):
        act = (a.get("account_id") or "").replace("act_", "")
        cid = a.get("campaign_id") or ""
        url = f"https://www.facebook.com/adsmanager/manage/adsets?act={act}&selected_campaign_ids={cid}" if act and cid else ""
        return (f"- [{a.get('tier','?').upper()}] {a.get('campaign_name','?')[:80]} | "
                f"chi {int(a.get('spend',0)):,}đ | mess {a.get('messages',0)} | "
                f"giá/mess {int(a.get('cost_per_message',0)):,}đ | ROAS {a.get('roas',0):.2f} | "
                f"TK {a.get('account','?')} ({a.get('bm','?')}) | "
                f"camp_id={cid} | url={url}")

    # Số ngày trong range để bot không nhầm "hôm nay" với "7 ngày"
    try:
        d1 = date.fromisoformat(entry.get('date_from'))
        d2 = date.fromisoformat(entry.get('date_to'))
        days = (d2 - d1).days + 1
    except Exception:
        days = 1
    range_label = "hôm nay" if days == 1 and entry.get('date_to') == date.today().isoformat() else f"{days} ngày ({entry.get('date_from')} → {entry.get('date_to')})"

    lines = [
        f"## Snapshot Eye Plus FB Ads — KHOẢNG {range_label.upper()}",
        f"",
        f"**Tổng quan**:",
        f"- Tổng ads: {total} (active {len(active)}, paused {len(paused)})",
        f"- Tổng chi (đã VAT 10%): {int(spend_total):,}đ",
        f"- Chi vào ad XẤU (đốt): {int(spend_bad):,}đ ({int(spend_bad/spend_total*100) if spend_total else 0}%)",
        f"- Chi vào ad TỐT: {int(spend_good):,}đ ({int(spend_good/spend_total*100) if spend_total else 0}%)",
        f"",
        f"**Ngưỡng KPI hiện tại**:",
        f"- TOFU (tên có GC/CT1/CT2): mess ≤ {cfg['tofu_mess_max']:,}đ (min spend {cfg['tofu_min_spend']:,}đ)",
        f"- BOFU (còn lại): mess ≤ {cfg['bofu_mess_max']:,}đ VÀ ROAS ≥ {cfg['bofu_roas_min']} (min spend {cfg['bofu_min_spend']:,}đ)",
        f"",
        f"**Top 10 ad đang ĐỐT nhiều nhất (active, đang flag XẤU)**:",
    ]
    lines.extend(_ad_line(a) for a in bad_sorted)
    lines.append("")
    lines.append("**Top 5 ad TỐT (đặc biệt + good)**:")
    lines.extend(_ad_line(a) for a in good_sorted)

    return "\n".join(lines)


CHAT_SYSTEM_PROMPT = """Bạn là trợ lý phân tích FB Ads của Eye Plus (chuỗi mắt kính, 14 cửa hàng HN/HCM/HP/BN).

Bối cảnh kinh doanh:
- Phòng MKT chạy 6 tài khoản FB Ads qua 3 BM. Mục tiêu: tin nhắn (mess) → CSKH chốt đơn.
- TOFU (top of funnel): cam có "GC" hoặc "CT1"/"CT2" trong tên → mục tiêu rẻ tin nhắn, không cần ROAS cao.
- BOFU (bottom of funnel): còn lại → bắt buộc ROAS đủ cao.
- KHÔNG có khái niệm "MOFU" trong hệ thống này — chỉ TOFU và BOFU.
- Auto-scan rule chạy mỗi 8h flag ad chi > 200K mà không đạt KPI để đề xuất tắt.

Quy tắc trả lời:
1. NGẮN GỌN, vào thẳng vấn đề. Dùng bullet/bảng khi cần. Không lảm nhảm.
2. **Snapshot dưới đây là DỮ LIỆU HIỆN TẠI cho range đã ghi rõ ở header.**
   Nếu user hỏi range khác với snapshot, dùng ĐÚNG range trong snapshot — KHÔNG nói "không có data" nếu user request range = range snapshot.
   Vd: snapshot là 3 NGÀY (24-26/5) → user hỏi "3 ngày qua" → trả lời từ snapshot, không kêu thiếu data.
3. **Bỏ qua các câu trả lời cũ trong conversation history nếu nó nói thiếu data** — snapshot mới đã có data, refresh hiểu biết.
4. Dữ liệu CHÍNH XÁC từ snapshot — không bịa số.
5. Tiền: dấu phẩy hàng nghìn + đ. Phần trăm: làm tròn %.
6. **KHI NHẮC TÊN CAMPAIGN, LUÔN dùng markdown link** với `url=` của cam đó từ snapshot:
   `[Tên campaign](url-fb-ads-manager)` → user click ra Ads Manager ngay.
7. Khi user yêu cầu hành động (tắt cam, bật lại, tăng/giảm budget %), DÙNG TOOL tương ứng.
   - Trước khi gọi tool, confirm 1 câu ngắn: "Em tắt cam X nhé?" (trừ khi user đã rõ "tắt ngay").
   - Sau khi tool chạy xong, báo lại kết quả 1 câu.
8. Không nhắc lại snapshot — chỉ trả lời câu hỏi.
"""


# ─── Chatbot tools ──────────────────────────────────────────────────────
CHAT_TOOLS = [
    {
        "name": "pause_campaign",
        "description": "Tắt 1 FB Ads campaign. Chỉ gọi khi user xác nhận muốn tắt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID (18 chữ số)"},
                "bm": {"type": "string", "enum": ["BM1", "BM2", "BM3"],
                       "description": "Business Manager của cam (lấy từ snapshot)"},
                "reason": {"type": "string", "description": "Lý do tắt — short"},
            },
            "required": ["campaign_id", "bm"],
        },
    },
    {
        "name": "resume_campaign",
        "description": "Bật lại 1 campaign đã tắt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "bm": {"type": "string", "enum": ["BM1", "BM2", "BM3"]},
            },
            "required": ["campaign_id", "bm"],
        },
    },
    {
        "name": "adjust_campaign_budget",
        "description": "Tăng/giảm daily_budget của campaign theo %. pct=20 = +20%, pct=-30 = -30%. Chỉ work cho campaign CBO.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "bm": {"type": "string", "enum": ["BM1", "BM2", "BM3"]},
                "pct": {"type": "number", "description": "Phần trăm thay đổi (-50 đến +200)"},
            },
            "required": ["campaign_id", "bm", "pct"],
        },
    },
]


def _execute_chat_tool(name: str, args: dict) -> dict:
    """Execute chatbot tool. Trả {ok, message, detail}."""
    bm = (args.get("bm") or "").strip().upper()
    cid = (args.get("campaign_id") or "").strip()
    if not cid:
        return {"ok": False, "message": "Thiếu campaign_id"}
    token = _token_for_bm(bm)
    if not token:
        return {"ok": False, "message": f"Không tìm thấy FB_TOKEN_{bm}"}

    if name == "pause_campaign":
        r = _set_campaign_status(cid, token, "PAUSED")
        return {"ok": r["ok"],
                "message": "Đã tắt campaign" if r["ok"] else f"Tắt fail: {r.get('error','?')}"}

    if name == "resume_campaign":
        r = _set_campaign_status(cid, token, "ACTIVE")
        return {"ok": r["ok"],
                "message": "Đã bật campaign" if r["ok"] else f"Bật fail: {r.get('error','?')}"}

    if name == "adjust_campaign_budget":
        pct = args.get("pct")
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return {"ok": False, "message": "pct không hợp lệ"}
        # Lấy daily_budget hiện tại
        try:
            rg = requests.get(f"{FB_BASE_URL}/{cid}",
                              params={"access_token": token, "fields": "daily_budget,name"},
                              timeout=15)
            meta = rg.json()
        except Exception as e:
            return {"ok": False, "message": f"GET fail: {e}"}
        if "error" in meta:
            return {"ok": False, "message": meta["error"].get("message", "?")}
        current = int(meta.get("daily_budget") or 0)
        if not current:
            return {"ok": False,
                    "message": "Campaign không có daily_budget (chưa bật CBO). Hãy bật CBO rồi thử lại."}
        new_val = int(round(current * (1 + pct / 100)))
        if new_val <= 0:
            return {"ok": False, "message": "Budget mới phải > 0"}
        try:
            ru = requests.post(f"{FB_BASE_URL}/{cid}",
                               params={"access_token": token},
                               data={"daily_budget": new_val}, timeout=15)
            if ru.status_code >= 400:
                return {"ok": False, "message": ru.json().get("error", {}).get("message", "?")}
        except Exception as e:
            return {"ok": False, "message": f"POST fail: {e}"}
        _log_budget_change(cid, meta.get("name", ""), bm, current, new_val,
                            source="chat_tool", pct=pct)
        return {"ok": True,
                "message": f"Đã đổi budget: {current:,}đ → {new_val:,}đ ({'+' if pct>=0 else ''}{pct:.0f}%)"}

    return {"ok": False, "message": f"Tool không hợp lệ: {name}"}


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Streaming SSE chat với Claude. Auto-inject snapshot data làm context."""
    client = _get_anthropic_client()
    if not client:
        return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY chưa cấu hình"}), 500

    body = request.get_json(silent=True) or {}
    history = body.get("messages") or []  # list of {role, content}
    user_msg = (body.get("user_message") or "").strip()
    model_in = (body.get("model") or "").strip()
    # Range context — từ UI hiện tại (preset hoặc from/to custom)
    preset = (body.get("preset") or "").strip()
    frm = (body.get("from") or "").strip()
    to = (body.get("to") or "").strip()
    # Whitelist: chỉ cho phép 2 model
    ALLOWED_MODELS = {
        "opus": "claude-opus-4-7",
        "sonnet": "claude-sonnet-4-6",
    }
    model = ALLOWED_MODELS.get(model_in, "claude-opus-4-7")
    if not user_msg:
        return jsonify({"ok": False, "error": "Thiếu user_message"}), 400

    # Snapshot data — theo range user đang xem
    snapshot = _build_chat_context(preset=preset, frm=frm, to=to)

    # Build messages
    messages = []
    for m in history[-20:]:  # limit 20 turn gần nhất
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": str(m["content"])})
    messages.append({"role": "user", "content": user_msg})

    def event_stream():
        try:
            current_messages = list(messages)
            total_in = total_out = total_cache_read = total_cache_write = 0
            # Tool use loop: tối đa 4 lượt (tránh infinite loop)
            for hop in range(4):
                with client.messages.stream(
                    model=model,
                    max_tokens=2048,
                    system=[
                        {"type": "text", "text": CHAT_SYSTEM_PROMPT},
                        {"type": "text", "text": snapshot, "cache_control": {"type": "ephemeral"}},
                    ],
                    tools=CHAT_TOOLS,
                    messages=current_messages,
                ) as stream:
                    for text in stream.text_stream:
                        yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
                    final = stream.get_final_message()

                u = final.usage
                total_in += u.input_tokens
                total_out += u.output_tokens
                total_cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
                total_cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

                # Có tool_use không?
                tool_uses = [b for b in final.content if b.type == "tool_use"]
                if not tool_uses or final.stop_reason != "tool_use":
                    break

                # Push assistant turn (must include tool_use blocks)
                current_messages.append({
                    "role": "assistant",
                    "content": [b.model_dump() for b in final.content],
                })

                # Execute mỗi tool
                tool_results = []
                for tu in tool_uses:
                    yield f"data: {json.dumps({'type': 'tool_call', 'name': tu.name, 'input': tu.input})}\n\n"
                    res = _execute_chat_tool(tu.name, tu.input or {})
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': tu.name, 'ok': res['ok'], 'message': res.get('message','')})}\n\n"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(res, ensure_ascii=False),
                        "is_error": not res["ok"],
                    })
                current_messages.append({"role": "user", "content": tool_results})
                # Lặp lại — Claude sẽ thấy kết quả tool + trả lời tiếp

            done_payload = {
                "type": "done",
                "usage": {"in": total_in, "out": total_out,
                          "cache_read": total_cache_read, "cache_write": total_cache_write},
            }
            yield f"data: {json.dumps(done_payload)}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/chat/health", methods=["GET"])
def api_chat_health():
    """Check chatbot enabled không."""
    client = _get_anthropic_client()
    return jsonify({"enabled": client is not None})


# ─── ──────────────────────────────────────────────────────────────────────


@app.route("/api/version", methods=["GET"])
def api_version():
    """Trả version + update state. UI dùng để show banner."""
    return jsonify({
        "version": APP_VERSION,
        "github_repo": GITHUB_REPO,
        "update": _UPDATE_STATE,
    })


@app.route("/api/version/check-now", methods=["POST"])
def api_version_check_now():
    """Force check ngay không đợi scheduler."""
    return jsonify(check_for_update())


@app.route("/api/auto-pause/log", methods=["GET"])
def api_auto_pause_log():
    """Trả danh sách run gần nhất từ auto_pause_log.jsonl."""
    limit = int(request.args.get("limit", "30"))
    entries = _read_auto_pause_log(limit)
    next_run_at = None
    with _lock:
        last_ts = None
        for e in entries:
            if e.get("trigger") == "scheduler":
                last_ts = e.get("ts")
                break
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            next_dt = last_dt + timedelta(hours=AUTO_PAUSE_INTERVAL_HOURS)
            next_run_at = next_dt.isoformat(timespec="seconds")
        except Exception:
            pass
    return jsonify({
        "interval_hours": AUTO_PAUSE_INTERVAL_HOURS,
        "lookback_days": AUTO_PAUSE_LOOKBACK_DAYS,
        "enabled": os.environ.get("ENABLE_AUTO_PAUSE", "true").lower() == "true",
        "next_scheduled_at": next_run_at,
        "runs": entries,
    })


@app.route("/api/auto-pause/trigger", methods=["POST"])
def api_auto_pause_trigger():
    """Trigger auto-scan job ngay lập tức (manual)."""
    result = auto_scan_job(trigger="manual")
    return jsonify({"ok": True, "result": result})


@app.route("/refresh", methods=["POST"])
def trigger_refresh():
    preset = request.args.get("preset", "today")
    frm = request.args.get("from", "")
    to = request.args.get("to", "")
    if frm and to:
        threading.Thread(target=refresh_data, args=(frm, to), daemon=True).start()
    else:
        f, t = resolve_preset(preset)
        threading.Thread(target=refresh_data, args=(f, t), daemon=True).start()
    return jsonify({"ok": True, "msg": "Đang refresh..."})


# ── MKT Team Reports ──────────────────────────────────────────────────────────

@app.route("/reports")
@login_required
def reports_hub():
    return render_template("report_hub.html", page="reports",
                           members=rpt.get_members())


@app.route("/reports/daily", methods=["GET", "POST"])
@login_required
def reports_daily():
    members = rpt.get_members()
    today = date.today().isoformat()
    saved = False
    existing = None
    sel_member = request.args.get("member") or (members[0] if members else "")
    sel_date = request.args.get("date", today)

    if request.method == "POST":
        member = request.form.get("member", "").strip()
        rep_date = request.form.get("report_date", today)
        rpt.save_daily(
            member=member,
            date=rep_date,
            q1=request.form.get("q1", ""),
            q2=request.form.get("q2", ""),
            q3=request.form.get("q3", ""),
            q4=request.form.get("q4", ""),
            unfinished=request.form.get("unfinished", ""),
            tomorrow_plan=request.form.get("tomorrow_plan", ""),
            blockers=request.form.get("blockers", ""),
        )
        saved = True
        sel_member = member
        sel_date = rep_date

    existing = rpt.get_daily(sel_member, sel_date)
    return render_template("report_daily.html", page="reports",
                           members=members, today=today,
                           sel_member=sel_member, sel_date=sel_date,
                           existing=existing, saved=saved)


@app.route("/reports/weekly", methods=["GET", "POST"])
@login_required
def reports_weekly():
    members = rpt.get_members()
    # Monday của tuần hiện tại
    today_d = date.today()
    monday = (today_d - timedelta(days=today_d.weekday())).isoformat()
    saved = False
    sel_member = request.args.get("member") or (members[0] if members else "")
    sel_week = request.args.get("week", monday)

    if request.method == "POST":
        member = request.form.get("member", "").strip()
        week_start = request.form.get("week_start", monday)
        rpt.save_weekly(
            member=member,
            week_start=week_start,
            done_items=request.form.get("done_items", ""),
            pending_items=request.form.get("pending_items", ""),
            priorities=request.form.get("priorities", ""),
            lessons=request.form.get("lessons", ""),
            support_needed=request.form.get("support_needed", ""),
        )
        saved = True
        sel_member = member
        sel_week = week_start

    existing = rpt.get_weekly(sel_member, sel_week)
    return render_template("report_weekly.html", page="reports",
                           members=members, monday=monday,
                           sel_member=sel_member, sel_week=sel_week,
                           existing=existing, saved=saved)


@app.route("/reports/monthly", methods=["GET", "POST"])
@login_required
def reports_monthly():
    members = rpt.get_members()
    this_month = date.today().strftime("%Y-%m")
    saved = False
    sel_member = request.args.get("member") or (members[0] if members else "")
    sel_month = request.args.get("month", this_month)

    if request.method == "POST":
        member = request.form.get("member", "").strip()
        rep_month = request.form.get("report_month", this_month)
        rpt.save_monthly(
            member=member,
            month=rep_month,
            kpis=request.form.get("kpis", ""),
            highlights=request.form.get("highlights", ""),
            challenges=request.form.get("challenges", ""),
            next_month_plan=request.form.get("next_month_plan", ""),
            support_needed=request.form.get("support_needed", ""),
        )
        saved = True
        sel_member = member
        sel_month = rep_month

    existing = rpt.get_monthly(sel_member, sel_month)
    return render_template("report_monthly.html", page="reports",
                           members=members, this_month=this_month,
                           sel_member=sel_member, sel_month=sel_month,
                           existing=existing, saved=saved)


@app.route("/reports/overview")
@login_required
def reports_overview():
    members = rpt.get_members()
    today = date.today().isoformat()
    today_d = date.today()
    monday = (today_d - timedelta(days=today_d.weekday())).isoformat()
    this_month = today_d.strftime("%Y-%m")

    tab = request.args.get("tab", "daily")
    sel_date = request.args.get("date", today)
    sel_week = request.args.get("week", monday)
    sel_month = request.args.get("month", this_month)

    daily_reports = rpt.get_daily_all(sel_date)
    weekly_reports = rpt.get_weekly_all(sel_week)
    monthly_reports = rpt.get_monthly_all(sel_month)

    return render_template("reports_overview.html", page="reports",
                           members=members, today=today, monday=monday,
                           this_month=this_month, tab=tab,
                           sel_date=sel_date, sel_week=sel_week, sel_month=sel_month,
                           daily_reports=daily_reports,
                           weekly_reports=weekly_reports,
                           monthly_reports=monthly_reports)


@app.route("/api/reports/members", methods=["GET"])
@login_required
def api_reports_members_get():
    return jsonify({"members": rpt.get_members()})


@app.route("/api/reports/members/add", methods=["POST"])
@login_required
def api_reports_members_add():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Tên không được để trống"}), 400
    rpt.add_member(name)
    return jsonify({"ok": True, "members": rpt.get_members()})


@app.route("/api/reports/members/remove", methods=["POST"])
@login_required
def api_reports_members_remove():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Tên không hợp lệ"}), 400
    rpt.remove_member(name)
    return jsonify({"ok": True, "members": rpt.get_members()})


def auto_scan_job(trigger: str = "scheduler") -> dict:
    """Job: fetch range LOOKBACK_DAYS + scan campaigns đạt rule, CHỈ ghi log.

    KHÔNG tự gọi FB API pause. User tự decide pause qua UI bulk action.
    """
    started_at = datetime.now()
    enabled = os.environ.get("ENABLE_AUTO_PAUSE", "true").lower() == "true"
    if not enabled:
        result = {
            "ts": started_at.isoformat(timespec="seconds"),
            "trigger": trigger,
            "enabled": False,
            "skip_reason": "ENABLE_AUTO_PAUSE=false",
            "evaluated": 0, "candidates": 0,
            "matches": [],
            "lookback_days": AUTO_PAUSE_LOOKBACK_DAYS,
            "duration_ms": 0,
        }
        _append_auto_pause_log(result)
        print("[auto-scan] ENABLE_AUTO_PAUSE=false → skip")
        return result

    print(f"[{started_at.isoformat(timespec='seconds')}] 🔍 Auto-scan start "
          f"(trigger={trigger}, lookback={AUTO_PAUSE_LOOKBACK_DAYS}d)")
    today = date.today()
    date_to = today.isoformat()
    date_from = (today - timedelta(days=AUTO_PAUSE_LOOKBACK_DAYS - 1)).isoformat()
    refresh_data(date_from, date_to)
    with _lock:
        entry = _state_by_range.get(range_key(date_from, date_to))
    if not entry:
        result = {
            "ts": started_at.isoformat(timespec="seconds"),
            "trigger": trigger,
            "enabled": True,
            "skip_reason": "no_data",
            "evaluated": 0, "candidates": 0,
            "matches": [],
            "lookback_days": AUTO_PAUSE_LOOKBACK_DAYS,
            "date_from": date_from, "date_to": date_to,
            "duration_ms": int((datetime.now() - started_at).total_seconds() * 1000),
        }
        _append_auto_pause_log(result)
        return result

    ads = entry.get("data") or []
    candidate_ads = [a for a in ads if a.get("auto_pause")]

    # Group theo campaign, dedupe
    by_camp = {}
    for ad in candidate_ads:
        cid = ad.get("campaign_id")
        if not cid:
            continue
        if cid not in by_camp:
            by_camp[cid] = {
                "campaign_id": cid,
                "campaign_name": ad.get("campaign_name", ""),
                "account": ad.get("account", ""),
                "account_id": ad.get("account_id", ""),
                "bm": ad.get("bm", ""),
                "rule_id": ad.get("auto_pause_rule_id"),
                "rule_name": ad.get("auto_pause_rule_name"),
                "spend": 0.0,
                "messages": 0,
                "purchases": 0,
                "roas": 0.0,
                "ad_count": 0,
                "paused_ad_count": 0,
                "any_active": False,
                "max_cpm": 0,
                "thumbnail_url": ad.get("thumbnail_url", ""),
                "first_ad_id": ad.get("ad_id"),
            }
        c = by_camp[cid]
        c["ad_count"] += 1
        c["spend"] += float(ad.get("spend") or 0)
        c["messages"] += int(ad.get("messages") or 0)
        c["purchases"] += int(ad.get("purchases") or 0)
        if ad.get("is_paused"):
            c["paused_ad_count"] += 1
        else:
            c["any_active"] = True
        cpm = int(ad.get("cost_per_message") or 0)
        if cpm > c["max_cpm"]:
            c["max_cpm"] = cpm
        # ROAS weighted average sau
    matches = list(by_camp.values())
    # Compute weighted ROAS per campaign
    for c in matches:
        # weighted by spend
        rev = 0.0
        for ad in candidate_ads:
            if ad.get("campaign_id") == c["campaign_id"]:
                rev += float(ad.get("spend") or 0) * float(ad.get("roas") or 0)
        c["roas"] = rev / c["spend"] if c["spend"] > 0 else 0
        # Mark campaign-level current paused state (from _paused_campaign_ids set)
        c["is_paused"] = (c["campaign_id"] in _paused_campaign_ids) or (
            c["paused_ad_count"] == c["ad_count"] and c["ad_count"] > 0
        )

    # Sort: spend desc
    matches.sort(key=lambda x: -x["spend"])

    duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
    result = {
        "ts": started_at.isoformat(timespec="seconds"),
        "trigger": trigger,
        "enabled": True,
        "evaluated": len(ads),
        "candidates": len(matches),
        "matches": matches,
        "lookback_days": AUTO_PAUSE_LOOKBACK_DAYS,
        "date_from": date_from, "date_to": date_to,
        "duration_ms": duration_ms,
    }
    _append_auto_pause_log(result)
    print(f"[auto-scan] Done — {len(matches)} candidates ({duration_ms}ms) "
          f"on {date_from}→{date_to}")
    return result


# Backward-compat alias (cũ → mới)
auto_pause_job = auto_scan_job


def _append_auto_pause_log(entry: dict) -> None:
    """Append 1 dòng JSON vào auto_pause_log.jsonl."""
    try:
        with AUTO_PAUSE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        print(f"⚠️  Log write failed: {e}")


def _read_auto_pause_log(limit: int = 30) -> list:
    """Đọc N dòng log gần nhất, sort mới → cũ."""
    if not AUTO_PAUSE_LOG.exists():
        return []
    try:
        with AUTO_PAUSE_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        entries.reverse()
        return entries
    except Exception as e:
        print(f"⚠️  Log read failed: {e}")
        return []


def _license_recheck_job() -> None:
    """Periodic license check. Nếu bị disable + user đã từng login (có LICENSE_KEY) → kill process.

    Nếu chưa có LICENSE_KEY (user chưa login lần nào) → skip, không kill.
    """
    if not LICENSE_KEY.strip():
        return  # chưa setup key, đợi user login
    result = check_license_and_update()
    if not result["ok"]:
        print(f"⛔ License revoked. App sẽ exit trong 5 giây...")
        import time as _t
        _t.sleep(5)
        os._exit(1)


def shadow_scan_job(trigger: str = "scheduler"):
    """Job ĐỐI CHỨNG: fetch cộng dồn SHADOW_LOOKBACK_DAYS → ghi snapshot + quyết định v3.

    CHỈ ghi nhận vào shadow.db local — KHÔNG gọi FB API pause/budget.
    """
    if not SHADOW_MODE:
        return None
    try:
        import shadow
        today = date.today()
        date_from = (today - timedelta(days=shadow.SHADOW_LOOKBACK_DAYS - 1)).isoformat()
        print(f"[shadow] 🔍 Scan start (trigger={trigger}, {date_from} → {today.isoformat()})")
        result = fetch_all_ads(date_from, today.isoformat())
        ads = result.get("ads") or []

        # v3.1: cửa sổ trượt N ngày gần nhất cho ad trưởng thành (Cổng 2)
        r3_from = (today - timedelta(days=shadow.RECENT_WINDOW_DAYS - 1)).isoformat()
        r3 = fetch_all_ads(r3_from, today.isoformat())
        r3map = {}
        for a in (r3.get("ads") or []):
            r3map[a.get("ad_id")] = {
                "r3_spend": float(a.get("spend_raw") or 0),
                "r3_messages": int(a.get("messages") or 0),
                "r3_purchases": int(a.get("purchases") or 0),
                "r3_roas": float(a.get("roas_raw") or 0),
            }
        for a in ads:
            m = r3map.get(a.get("ad_id"))
            if m:
                a.update(m)

        res = shadow.run_shadow_scan(ads)
        # Sau khi có quyết định mới → tính luôn REVIEW ngày qua
        try:
            shadow.compute_daily_review()
        except Exception as e:
            print(f"[review] ⚠️ {e}")
        return res
    except Exception as e:
        print(f"[shadow] ⚠️ scan failed: {e}")
        return None


def _maybe_run_shadow_at_startup() -> None:
    """Chạy scan + review khi app start NẾU job cron 1h30 đã lỡ (máy tắt ban đêm)."""
    if not SHADOW_MODE:
        return
    try:
        import shadow
        today = date.today().isoformat()
        scanned = (shadow.last_scan_ts() or "")[:10] == today
        reviewed = (shadow.last_review_date() or "") == today \
            or (shadow.get_latest_review() or {}).get("_created_at", "")[:10] == today
        if scanned and reviewed:
            print("[shadow] Hôm nay đã scan + review — đợi cron 1h30 sáng mai")
            return
        print("[shadow] Chưa đủ scan/review hôm nay → chạy sau 90s")
        threading.Timer(90, lambda: shadow_scan_job(trigger="startup")).start()
    except Exception as e:
        print(f"[shadow] startup check failed: {e}")


def start_scheduler() -> None:
    sched = BackgroundScheduler()
    sched.add_job(lambda: refresh_data(), "interval",
                  hours=REFRESH_INTERVAL_HOURS, id="refresh_today")
    sched.add_job(auto_scan_job, "interval",
                  hours=AUTO_PAUSE_INTERVAL_HOURS, id="auto_scan")
    if SHADOW_MODE:
        # Đối chứng + REVIEW: 1h30 sáng hằng ngày (data ngày qua đã chốt) → quét quyết định + đánh giá
        sched.add_job(shadow_scan_job, "cron", hour=1, minute=30, id="shadow_scan")
    if LICENSE_CHECK_URL and not IS_RAILWAY:
        sched.add_job(_license_recheck_job, "interval",
                      hours=LICENSE_CHECK_INTERVAL_HOURS, id="license_check")
    # Update check: bỏ trên Railway (server luôn latest sau push)
    if not IS_RAILWAY:
        sched.add_job(check_for_update, "interval",
                      hours=UPDATE_CHECK_INTERVAL_HOURS, id="update_check")
    # Chạy 1 lần ngay khi start (sau 60s để Flask ổn định)
    import threading as _th
    if not IS_RAILWAY:
        _th.Timer(60, check_for_update).start()
    sched.start()
    print(f"⏰ Scheduler: refresh {REFRESH_INTERVAL_HOURS}h · auto-scan {AUTO_PAUSE_INTERVAL_HOURS}h"
          + (f" · license-check {LICENSE_CHECK_INTERVAL_HOURS}h" if LICENSE_CHECK_URL else ""))


def _maybe_run_auto_pause_at_startup() -> None:
    """Chạy auto-scan ngay khi app start NẾU lần chạy scheduler cuối > AUTO_PAUSE_INTERVAL_HOURS."""
    try:
        entries = _read_auto_pause_log(50)
        last_sched = next((e for e in entries if e.get("trigger") == "scheduler"), None)
        if not last_sched:
            print("[auto-scan] Chưa có log scheduler nào → scan lần đầu sau 60s")
            threading.Timer(60, lambda: auto_scan_job(trigger="startup")).start()
            return
        last_dt = datetime.fromisoformat(last_sched["ts"])
        hours_since = (datetime.now() - last_dt).total_seconds() / 3600
        if hours_since >= AUTO_PAUSE_INTERVAL_HOURS:
            print(f"[auto-scan] Lần scan cuối {hours_since:.1f}h trước (≥{AUTO_PAUSE_INTERVAL_HOURS}h) → scan ngay sau 60s")
            threading.Timer(60, lambda: auto_scan_job(trigger="startup")).start()
        else:
            print(f"[auto-scan] Lần scan cuối {hours_since:.1f}h trước, chưa tới hạn — đợi scheduler")
    except Exception as e:
        print(f"[auto-scan] startup check failed: {e}")


def main(open_browser: bool = False) -> None:
    """Entry point — dùng được cả khi chạy python app.py lẫn từ launcher.py (PyInstaller)."""
    load_cache_from_disk()
    today_key = range_key(date.today().isoformat(), date.today().isoformat())
    if today_key not in _state_by_range:
        threading.Thread(target=refresh_data, daemon=True).start()
    start_scheduler()
    _maybe_run_auto_pause_at_startup()
    _maybe_run_shadow_at_startup()
    port = int(os.getenv("PORT", "5050"))
    print(f"🚀 Dashboard: http://localhost:{port}")
    print("   (Cmd+C để dừng)\n")

    if open_browser:
        # Mở browser sau 2s để server kịp start
        import webbrowser
        def _open():
            import time
            time.sleep(2)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=_open, daemon=True).start()

    # Railway expose qua $PORT + cần listen 0.0.0.0; local giữ 127.0.0.1 cho an toàn
    host = "0.0.0.0" if IS_RAILWAY else "127.0.0.1"
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
