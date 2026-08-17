# BÀN GIAO — Phân tích & Tối ưu Quảng cáo FB Eye Plus

> Tài liệu chuyển giao cho nhân sự Digital tiếp tục phân tích. Đọc hết phần I-II trước
> khi đụng vào số. Mở file này trong VSCode + Claude để Claude hiểu ngữ cảnh khi bạn hỏi.
> Cập nhật lần cuối: 2026-06-15.

---

## PHẦN I — TƯ DUY (vì sao công cụ này tồn tại)

### 1. Vấn đề gốc
Phòng MKT có "rule phễu" (To/Mo/Bo) để bật/tắt ads, nhưng **nhân sự không tuân thủ**.
Sếp không quy lỗi cho người — mà nghi ngờ chính cái rule. Đó là điểm khởi đầu đúng:
**trước khi ép người, hãy kiểm chứng cái thước.**

### 2. Phát hiện cốt lõi sau khi audit 1 tháng (986 ads, 890tr)
Rule cũ **đo sai thước**. Cụ thể:
- "Phễu To/Mo/Bo" **không tồn tại** trong dữ liệu — 749/760 cam không có nhãn phễu.
  CT1–CT6 là **mã khuyến mãi**, không phải tầng phễu. App cũ gán "CT3-6 = chốt đơn" là gán bừa.
- Rule cũ tắt ads khi "giá tin > 60K" — nhưng dữ liệu chứng minh **tin càng rẻ khách càng kém**.
  Nhân sự thấy "ad tin đắt nhưng ra đơn" nên không nỡ tắt → **họ đúng, rule sai.**

### 3. Triết lý chốt
> **"Đo đúng thước thì tuân thủ là tự nhiên, không phải ép buộc."**
Khi đổi thước về "chi phí cho 1 khách mua THẬT theo từng vùng", điều nhân sự làm bằng
trực giác và điều rule yêu cầu sẽ trùng nhau.

### 4. Bốn nguyên lý phân tích (áp dụng cho MỌI phân tích sau này)
1. **Chỉ tin kết quả cuối cùng.** Xếp hạng dựa trên khách có HÓA ĐƠN THẬT (khớp Nhanh qua
   CAPI), không dựa lượt bấm / lượt nhắn (chỉ số trung gian, dễ ảo).
2. **Chỉ so sánh tương đối trong CÙNG vùng.** Sai lệch của phép khớp (khách quen vẫn được
   tính công cho ad) phủ đều mọi ad cùng vùng → khi so ad-với-ad nó tự triệt tiêu.
3. **Kiểm chứng tiến cứu.** Không chỉ đo tương quan trên dữ liệu đã qua — lấy tín hiệu
   3 ngày đầu để THỬ DỰ ĐOÁN kết quả về sau, đúng cách rule sẽ vận hành thật.
4. **Tự phản biện trước khi kết luận.** Mỗi phát hiện đều bị nghi ngờ ngược: kiểm hiệu ứng
   sống sót (dữ liệu bị tay người sàng làm méo), kiểm độ tin của phép khớp đơn.

### 5. Nguyên tắc ĐỐI CHỨNG (quan trọng nhất về mặt vận hành)
Rule mới **không tự tắt ads ngay**. Nó chạy ngầm 2 tuần ở "chế độ đối chứng" (shadow mode),
chỉ GHI NHẬN "rule sẽ quyết gì" rồi so 3 cột:
**quy tắc quyết gì — đội ngũ làm gì — kết quả thật ra sao.**
Bên nào thắng bằng số liệu thì theo bên đó. Hết tranh cãi "tuân thủ hay không".

---

## PHẦN II — KẾT LUẬN AUDIT (sự thật về chỉ số)

Xếp hạng 4 chỉ số — đâu mới là thứ nhận định khách hiệu quả:

