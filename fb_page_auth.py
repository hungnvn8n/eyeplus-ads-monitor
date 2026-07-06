"""
Facebook Page OAuth — lấy Page Access Token vĩnh viễn cho Page Insights.

Flow:
  1. /auth/fb          → redirect đến FB login dialog
  2. /auth/fb/callback → exchange code → long-lived user token → page tokens
  3. Lưu vào .env, dùng ngay cho organic reach

Chạy một lần duy nhất; page token không bao giờ hết hạn.
"""
import os
import re
import urllib.parse
from pathlib import Path

import requests
from flask import Blueprint, redirect, render_template_string, request, url_for

GRAPH = "https://graph.facebook.com/v19.0"
REDIRECT_PATH = "/auth/fb/callback"
# CHỈ dùng scope hợp lệ với app này. read_insights & pages_read_user_content
# bị Facebook từ chối ("Invalid Scopes") vì app chưa qua App Review → reach tự
# nhiên KHÔNG lấy được qua app FB của mình, phải đi qua Pancake (đối tác Meta).
SCOPES = "pages_show_list,pages_read_engagement"

ENV_FILE = Path(__file__).parent / ".env"

# Pages Eye Plus cần theo dõi
TARGET_PAGES = {
    "821332004654252":  "FB_PAGE_TOKEN_CHINH",   # Kính Mắt Eye Plus
    "552228558319706":  "FB_PAGE_TOKEN_NU",       # Kính Mắt Eye Plus - Nữ
    "1062539773905872": "FB_PAGE_TOKEN_YOUNG",    # 4Young
}

bp = Blueprint("fb_auth", __name__)


def _app_id() -> str:
    return os.getenv("FB_APP_ID", "2699500137102333")


def _app_secret() -> str:
    return os.getenv("FB_APP_SECRET", "")


def _redirect_uri() -> str:
    port = os.getenv("PORT", "5050")
    return f"http://localhost:{port}{REDIRECT_PATH}"


# ── OAuth flow ─────────────────────────────────────────────────────────────────

def build_oauth_url() -> str:
    params = {
        "client_id": _app_id(),
        "redirect_uri": _redirect_uri(),
        "scope": SCOPES,
        "response_type": "code",
        "auth_type": "rerequest",  # force re-ask khi thêm scope mới
    }
    return "https://www.facebook.com/dialog/oauth?" + urllib.parse.urlencode(params)


def exchange_code_for_token(code: str) -> str:
    """Exchange authorization code → short-lived user token."""
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "client_id": _app_id(),
        "client_secret": _app_secret(),
        "redirect_uri": _redirect_uri(),
        "code": code,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def exchange_long_lived(short_token: str) -> str:
    """Đổi short-lived token → long-lived token (60 ngày)."""
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": _app_id(),
        "client_secret": _app_secret(),
        "fb_exchange_token": short_token,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def get_page_tokens(user_token: str) -> dict[str, dict]:
    """Lấy page access token cho từng page. Token này vĩnh viễn không hết hạn."""
    r = requests.get(f"{GRAPH}/me/accounts", params={
        "access_token": user_token,
        "limit": 50,
    }, timeout=15)
    r.raise_for_status()
    pages = {}
    for p in r.json().get("data", []):
        pages[p["id"]] = {
            "name": p["name"],
            "token": p["access_token"],
        }
    return pages


def save_tokens_to_env(page_tokens: dict[str, dict]) -> list[str]:
    """Ghi/cập nhật page token vào .env. Trả về list các key đã lưu."""
    content = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    saved = []
    for page_id, env_key in TARGET_PAGES.items():
        if page_id not in page_tokens:
            continue
        token = page_tokens[page_id]["token"]
        if re.search(rf"^{env_key}=", content, re.MULTILINE):
            content = re.sub(rf"^{env_key}=.*$", f"{env_key}={token}", content, flags=re.MULTILINE)
        else:
            content += f"\n{env_key}={token}"
        os.environ[env_key] = token
        saved.append(env_key)
    ENV_FILE.write_text(content, encoding="utf-8")
    return saved


# ── Page Insights ──────────────────────────────────────────────────────────────

INSIGHT_METRICS = [
    "page_impressions_organic_unique",       # Reach tự nhiên page
    "page_post_impressions_organic_unique",  # Reach tự nhiên bài đăng
    "page_impressions_viral_unique",         # Reach viral
    "page_impressions_paid_unique",          # Reach trả phí (so sánh)
]

METRIC_LABELS = {
    "page_impressions_organic_unique":      "Reach TN page",
    "page_post_impressions_organic_unique": "Reach TN bài đăng",
    "page_impressions_viral_unique":        "Reach viral",
    "page_impressions_paid_unique":         "Reach trả phí",
}


def fetch_page_reach(page_id: str, page_token: str, days: int = 30) -> list[dict]:
    """Kéo reach từng ngày. Trả về list {date, metric_name, value}."""
    from datetime import datetime, timedelta
    since = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.today().strftime("%Y-%m-%d")

    r = requests.get(f"{GRAPH}/{page_id}/insights", params={
        "metric": ",".join(INSIGHT_METRICS),
        "period": "day",
        "since": since,
        "until": until,
        "access_token": page_token,
    }, timeout=20)
    r.raise_for_status()

    rows = []
    for metric_obj in r.json().get("data", []):
        name = metric_obj["name"]
        label = METRIC_LABELS.get(name, name)
        for pt in metric_obj.get("values", []):
            rows.append({
                "date": pt["end_time"][:10],
                "metric": name,
                "label": label,
                "value": pt["value"],
            })
    return rows


