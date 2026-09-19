"""ĐỐI CHỨNG cho TikTok Ads — bản song sinh của shadow.py (Facebook).

Nguyên tắc: DÙNG CHUNG bộ quy tắc của Facebook (shadow.evaluate_v3, cùng mốc
chi/ngưỡng CPA/ROAS, cùng bậc thang GIẢM 50% → TẠM DỪNG). Anh Hùng chốt
2026-09-19: "quy tắc thì như bên FB nhé". Ở đây chỉ đổi NGUỒN DỮ LIỆU, không
viết lại logic chấm — tránh 2 bộ quy tắc trôi lệch nhau theo thời gian.

Khác Facebook ở 3 điểm (do dữ liệu TikTok có/không có gì):
  1. Chấm ở cấp CHIẾN DỊCH (campaign), không phải từng quảng cáo — TikTok API
     không trả kết quả tin nhắn theo ad cho camp MESSAGE_CLUE.
  2. "Tin nhắn" lấy từ bảng tiktok_inbox_intents (Pancake ad_clicks) qua
     inbox_db.campaign_quality(source="tiktok"), KHÔNG lấy field `conversion`
     của TikTok API — xem [[project_tiktok_mess_message_clue]].
  3. Chi phí/đơn/ROAS lấy từ tiktok_daily_campaign (cache theo ngày, tiktok_db).

Dữ liệu ghi vào CHUNG shadow.db nhưng bảng riêng tiền tố tt_ — không đụng dữ
liệu đối chứng Facebook.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import shadow   # dùng lại quy tắc + phân loại vùng của Facebook

ROOT = Path(__file__).resolve().parent

# Cửa sổ cộng dồn + cửa sổ trượt: lấy y hệt Facebook để 2 bên so được với nhau
LOOKBACK_DAYS = int(os.getenv("TT_SHADOW_LOOKBACK_DAYS", str(shadow.SHADOW_LOOKBACK_DAYS)))
RECENT_WINDOW_DAYS = int(os.getenv("TT_SHADOW_RECENT_DAYS", str(shadow.RECENT_WINDOW_DAYS)))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(shadow._db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tt_snapshots (
            snap_date TEXT, campaign_id TEXT,
            campaign_name TEXT, advertiser_id TEXT, advertiser_name TEXT,
            region TEXT, status TEXT, op_status TEXT DEFAULT '',
            spend_cum REAL, messages INTEGER, purchases INTEGER, roas REAL,
            PRIMARY KEY (snap_date, campaign_id)
        );
        CREATE TABLE IF NOT EXISTS tt_decisions (
            snap_date TEXT, campaign_id TEXT, campaign_name TEXT,
            advertiser_id TEXT, advertiser_name TEXT,
            region TEXT, gate TEXT,
            spend_cum REAL, messages INTEGER, purchases INTEGER,
            cost_per_msg INTEGER, cpa INTEGER, benchmark INTEGER, roas REAL,
            decision TEXT, reason TEXT,
            eval_window TEXT DEFAULT '', win_spend REAL DEFAULT 0,
            win_purchases INTEGER DEFAULT 0, win_cpa INTEGER DEFAULT 0, win_roas REAL DEFAULT 0,
            phone_pct REAL DEFAULT 0, ghost_pct REAL DEFAULT 0,
            PRIMARY KEY (snap_date, campaign_id)
        );
        CREATE TABLE IF NOT EXISTS tt_team_actions (
            action_date TEXT, campaign_id TEXT, campaign_name TEXT,
            action TEXT, detail TEXT,
            PRIMARY KEY (action_date, campaign_id, action)
        );
        CREATE TABLE IF NOT EXISTS tt_scan_log (
            ts TEXT PRIMARY KEY, n_camps INTEGER, n_decisions INTEGER,
            n_team_actions INTEGER, duration_ms INTEGER, note TEXT
        );
        """)
        # Migrate cột thêm sau (SQLite không có ADD COLUMN IF NOT EXISTS)
        for tbl, col, ddl in (
            ("tt_snapshots", "op_status", "TEXT DEFAULT ''"),
            ("tt_decisions", "phone_pct", "REAL DEFAULT 0"),
            ("tt_decisions", "ghost_pct", "REAL DEFAULT 0"),
            ("tt_decisions", "budget", "REAL DEFAULT 0"),
            ("tt_decisions", "budget_mode", "TEXT DEFAULT ''"),
        ):
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({tbl})")}
            if col not in have:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")


