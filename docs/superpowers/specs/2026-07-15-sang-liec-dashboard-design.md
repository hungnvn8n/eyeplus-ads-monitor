# Bảng "Sáng Liếc" — Thiết kế

**Ngày:** 2026-07-15
**Dự án:** fb_ad_local (dashboard Railway `eyeplus-ads-monitor`)
**Trạng thái:** Đã duyệt thiết kế — chờ viết kế hoạch triển khai

## Mục tiêu

Một màn hình duy nhất để CEO "sáng mở ra liếc 1 phát thấy hết": dàn chỉ số vận hành
(SĐT, chuyển đổi, mess, giá mess, DT/đơn, rủi ro, chi phí) + 4 thẻ "nét tích cực"
tự động (Ads / Content / Hàng hóa / Khách hàng). Kèm ảnh card gửi Lark mỗi sáng để
liếc trên điện thoại, không cần mở web.

**Nguyên tắc:** Không dựng app mới. Không đụng pipeline/backfill. Chỉ TIÊU THỤ dữ liệu
đã có trong kho Postgres. Thêm 1 tab vào dashboard đang chạy + 1 ảnh nối vào Bot Ads sáng.

## Phạm vi (v1)

Trong phạm vi:
- Tab web `/sang-liec` 2 tầng (chỉ số + nét tích cực).
- Nút "ghim/đổi" cho 4 thẻ nét tích cực, lưu vào bảng `sang_liec_pins`.
- Ảnh card Lark sáng, render từ tab này, gửi `MKT_RECEIVER_OPEN_ID`.

Ngoài phạm vi (làm sau nếu cần):
- Chỉnh sửa ngưỡng qua UI (ngưỡng để trong code như hiện tại).
- Lịch sử/biểu đồ xu hướng dài hạn (chỉ so hôm nay vs hôm trước).
- Bất kỳ thay đổi nào tới sync/backfill dữ liệu nguồn.

## Kiến trúc

Thêm vào Flask app fb_ad_local:

1. **Route `/sang-liec`** — render template `sang_liec.html`.
2. **Module tính toán** (vd `sang_liec.py`) — mỗi chỉ số + mỗi nét tích cực là 1 hàm
   thuần, nhận `conn` (Postgres) + ngày, trả về dict `{value, prev, status, arrow}` hoặc
   list top-3. Tách hàm để test độc lập từng chỉ số.
3. **Bảng mới `sang_liec_pins`** — lưu lựa chọn ghim của CEO.
4. **Endpoint ghim** `POST /sang-liec/pin` — ghi `(ngày, mảng, entity_id)`.
5. **Job ảnh sáng** — nối vào bot sáng đang có: render `/sang-liec` (hoặc template card
   riêng) ra ảnh bằng Chrome headless (giống `baocao_card.html`), gửi Lark.

Dữ liệu chảy 1 chiều: Postgres → hàm tính → template/JSON → (a) trình duyệt, (b) ảnh Lark.

## Tầng 1 — Dàn chỉ số

Mỗi ô: `{giá trị, so hôm trước (▲▼), trạng thái 🟢🟡🔴}`.

| Ô | Nguồn | Cách tính | Ngưỡng |
|---|---|---|---|
| Tỉ lệ SĐT xin được | `pancake_inbox_intents` | hội thoại có SĐT ÷ tổng hội thoại | mốc gần nhất ~2,9% (HCM); tinh chỉnh theo vùng |
| Tỉ lệ chuyển đổi | `pancake_inbox_intents` × `nhanh_bills` | đơn ÷ mess | theo vùng |
| Số mess (QC + tự nhiên) | logic `mkt_daily_briefing` | tách QC / tự nhiên khi có số | so target ngày |
| Giá mess | chi FB (ads) ÷ số mess | theo vùng | HN/HCM/BN/HP khác nhau |
| DT trung bình/đơn | `nhanh_bills` (bán lẻ) | Σ tiền ÷ số đơn | so trung bình tuần |
| Rủi ro | rule v3.2 (`shadow`/`rules`) | đếm cam cần Tăng/Giảm/Tạm dừng | có cam Tạm dừng = 🔴 |
| Chi phí | %ads ÷ DT | chi ads ÷ DT bán lẻ | ads ≤13,5% · digital ≤14,5% |

