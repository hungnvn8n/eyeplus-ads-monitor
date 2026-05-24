# Build EyePlusAds standalone executable

## Tổng quan
- Dùng **PyInstaller** bundle Python + Flask + tất cả deps thành 1 file `.exe` (Win) hoặc binary (Mac)
- Output: `dist/EyePlusAds` (~30-40 MB)
- Không cần Python cài sẵn trên máy đích
- Templates HTML, .env.example đều bundle bên trong

## Build trên macOS

```bash
cd fb_ad_local
./build_mac.sh
```

Output: `dist/EyePlusAds` — copy folder `dist/` sang máy Mac khác là chạy được.

## Build trên Windows

```cmd
cd fb_ad_local
build_win.bat
```

Output: `dist\EyePlusAds.exe` — copy thư mục `dist\` sang máy Win khác.

## Cross-platform note
**KHÔNG cross-compile được** — phải build trên đúng OS:
- Mac binary chỉ chạy trên Mac
- Windows .exe chỉ chạy trên Windows

Để có cả 2 phiên bản:
- Build trên Mac → có Mac binary
- Build trên Windows (hoặc CI/CD) → có Win binary

## CI/CD (optional)
GitHub Actions chạy matrix `[macos-latest, windows-latest]` → build cả 2 phiên bản tự động khi push code. Em chưa setup nhưng có thể add `.github/workflows/build.yml`.

## Code signing (không bắt buộc)
- **Mac**: chưa sign → user thấy Gatekeeper warning "không xác minh được nhà phát triển". Click chuột phải → Open → Open. Hoặc cần Apple Developer ($99/năm).
- **Windows**: chưa sign → SmartScreen warning. Click "More info" → "Run anyway".

## Troubleshooting

### Build fail: "ModuleNotFoundError"
Thêm module vào `hiddenimports` trong `fb_ad_local.spec`, rebuild.

### Binary chạy nhưng app crash khi load template
Kiểm tra `datas` trong spec — phải có `('templates', 'templates')`.

### App không tìm thấy .env
Launcher chdir tới thư mục chứa executable. Đảm bảo .env ở cùng folder.

### Binary quá to (>100MB)
Mở rộng `excludes` trong spec để loại module không dùng.

### App khởi động chậm (>10s lần đầu)
PyInstaller --onefile extract vào temp folder mỗi lần chạy. Đổi sang `--onedir` (folder) trong spec sẽ nhanh hơn nhưng nhiều file hơn.