# ─── Gom dữ liệu ──────────────────────────────────────────────────────────────

def _agg_days(day_rows: list) -> dict:
    """Gộp các dòng-ngày (tiktok_daily_campaign) → {campaign_id: số cộng dồn}."""
    out: dict = {}
    for r in day_rows:
        cid = str(r["campaign_id"])
        a = out.setdefault(cid, {
            "campaign_id": cid, "campaign_name": r["campaign_name"] or "",
            "advertiser_id": str(r["advertiser_id"] or ""),
            "spend": 0.0, "purchases": 0, "purchase_value": 0.0,
        })
        if r["campaign_name"]:
            a["campaign_name"] = r["campaign_name"]
        a["spend"] += float(r["spend"] or 0)
        # "Đơn" của TikTok = mua tại cửa hàng (offline) + mua web, khớp cột Đơn trang TikTok
        a["purchases"] += int(r["purchases"] or 0)
        a["purchase_value"] += float(r["purchase_value"] or 0)
    for a in out.values():
        a["roas"] = round(a["purchase_value"] / a["spend"], 2) if a["spend"] > 0 else 0.0
    return out


def fetch_campaign_ages(today: date) -> dict:
    """{campaign_id: số NGÀY ĐÃ CHI} tính tới hôm qua — cho bậc thang TẮT."""
    import tiktok_db
    yest = today - timedelta(days=1)
    frm = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    ages: dict = {}
    try:
        rows = tiktok_db.get_campaign_days(frm, yest.isoformat())
    except Exception as e:
        print(f"[tt-shadow] ⚠️ fetch_campaign_ages: {e}")
        return ages
    first: dict = {}
    for r in rows:
        if float(r["spend"] or 0) <= 0:
            continue
        cid = str(r["campaign_id"])
        d = r["date"]
        if cid not in first or d < first[cid]:
            first[cid] = d
    for cid, d0 in first.items():
        ages[cid] = (yest - date.fromisoformat(d0)).days + 1
    return ages


def fetch_operation_status() -> dict:
    """{campaign_id: {'op': 'ENABLE'|'DISABLE', 'sec': <secondary_status>}}.

    KHÔNG dùng tiktok_fetcher.fetch_campaign_statuses() (gom 3 nhóm on/off/paused)
    vì ở đây cần phân biệt đúng 1 thứ: ĐỘI NGŨ CÓ BẬT camp này không (operation
    ENABLE) — tách khỏi trạng thái phân phối tức thời. Camp ENABLE mà
    BUDGET_EXCEED (đã tiêu hết ngân sách ngày) VẪN là camp đang đốt tiền, phải
    đưa vào chấm; nhóm cũ xếp nó vào 'paused' rồi bị bỏ qua (đo 19/09/2026:
    5/102 camp rơi đúng vào ô này).
    """
    import json
    import tiktok_fetcher
    out: dict = {}
    try:
        for adv in tiktok_fetcher._advertiser_ids():
            page = 1
            while True:
                d = tiktok_fetcher._get("/campaign/get/", {
                    "advertiser_id": adv, "page": page, "page_size": 100,
                    "fields": json.dumps(["campaign_id", "operation_status", "secondary_status"]),
                })
                if d.get("code") != 0:
                    break
                data = d.get("data") or {}
                for c in data.get("list") or []:
                    out[str(c.get("campaign_id", ""))] = {
                        "op": str(c.get("operation_status") or ""),
                        "sec": str(c.get("secondary_status") or ""),
                    }
                if page >= int((data.get("page_info") or {}).get("total_page") or 1):
                    break
                page += 1
    except Exception as e:
        print(f"[tt-shadow] ⚠️ fetch_operation_status: {e}")
    return out


# Nhãn tiếng Việt cho trạng thái phụ hay gặp — hiện trên trang cho dễ hiểu
SEC_VI = {
    "CAMPAIGN_STATUS_DISABLE": "Đã tắt",
    "CAMPAIGN_STATUS_BUDGET_EXCEED": "Hết ngân sách ngày",
    "CAMPAIGN_STATUS_DELIVERY_OK": "Đang phân phối",
    "CAMPAIGN_STATUS_ENABLE": "Đang bật",
    "CAMPAIGN_STATUS_TIME_DONE": "Hết lịch chạy",
    "CAMPAIGN_STATUS_NOT_START": "Chưa tới lịch",
}