| Chỉ số | Phán quyết | Vì sao |
|---|---|---|
| **Chi phí/1 khách (CPA) + ROAS** | ✅ THƯỚC CHÍNH | Duy nhất gắn khách thật, đúng cả 4 vùng |
| **Tỷ lệ tin → khách mua** | ✅ Tín hiệu sớm | Tương quan mạnh nhất với ROAS (0,59) |
| Giá mỗi tin nhắn | ⚠️ Gây hiểu lầm | Tin RẺ ≤30K chuyển đổi TỆ NHẤT (7%). Vùng ngọt là GIỮA (45–70K) |
| CTR (tỷ lệ bấm) | ⛔ BẪY | CTR cao nhất lại ROAS thấp nhất. Bỏ khỏi rule bật/tắt |
| Phễu To/Mo/Bo | ⛔ KHÔNG dùng | Không tồn tại. Thay bằng trục **Vùng × Chương trình** |

**Ngưỡng chi phí/1 khách theo VÙNG** (1 ngưỡng quốc gia là sai — HN ra khách rẻ gần gấp đôi HP):
`HN 200K · HCM 300K · HP 370K · BN 310K · TQ 200K`

**Video vs Ảnh:** ẢNH THẮNG VIDEO cả 3 bậc giá. Ảnh có giá trên hình → lọc khách trước khi
nhắn. Video cầm mẫu đẹp không neo giá → hút người tò mò → vỡ ở khâu báo giá.
→ **Video PHẢI neo giá; dịch ngân sách video→ảnh.**

**ADV vs Nhắm tay:** PHẢI lấy cờ targeting từ **FB API** (`targeting_automation.advantage_audience`
hoặc `targeting_optimization=expansion_all`), **KHÔNG suy từ tên cam**. ADV sinh tin rẻ hơn
nhưng kết cục ngang nhắm tay → ADV không phải thủ phạm. Thủ phạm tin-rẻ-rác = tầng content.

**Cách đo (nền tảng mọi con số):** chuỗi CAPI khép kín — khách thấy ad → nhắn Pancake →
CSKH chốt → đơn vào Nhanh → tự đẩy về FB mỗi 30' → FB khớp SĐT về đúng ad. ⚠️ **Đơn quy về
trong CỬA SỔ 7 NGÀY** → ROAS của ngày mới luôn THẤP GIẢ, phải đợi ~3 ngày mới tin được.

---

## PHẦN III — RULE v3.1 (quy tắc bật/tắt — chốt 2026-06-14)

Xét theo **tiền đã chi**, qua 2 "trạm kiểm tra":

**TRẠM 1 — khi đã tiêu 200K:**
- Có ≥1 khách mua → **GIỮ** (bất kể giá tin — đơn là bằng chứng mạnh nhất)
- 0 tin nhắn → **TẠM DỪNG**
- Có tin chưa có khách: tin đắt >60K → **GIẢM 50%** (chưa tắt vội, tin đắt = khách nghiêm túc)
  · tin rẻ ≤30K → **ĐÁNH DẤU** (tiêu tới 400K phải có khách, không thì video TẮT/ảnh GIẢM)
  · tin giữa 30–60K → **THEO DÕI**

**TRẠM 2 — khi đã tiêu 500K (ad lớn) — KEY FIX:**
Xét theo **CỬA SỔ TRƯỢT 3 NGÀY GẦN NHẤT** (chi 3 ngày ÷ khách 3 ngày), **KHÔNG dùng cộng dồn**
— vì cộng dồn để quá khứ ngon che hiện tại đang rò tiền. (Ad gần như dừng chi thì xét cộng dồn.)
- 3 ngày gần đây 0 khách → **TẠM DỪNG** (bất kể quá khứ đẹp cỡ nào)
- ROAS ≥ **3** hoặc CPA rẻ hơn chuẩn vùng 20% → **TĂNG NS**
- ROAS ≥ **2** hoặc CPA quanh chuẩn (đến gấp rưỡi) → **GIỮ**
- Tệ hơn → **GIẢM 50%**; lần sau vẫn vậy → **TẠM DỪNG**

