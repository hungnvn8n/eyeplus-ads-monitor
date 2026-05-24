═══════════════════════════════════════════════════
  EyePlus Ads Monitor — Hướng dẫn cài đặt
═══════════════════════════════════════════════════

Phần mềm quản lý quảng cáo Facebook nội bộ Eye Plus.
Chạy 100% trên máy anh, KHÔNG gửi data lên server.


CÀI ĐẶT
────────────────────────────────────────────────────

▸ macOS — từ file .dmg
  1. Double-click file "EyePlus-Ads-1.0.0.dmg"
  2. Cửa sổ Finder mở → kéo icon "EyePlus Ads" sang folder "Applications"
  3. Eject .dmg (chuột phải → Eject)
  4. Mở Launchpad / Applications, tìm "EyePlus Ads"
  5. LẦN ĐẦU mở: chuột PHẢI vào icon → Open → Open
     (bypass Gatekeeper warning, chỉ cần 1 lần)

▸ Windows — từ file Setup
  1. Double-click "EyePlusAds-Setup-1.0.0.exe"
  2. Wizard cài đặt → Next → Next → Install
  3. (tuỳ chọn) Tick "Tạo shortcut Desktop"
  4. LẦN ĐẦU mở: SmartScreen hỏi → "More info" → "Run anyway"


CHẠY LẦN ĐẦU
────────────────────────────────────────────────────

1. Mở app "EyePlus Ads"

2. App tạo file config trong:
   ▸ Mac: ~/Library/Application Support/EyePlusAds/.env
   ▸ Win: %APPDATA%\EyePlusAds\.env
   App sẽ MỞ folder này tự động + hiện popup hướng dẫn rồi exit.

3. Mở file .env vừa hiện bằng text editor (Notepad / TextEdit)
   Điền 3 token Facebook (xin từ admin):
     FB_TOKEN_BM1=EAAxxxxx...
     FB_TOKEN_BM2=EAAxxxxx...
     FB_TOKEN_BM3=EAAxxxxx...
   Lưu file.

4. Mở lại app. App tự khởi động + mở browser tới http://localhost:5050


SỬ DỤNG HÀNG NGÀY
────────────────────────────────────────────────────

- Mở app EyePlus Ads (từ Launchpad / Start menu / Desktop)
- Browser tự mở dashboard sau 2-3 giây
- App tự fetch FB data mỗi 3h
- Auto-scan flag campaigns vi phạm rule mỗi 8h (chỉ log, không tự tắt)
- Đóng app: tắt cửa sổ console (đen)


5 TRANG CHÍNH
────────────────────────────────────────────────────

🏠 Tổng quan   — Phễu phân loại ads, danh sách ad cần tắt
📊 Tài khoản   — So sánh 6 TK / 3 BM
📈 Campaign    — Filter, sort, bulk pause, xuất Excel
💡 Insights    — Charts ROAS, distribution
🤖 Auto-log    — Lịch sử scan + bulk pause candidates


FILE QUAN TRỌNG
────────────────────────────────────────────────────

Tất cả nằm trong:
  Mac: ~/Library/Application Support/EyePlusAds/
  Win: %APPDATA%\EyePlusAds\

  .env                 ← Config (FB tokens, ngưỡng KPI)
  .env.example         ← Template tham khảo
  cache.json           ← Cache data FB (tự tạo, đừng xóa khi app đang chạy)
  rules.json           ← Rule auto-scan (sửa từ UI /auto-log)
  auto_pause_log.jsonl ← Log scan history


TROUBLESHOOT
────────────────────────────────────────────────────

▸ "Thiếu FB_TOKEN_BM*" trên dashboard
  → Mở file .env, kiểm tra 3 token đã điền đúng

▸ "0 ads" hiển thị
  → FB token có thể hết hạn. Xin token mới từ admin

▸ Browser không tự mở
  → Mở thủ công: http://localhost:5050

▸ App đang chạy nhưng không thấy data
  → Đợi 30s rồi F5 trang. Lần đầu fetch FB API mất ~30s

▸ Port 5050 bị chiếm (AirPlay Receiver trên Mac)
  → Mở .env, thêm dòng: PORT=5060

▸ Muốn xóa toàn bộ data (reset về cài đặt)
  → Xóa folder data: ~/Library/Application Support/EyePlusAds/
                     hoặc %APPDATA%\EyePlusAds


GỠ CÀI ĐẶT
────────────────────────────────────────────────────

▸ Mac: Kéo "EyePlus Ads.app" từ Applications vào Trash
       (Tuỳ chọn) Xóa folder data: ~/Library/Application Support/EyePlusAds

▸ Win: Control Panel → Programs → Uninstall "EyePlus Ads"
       (Tuỳ chọn) Xóa folder data: %APPDATA%\EyePlusAds


HỖ TRỢ
────────────────────────────────────────────────────

Liên hệ: Hùng Nguyễn — CEO Eye Plus