def collect_campaigns(today: date) -> list:
    """Gom số liệu mọi campaign TikTok trong cửa sổ lookback + cửa sổ trượt +
    chất lượng inbox, trả list dict sẵn sàng đưa vào bộ quy tắc."""
    import tiktok_db
    import inbox_db

    frm = (today - timedelta(days=LOOKBACK_DAYS - 1)).isoformat()
    to = today.isoformat()
    cum = _agg_days(tiktok_db.get_campaign_days(frm, to))

    # Cửa sổ trượt: chỉ ngày ĐÃ hoàn chỉnh (không tính hôm nay)
    yest = (today - timedelta(days=1)).isoformat()
    win_frm = (today - timedelta(days=RECENT_WINDOW_DAYS)).isoformat()
    win = _agg_days(tiktok_db.get_campaign_days(win_frm, yest))

    # Tin nhắn + chất lượng inbox (SĐT / mất tích) theo campaign
    try:
        qual = inbox_db.campaign_quality(frm, to, source="tiktok") or {}
        if "__error__" in qual:
            print(f"[tt-shadow] ⚠️ inbox tiktok: {qual['__error__']}")
            qual = {}
    except Exception as e:
        print(f"[tt-shadow] ⚠️ inbox tiktok: {e}")
        qual = {}
    try:
        qual_win = inbox_db.campaign_quality(win_frm, yest, source="tiktok") or {}
        if "__error__" in qual_win:
            qual_win = {}
    except Exception:
        qual_win = {}

    # Trạng thái bật/tắt + tên tài khoản (TikTok API, không nằm trong cache ngày)
    statuses = fetch_operation_status()
    try:
        import tiktok_fetcher
        adv_names = tiktok_fetcher.fetch_advertiser_names() or {}
        budgets = tiktok_fetcher.fetch_campaign_budgets() or {}
    except Exception as e:
        print(f"[tt-shadow] ⚠️ tên tài khoản / ngân sách: {e}")
        adv_names, budgets = {}, {}

    ages = fetch_campaign_ages(today)

    out = []
    for cid, a in cum.items():
        q = qual.get(cid) or {}
        qw = qual_win.get(cid) or {}
        conv = int(q.get("conv") or 0)
        w = win.get(cid) or {}
        st = statuses.get(cid) or {}
        out.append({
            "campaign_id": cid,
            "campaign_name": a["campaign_name"],
            "advertiser_id": a["advertiser_id"],
            "advertiser_name": adv_names.get(a["advertiser_id"], ""),
            "op_status": st.get("op", ""),
            "status": SEC_VI.get(st.get("sec", ""), st.get("sec", "")),
            # Ngân sách cấp chiến dịch; camp ABO để INFINITE → 0, trang tự hỏi
            # cấp nhóm QC qua /tiktok/campaign/<id>/budget khi cần.
            "budget": float((budgets.get(cid) or {}).get("budget") or 0),
            "budget_mode": (budgets.get(cid) or {}).get("budget_mode", ""),
            "spend": a["spend"],
            "messages": conv,
            "purchases": a["purchases"],
            "roas": a["roas"],
            "phone_pct": round((q.get("phone") or 0) / conv * 100, 1) if conv else 0.0,
            "ghost_pct": round((q.get("ghost") or 0) / conv * 100, 1) if conv else 0.0,
            "win_spend": float(w.get("spend") or 0),
            "win_messages": int((qw.get("conv") or 0)),
            "win_purchases": int(w.get("purchases") or 0),
            "win_roas": float(w.get("roas") or 0),
            "age_days": ages.get(cid, 0),
        })
    return out


# ─── Quét + chấm ──────────────────────────────────────────────────────────────

