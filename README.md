# fb_ad_local — Dashboard FB Ads phễu (chạy local trên máy Mac)

Web dashboard chạy hoàn toàn trên máy anh. Tự kéo FB Ads data mỗi 3h, phân loại 2 tầng phễu, đề xuất ad nên TẮT.

**Read-only** — anh tự vào FB Ads Manager pause thủ công.

---

## Rule chốt (cập nhật 2026-05-24)

| Tầng | Phân loại (tên có chứa) | Rule giữ ad |
|---|---|---|
| **ToFu** | `GC` hoặc `CT1` hoặc `CT2` | Mess ≤ **60.000đ** |
| **BoFu** | Phần còn lại (không có GC/CT1/CT2) | Mess ≤ 100.000đ **VÀ** ROAS ≥ **2.5** |

Vi phạm điều kiện → đề xuất **TẮT**. ToFu skip nếu chi < 20K; BoFu skip nếu chi < 50K.

**Đặc biệt** (champion): ToFu Mess ≤ 30K + ≥3 mess, hoặc BoFu ROAS ≥ 4.17 (≈ 1.67× ngưỡng).

---

# CÀI ĐẶT LẦN ĐẦU (làm 1 lần)

## Bước 1 — Mở Terminal trên Mac

`Cmd + Space` → gõ `Terminal` → Enter.

## Bước 2 — Vào folder app

Copy đoạn dưới, paste vào Terminal, Enter:

```bash
cd "/Users/hungnguyen/Công Việc/AI/fb_ad_local"
```

## Bước 3 — Cài Python deps

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Đợi ~30 giây.

## Bước 4 — Lấy FB token từ Railway

1. Mở https://railway.app/dashboard
2. Click vào project → service `fb_chatbot` → tab **`Variables`**
3. Tìm 3 biến: `FB_TOKEN_BM1`, `FB_TOKEN_BM2`, `FB_TOKEN_BM3` — copy giá trị từng cái

## Bước 5 — Tạo file `.env`

Trong Terminal (vẫn ở folder fb_ad_local):

```bash
cp .env.example .env
open -e .env
```

TextEdit mở file. Anh thay 3 dòng đầu bằng giá trị copy từ Railway:

```
FB_TOKEN_BM1=EAA...<paste giá trị thật>
FB_TOKEN_BM2=EAA...<paste giá trị thật>
FB_TOKEN_BM3=EAA...<paste giá trị thật>
```

Save (`Cmd+S`) → đóng TextEdit.

---

# CHẠY APP

## Mỗi lần dùng

```bash
cd "/Users/hungnguyen/Công Việc/AI/fb_ad_local"
source venv/bin/activate
python app.py
```

Terminal sẽ hiện:
```
📂 Loaded cache từ disk: ... ads
⏰ Scheduler đã start — refresh mỗi 3h
🚀 Dashboard: http://localhost:5050
```

**Mở browser** → vào http://localhost:5050

## Tắt app
Trong Terminal nhấn `Ctrl + C`.

## App đang chạy gì?
- Một Flask server tại `localhost:5050`
- Một scheduler nền tự gọi FB API mỗi 3 giờ
- Lưu data vào `cache.json` (anh đóng app, mở lại vẫn thấy data cũ)

---

# 4 DASHBOARD CÓ GÌ

App có **4 trang** liên kết qua sidebar bên trái:

## 🏠 `/` — Tổng quan
- Tiêu đề lớn hiển thị **tổng số tiền đang đốt/ngày** (VND lớn nổi bật)
- 3 KPI cards: GIỮ / TẮT / SKIP
- Cảnh báo vàng khi FB API có lỗi (token hết hạn, rate limit...)
- **Bảng đỏ** ad cần TẮT — mở sẵn, sort theo Chi giảm dần
  - Click vào row để **copy ad_id** vào clipboard (paste vào FB Ads Manager search)
- Bảng GIỮ + SKIP collapse, click mở khi cần

