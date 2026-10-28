"""adjust_history — "Lịch sử điều chỉnh gần nhất" cho mỗi chiến dịch.

Gom các lần ĐIỀU CHỈNH THẬT (đổi ngân sách, tắt/bật) gần nhất của từng chiến
dịch để cột "Điều chỉnh gần nhất" ở bảng tab Inbox hiển thị: giờ-phút + làm gì.

Nguồn (chỉ lấy hành động ĐÃ LÀM, có thời điểm giờ-phút):
- budget_log.jsonl        — đổi ngân sách Facebook (cả nút người bấm lẫn bot rule)
- inbox_adjust_log.jsonl  — tắt chiến dịch bấm thẳng từ tab Inbox (file này sinh ra
                            ở đây; trước đó nút Tắt FB không ghi lại ở đâu cả)
- tiktok_log (shadow.db)  — thao tác TikTok (tắt/bật/đổi ngân sách)

CỐ Ý KHÔNG lấy auto_pause_log.jsonl: đó chỉ là danh sách chiến dịch ĐẠT NGƯỠNG
cảnh báo (app flag, KHÔNG tự tắt) — không phải điều chỉnh đã thực hiện.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("adjust_history")

ROOT = Path(__file__).resolve().parent
BUDGET_LOG = ROOT / "budget_log.jsonl"
INBOX_ADJUST_LOG = ROOT / "inbox_adjust_log.jsonl"


def log(campaign_id: str, campaign_name: str, text: str, platform: str = "fb") -> None:
    """Ghi 1 điều chỉnh làm từ tab Inbox vào inbox_adjust_log.jsonl."""
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "campaign_id": str(campaign_id or ""),
            "campaign_name": campaign_name or "",
            "text": text,
            "platform": platform,
        }
        with INBOX_ADJUST_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"ghi inbox_adjust_log lỗi: {e}")


def _budget_text(e: dict) -> str:
    """Diễn giải 1 dòng budget_log thành 'làm gì'. Bot (source != user_button)
    được gắn tiền tố 'Tự' để phân biệt với người bấm."""
    pct = e.get("pct")
    delta = e.get("delta")
    if pct is not None:
        verb = "Tăng" if pct >= 0 else "Giảm"
        body = f"{verb} NS {abs(round(pct))}%"
    elif isinstance(delta, (int, float)) and delta:
        body = ("Tăng" if delta > 0 else "Giảm") + " ngân sách"
    else:
        body = "Đổi ngân sách"
    if (e.get("source") or "") not in ("user_button", "user", ""):
        body = "Tự " + body[0].lower() + body[1:]
    return body


def _consider(bucket: dict, key: str, ts: str, text: str) -> None:
    """Giữ bản ghi có ts MỚI NHẤT cho mỗi key."""
    if not key or not ts:
        return
    cur = bucket.get(key)
    if cur is None or ts > cur["ts"]:
        bucket[key] = {"ts": ts, "text": text}


def _read_jsonl(path: Path, cutoff: str, by_id: dict,
                text_fn, id_key="campaign_id") -> None:
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("ts") or ""
            if ts < cutoff:
                continue
            cid = str(e.get(id_key) or "")
            if cid:
                _consider(by_id, cid, ts, text_fn(e))
    except Exception as ex:
        logger.warning(f"đọc {path.name} lỗi: {ex}")


def latest(days: int = 60) -> dict:
    """Trả {'by_id': {cid: {ts, text}}, 'by_name': {name: {ts, text}}}.
    Client tra by_id[campaign_id] trước, không có thì by_name[campaign_name]."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    by_id: dict = {}
    by_name: dict = {}

    # 1) Đổi ngân sách Facebook
    _read_jsonl(BUDGET_LOG, cutoff, by_id, _budget_text)
    # 2) Tắt chiến dịch bấm từ tab Inbox (FB + TikTok)
    _read_jsonl(INBOX_ADJUST_LOG, cutoff, by_id, lambda e: e.get("text") or "Điều chỉnh")

    # 3) Thao tác TikTok từ shadow.db (map theo tên vì tiktok_log lưu campaign_name)
    try:
        import shadow
        from_date = (datetime.now() - timedelta(days=days)).date().isoformat()
        with shadow._conn() as c:
            rows = c.execute(
                "SELECT created_at, campaign_name, action FROM tiktok_log "
                "WHERE log_date >= ? ORDER BY created_at",
                (from_date,),
            ).fetchall()
        for created_at, name, action in rows:
            if name and created_at:
                _consider(by_name, name, created_at, action or "Điều chỉnh")
    except Exception as ex:
        logger.warning(f"đọc tiktok_log lỗi: {ex}")

    return {"by_id": by_id, "by_name": by_name}
