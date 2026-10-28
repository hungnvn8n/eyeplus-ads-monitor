"""Read-only layer cho inbox intents — 2 nguồn, cùng schema, cùng cách chấm điểm.

- fb     : pancake_inbox_intents (fb_chatbot webhook ghi)
- tiktok : tiktok_inbox_intents  (tiktok_inbox_sync.py ghi)
"""
import os
import re
import time
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager

# Cho phép dấu cách/chấm/gạch ngang xen giữa các chữ số (khách hay gõ kiểu
# "0908.208.365" hoặc "0962 051 895") — verify trên 14 ngày thật: bắt thêm
# ~3,5% hội thoại có SĐT, không mất/không bắt nhầm số nào so với bản cũ.
_PHONE_RE = re.compile(r'0[35789][ .\-]?[0-9]([ .\-]?[0-9]){7}')
_PHONE_RE_SQL = r"0[35789][ .\-]?[0-9]([ .\-]?[0-9]){7}"

_stats_cache: dict = {}
_stats_cache_ts: dict = {}

# Hai kênh, hai bảng, CÙNG tên cột nên dùng chung mọi truy vấn bên dưới.
# tiktok_inbox_intents do tiktok_inbox_sync.py ghi (gắn campaign qua ad_clicks
# của Pancake, vì TikTok Ads API không trả kết quả tin nhắn cho camp MESSAGE_CLUE).
TABLES = {"fb": "pancake_inbox_intents", "tiktok": "tiktok_inbox_intents"}
CACHE_TTL = 60  # seconds

_pool = None  # psycopg2.pool.ThreadedConnectionPool


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    url = os.environ.get("ROLLUP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ROLLUP_DATABASE_URL chưa set")
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 3, url, connect_timeout=15)
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()  # reset state
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def _table(source: str) -> str:
    return TABLES.get(source, TABLES["fb"])