## 🏢 `/accounts` — So sánh tài khoản
- **3 cards BM** (Business Manager): BM1, BM2, BM3 — tổng chi + đốt + bar tỉ lệ giữ/tắt
- **Bảng xếp hạng 6 tài khoản:** chi, đốt, % vi phạm, ROAS avg, cost/mess avg
  - Tô đỏ khi % vi phạm > 60%
- **Stacked bar chart** chi tiêu / ngày từng TK — phần xanh = chi vào ad tốt, phần đỏ = đốt

## 📊 `/campaigns` — Rollup theo campaign
- 2 cards tổng: số campaigns, số campaign 100% bad
- **Filter bar**: tất cả / có ad cần tắt / toàn ad giữ
- **Sort options**: chi, đốt, % cần tắt, ROAS thấp → cao
- **Bảng campaigns** với column status pills (Giữ + Tắt + Skip)
  - Click vào row → **expand** xem chi tiết tất cả ads trong campaign đó

## 📈 `/insights` — Charts & phân tích
- **Donut chart** chi ToFu vs BoFu
- **Donut chart** chi vào ad GIỮ vs đang ĐỐT
- **Gauge sức khỏe** 0-100 (% chi đi vào ad tốt) — màu xanh/vàng/đỏ
- **Histogram ROAS** (BoFu only) — buckets 0 / 0-1 / 1-2 / 2-3 / 3-5 / 5-10 / 10+
- **Histogram Cost/Mess** — 6 buckets ≤25K → >150K
- **Top 10 ads đốt tiền** với bar chart % tổng chi

## Refresh tự động
UI tự gọi `/api/data` mỗi 30 giây. Scheduler nền fetch FB API mỗi 3h.
Anh muốn force refresh → click nút **"Refresh"** ở header (~30 giây).

---

# TINH CHỈNH NGƯỠNG

Mở `.env`, sửa các biến này, lưu lại:

```env
TOFU_MESS_MAX=50000           # ToFu: giá Mess tối đa
TOFU_MIN_SPEND=20000          # ToFu: chi tối thiểu để đánh giá

BOFU_MESS_MAX=100000          # BoFu: giá Mess tối đa
BOFU_ROAS_MIN=3.0             # BoFu: ROAS tối thiểu
BOFU_MIN_SPEND=50000          # BoFu: chi tối thiểu để đánh giá

REFRESH_INTERVAL_HOURS=3      # Tần suất auto refresh (tiếng)
```

Restart app: Ctrl+C → `python app.py` lại.

---

# TROUBLESHOOT

| Triệu chứng | Nguyên nhân & cách xử lý |
|---|---|
| App in lỗi `Thiếu env FB_TOKEN_BM2` | Chưa điền token vào `.env`, làm lại Bước 4 + 5 |
| Dashboard mở nhưng "Chưa có data" | Click "Refresh ngay" → đợi 30-60 giây |
| Card cảnh báo vàng "token hết hạn" | FB token Railway hết hạn → vào Railway gia hạn → copy lại vào `.env` local |
| Browser báo "không kết nối được localhost:5050" | App chưa chạy, hoặc Terminal đã Ctrl+C → chạy lại `python app.py` |
| Port 5050 đã có app khác chiếm | Mở `.env` thêm dòng `PORT=8080` (hoặc số khác) |

---

# CẤU TRÚC

```
fb_ad_local/
├── app.py                    # Flask server + scheduler
├── fetcher.py                # Gọi FB API 6 tài khoản
├── rules.py                  # Phân loại 2 tầng + đánh giá rule
├── templates/dashboard.html  # Giao diện web
├── requirements.txt          # 4 thư viện Python
├── .env.example              # Template config (commit)
├── .env                      # Config thực (KHÔNG commit, chứa token)
├── cache.json                # Data cache (auto tạo)
├── .gitignore
└── README.md                 # File này
```

---

# LIÊN QUAN
- Dashboard chính (rule kill phức tạp): `https://<fb_chatbot>.up.railway.app/app/measurement`
- App này CHẠY SONG SONG, không thay thế dashboard chính.