def run_scan(campaigns: list) -> dict:
    """Ghi snapshot + bắt hành động đội ngũ + chấm quyết định (quy tắc FB).
    KHÔNG gọi TikTok API để tắt/đổi ngân sách — chỉ ghi nhận."""
    started = datetime.now()
    init_db()
    today = date.today().isoformat()

    with _conn() as c:
        prev_date = c.execute(
            "SELECT MAX(snap_date) m FROM tt_snapshots WHERE snap_date < ?", (today,)).fetchone()["m"]
        prev_snap = {}
        if prev_date:
            for r in c.execute("SELECT * FROM tt_snapshots WHERE snap_date = ?", (prev_date,)):
                prev_snap[r["campaign_id"]] = dict(r)

        prev_dec = {}
        for r in c.execute(
                "SELECT campaign_id, decision FROM tt_decisions WHERE snap_date = "
                "(SELECT MAX(snap_date) FROM tt_decisions WHERE snap_date < ?)", (today,)):
            prev_dec[r["campaign_id"]] = r["decision"]

        n_dec = n_act = 0
        for a in campaigns:
            cid = a["campaign_id"]
            region = shadow.classify_region(a["campaign_name"])
            status = a.get("status") or ""        # nhãn tiếng Việt (Hết ngân sách ngày...)
            op = a.get("op_status") or ""         # ENABLE / DISABLE — đội ngũ bật hay tắt

            c.execute(
                "INSERT OR REPLACE INTO tt_snapshots "
                "(snap_date, campaign_id, campaign_name, advertiser_id, advertiser_name, "
                " region, status, op_status, spend_cum, messages, purchases, roas) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (today, cid, a["campaign_name"], a["advertiser_id"], a["advertiser_name"],
                 region, status, op, a["spend"], a["messages"], a["purchases"], a["roas"]))

            # Hành động đội ngũ: so ĐÃ BẬT/ĐÃ TẮT với lần quét trước
            p = prev_snap.get(cid)
            prev_op = (p or {}).get("op_status") or ""
            if p and prev_op and op:
                if prev_op == "ENABLE" and op != "ENABLE":
                    c.execute("INSERT OR REPLACE INTO tt_team_actions VALUES (?,?,?,?,?)",
                              (today, cid, a["campaign_name"], "TẠM DỪNG",
                               f"{prev_op} → {op} ({status})"))
                    n_act += 1
                elif prev_op != "ENABLE" and op == "ENABLE":
                    c.execute("INSERT OR REPLACE INTO tt_team_actions VALUES (?,?,?,?,?)",
                              (today, cid, a["campaign_name"], "BẬT LẠI",
                               f"{prev_op} → {op} ({status})"))
                    n_act += 1

            # Chỉ khuyến nghị cho campaign ĐỘI NGŨ ĐANG BẬT (ENABLE) — camp bật mà
            # hết ngân sách ngày vẫn tính, vì nó vẫn đốt tiền mỗi ngày.
            if op != "ENABLE":
                continue

            win_in = {"spend": a["win_spend"], "messages": a["win_messages"],
                      "purchases": a["win_purchases"], "roas": a["win_roas"]}
            gate, decision, reason, win = shadow.evaluate_v3(
                a["spend"], a["messages"], a["purchases"], region, "?",
                prev_dec.get(cid), a["roas"], win_in, age_days=a["age_days"])
            if decision == "CHƯA XÉT":
                continue
            # Quy tắc dùng chung với Facebook nên câu lý do ghi "ROAS FB" —
            # đổi chữ cho đúng kênh, KHÔNG đụng vào logic chấm.
            reason = reason.replace("ROAS FB", "ROAS TikTok")

            cpm = int(a["spend"] / a["messages"]) if a["messages"] else 0
            cpa = int(a["spend"] / a["purchases"]) if a["purchases"] else 0
            bench = shadow.REGION_CPA_BENCHMARK.get(region, shadow.REGION_CPA_BENCHMARK["?"])
            c.execute(
                "INSERT OR REPLACE INTO tt_decisions "
                "(snap_date, campaign_id, campaign_name, advertiser_id, advertiser_name, "
                " region, gate, spend_cum, messages, purchases, cost_per_msg, cpa, benchmark, "
                " roas, decision, reason, eval_window, win_spend, win_purchases, win_cpa, "
                " win_roas, phone_pct, ghost_pct, budget, budget_mode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (today, cid, a["campaign_name"], a["advertiser_id"], a["advertiser_name"],
                 region, gate, a["spend"], a["messages"], a["purchases"], cpm, cpa, bench,
                 a["roas"], decision, reason, win.get("window", ""), win.get("spend", 0),
                 win.get("purchases", 0), win.get("cpa", 0), win.get("roas", 0),
                 a["phone_pct"], a["ghost_pct"], a.get("budget", 0), a.get("budget_mode", "")))
            n_dec += 1

        dur = int((datetime.now() - started).total_seconds() * 1000)
        c.execute("INSERT OR REPLACE INTO tt_scan_log VALUES (?,?,?,?,?,?)",
                  (started.isoformat(timespec="seconds"), len(campaigns), n_dec, n_act, dur, ""))

    print(f"[tt-shadow] ✅ {len(campaigns)} camp → {n_dec} quyết định, {n_act} hành động ({dur}ms)")
    return {"campaigns": len(campaigns), "decisions": n_dec, "team_actions": n_act, "duration_ms": dur}


