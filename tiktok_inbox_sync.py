#!/usr/bin/env python3
"""Đồng bộ hộp thư TikTok (Pancake) → Postgres, có gắn chiến dịch.

Vì sao cần: TikTok Ads API KHÔNG trả kết quả tin nhắn cho nhóm quảng cáo đặt mục
tiêu MESSAGE_CLUE (cột Mess trống trên bảng), nên không so sánh được chiến dịch
nào tốt hơn. Pancake giữ toàn bộ hộp thư TikTok VÀ lưu lịch sử bấm quảng cáo của
từng khách (`ad_clicks`, type="tt_ads") — ghép hai thứ này lại là dựng được điểm
chất lượng theo chiến dịch, giống hệt cách đang chấm inbox Facebook.

Đường đi: hội thoại → khách → ad_clicks[ad_id, thời điểm] → ad_id → chiến dịch
(tra qua TikTok API). Độ phủ đo thực tế 12/08/2026: 16/20 hội thoại có ad_clicks.

Bảng đích `tiktok_inbox_intents` cố ý dùng ĐÚNG tên cột của
`pancake_inbox_intents` để inbox_db.py chấm điểm bằng chung một bộ truy vấn.

Chạy:
    python tiktok_inbox_sync.py --days 30
"""
import argparse
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tiktok_inbox_sync")

PAGE_ID = os.environ.get(
    "PANCAKE_TIKTOK_PAGE_ID",
    "ttm_-000joYahmQnwoAD0YCesgdKy06A3im6kcot",
)
PAGE_LABEL = "TikTok_EyePlusOfficial"
API = "https://pancake.vn/api/v1/pages"
PAGE_SIZE = 40          # Pancake trả tối đa 40/lần dù xin nhiều hơn
THROTTLE = 1.2          # giây giữa 2 lần gọi — Pancake trả 429 nếu nhanh hơn

# ─── Phân loại ý định — copy nguyên từ fb_chatbot/db_inbox.py để hai kênh chấm
# cùng một thước đo. Sửa bên nào thì phải sửa cả bên kia.
PRICE_KW = ["giá", "bao nhiêu", "bnh", "bn", "tiền", "phí", "báo giá", "mấy tiền", "bao tien", "price"]
ADDR_KW = ["địa chỉ", "ở đâu", "chỗ nào", "chi nhánh", "showroom", "cửa hàng", "hà nội", "hcm", "sài gòn",
           "bắc ninh", "hải phòng", "quận", "phường"]
BUY_KW = ["đặt", "mua", "chốt", "ship", "order", "đơn", "thanh toán", "chuyển khoản", "inbox chốt"]
CONSULT_KW = ["tư vấn", "độ kính", "cận", "loạn", "tròng", "gọng", "mẫu", "kiểu", "màu", "size",
              "chống ánh sáng", "uv", "đa tròng", "chống nước"]
NEG_KW = ["tệ", "dở", "chán", "thất vọng", "phàn nàn", "hoàn tiền", "lừa", "giả", "fake", "kém"]

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_message(m: dict) -> str:
    """Bỏ thẻ HTML + ghép lại số điện thoại.

    Pancake bọc SĐT trong thẻ <Copy> và hiển thị có dấu cách ("0328 577 820"),
    nên sau khi bóc thẻ thì biểu thức tìm số của inbox_db (0xxxxxxxxx liền nhau)
    không khớp. Nối thêm dạng chuẩn từ phone_info để hai kênh dò SĐT như nhau.
    """
    text = _TAG_RE.sub("", m.get("message") or "").strip()
    nums = [str(p.get("phone_number") or "").strip()
            for p in (m.get("phone_info") or []) if p.get("phone_number")]
    for n in nums:
        if n and n not in text:
            text = f"{text} {n}".strip()
    return text


def _conv_phones(conv: dict, detail: dict) -> list:
    """SĐT của hội thoại, gom từ mọi chỗ Pancake có thể để.

    Hai định dạng khác nhau tuỳ endpoint: danh sách hội thoại trả về
    [{"phone_number": "0328577820", ...}], còn chi tiết trả về ["0328577820"].
    Đo thực tế 12/08: 6,2% hội thoại TikTok có SĐT — chỉ đọc ở chi tiết thì sót
    gần hết, phải lấy cả từ danh sách.
    """
    out = []
    for src in (conv.get("recent_phone_numbers"), detail.get("conv_phone_numbers"),
                detail.get("recent_phone_numbers")):
        for x in src or []:
            n = x.get("phone_number") if isinstance(x, dict) else x
            n = str(n or "").strip()
            if n and n not in out:
                out.append(n)
    return out


def classify(msg: str) -> str:
    if not msg:
        return "other"
    m = msg.lower()
    if any(k in m for k in NEG_KW):
        return "neg"
    if any(k in m for k in BUY_KW):
        return "buy"
    if any(k in m for k in PRICE_KW):
        return "price"
    if any(k in m for k in CONSULT_KW):
        return "consult"
    if any(k in m for k in ADDR_KW):
        return "addr"
    return "other"


