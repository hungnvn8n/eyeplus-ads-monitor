"""Đo độ hiện diện trên AI (AI SEO) từ nhật ký máy chủ kinhmateyeplus.com.

Khác với SEO thường, AI không có "thứ hạng" để tra. Hai thứ đo được khách quan:
  1. Bot AI có vào đọc web mình không, đọc trang nào
  2. Có khách nào từ ChatGPT/Perplexity/Gemini bấm vào web không

Cách lấy: đẩy một worker PHP có khoá ngẫu nhiên lên máy chủ qua cPanel API,
gọi một lần, xoá ngay. Không để lại gì trên máy chủ.

Cần biến môi trường EYEPLUS_CPANEL = "jsrmamtihosting:<khoá API cPanel>"
(chạy ở máy cá nhân thì đọc ~/.eyeplus-cpanel).
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import time
import ipaddress
import urllib.parse
import urllib.request

MAY_CHU = "https://kinhmateyeplus.com:2083"
WEB = "https://kinhmateyeplus.com"
GOC = "/home/jsrmamtihosting/public_html"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / "data" / "ai_seo_cache.json"
CACHE_GIO = float(os.getenv("AI_SEO_CACHE_HOURS", "6"))

# Bot nào là AI, bot nào là máy tìm kiếm thường
BOT_AI = {
    "ChatGPT (tim kiem)", "ChatGPT (nguoi dung bam)", "OpenAI (hoc du lieu)",
    "Perplexity", "Perplexity (nguoi dung)", "Claude (tim kiem)",
    "Claude (nguoi dung)", "Anthropic (hoc du lieu)", "Google Gemini",
    "Meta AI", "TikTok/ByteDance", "Amazon", "Apple",
}
NGUON_AI = {"ChatGPT", "Perplexity", "Gemini", "Claude", "Copilot"}

# Danh sách IP chính thức do nhà cung cấp công bố. Tên bot trong nhật ký là do
# chính bên gọi tự khai nên GIẢ MẠO ĐƯỢC — đã bắt được kẻ đội lốt bot AI để dò
# tìm khoá SSH, file .env trên máy chủ (18/10/2026). Vì vậy phải đối chiếu IP.
NGUON_IP = {
    "ChatGPT (tim kiem)": "https://openai.com/searchbot.json",
    "ChatGPT (nguoi dung bam)": "https://openai.com/chatgpt-user.json",
    "OpenAI (hoc du lieu)": "https://openai.com/gptbot.json",
}
_IP_CACHE: dict[str, list] = {}


def _dai_ip(url: str) -> list:
    """Tải danh sách dải IP chính thức, nhớ trong bộ nhớ."""
    if url in _IP_CACHE:
        return _IP_CACHE[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
        out = []
        for x in d.get("prefixes", []):
            v = x.get("ipv4Prefix") or x.get("ipv6Prefix")
            if v:
                try:
                    out.append(ipaddress.ip_network(v))
                except ValueError:
                    pass
    except Exception:
        out = []
    _IP_CACHE[url] = out
    return out


def _tach_that_gia(bot_ip: dict) -> tuple[dict, dict]:
    """Chia số lượt thành (thật, giả mạo) dựa trên IP công bố.

    Bot nào nhà cung cấp không công bố IP thì coi như thật — không có cơ sở
    để kết luận ngược lại.
    """
    that, gia = {}, {}
    for ten, ips in (bot_ip or {}).items():
        url = NGUON_IP.get(ten)
        dai = _dai_ip(url) if url else []
        if not dai:
            that[ten] = sum(ips.values())
            continue
        for ip, n in ips.items():
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                gia[ten] = gia.get(ten, 0) + n
                continue
            if any(a in m for m in dai):
                that[ten] = that.get(ten, 0) + n
            else:
                gia[ten] = gia.get(ten, 0) + n
    return that, gia


def _khoa() -> str:
    k = os.getenv("EYEPLUS_CPANEL", "").strip()
    if k:
        return k
    t = pathlib.Path.home() / ".eyeplus-cpanel"
    if t.exists():
        return t.read_text().strip()
    raise RuntimeError("Chưa có khoá cPanel (EYEPLUS_CPANEL hoặc ~/.eyeplus-cpanel)")


def _goi(duong_dan: str, **ts):
    url = f"{MAY_CHU}/{duong_dan}" + (("?" + urllib.parse.urlencode(ts)) if ts else "")
    req = urllib.request.Request(url, headers={"Authorization": f"cpanel {_khoa()}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _day_len(noi_dung: bytes, ten: str):
    ranh = "----epai" + str(int(time.time()))
    than = b""
    for k, v in (("dir", GOC), ("overwrite", "1")):
        than += (f"--{ranh}\r\nContent-Disposition: form-data; "
                 f"name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    than += (f"--{ranh}\r\nContent-Disposition: form-data; name=\"file-1\"; "
             f"filename=\"{ten}\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n").encode() + noi_dung + b"\r\n"
    than += f"--{ranh}--\r\n".encode()
    req = urllib.request.Request(
        MAY_CHU + "/execute/Fileman/upload_files", data=than,
        headers={"Authorization": f"cpanel {_khoa()}",
                 "Content-Type": f"multipart/form-data; boundary={ranh}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def _xoa(ten: str) -> bool:
    try:
        r = _goi("json-api/cpanel", cpanel_jsonapi_user="jsrmamtihosting",
                 cpanel_jsonapi_apiversion=2, cpanel_jsonapi_module="Fileman",
                 cpanel_jsonapi_func="fileop", op="unlink",
                 sourcefiles="/public_html/" + ten, doubledecode=0)
        return r["cpanelresult"]["data"][0].get("result") == 1
    except Exception:
        return False


def _quet(loc: str = "domlogs", toi_da: int = 9_000_000) -> dict:
    mau = (ROOT / "ai_seo_worker.php").read_text(encoding="utf-8")
    token = secrets.token_urlsafe(24)
    ten = f"ep-ai-{secrets.token_hex(5)}.php"
    _day_len(mau.replace("__TOKEN__", token).encode("utf-8"), ten)
    try:
        url = f"{WEB}/{ten}?k={token}&f={loc}&max={toi_da}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read())
    finally:
        _xoa(ten)


def du_lieu(buoc_lai: bool = False, loc: str = "domlogs") -> dict:
    """Trả dữ liệu đã dọn sẵn cho trang web. Có nhớ tạm để khỏi quét liên tục."""
    if not buoc_lai and CACHE.exists():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - c.get("luc", 0) < CACHE_GIO * 3600:
                return c
        except Exception:
            pass

    try:
        d = _quet(loc)
        loi = ""
    except Exception as e:
        if CACHE.exists():
            c = json.loads(CACHE.read_text(encoding="utf-8"))
            c["loi"] = f"Không quét được, đang hiện số cũ: {e}"
            return c
        return {"loi": str(e), "luc": time.time(), "bot_ai": [], "bot_thuong": [],
                "nguon_ai": [], "nguon_thuong": [], "dong": 0, "trang_ai": {}}

    bot_goc = d.get("bot") or {}
    that, gia = _tach_that_gia(d.get("bot_ip") or {})
    # với bot có công bố IP thì lấy số đã xác minh, còn lại giữ số tự khai
    bot = {k: (that.get(k, 0) if (k in that or k in gia) else v)
           for k, v in bot_goc.items()}
    bot = {k: v for k, v in bot.items() if v > 0}
    ng = d.get("nguon_ai") or {}
    ket = {
        "luc": time.time(),
        "loi": loi,
        "dong": d.get("dong_da_doc", 0),
        "ngay": sorted({x for v in (d.get("bot_theo_ngay") or {}).values() for x in v}),
        "bot_ai": sorted(((k, v) for k, v in bot.items() if k in BOT_AI),
                         key=lambda x: -x[1]),
        "bot_thuong": sorted(((k, v) for k, v in bot.items() if k not in BOT_AI),
                             key=lambda x: -x[1]),
        "nguon_ai": sorted(((k, v) for k, v in ng.items() if k in NGUON_AI),
                           key=lambda x: -x[1]),
        "nguon_thuong": sorted(((k, v) for k, v in ng.items() if k not in NGUON_AI),
                               key=lambda x: -x[1]),
        "trang_ai": {k: list(v.items())[:6]
                     for k, v in (d.get("bot_url") or {}).items() if k in BOT_AI},
        "gia_mao": sorted(gia.items(), key=lambda x: -x[1]),
        "tong_gia_mao": sum(gia.values()),
    }
    ket["tong_bot_ai"] = sum(v for _, v in ket["bot_ai"])
    ket["tong_nguon_ai"] = sum(v for _, v in ket["nguon_ai"])
    ket["tong_nguon_thuong"] = sum(v for _, v in ket["nguon_thuong"])
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(ket, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return ket