Câu thần chú: **"Soi tin rẻ, nương tin đắt, tin vào đơn sớm."**

### Khung 4-góc (dùng khi phân bổ lại ngân sách)
Phân mọi cam theo 2 trục [ROAS ≥2,2?] × [giá hội thoại <55K?]:
- **A (rẻ + lãi):** vàng ròng — luôn ưu tiên tăng/nhân bản.
- **B (lãi, hội thoại đắt):** lãi cao nhưng tốn ~80K/hội thoại → tăng có giới hạn (đẩy giá mess lên).
- **C (rẻ nhưng lỗ):** giữ làm "mỏ neo" volume + giá mess, dù tự nó lỗ (cả rổ vẫn lãi).
- **D (đắt + lỗ):** tắt thẳng.

⚠️ **Mâu thuẫn cốt lõi cần nhớ:** hội thoại RẺ nằm ở nhóm A (tốt) và C (lỗ); ROAS CAO nằm ở
A (tốt) và B (đắt). Chỉ A thỏa cả hai nhưng A nhỏ. → "Nhiều hội thoại" và "ROAS cao" KÉO NGƯỢC
nhau. Mọi kế hoạch là bài toán cân bằng, không có lời giải hoàn hảo.

---

## PHẦN IV — CÔNG CỤ (cách dùng app)

**App `fb_ad_local`** — Flask, chạy `venv/bin/python app.py` → `http://localhost:5050`
(mật khẩu trong `.env: APP_PASSWORD`). Đọc ads từ 6 tài khoản FB qua API (token `FB_TOKEN_BM1/2/3`).

| Trang | Việc |
|---|---|
| `/campaigns` | Gộp ads theo cam, lọc theo vùng/giới/tuổi/CTKM, có cột Giá/Mess, Tần suất, CTR, ROAS |
| `/doichung` | **Trang chính cho phân tích** — hệ đối chứng v3.1 |

**Trang `/doichung`** (bật bằng `SHADOW_MODE=true` trong `.env`, dữ liệu lưu local `shadow.db`):
- **Review hằng ngày** (panel trên cùng) — tự chạy **1h30 sáng**: chấm ngày qua so 4 mục tiêu
  (chi <35tr · hội thoại >700 · giá mess <55K · ROAS >2,2) + xu hướng + hướng điều chỉnh.
- **Dải KPI** — tiền vùng đỏ, chi thêm sau khuyến nghị.
- **Hàng đợi xử lý** — nhóm gập/mở (TẮT/GIẢM/TĂNG…), tích chọn → bấm hàng loạt
  (Tắt/Bật/±% ngân sách/Nhân bản). Cột "Đã điều chỉnh" = log thật từng cam.
- **Cờ "3 ngày"** = ad đó đang xét theo cửa sổ trượt.

**Áp một phương án hàng loạt:** `apply_plan.py` (MODE=dry để xem trước, MODE=live để chạy thật).
⚠️ 3 cái bẫy đã gặp khi chạy thật:
1. **Cam Reach:** bỏ qua, không tắt — nhận diện qua `optimization_goal` cấp ad set (REACH/IMPRESSIONS…),
   KHÔNG qua objective cấp campaign (đều là OUTCOME_ENGAGEMENT, không phân biệt được).
2. **Cam ABO** (ngân sách ở ad set, không phải campaign): tăng phải vào **TỪNG ad set**, không
   tăng cấp campaign (sẽ báo "no daily_budget").
3. **Rate-limit FB:** chạy >100 cam liên tục sẽ dính "User request limit reached" → giãn nhịp,
   check lỗi ở mọi nhánh, retry sau vài phút.

**Script phân tích:** `plan_export.py` / `plan_xlsx.py` — phân nhóm A/B/C/D, xuất CSV/Excel
campaign_id + hành động.

---

## PHẦN V — CÁCH TIẾP TỤC VỚI CLAUDE + VSCODE (đọc kỹ phần này)