def _token() -> str:
    tok = os.environ.get("PANCAKE_JWT", "").strip()
    if not tok:
        raise RuntimeError("PANCAKE_JWT chưa set (token đăng nhập Pancake)")
    return tok


def _get(path: str, params: dict, tries: int = 4):
    """GET Pancake API. Lùi dần khi bị 429 — giới hạn nhịp của họ khá chặt."""
    url = f"{API}/{PAGE_ID}/{path}?" + urllib.parse.urlencode({"access_token": _token(), **params})
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                body = json.loads(r.read().decode())
            if isinstance(body, dict) and body.get("error_code") == 429:
                time.sleep(5 * (i + 1))
                continue
            return body
        except Exception as e:
            if i == tries - 1:
                log.warning(f"  GET {path} lỗi: {e}")
                return {}
            time.sleep(3 * (i + 1))
    return {}


def _parse_ts(s: str):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_conversations(days: int) -> list:
    """Kéo hội thoại mới → cũ, dừng khi vượt mốc ngày. Phân trang bằng current_count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out, offset, seen = [], 0, set()
    while True:
        body = _get("conversations", {
            "current_count": offset,
            "unread_first": "false",
            "mode": "NONE",
            "tags": '"ALL"',
            "except_tags": "[]",
            "cursor_mode": "true",
            "from_platform": "web",
        })
        convs = (body or {}).get("conversations") or []
        if not convs:
            break
        fresh = [c for c in convs if c.get("id") not in seen]
        if not fresh:
            break
        for c in fresh:
            seen.add(c["id"])
        out.extend(fresh)
        oldest = _parse_ts(fresh[-1].get("updated_at") or "")
        log.info(f"  trang offset={offset}: +{len(fresh)} hội thoại (tổng {len(out)})")
        if oldest and oldest < cutoff:
            break
        offset += PAGE_SIZE
        if offset > 8000:      # chặn vòng lặp vô hạn nếu Pancake trả lặp
            log.warning("  chạm trần 8000 hội thoại — dừng")
            break
        time.sleep(THROTTLE)
    return [c for c in out if (_parse_ts(c.get("updated_at") or "") or cutoff) >= cutoff]


def fetch_detail(conv: dict) -> dict:
    custs = conv.get("customers") or []
    cust_id = custs[0].get("id") if custs else None
    if not cust_id:
        return {}
    cid = urllib.parse.quote(conv["id"], safe="")
    return _get(f"conversations/{cid}/messages", {"customer_id": cust_id}) or {}


def pick_ad_id(detail: dict, conv_start) -> str | None:
    """Quảng cáo nào sinh ra hội thoại này.

    Một khách có thể bấm nhiều quảng cáo qua nhiều tháng. Quy ước: lấy lần bấm
    GẦN NHẤT TRƯỚC tin đầu của hội thoại — cùng logic quy đổi lần-chạm-cuối mà
    Facebook dùng. Nếu không có lần bấm nào trước đó (lệch giờ, hoặc Pancake ghi
    nhận muộn) thì lấy lần bấm sớm nhất, còn hơn là bỏ trắng.
    """
    clicks = [x for v in (detail.get("ad_clicks") or {}).values() if v for x in v]
    clicks = [c for c in clicks if c.get("type") == "tt_ads" and c.get("ad_id")]
    if not clicks:
        return None
    dated = [(_parse_ts(c.get("inserted_at") or ""), c["ad_id"]) for c in clicks]
    dated = [(t, a) for t, a in dated if t]
    if not dated:
        return clicks[-1]["ad_id"]
    dated.sort()
    if conv_start:
        before = [(t, a) for t, a in dated if t <= conv_start + timedelta(hours=1)]
        if before:
            return before[-1][1]
    return dated[0][1]


def map_ads_to_campaigns(ad_ids: list) -> dict:
    """ad_id → (campaign_id, campaign_name) qua TikTok Ads API."""
    if not ad_ids:
        return {}
    try:
        import tiktok_fetcher as tf
    except Exception as e:
        log.warning(f"  không nạp được tiktok_fetcher: {e}")
        return {}
    out = {}
    for adv in tf._advertiser_ids():
        remaining = [a for a in ad_ids if a not in out]
        for i in range(0, len(remaining), 100):     # TikTok giới hạn 100 id/lần
            chunk = remaining[i:i + 100]
            d = tf._get("/ad/get/", {
                "advertiser_id": adv,
                "filtering": json.dumps({"ad_ids": chunk}),
                "fields": json.dumps(["ad_id", "campaign_id", "campaign_name"]),
                "page_size": 100,
            })
            for r in (d.get("data") or {}).get("list") or []:
                out[str(r.get("ad_id"))] = (str(r.get("campaign_id") or ""), r.get("campaign_name") or "")
    return out


def _conn():
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    return psycopg2.connect(url, connect_timeout=15)


def ensure_table():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tiktok_inbox_intents (
                msg_id        TEXT PRIMARY KEY,
                conv_id       TEXT NOT NULL,
                page_id       TEXT,
                ad_id         TEXT,
                campaign_id   TEXT,
                campaign_name TEXT,
                customer_name TEXT,
                message       TEXT,
                label         TEXT DEFAULT 'other',
                label_source  TEXT DEFAULT 'rule',
                msg_ts        TIMESTAMPTZ,
                synced_at     TIMESTAMPTZ DEFAULT NOW(),
                cust_msg_count INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_tt_inbox_ts ON tiktok_inbox_intents (msg_ts DESC);
            CREATE INDEX IF NOT EXISTS ix_tt_inbox_camp ON tiktok_inbox_intents (campaign_id);
            CREATE INDEX IF NOT EXISTS ix_tt_inbox_conv ON tiktok_inbox_intents (conv_id);
        """)
        conn.commit()