def table_exists(source: str = "fb") -> bool:
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = %s
                )
            """, (_table(source),))
            return cur.fetchone()[0]
    except Exception:
        return False


# ─── Câu bấm sẵn (quick reply) — KHÔNG tính là khách chủ động hỏi ─────────
# Facebook/Pancake gắn sẵn mấy nút dưới khung chat ("Xem cửa hàng gần nhất!",
# "Tôi có thể mua hàng không?"...). Khách bấm một phát là sinh tin nhắn y hệt
# nhau, nên chỉ số "hỏi địa chỉ" bị thổi phồng: đo 10/2026 thì 4.642 lượt
# "Xem cửa hàng gần nhất!" chiếm 14% số hội thoại được tính là hỏi địa chỉ,
# và khiến chỉ số này bám doanh thu ÂM (−0,15) thay vì dương.
#
# Nhận diện: câu lặp ≥50 lần VÀ ≥70% số lần là tin MỞ ĐẦU hội thoại — khách
# tự gõ "Hcm", "Tphcm" thì nằm giữa cuộc nói chuyện nên không bị loại nhầm.
# Danh sách nằm ở app_settings['inbox_nut_bam'], sửa được trong Cài đặt.
_NUT_BAM_CACHE: tuple = (0.0, [])
_NUT_BAM_TTL = 300


def nut_bam() -> list:
    """Danh sách câu bấm sẵn. Cache 5 phút. Lỗi thì trả [] (không lọc gì)."""
    import json as _json
    global _NUT_BAM_CACHE
    if time.time() - _NUT_BAM_CACHE[0] < _NUT_BAM_TTL:
        return _NUT_BAM_CACHE[1]
    ds = []
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = 'inbox_nut_bam'")
            row = cur.fetchone()
            if row and row[0]:
                ds = [str(x) for x in _json.loads(row[0])]
    except Exception as e:
        print(f"⚠️ nut_bam: {e}")
        return _NUT_BAM_CACHE[1]
    _NUT_BAM_CACHE = (time.time(), ds)
    return ds


# ─── Bảng quản lý câu bấm sẵn (hiện ở cuối trang /inbox) ──────────────────
# Trước đây danh sách chỉ nạp được bằng tay thẳng vào kho, không màn hình nào
# sửa được, nên quảng cáo đẻ ra câu mới là chỉ số địa chỉ méo lại mà không ai
# hay. Ba hàm dưới cho phép XEM danh sách + số liệu, MÁY TỰ ĐỀ XUẤT câu mới,
# và GHI lại thay đổi.
_BANG_CACHE: dict = {}
_BANG_TTL = 600

# Luật máy tự nhận câu do công cụ quảng cáo sinh ra. Căn cứ: người thật gõ thì
# luôn đẻ dị bản (hoa/thường, thêm "ạ", rơi dấu) — máy thì không lệch một chữ.
# Đo 28/10/2026 trên 90 ngày: luật này ra 53 câu, không bắt nhầm câu nào của
# khách. Dấu ? hoặc ! ở cuối là chốt chặn — bỏ nó ra thì lọt 34 câu khách gõ.
NB_TOI_THIEU_CHU = 5      # câu ngắn như "ok", "giá" phải để yên
NB_TOI_THIEU_HT = 5       # dưới 5 hội thoại thì chưa đủ để kết luận


def nut_bam_bo_qua() -> list:
    """Câu đã bị tắt — máy không gợi lại nữa."""
    import json as _json
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM app_settings WHERE key = 'inbox_nut_bam_bo_qua'")
            row = cur.fetchone()
            return [str(x) for x in _json.loads(row[0])] if row and row[0] else []
    except Exception as e:
        print(f"⚠️ nut_bam_bo_qua: {e}")
        return []


def _ghi_setting(cur, key: str, gia_tri: list, ai: str):
    """Ghi 1 khoá + lưu vết vào settings_log để còn lần ngược được."""
    import json as _json
    from datetime import datetime as _dt
    moi = _json.dumps(gia_tri, ensure_ascii=False)
    cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
    row = cur.fetchone()
    cu = row[0] if row else None
    if cu == moi:
        return
    luc = _dt.now().isoformat(timespec="seconds")
    cur.execute("""
        INSERT INTO app_settings (key, value, updated_by, updated_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE
          SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
              updated_at = EXCLUDED.updated_at
    """, (key, moi, ai, luc))
    # Nhật ký là để lần ngược, không được để nó chặn việc lưu. Bảng này do app
    # MKT tạo; nếu vì lý do gì chưa có thì bỏ qua chứ không làm hỏng cú ghi.
    try:
        cur.execute("""
            INSERT INTO settings_log (key, old_value, new_value, changed_by, changed_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (key, cu, moi, ai, luc))
    except Exception as e:
        print(f"⚠️ settings_log ({key}): {e}")


def luu_nut_bam(dang_loc: list, bo_qua: list, ai: str = "app"):
    """Ghi cả 2 danh sách trong một lần, rồi xoá nhớ tạm."""
    global _NUT_BAM_CACHE
    with _conn() as conn:
        cur = conn.cursor()
        _ghi_setting(cur, "inbox_nut_bam", dang_loc, ai)
        _ghi_setting(cur, "inbox_nut_bam_bo_qua", bo_qua, ai)
        conn.commit()
    _NUT_BAM_CACHE = (0.0, [])
    _BANG_CACHE.clear()


def _chuan(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def bang_nut_bam(source: str = "fb", days: int = 90) -> dict:
    """Số liệu cho bảng Câu bấm sẵn.

    Trả về: tong_ht, addr_tho (số hội thoại bị tính là hỏi địa chỉ khi KHÔNG
    lọc gì), và danh sách dòng {cau, ht, nhan, nham, bat, moi}.

    "nham" = số hội thoại mà câu này là nguồn DUY NHẤT tạo ra nhãn địa chỉ —
    tức gỡ câu này ra thì đúng bấy nhiêu hội thoại rời khỏi chỉ số địa chỉ.
    Đây là con số hiện trên bảng, dễ hiểu hơn "điểm phần trăm".
    """
    khoa = f"{source}:{days}"
    goi = _BANG_CACHE.get(khoa)
    if goi and time.time() - goi[0] < _BANG_TTL:
        return goi[1]

    TBL = _table(source)
    dang_loc = nut_bam()
    bo_qua = nut_bam_bo_qua()
    loc_set = {_chuan(x) for x in dang_loc}
    bo_set = {_chuan(x) for x in bo_qua}

    g = f"""
        SELECT lower(regexp_replace(trim(message),'[[:space:]]+',' ','g')) AS dc,
               message, conv_id, label
        FROM {TBL}
        WHERE msg_ts > NOW() - INTERVAL %s
          AND message IS NOT NULL AND length(trim(message)) > 0
    """
    khoang = f"{days} days"
    with _conn() as conn:
        cur = conn.cursor()

        # 1. Tổng hội thoại + số bị tính là hỏi địa chỉ khi chưa lọc gì
        cur.execute(f"""
            WITH g AS ({g})
            SELECT COUNT(DISTINCT conv_id),
                   COUNT(DISTINCT conv_id) FILTER (WHERE label = 'addr')
            FROM g
        """, (khoang,))
        tong_ht, addr_tho = cur.fetchone()

        # 2. Câu nào là nguồn DUY NHẤT tạo nhãn địa chỉ cho hội thoại
        cur.execute(f"""
            WITH g AS ({g}),
            a AS (
                SELECT conv_id, COUNT(DISTINCT dc) AS n, MIN(dc) AS dc1
                FROM g WHERE label = 'addr' GROUP BY conv_id
            )
            SELECT dc1, COUNT(*) FROM a WHERE n = 1 GROUP BY dc1
        """, (khoang,))
        nham_map = {r[0]: r[1] for r in cur.fetchall()}

        # 3. Câu máy sinh (theo luật) + câu đang lọc (kể cả đã hết đạt luật)
        cur.execute(f"""
            WITH g AS ({g}),
            t AS (
                SELECT dc, MIN(message) AS cau, COUNT(DISTINCT conv_id) AS ht,
                       COUNT(DISTINCT message) AS cv,
                       array_length(string_to_array(dc,' '),1) AS sc,
                       mode() WITHIN GROUP (ORDER BY label) AS nhan
                FROM g GROUP BY dc
            )
            SELECT dc, cau, ht, nhan FROM t
            WHERE (cv = 1 AND sc >= %s AND ht >= %s AND dc ~ '[?!]$')
               OR dc = ANY(%s)
            ORDER BY ht DESC
        """, (khoang, NB_TOI_THIEU_CHU, NB_TOI_THIEU_HT, list(loc_set)))
        rows = cur.fetchall()

    NHAN_VIET = {"addr": "📍 Địa chỉ", "buy": "🛒 Mua hàng", "price": "💰 Giá",
                 "consult": "💬 Tư vấn", "neg": "😕 Chê", "other": "· Khác"}
    dong = []
    da_co = set()
    for dc, cau, ht, nhan in rows:
        if dc in bo_set and dc not in loc_set:
            continue                      # đã tắt bằng tay → không gợi lại
        da_co.add(dc)
        dong.append({
            "cau": cau, "ht": int(ht or 0),
            "nhan": NHAN_VIET.get(nhan or "other", "· Khác"),
            "nham": int(nham_map.get(dc, 0)),
            "bat": dc in loc_set,
            "moi": dc not in loc_set,
        })

    # Câu đang lọc nhưng kỳ này không còn xuất hiện — vẫn phải hiện để tắt được
    for cau in dang_loc:
        if _chuan(cau) not in da_co:
            dong.append({"cau": cau, "ht": 0, "nhan": "· Khác", "nham": 0,
                         "bat": True, "moi": False})

    # Câu đang làm sai chỉ số địa chỉ lên đầu — đó là thứ cần nhìn trước.
    # Sau đó mới tới câu hay gặp. Bảng có thể dài 50+ dòng nên thứ tự quan trọng.
    dong.sort(key=lambda d: (-d["nham"], -d["ht"]))

    out = {"tong_ht": int(tong_ht or 0), "addr_tho": int(addr_tho or 0),
           "days": days, "dong": dong}
    _BANG_CACHE[khoa] = (time.time(), out)
    return out


def _cc(tbl: str) -> str:
    """CTE gắn chiến dịch THEO HỘI THOẠI — lấy quảng cáo của tin SỚM NHẤT có gắn.

    Vì sao cần (đo 20/09/2026): Pancake chỉ đính thông tin quảng cáo vào một
    vài tin trong hội thoại (thường là tin mở đầu), các tin sau để trống. Gom
    nhóm theo TỪNG TIN thì một hội thoại vừa được tính cho chiến dịch của nó,
    vừa bị đếm thêm vào ô "(chưa link)" — 797/3.106 hội thoại Facebook rơi
    đúng cảnh này, làm ô "chưa link" phồng lên và loãng điểm của chính chiến
    dịch đó.

    CTE quét TOÀN bảng (không giới hạn ngày) vì tin mở đầu mang quảng cáo có
    thể nằm ngoài khoảng đang xem — khách cũ nhắn lại vẫn phải về đúng chiến
    dịch đã kéo họ đến lần đầu.
    """
    return f"""cc AS (
        SELECT DISTINCT ON (conv_id) conv_id, campaign_id, campaign_name
        FROM {tbl} WHERE campaign_id IS NOT NULL
        ORDER BY conv_id, msg_ts
    )"""


def campaign_quality(date_from: str, date_to: str, source: str = "fb") -> dict:
    """{campaign_id: {conv, phone, addr, ghost}} trong khoảng ngày.

    Bản rút gọn của query_all — chỉ 3 chỉ số chất lượng theo chiến dịch, để
    trang Facebook/TikTok gắn thêm cột mà không phải tải cả đống tin nhắn.
    Đơn vị đếm là HỘI THOẠI, giống hệt tab Inbox, để 2 nơi không lệch số.
    """
    TBL = _table(source)
    out: dict = {}
    # date_to + 1 ngày vì msg_ts có giờ phút, so '<' cho gọn
    rng = (date_from, date_to)
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cc = _cc(TBL)
            cur.execute(f"""
                WITH {cc}
                SELECT cc.campaign_id, COUNT(DISTINCT t.conv_id)
                FROM {TBL} t JOIN cc ON cc.conv_id = t.conv_id
                WHERE t.msg_ts::date BETWEEN %s AND %s
                GROUP BY 1""", rng)
            for cid, n in cur.fetchall():
                out[str(cid)] = {"conv": n, "phone": 0, "addr": 0, "ghost": 0}

            cur.execute(f"""
                WITH {cc}
                SELECT cc.campaign_id, COUNT(DISTINCT t.conv_id)
                FROM {TBL} t JOIN cc ON cc.conv_id = t.conv_id
                WHERE t.msg_ts::date BETWEEN %s AND %s
                  AND t.message ~ '0[35789][ .\-]?[0-9]([ .\-]?[0-9]){{7}}'
                GROUP BY 1""", rng)
            for cid, n in cur.fetchall():
                if str(cid) in out:
                    out[str(cid)]["phone"] = n

            cur.execute(f"""
                WITH {cc}
                SELECT cc.campaign_id, COUNT(DISTINCT t.conv_id)
                FROM {TBL} t JOIN cc ON cc.conv_id = t.conv_id
                WHERE t.msg_ts::date BETWEEN %s AND %s AND t.label = 'addr'
                  AND TRIM(t.message) <> ALL(%s)
                GROUP BY 1""", rng + (nut_bam(),))
            for cid, n in cur.fetchall():
                if str(cid) in out:
                    out[str(cid)]["addr"] = n

            # Mất tích = hội thoại khách chỉ gửi ≤1 tin rồi thôi (đếm thẳng số
            # dòng, không dựa cột cust_msg_count vì webhook FB không ghi cột đó)
            cur.execute(f"""
                WITH {cc}
                SELECT cc.campaign_id, COUNT(*) FROM (
                    SELECT conv_id, COUNT(*) AS n FROM {TBL}
                    WHERE msg_ts::date BETWEEN %s AND %s GROUP BY conv_id
                ) t JOIN cc ON cc.conv_id = t.conv_id
                WHERE t.n <= 1 GROUP BY 1""", rng)
            for cid, n in cur.fetchall():
                if str(cid) in out:
                    out[str(cid)]["ghost"] = n
    except Exception as e:
        return {"__error__": str(e)}
    return out


def query_all(days: int = 7, campaign_id: str = None, no_campaign: bool = False,
              label: str = None, search: str = None, limit: int = 500,
              source: str = "fb") -> dict:
    """Single-connection query: stats + messages + sync_status.

    source="fb" → inbox Facebook (Pancake webhook); "tiktok" → inbox TikTok.
    """
    global _stats_cache, _stats_cache_ts

    TBL = _table(source)
    now = time.time()
    cached = _stats_cache.get(source) or {}
    use_cache = (now - _stats_cache_ts.get(source, 0) < CACHE_TTL
                 and cached.get("days") == days)

    try:
        with _conn() as conn:
            cur = conn.cursor()
            if True:
                # --- Stats (cached) ---
                # Đơn vị đếm = HỘI THOẠI (DISTINCT conv_id), không phải từng tin —
                # webhook lưu mọi tin của khách nên đếm tin sẽ phồng số + loãng %.
                if not use_cache:
                    cur.execute(f"""
                        SELECT label, COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                          AND TRIM(message) <> ALL(%s)
                        GROUP BY label
                    """, (f"{days} days", nut_bam()))
                    by_label = {r[0]: r[1] for r in cur.fetchall()}

                    # Gắn chiến dịch THEO HỘI THOẠI (xem _cc) — không theo từng tin,
                    # nếu không 1 hội thoại vừa vào chiến dịch vừa vào "(chưa link)".
                    cc = _cc(TBL)
                    cur.execute(f"""
                        WITH {cc}
                        SELECT cc.campaign_id, cc.campaign_name, t.label,
                               COUNT(DISTINCT t.conv_id) AS n
                        FROM {TBL} t LEFT JOIN cc ON cc.conv_id = t.conv_id
                        WHERE t.msg_ts > NOW() - INTERVAL %s
                          -- câu bấm sẵn không tính cho nhãn nào (xem nut_bam)
                          AND TRIM(t.message) <> ALL(%s)
                        GROUP BY 1, 2, 3
                        ORDER BY n DESC
                    """, (f"{days} days", nut_bam()))
                    camp_rows = cur.fetchall()

                    campaigns = {}
                    for cid, cname, lbl, n in camp_rows:
                        key = cid or "__no_ad__"
                        if key not in campaigns:
                            campaigns[key] = {
                                "campaign_id": cid,
                                "campaign_name": cname or "(chưa link)",
                                "total": 0, "by_label": {}
                            }
                        campaigns[key]["by_label"][lbl] = n

                    # tổng hội thoại per campaign (1 conv có nhiều label vẫn đếm 1)
                    cur.execute(f"""
                        WITH {cc}
                        SELECT cc.campaign_id, COUNT(DISTINCT t.conv_id)
                        FROM {TBL} t LEFT JOIN cc ON cc.conv_id = t.conv_id
                        WHERE t.msg_ts > NOW() - INTERVAL %s
                        GROUP BY 1
                    """, (f"{days} days",))
                    for cid, n in cur.fetchall():
                        key = cid or "__no_ad__"
                        if key in campaigns:
                            campaigns[key]["total"] = n

                    # hội thoại có SĐT — overall
                    cur.execute(f"""
                        SELECT COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                        AND message ~ '0[35789][ .\-]?[0-9]([ .\-]?[0-9]){{7}}'
                    """, (f"{days} days",))
                    phone_count = cur.fetchone()[0] or 0

                    # hội thoại có SĐT per campaign
                    cur.execute(f"""
                        WITH {cc}
                        SELECT cc.campaign_id, COUNT(DISTINCT t.conv_id)
                        FROM {TBL} t LEFT JOIN cc ON cc.conv_id = t.conv_id
                        WHERE t.msg_ts > NOW() - INTERVAL %s
                        AND t.message ~ '0[35789][ .\-]?[0-9]([ .\-]?[0-9]){{7}}'
                        GROUP BY 1
                    """, (f"{days} days",))
                    camp_phone = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}

                    # Khách mất tích = hội thoại khách chỉ gửi ≤1 tin rồi thôi.
                    #
                    # TRƯỚC ĐÂY lọc theo cột cust_msg_count. Cột đó chỉ được luồng
                    # đồng bộ TikTok ghi; luồng Facebook chuyển sang webhook ngày
                    # 25/06/2026 và webhook KHÔNG ghi cột này (webhook nhận từng tin
                    # một, không biết tổng số tin của hội thoại). Kết quả: tháng 6 còn
                    # 43,5% dòng có số, từ tháng 7 về 0% → chỉ số "mất tích" luôn = 0.
                    #
                    # Nay ĐẾM THẲNG số dòng theo conv_id trong chính bảng này — không
                    # phụ thuộc cột kia nữa, và áp dụng được cho cả dữ liệu cũ.
                    cur.execute(f"""
                        WITH {cc}
                        SELECT cc.campaign_id, COUNT(*) FROM (
                            SELECT conv_id, COUNT(*) AS n FROM {TBL}
                            WHERE msg_ts > NOW() - INTERVAL %s GROUP BY conv_id
                        ) t LEFT JOIN cc ON cc.conv_id = t.conv_id
                        WHERE t.n <= 1 GROUP BY 1
                    """, (f"{days} days",))
                    camp_ghost = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}
                    ghost_count = sum(camp_ghost.values())

                    # Mẫu số = TẤT CẢ hội thoại trong kỳ (trước đây chỉ đếm hội thoại
                    # có cust_msg_count nên mẫu số cũng teo dần về 0).
                    cur.execute(f"""
                        SELECT campaign_id, COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                        GROUP BY campaign_id
                    """, (f"{days} days",))
                    camp_scanned = {(r[0] or "__no_ad__"): r[1] for r in cur.fetchall()}
                    # Tổng KHÔNG được cộng dồn các nhóm: một hội thoại có thể dính
                    # nhiều campaign_id nên cộng lại sẽ đếm trùng (đo được 5.489 so
                    # với 4.533 hội thoại thật) → mẫu số phình, %mất tích bị hạ thấp.
                    cur.execute(f"""
                        SELECT COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                    """, (f"{days} days",))
                    scanned_count = cur.fetchone()[0] or 0

                    # merge per-campaign vào campaigns dict
                    for key in campaigns:
                        campaigns[key]["phone"]   = camp_phone.get(key, 0)
                        campaigns[key]["ghost"]   = camp_ghost.get(key, 0)
                        campaigns[key]["scanned"] = camp_scanned.get(key, 0)

                    cur.execute(f"""
                        SELECT COUNT(DISTINCT conv_id) FROM {TBL}
                        WHERE msg_ts > NOW() - INTERVAL %s
                    """, (f"{days} days",))
                    total_all = cur.fetchone()[0] or 0
                    _stats_cache[source] = {
                        "days": days,
                        "by_label": by_label,
                        "total": total_all,
                        "campaigns": campaigns,
                        "phone_count": phone_count,
                        "ghost_count": ghost_count,
                        "scanned_count": scanned_count,
                    }
                    _stats_cache_ts[source] = now

                stats = _stats_cache[source]

                # --- Sync status ---
                cur.execute(f"SELECT COUNT(*), MAX(synced_at) FROM {TBL}")
                total_db, last_sync = cur.fetchone()

                # --- Messages ---
                conds = ["msg_ts > NOW() - INTERVAL %s"]
                params: list = [f"{days} days"]

                # Lọc theo HỘI THOẠI, không theo từng tin: tin sau trong cùng hội
                # thoại thường trống campaign_id, lọc theo tin sẽ nuốt mất phần
                # lớn nội dung của chính chiến dịch đang xem.
                _cc_sub = (f"SELECT conv_id FROM {TBL} WHERE campaign_id IS NOT NULL")
                if no_campaign:
                    conds.append(f"conv_id NOT IN ({_cc_sub})")
                elif campaign_id:
                    conds.append(f"conv_id IN (SELECT conv_id FROM {TBL} "
                                 f"WHERE campaign_id = %s)")
                    params.append(campaign_id)
                if label:
                    conds.append("label = %s")
                    params.append(label)
                if search:
                    conds.append("message ILIKE %s")
                    params.append(f"%{search}%")

                where = " AND ".join(conds)
                # Gom theo HỘI THOẠI: 1 conv = 1 card (tin mới nhất), cờ SĐT/địa chỉ
                # xét trên toàn bộ tin trong conv chứ không riêng tin cuối.
                cur.execute(f"""
                    WITH filtered AS (
                        SELECT * FROM {TBL} WHERE {where}
                    ), agg AS (
                        SELECT conv_id,
                               COUNT(*)                                AS conv_msgs,
                               BOOL_OR(label = 'addr')                 AS has_addr,
                               BOOL_OR(message ~ '0[35789][ .\-]?[0-9]([ .\-]?[0-9]){{7}}') AS has_phone
                        FROM filtered GROUP BY conv_id
                    )
                    SELECT * FROM (
                        SELECT DISTINCT ON (f.conv_id)
                               f.msg_id, f.conv_id, f.page_id, f.ad_id,
                               f.campaign_id, f.campaign_name,
                               f.customer_name, f.message, f.cust_msg_count,
                               f.msg_ts AT TIME ZONE 'Asia/Ho_Chi_Minh' AS msg_ts,
                               f.label, a.conv_msgs, a.has_addr, a.has_phone
                        FROM filtered f JOIN agg a USING (conv_id)
                        ORDER BY f.conv_id, f.msg_ts DESC
                    ) t ORDER BY msg_ts DESC LIMIT %s
                """, params + [limit])

                cols = [d[0] for d in cur.description]
                msgs = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    if d.get("msg_ts"):
                        d["msg_ts"] = d["msg_ts"].isoformat()
                    cc = d.get("cust_msg_count")
                    d["is_ghost"] = (cc is not None and cc <= 1)
                    d["is_phone"] = bool(d.pop("has_phone", False))
                    # conv từng hỏi địa chỉ nhưng tin cuối là label khác → giữ badge địa chỉ
                    if d.pop("has_addr", False) and d.get("label") != "addr":
                        d["label"] = "addr"
                    msgs.append(d)

        out = dict(stats)
        out["messages"] = msgs
        out["total_in_db"] = total_db or 0
        out["last_synced_at"] = last_sync.isoformat() if last_sync else None
        return out

    except Exception as e:
        return {
            "by_label": {}, "total": 0, "campaigns": {},
            "messages": [], "total_in_db": 0, "last_synced_at": None,
            "error": str(e)
        }