### Nguồn dữ liệu cốt lõi: MCP `eyeplus-data` (kho Railway)
Đây là thứ mạnh nhất để phân tích. Trong Claude, gọi tool `mcp__eyeplus-data__query` chạy
SQL read-only. Bảng chính:

`fb_ads_daily` (ads theo NGÀY) — cột quan trọng:
- `date, campaign_id, campaign_name, account_id, adset_name, ad_name`
- `spend_raw` (chi, pre-VAT) · `impressions, reach, clicks`
- `messages_first` (lượt nhắn đầu) · **`messages_conv_7d`** (hội thoại tính cửa sổ 7 ngày —
  ĐÂY là "mess" sếp đếm khi nói mục tiêu 700)
- `purchases, purchase_value` (đơn + doanh thu THẬT, khớp qua CAPI) · `effective_status`

Bảng khác: `daily_rollup` (KPI bán lẻ toàn chuỗi), `nhanh_bills/orders` (đơn gốc),
`pancake_conv_ad_link` (hội thoại ↔ ad).

### Mẫu prompt để nhờ Claude phân tích
- *"Truy fb_ads_daily 7 ngày, phân cam theo 4-góc A/B/C/D (ROAS≥2,2 × giá hội thoại<55K),
  trừ TQ, cho tôi danh sách tăng/giảm/tắt."*
- *"Chấm ROAS phải dùng cửa sổ 7 ngày (đơn quy về trễ), nhịp chi dùng 3 ngày gần nhất."*

### Quy ước BẮT BUỘC khi phân tích (Claude phải tuân)
- **Tiếng Việt thuần**, dịch hết thuật ngữ Anh (ROAS = doanh thu/đồng quảng cáo…).
- **Chỉ tính bán lẻ**, TUYỆT ĐỐI không gộp TMĐT (BU độc lập).
- **ROAS/giá tin của ngày mới luôn thấp giả** — đơn quy về 7 ngày. Đừng kết luận vội.
- **"mess" của sếp = `messages_conv_7d`**, không phải `messages_first`.
- So sánh ADV/nhắm tay: **lấy cờ từ FB API**, không suy từ tên cam.
- Giá vốn + lợi nhuận đang KHÓA — chỉ mở khi sếp cấp pass.

### Mục tiêu hiện hành (phương án B, đang chạy)
`chi <35tr/ngày · hội thoại >700 · giá mess <55K · ROAS >2,2 · mỗi cam +30% max · TQ khóa`

---

## PHẦN VI — VIỆC ĐANG DỞ & HƯỚNG TIẾP

1. **Đối chứng 2 tuần** đang chạy (bắt đầu ~12/06). Cuối kỳ (~26/06) chốt: rule thắng người
   bao nhiêu → quyết bật **bán tự động** (app gửi đề xuất, người bấm xác nhận).
2. **Phương án B đã áp 14/06** (tắt 56 cam, +30% cho 74 cam). Đang theo dõi — review 1h30 tự chấm.
   Việc cần canh: **giá mess có vượt 55K không** (vì bơm nhóm B đẩy giá lên). Nếu vượt → gỡ
   bằng giảm bớt +30% nhóm B hoặc giữ thêm cam C.
3. **Câu hỏi mở chưa giải:**
   - Lợi nhuận THẬT (cần mở pass giá vốn để chuyển ROAS doanh thu → ROAS lợi nhuận gộp).
   - Đo giá trị thật của quảng cáo (thử nghiệm tắt có kiểm soát 1 vùng nhỏ, so doanh thu dự báo).
   - Đám cam reach chi thấp (<150K/ngày) chưa rà — sếp dặn "để đó báo sau".

---

*Người bàn giao: hệ thống phân tích AI (Claude) cùng sếp. Mọi con số trong tài liệu này
truy được lại từ `mcp__eyeplus-data__query`. Khi nghi ngờ — luôn truy DB, đừng đọc số từ trí nhớ.*
