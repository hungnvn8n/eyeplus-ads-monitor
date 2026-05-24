# Hướng dẫn release bản mới

App có **in-app update check** tự động — user mở app sẽ thấy banner "🆕 Có bản mới" khi anh push release. Họ click "⬇ Tải xuống" → cài lại lên cũ là xong.

## Quy trình release bản mới (sau khi sửa code)

### 1. Bump version

Sửa 2 chỗ:

**`app.py`** (dòng có `APP_VERSION`):
```python
APP_VERSION = "1.0.1"   # tăng từ 1.0.0
```

**`installer/win_installer.iss`** (cùng version cho Windows installer):
```ini
#define MyAppVersion "1.0.1"
```

### 2. Commit + push

```bash
cd "/Users/hungnguyen/Công Việc/AI/fb_ad_local"
git add -A
git commit -m "feat: <mô tả thay đổi>"
git push origin main
```

### 3. Tag version mới

```bash
git tag v1.0.1
git push origin v1.0.1
```

### 4. Đợi GitHub Actions build (~5 phút)

GitHub Actions tự:
- Build .dmg trên Mac runner
- Build .exe trên Windows runner
- Tạo Release v1.0.1 với 2 file attached

Theo dõi: https://github.com/hungnvn8n/eyeplus-ads-monitor/actions

### 5. Done

Trong vòng 24h, mọi user mở app sẽ thấy banner thông báo update.
- Họ click "⬇ Tải xuống" → tải file .dmg hoặc .exe
- Cài đè lên (Mac: drag mới vào Applications; Win: chạy Setup mới, Inno Setup nhận ra cài đè)
- Mở lại app → bản mới

Data của user (.env, cache, rules) **không bị mất** vì lưu ở `~/Library/Application Support/EyePlusAds/` (Mac) hoặc `%APPDATA%\EyePlusAds\` (Win), không nằm trong .app/.exe.

---

## Versioning convention

Theo SemVer **X.Y.Z**:
- **X** (major): breaking change, đổi schema config — tăng khi không tương thích ngược
- **Y** (minor): thêm tính năng mới, tương thích ngược
- **Z** (patch): bug fix, cải tiến nhỏ

Ví dụ:
- `1.0.1` — fix bug login
- `1.1.0` — thêm tab mới
- `2.0.0` — đổi cách lưu data (cần migration)

---

## Force check update từ UI

User không muốn đợi 24h auto-check, có thể force:

```bash
curl -X POST http://localhost:5050/api/version/check-now
```

(Tương lai có thể add nút "Check update" trong settings.)

---

## Hot fix khẩn cấp (không cần đổi version)

Nếu chỉ fix bug nhỏ ở `app.py` mà không muốn tag mới:

1. Sửa code
2. Commit + push
3. User mở app sẽ không thấy update banner (vì cùng version)
4. Cách duy nhất user dùng được code mới: anh phải tag + release

→ Tóm lại: **mọi update gửi tới user đều phải qua tag + Release**.

---

## Rollback nếu release v1.0.1 lỗi

```bash
# Xóa release + tag bị lỗi
gh release delete v1.0.1 --yes
git tag -d v1.0.1
git push origin :refs/tags/v1.0.1

# User ở v1.0.0 vẫn ổn. Sau đó:
# - Sửa lỗi
# - Tag lại v1.0.2 (không reuse v1.0.1)
```

User đã cài v1.0.1 trước khi rollback → app sẽ vẫn chạy phiên bản đó. Khi anh release v1.0.2, họ thấy banner update.

---

## Cấu trúc data dir (không bị xóa khi update)

```
~/Library/Application Support/EyePlusAds/   ← Mac
%APPDATA%\EyePlusAds\                        ← Win
├── .env                  ← Config (FB tokens, license key)
├── .env.example
├── .install_id           ← UUID unique per machine (license binding)
├── .session_key          ← Random per install
├── cache.json            ← FB data cache
├── rules.json            ← Auto-pause rules (user editable)
└── auto_pause_log.jsonl  ← Scan log history
```

Khi cài bản mới đè, các file này giữ nguyên → user không phải config lại.
