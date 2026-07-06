# -*- coding: utf-8 -*-
"""Áp phương án B (mục tiêu 700 hội thoại): TẮT nhóm D · TĂNG +30% nhóm A+B · giữ C · khóa TQ.

An toàn:
- BỎ QUA cam mục tiêu Reach/Awareness (chỉ đọc, báo cáo riêng — user xử sau).
- Cam CBO: tăng +30% ngân sách cấp campaign.
- Cam ABO: tăng +30% vào TỪNG ad set (không phải cấp campaign).
- MODE=dry chỉ đọc; MODE=live mới thực thi thật.
"""
import os, re, sys, json, time
import psycopg2, requests

MODE = os.getenv("MODE", "dry")  # dry | live
PCT = 30
FB = "https://graph.facebook.com/v19.0"

# env
ENV = {}
for line in open("/Users/hungnguyen/Công Việc/AI/fb_ad_local/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("="); ENV[k.strip()] = v.strip()
DB = ENV["ROLLUP_DATABASE_URL"]

sys.path.insert(0, "/Users/hungnguyen/Công Việc/AI/fb_ad_local")
from fetcher import AD_ACCOUNTS
ACC2TOKEN = {a["account_id"]: ENV.get(a["token_env"], "") for a in AD_ACCOUNTS}
ACC2BM = {a["account_id"]: a["bm"] for a in AD_ACCOUNTS}

# Reach/awareness objectives → KHÔNG đụng
REACH_OBJ = {"OUTCOME_AWARENESS", "BRAND_AWARENESS", "REACH", "AD_RECALL_LIFT",
             "OUTCOME_TRAFFIC", "LINK_CLICKS", "POST_ENGAGEMENT", "VIDEO_VIEWS"}

SQL = """
WITH c AS (
  SELECT campaign_id, max(campaign_name) cn, max(account_id) acc,
    sum(spend_raw) sp7, sum(messages_conv_7d) conv7, sum(purchases) pu7, sum(purchase_value) rev7
  FROM fb_ads_daily WHERE date >= current_date - INTERVAL '7 days' GROUP BY campaign_id),
r3 AS (SELECT campaign_id, sum(spend_raw) sp3, sum(messages_conv_7d) conv3
  FROM fb_ads_daily WHERE date >= current_date - INTERVAL '3 days' GROUP BY campaign_id)
SELECT c.campaign_id, c.cn, c.acc, c.rev7::float/NULLIF(c.sp7,0) roas,
  c.sp7::float/NULLIF(c.conv7,0) gc
FROM c LEFT JOIN r3 USING(campaign_id) WHERE COALESCE(r3.sp3,0) >= 150000;
"""
TQ_RE = re.compile(r"(?<![A-Z])(TQ|TOAN QUOC|TOÀN QUỐC)(?![A-Z])")

def action_of(cn, roas, gc):
    roas = roas or 0; gc = gc or 0
    if TQ_RE.search((cn or "").upper()):
        return ("TẮT" if roas == 0 else "GIỮ")  # TQ test chết thì tắt, còn lại khóa
    if roas >= 2.2:           # A + B
        return "TĂNG"
    if gc < 55000:            # C — giữ
        return "GIỮ"
    return "TẮT"              # D

def fb_get(path, token, params=None):
    p = {"access_token": token}; p.update(params or {})
    return requests.get(f"{FB}/{path}", params=p, timeout=30).json()

def fb_post(path, token, data):
    d = {"access_token": token}; d.update(data)
    return requests.post(f"{FB}/{path}", data=d, timeout=30).json()

conn = psycopg2.connect(DB); rows = []
with conn.cursor() as cur:
    cur.execute(SQL)
    for cid, cn, acc, roas, gc in cur.fetchall():
        rows.append((str(cid), cn, str(acc), roas, gc))
conn.close()

# Chỉ xử lý cam có hành động TẮT hoặc TĂNG
todo = [(cid, cn, acc, action_of(cn, roas, gc)) for cid, cn, acc, roas, gc in rows]
todo = [t for t in todo if t[3] in ("TẮT", "TĂNG")]

report = {"paused": [], "cbo_up": [], "abo_up": [], "reach_skip": [], "errors": []}
print(f"=== MODE={MODE} · {len(todo)} cam cần xử lý ===\n")

for cid, cn, acc, act in todo:
    token = ACC2TOKEN.get(acc, "")
    if not token:
        report["errors"].append((cid, cn, "thiếu token cho acc " + acc)); continue
    meta = fb_get(cid, token, {"fields": "objective,daily_budget,name,effective_status"})
    if "error" in meta:
        report["errors"].append((cid, cn, meta["error"].get("message", "?"))); continue
    obj = meta.get("objective", "")
    # Lọc Reach
    if obj in REACH_OBJ:
        report["reach_skip"].append((cid, cn, obj)); continue

    if act == "TẮT":
        if MODE == "live":
            r = fb_post(cid, token, {"status": "PAUSED"})
            if "error" in r: report["errors"].append((cid, cn, "pause: " + r["error"].get("message", "?"))); continue
        report["paused"].append((cid, cn))
    else:  # TĂNG +30%
        camp_budget = int(meta.get("daily_budget") or 0)
        if camp_budget > 0:  # CBO
            newb = int(round(camp_budget * (1 + PCT/100)))
            if MODE == "live":
                r = fb_post(cid, token, {"daily_budget": newb})
                if "error" in r: report["errors"].append((cid, cn, "cbo: " + r["error"].get("message", "?"))); continue
            report["cbo_up"].append((cid, cn, camp_budget, newb))
        else:  # ABO → tăng từng ad set ACTIVE
            adsets = fb_get(f"{cid}/adsets", token, {"fields": "id,daily_budget,effective_status", "limit": 50})
            n_up = 0
            for a in adsets.get("data", []):
                ab = int(a.get("daily_budget") or 0)
                if ab <= 0 or a.get("effective_status") != "ACTIVE":
                    continue
                newb = int(round(ab * (1 + PCT/100)))
                if MODE == "live":
                    r = fb_post(a["id"], token, {"daily_budget": newb})
                    if "error" in r:
                        report["errors"].append((cid, cn, f"abo adset {a['id']}: " + r["error"].get("message", "?"))); continue
                n_up += 1
            report["abo_up"].append((cid, cn, n_up))
    time.sleep(0.15)

# ─── In báo cáo ───
print(f"🔴 TẮT (đã{'' if MODE=='live' else ' sẽ'} tạm dừng): {len(report['paused'])} cam")
print(f"🟢 TĂNG CBO (cấp campaign +30%): {len(report['cbo_up'])} cam")
print(f"🟢 TĂNG ABO (vào ad set +30%): {len(report['abo_up'])} cam · {sum(x[2] for x in report['abo_up'])} ad set")
print(f"⚪ BỎ QUA cam Reach (để đó, báo sau): {len(report['reach_skip'])} cam")
print(f"❌ Lỗi: {len(report['errors'])}")

if report["reach_skip"]:
    print("\n--- Cam REACH bỏ qua ---")
    for cid, cn, obj in report["reach_skip"]:
        print(f"  [{obj}] {cn[:50]}")
if report["abo_up"]:
    print("\n--- Cam ABO (tăng ở ad set) ---")
    for cid, cn, n in report["abo_up"][:20]:
        print(f"  {n} adset ← {cn[:48]}")
if report["errors"]:
    print("\n--- Lỗi ---")
    for cid, cn, e in report["errors"][:15]:
        print(f"  {cn[:40]}: {e}")

json.dump(report, open("/tmp/apply_plan_result.json", "w"), ensure_ascii=False, indent=1)
print(f"\n(chi tiết: /tmp/apply_plan_result.json)")