def upsert(rows: list) -> int:
    if not rows:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO tiktok_inbox_intents
                (msg_id, conv_id, page_id, ad_id, campaign_id, campaign_name,
                 customer_name, message, label, label_source, msg_ts, cust_msg_count)
            VALUES %s
            ON CONFLICT (msg_id) DO UPDATE SET
                ad_id = EXCLUDED.ad_id,
                campaign_id = EXCLUDED.campaign_id,
                campaign_name = EXCLUDED.campaign_name,
                label = EXCLUDED.label,
                cust_msg_count = EXCLUDED.cust_msg_count,
                synced_at = NOW()
        """, rows, page_size=500)
        conn.commit()
    return len(rows)


def sync(days: int = 30, limit_convs: int | None = None) -> dict:
    ensure_table()
    log.info(f"Kéo hội thoại TikTok {days} ngày…")
    convs = fetch_conversations(days)
    if limit_convs:
        convs = convs[:limit_convs]
    log.info(f"→ {len(convs)} hội thoại trong khoảng")

    staged, ad_ids, no_ad = [], set(), 0
    for i, c in enumerate(convs, 1):
        detail = fetch_detail(c)
        time.sleep(THROTTLE)
        msgs = detail.get("messages") or []
        # from.id của TIN = PAGE_ID khi do trang gửi. Đừng dùng conv["from"] —
        # trường đó là KHÁCH, lấy nhầm sẽ đảo ngược bộ lọc và đếm tin nhân viên.
        cust_msgs = [m for m in msgs if (m.get("from") or {}).get("id") != PAGE_ID]
        conv_start = _parse_ts((cust_msgs[0] if cust_msgs else c).get("inserted_at") or "")
        ad_id = pick_ad_id(detail, conv_start)
        if ad_id:
            ad_ids.add(ad_id)
        else:
            no_ad += 1
        cname = (c.get("customers") or [{}])[0].get("name")
        # SĐT: Pancake ghi ở CẤP HỘI THOẠI (conv_phone_numbers) chứ không phải
        # lúc nào cũng gắn vào tin. Nếu không tin nào chứa số thì gắn vào tin
        # cuối của khách, để inbox_db dò ra bằng đúng biểu thức dùng cho Facebook.
        conv_phones = _conv_phones(c, detail)
        rows_here = []
        for m in cust_msgs:
            text = _clean_message(m)
            row = {
                "msg_id": m.get("id"), "conv_id": c["id"], "ad_id": ad_id,
                "customer_name": cname, "message": text[:2000],
                "label": classify(text), "msg_ts": _parse_ts(m.get("inserted_at") or ""),
                "cust_msg_count": len(cust_msgs),
            }
            rows_here.append(row)
            staged.append(row)
        if conv_phones and rows_here:
            if not any(re.search(r"0[35789][ .\-]?[0-9]([ .\-]?[0-9]){7}", r["message"] or "") for r in rows_here):
                last = rows_here[-1]
                last["message"] = (last["message"] + " " + " ".join(conv_phones)).strip()[:2000]
        if i % 25 == 0:
            log.info(f"  …{i}/{len(convs)} hội thoại")

    log.info(f"Tra chiến dịch cho {len(ad_ids)} quảng cáo…")
    camp = map_ads_to_campaigns(sorted(ad_ids))

    rows = []
    for s in staged:
        if not s["msg_id"] or not s["msg_ts"]:
            continue
        cid, cname_ = camp.get(s["ad_id"] or "", ("", ""))
        rows.append((s["msg_id"], s["conv_id"], PAGE_LABEL, s["ad_id"], cid or None,
                     cname_ or None, s["customer_name"], s["message"], s["label"],
                     "rule", s["msg_ts"], s["cust_msg_count"]))
    n = upsert(rows)
    stats = {
        "hoi_thoai": len(convs),
        "khong_co_quang_cao": no_ad,
        "quang_cao": len(ad_ids),
        "chien_dich": len({v[0] for v in camp.values() if v[0]}),
        "tin_nhan_ghi": n,
    }
    log.info(f"Xong: {stats}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=None, help="giới hạn số hội thoại (để thử)")
    a = ap.parse_args()
    sync(days=a.days, limit_convs=a.limit)