# ── Flask routes ───────────────────────────────────────────────────────────────

_STATUS_HTML = """
<!doctype html><html><head><meta charset="utf-8">
<title>Kết nối Facebook Page</title>
<style>
  body{font-family:sans-serif;max-width:600px;margin:60px auto;padding:0 20px;color:#333}
  h2{color:#1877f2}.btn{display:inline-block;padding:12px 24px;background:#1877f2;
  color:#fff;border-radius:6px;text-decoration:none;font-size:15px;margin-top:20px}
  .ok{color:#22c55e;font-weight:bold}.err{color:#ef4444;font-weight:bold}
  pre{background:#f5f5f5;padding:12px;border-radius:4px;font-size:13px}
  .warn{background:#fef9c3;border-left:4px solid #eab308;padding:10px 16px;margin:16px 0}
</style></head><body>
<h2>Kết nối Facebook Page Insights</h2>
{% if missing_secret %}
<div class="warn">
  ⚠️ Chưa có <code>FB_APP_SECRET</code> trong <code>.env</code>.<br><br>
  Vào <a href="https://developers.facebook.com/apps/2699500137102333/settings/basic/" target="_blank">
  Meta Developer Console → App 2699500137102333 → Basic Settings</a> → copy <b>App Secret</b>
  → thêm vào <code>fb_ad_local/.env</code>:<br>
  <pre>FB_APP_SECRET=xxxxxxxxxxxxxx</pre>
  Sau đó restart app và quay lại trang này.
</div>
{% else %}
<p><b>App:</b> Kéo chỉ số ads - claude ({{ app_id }})</p>
<p><b>Redirect URI đã đăng ký:</b> <code>{{ redirect_uri }}</code><br>
<small>(Cần thêm URI này vào <a href="https://developers.facebook.com/apps/2699500137102333/fb-login/settings/" target="_blank">Valid OAuth Redirect URIs</a> của app)</small></p>
<h3>Trạng thái token hiện tại</h3>
<ul>{% for k, v in token_status.items() %}<li>{{ v.name }}: <span class="{{ 'ok' if v.ok else 'err' }}">{{ '✅ Đã có' if v.ok else '❌ Chưa có' }}</span></li>{% endfor %}</ul>
<a href="{{ oauth_url }}" class="btn">🔗 Đăng nhập Facebook để lấy token</a>
<p><small>Sau khi đăng nhập, token sẽ được lưu tự động vào <code>.env</code> và có hiệu lực vĩnh viễn.</small></p>
{% endif %}
</body></html>
"""

_CALLBACK_HTML = """
<!doctype html><html><head><meta charset="utf-8"><title>Kết quả</title>
<style>body{font-family:sans-serif;max-width:600px;margin:60px auto;padding:0 20px}
.ok{color:#22c55e;font-weight:bold}.err{color:#ef4444}
a{color:#1877f2}</style></head><body>
{% if error %}
<h2 class="err">❌ Lỗi</h2><pre>{{ error }}</pre>
{% else %}
<h2 class="ok">✅ Thành công!</h2>
<p>Đã lưu token cho {{ saved|length }} page:</p>
<ul>{% for k in saved %}<li><code>{{ k }}</code></li>{% endfor %}</ul>
<p>Token này vĩnh viễn không hết hạn.</p>
{% endif %}
<p><a href="/auth/fb">← Quay lại</a> | <a href="/organic-reach">Xem Reach Tự Nhiên →</a></p>
</body></html>
"""


@bp.route("/auth/fb")
def fb_auth_start():
    missing_secret = not _app_secret()
    token_status = {}
    for page_id, env_key in TARGET_PAGES.items():
        name = {
            "821332004654252":  "Kính Mắt Eye Plus",
            "552228558319706":  "Kính Mắt Eye Plus - Nữ",
            "1062539773905872": "Kính Mắt Eye Plus 4Young",
        }.get(page_id, page_id)
        token_status[env_key] = {"name": name, "ok": bool(os.getenv(env_key))}

    return render_template_string(_STATUS_HTML,
        missing_secret=missing_secret,
        app_id=_app_id(),
        redirect_uri=_redirect_uri(),
        oauth_url=build_oauth_url(),
        token_status=token_status,
    )


@bp.route(REDIRECT_PATH)
def fb_auth_callback():
    error_msg = request.args.get("error_description") or request.args.get("error")
    if error_msg:
        return render_template_string(_CALLBACK_HTML, error=error_msg, saved=[])

    code = request.args.get("code")
    if not code:
        return render_template_string(_CALLBACK_HTML, error="Không nhận được code từ Facebook.", saved=[])

    try:
        short_token = exchange_code_for_token(code)
        long_token = exchange_long_lived(short_token)
        page_tokens = get_page_tokens(long_token)
        saved = save_tokens_to_env(page_tokens)
        return render_template_string(_CALLBACK_HTML, error=None, saved=saved)
    except Exception as e:
        return render_template_string(_CALLBACK_HTML, error=str(e), saved=[])
