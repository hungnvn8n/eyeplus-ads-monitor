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

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from fetcher import fetch_all_ads, FB_BASE_URL
from rules import (
    DEFAULT_AUTO_PAUSE_RULES, auto_pause_decision, classify,
    evaluate, grade, matching_rule,
)

# Khi chạy từ PyInstaller bundle: dùng CWD (launcher.py đã chdir tới folder chứa exe)
# để .env / cache.json / rules.json đều bên cạnh executable (user editable).
if getattr(sys, "frozen", False):
    ROOT = Path(os.getcwd())
else:
    ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

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
RULES_FILE = ROOT / "rules.json"
REFRESH_INTERVAL_HOURS = int(os.getenv("REFRESH_INTERVAL_HOURS", "3"))
AUTO_PAUSE_INTERVAL_HOURS = int(os.getenv("AUTO_PAUSE_INTERVAL_HOURS", "8"))
AUTO_PAUSE_LOOKBACK_DAYS = int(os.getenv("AUTO_PAUSE_LOOKBACK_DAYS", "7"))
CACHE_TTL_SEC = REFRESH_INTERVAL_HOURS * 3600

_lock = threading.Lock()
# Cache theo (date_from, date_to) key. Mỗi entry: {data, fetched_at, errors, date_from, date_to}
_state_by_range: dict = {}
# Lock cho mỗi range đang fetch (để tránh fetch trùng song song)
_fetching: set = set()
# Campaign IDs đã pause (qua app này) — override is_paused across mọi cache entry
_paused_campaign_ids: set = set()


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
        return (today - timedelta(days=2)).isoformat(), today.isoformat()
    if preset == "7d":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
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
    except Exception as e:
        print(f"⚠️  Cache load failed: {e}")


# ── Flask app ─────────────────────────────────────────────────────────────────
from functools import wraps
from flask import session, redirect, url_for

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
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

APP_PASSWORD = os.getenv("APP_PASSWORD", "Eyeplus123@@")


@app.before_request
def require_auth_globally():
    """Mọi route trừ /login, /logout, static đều cần auth."""
    public = ("login_page", "logout_page", "static", "refresh_endpoint")
    if request.endpoint in public:
        return None
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
                           license_check_url=bool(LICENSE_CHECK_URL))


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


def start_scheduler() -> None:
    sched = BackgroundScheduler()
    sched.add_job(lambda: refresh_data(), "interval",
                  hours=REFRESH_INTERVAL_HOURS, id="refresh_today")
    sched.add_job(auto_scan_job, "interval",
                  hours=AUTO_PAUSE_INTERVAL_HOURS, id="auto_scan")
    if LICENSE_CHECK_URL:
        sched.add_job(_license_recheck_job, "interval",
                      hours=LICENSE_CHECK_INTERVAL_HOURS, id="license_check")
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

    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