def scan_now() -> dict:
    """Gom dữ liệu rồi quét — dùng cho scheduler và nút 'Quét lại' trên trang."""
    return run_scan(collect_campaigns(date.today()))


# ─── Dữ liệu cho trang ────────────────────────────────────────────────────────

def get_dashboard_data() -> dict:
    init_db()
    today = date.today().isoformat()
    with _conn() as c:
        latest = c.execute("SELECT MAX(snap_date) m FROM tt_decisions").fetchone()["m"] or today

        decisions = [dict(r) for r in c.execute(
            "SELECT * FROM tt_decisions WHERE snap_date = ? "
            "ORDER BY CASE decision WHEN 'TẠM DỪNG' THEN 0 WHEN 'GIẢM 50%' THEN 1 "
            "WHEN 'ĐÁNH DẤU' THEN 2 WHEN 'TĂNG NS' THEN 3 WHEN 'THEO DÕI' THEN 4 ELSE 5 END, "
            "spend_cum DESC", (latest,))]

        # Chuỗi khuyến nghị xấu liên tiếp + tiền chi thêm kể từ khuyến nghị đầu
        BAD = ("TẠM DỪNG", "GIẢM 50%")
        hist = defaultdict(dict)
        for r in c.execute("SELECT campaign_id, snap_date, decision, spend_cum FROM tt_decisions"):
            hist[r["campaign_id"]][r["snap_date"]] = (r["decision"], r["spend_cum"])
        scan_dates = sorted({d for camp in hist.values() for d in camp}, reverse=True)
        for d_row in decisions:
            streak, first_spend = 0, None
            for dt in scan_dates:
                if dt > latest:
                    continue
                rec = hist.get(d_row["campaign_id"], {}).get(dt)
                if rec and rec[0] in BAD:
                    streak += 1
                    first_spend = rec[1]
                else:
                    break
            d_row["flag_streak"] = streak
            d_row["extra_spend"] = max(0, d_row["spend_cum"] - first_spend) \
                if (first_spend is not None and streak > 1) else 0

        counts: dict = {}
        for d in decisions:
            counts[d["decision"]] = counts.get(d["decision"], 0) + 1

        actions = [dict(r) for r in c.execute(
            "SELECT * FROM tt_team_actions ORDER BY action_date DESC LIMIT 200")]

        # Bất đồng: quy tắc bảo DỪNG/GIẢM nhưng camp vẫn đang bật
        disagreements = [dict(r) for r in c.execute("""
            SELECT d.*, s.status AS effective_status
            FROM tt_decisions d
            JOIN tt_snapshots s ON s.campaign_id = d.campaign_id AND s.snap_date = ?
            WHERE d.snap_date < ? AND d.decision IN ('TẠM DỪNG', 'GIẢM 50%')
              AND s.op_status = 'ENABLE'
            GROUP BY d.campaign_id HAVING d.snap_date = MAX(d.snap_date)
            ORDER BY d.spend_cum DESC LIMIT 100
        """, (latest, latest))]

        scans = [dict(r) for r in c.execute("SELECT * FROM tt_scan_log ORDER BY ts DESC LIMIT 30")]
        n_days = c.execute("SELECT COUNT(DISTINCT snap_date) n FROM tt_snapshots").fetchone()["n"]

    return {
        "latest_date": latest,
        "n_days_running": n_days,
        "counts": counts,
        "decisions": decisions,
        "team_actions": actions,
        "disagreements": disagreements,
        "scans": scans,
        "benchmarks": shadow.REGION_CPA_BENCHMARK,
        "config": {
            "gate1": shadow.GATE1_SPEND, "gate2": shadow.GATE2_SPEND,
            "flag_deadline": shadow.GATE1_FLAG_DEADLINE,
            "cpm_cheap": shadow.CPM_CHEAP, "cpm_expensive": shadow.CPM_EXPENSIVE,
            "lookback_days": LOOKBACK_DAYS,
        },
    }


def last_scan_ts() -> str | None:
    try:
        with _conn() as c:
            return c.execute("SELECT MAX(ts) m FROM tt_scan_log").fetchone()["m"]
    except Exception:
        return None