def phone_daily(date_from: str, date_to: str, source: str = "fb") -> list[dict]:
    """[{date, phone, conv}] — số hội thoại ĐỂ LẠI SỐ ĐIỆN THOẠI theo từng ngày.

    Mỗi hội thoại tính vào NGÀY ĐẦU TIÊN nó để lại số (và ngày đầu tiên nó
    xuất hiện, với cột tổng hội thoại) — khách nhắn rải nhiều ngày vẫn chỉ
    đếm 1 lần, nên cộng cả kỳ đúng bằng con số ở cột 📞 SĐT trong bảng.
    """
    TBL = _table(source)
    rng = (date_from, date_to)
    ngay: dict = {}
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT d, COUNT(*) FROM (
                    SELECT conv_id, MIN(msg_ts::date) AS d FROM {TBL}
                    WHERE msg_ts::date BETWEEN %s AND %s
                    GROUP BY conv_id
                ) x GROUP BY d""", rng)
            for d, n in cur.fetchall():
                ngay[str(d)] = {"date": str(d), "conv": n, "phone": 0}

            cur.execute(f"""
                SELECT d, COUNT(*) FROM (
                    SELECT conv_id, MIN(msg_ts::date) AS d FROM {TBL}
                    WHERE msg_ts::date BETWEEN %s AND %s
                      AND message ~ '{_PHONE_RE_SQL}'
                    GROUP BY conv_id
                ) x GROUP BY d""", rng)
            for d, n in cur.fetchall():
                ngay.setdefault(str(d), {"date": str(d), "conv": 0, "phone": 0})
                ngay[str(d)]["phone"] = n
    except Exception as e:
        return [{"__error__": str(e)}]
    return [ngay[k] for k in sorted(ngay)]


def phone_daily_by_campaign(date_from: str, date_to: str,
                            source: str = "fb") -> list[dict]:
    """[{date, campaign_id, phone, conv}] — SĐT theo NGÀY và theo TỪNG chiến dịch.

    Dùng cho biểu đồ trong panel bung ra của mỗi chiến dịch. Gắn chiến dịch
    theo HỘI THOẠI (cùng CTE với campaign_quality) nên không rơi vào cảnh một
    hội thoại vừa tính cho chiến dịch vừa rơi vào ô "chưa link".
    """
    TBL = _table(source)
    rng = (date_from, date_to)
    out: dict = {}
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cc = _cc(TBL)
            cur.execute(f"""
                WITH {cc}, hoi_thoai AS (
                    SELECT t.conv_id, cc.campaign_id, MIN(t.msg_ts::date) AS d
                    FROM {TBL} t JOIN cc ON cc.conv_id = t.conv_id
                    WHERE t.msg_ts::date BETWEEN %s AND %s
                    GROUP BY t.conv_id, cc.campaign_id
                )
                SELECT d, campaign_id, COUNT(*) FROM hoi_thoai GROUP BY 1, 2""", rng)
            for d, cid, n in cur.fetchall():
                out[(str(d), str(cid))] = {"date": str(d), "campaign_id": str(cid),
                                           "conv": n, "phone": 0}

            cur.execute(f"""
                WITH {cc}, co_sdt AS (
                    SELECT t.conv_id, cc.campaign_id, MIN(t.msg_ts::date) AS d
                    FROM {TBL} t JOIN cc ON cc.conv_id = t.conv_id
                    WHERE t.msg_ts::date BETWEEN %s AND %s
                      AND t.message ~ '{_PHONE_RE_SQL}'
                    GROUP BY t.conv_id, cc.campaign_id
                )
                SELECT d, campaign_id, COUNT(*) FROM co_sdt GROUP BY 1, 2""", rng)
            for d, cid, n in cur.fetchall():
                k = (str(d), str(cid))
                out.setdefault(k, {"date": str(d), "campaign_id": str(cid),
                                   "conv": 0, "phone": 0})
                out[k]["phone"] = n
    except Exception as e:
        return [{"__error__": str(e)}]
    return [out[k] for k in sorted(out)]


def phone_by_ad(date_from: str, date_to: str, source: str = "fb") -> dict:
    """{ad_id: {conv, phone}} — SĐT theo TỪNG QUẢNG CÁO trong khoảng ngày.

    Pancake có ghi mã quảng cáo (ad_id) trên tin nhắn, nên đo được tới từng
    mẫu chứ không dừng ở chiến dịch. Vẫn đếm theo HỘI THOẠI và gắn quảng cáo
    của tin SỚM NHẤT (cùng lối với _cc) để một hội thoại không bị chia đôi
    cho hai mẫu khi khách nhắn lại qua mẫu khác.
    """
    TBL = _table(source)
    rng = (date_from, date_to)
    out: dict = {}
    ca = f"""ca AS (
        SELECT DISTINCT ON (conv_id) conv_id, ad_id
        FROM {TBL} WHERE ad_id IS NOT NULL AND ad_id <> ''
        ORDER BY conv_id, msg_ts
    )"""
    try:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                WITH {ca}
                SELECT ca.ad_id, COUNT(DISTINCT t.conv_id)
                FROM {TBL} t JOIN ca ON ca.conv_id = t.conv_id
                WHERE t.msg_ts::date BETWEEN %s AND %s
                GROUP BY 1""", rng)
            for aid, n in cur.fetchall():
                out[str(aid)] = {"conv": n, "phone": 0}

            cur.execute(f"""
                WITH {ca}
                SELECT ca.ad_id, COUNT(DISTINCT t.conv_id)
                FROM {TBL} t JOIN ca ON ca.conv_id = t.conv_id
                WHERE t.msg_ts::date BETWEEN %s AND %s
                  AND t.message ~ '{_PHONE_RE_SQL}'
                GROUP BY 1""", rng)
            for aid, n in cur.fetchall():
                out.setdefault(str(aid), {"conv": 0, "phone": 0})
                out[str(aid)]["phone"] = n
    except Exception as e:
        return {"__error__": str(e)}
    return out
