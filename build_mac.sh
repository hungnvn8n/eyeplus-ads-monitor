#!/bin/bash
# Build .dmg installer cho macOS — output 1 file duy nhất
# Chạy: ./build_mac.sh

set -e
cd "$(dirname "$0")"

# Version: ưu tiên git tag (vd v1.0.1 → 1.0.1), fallback parse từ app.py APP_VERSION
VERSION=$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')
if [ -z "$VERSION" ]; then
  VERSION=$(grep -E '^APP_VERSION\s*=' app.py | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
fi
VERSION=${VERSION:-1.0.0}
APP_NAME="EyePlus Ads"
APP_BUNDLE="$APP_NAME.app"
DMG_NAME="EyePlus-Ads-$VERSION.dmg"

echo "═══════════════════════════════════════════════"
echo "  Build EyePlus Ads $VERSION cho macOS"
echo "═══════════════════════════════════════════════"

if [ ! -d "venv" ]; then
  echo "❌ Chưa có venv. Chạy:"
  echo "   python3.12 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source venv/bin/activate
pip install --quiet pyinstaller==6.3.0

# Clean build cũ — KHÔNG xoá installer/ vì có file win_installer.iss bên trong
rm -rf build dist
mkdir -p installer
# Xoá .dmg cũ nếu có
rm -f installer/EyePlus-Ads-*.dmg

# ─── Step 1: PyInstaller build .app bundle ────────────────────────────────
echo ""
echo "🔨 Step 1/3: PyInstaller build .app bundle (~1 phút)..."
pyinstaller --clean fb_ad_local.spec 2>&1 | /usr/bin/tail -3

if [ ! -d "dist/$APP_BUNDLE" ]; then
  echo "❌ Build .app fail. Check log trên."
  exit 1
fi

# Copy .env.example vào .app Resources (bundle với app)
cp .env.example "dist/$APP_BUNDLE/Contents/Resources/" 2>/dev/null || true

echo "   ✅ $(du -sh "dist/$APP_BUNDLE" | cut -f1) — dist/$APP_BUNDLE"

# ─── Step 2: Tạo .dmg installer ───────────────────────────────────────────
echo ""
echo "💿 Step 2/3: Tạo .dmg installer..."

STAGING="dist/.dmg_staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "dist/$APP_BUNDLE" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
cp DIST_README.txt "$STAGING/HƯỚNG DẪN.txt" 2>/dev/null || true

DMG_PATH="installer/$DMG_NAME"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  -fs HFS+ \
  "$DMG_PATH" > /dev/null 2>&1

rm -rf "$STAGING"

if [ ! -f "$DMG_PATH" ]; then
  echo "❌ Tạo .dmg fail."
  exit 1
fi

# ─── Step 3: Clean intermediate files ─────────────────────────────────────
echo ""
echo "🧹 Step 3/3: Clean intermediate files..."
rm -rf build dist
echo "   ✅ Xoá build/ và dist/ (chỉ giữ installer/)"

# ─── Tóm tắt ──────────────────────────────────────────────────────────────
DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
echo ""
echo "═══════════════════════════════════════════════"
echo "  ✅ BUILD XONG"
echo "═══════════════════════════════════════════════"
echo ""
echo "📦 1 file duy nhất để gửi:"
echo ""
echo "    $DMG_PATH  ($DMG_SIZE)"
echo ""
echo "📤 User cài:"
echo "   1. Double-click .dmg"
echo "   2. Kéo 'EyePlus Ads' vào Applications"
echo "   3. Mở từ Launchpad (chuột phải → Open lần đầu)"
echo ""