Ghi chú dữ liệu:
- KPI bán lẻ lấy từ `daily_rollup` — KHÔNG tự SUM `nhanh_bills` cho tổng doanh thu; chỉ
  dùng `nhanh_bills.products` cho phần top mã hàng hóa.
- Chi/tin & %ads là FB-only.
- Mọi chi phí tính theo % doanh thu, không dùng số tuyệt đối.
- Số "hôm qua" theo `daily_rollup`; độ trễ Nhanh 1-2 ngày là chấp nhận được cho card sáng.

## Tầng 2 — 4 thẻ Nét tích cực (lai: máy gợi top 3, CEO ghim/đổi)

Mỗi thẻ hiện top 3 gợi ý; CEO bấm ghim 1 cái → ảnh Lark lấy đúng cái đã ghim. Nếu chưa
ghim, mặc định lấy #1.

- **Ads**: top cam theo ROAS thực (hệ số 0,51 canonical), loại cam mới/ngân sách quá nhỏ.
- **Content**: top video TikTok theo ER (bảng content Tầng 1 đang có).
- **Hàng hóa**: top mã bán chạy hôm qua từ `nhanh_bills.products` (JSONB), LỌC bỏ dòng
  giá = 0 (phụ kiện tặng: nước lau, khăn lau, hộp kính). Ghép `nhanh_products` lấy
  danh mục/thương hiệu. Kèm bộ sưu tập dẫn đầu (Multi Look / Summer Boom ở Lark) nếu rẻ
  để tính; nếu không, chỉ top mã.
- **Khách hàng**: 1 điểm sáng chuyển đổi — chọn trong: % khách mua ≤7 ngày sau nhắn tin
  (~87% SĐT / 98% FB đa cửa sổ), số khách mới, hoặc cơ sở tăng tốt nhất.

Ngôn từ thẻ: dùng "công thức"/"content hiệu quả nhất", KHÔNG dùng "thắng"/"win". Nhãn dùng
"Mess" không dùng "lead". Từ vận hành lịch sự (Tăng/Giảm/Giữ/Tạm dừng).

## Bảng `sang_liec_pins`

```
sang_liec_pins(
  pin_date   date,
  area       text,        -- 'ads' | 'content' | 'hang_hoa' | 'khach_hang'
  entity_id  text,        -- id cam / id video / mã SP / khóa điểm sáng KH
  label      text,        -- text hiển thị đã chốt (để ảnh Lark khỏi tính lại)
  pinned_at  timestamptz,
  PRIMARY KEY (pin_date, area)
)
```

Ghim đè theo `(ngày, mảng)`. Mỗi ngày mỗi mảng 1 ghim.

## Ảnh Lark sáng

Nối vào bot sáng đang chạy (`mkt_daily_briefing`), KHÔNG tạo scheduler Railway riêng.
Render `/sang-liec` hoặc template card gọn ra ảnh (Chrome headless, cùng cơ chế
`baocao_card.html`), gửi `MKT_RECEIVER_OPEN_ID`. Nội dung ảnh = Tầng 1 + 4 nét tích cực
đã ghim. Excel không cần; đây là ảnh card, không phải bảng.

## Xử lý lỗi & rìa

- Thiếu dữ liệu 1 chỉ số → ô hiện "—" + xám, không làm sập cả trang.
- `nhanh_bills.products` có bản ghi không phải mảng/object rỗng → bỏ qua an toàn.
- Chưa có ghim → dùng gợi ý #1; ảnh vẫn gửi được.
- Render ảnh lỗi → log + không chặn phần còn lại của bot sáng.

## Kiểm thử

- Mỗi hàm chỉ số + mỗi hàm nét tích cực: test đơn vị với dữ liệu mẫu (gồm ca rìa:
  bills.products rỗng, dòng giá 0, thiếu vùng).
- Endpoint ghim: test ghi/đè đúng khóa (ngày, mảng).
- Render trang: smoke test route trả 200 với DB thật (đọc-chỉ).

## Rủi ro đã biết

- Ngưỡng SĐT/chuyển đổi theo vùng chưa chốt số cứng — dùng mốc gần nhất, để lộ trong
  code cho dễ chỉnh.
- "Bộ sưu tập dẫn đầu" cần đọc Lark; nếu chậm/token hết hạn thì thẻ Hàng hóa lùi về chỉ
  top mã (không chặn v1).
