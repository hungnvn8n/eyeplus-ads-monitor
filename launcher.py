"""Launcher native desktop window (pywebview).

Thay vì mở browser bên ngoài, app chạy trong cửa sổ native:
- Mac:   WKWebView (built-in macOS)
- Win:   EdgeWebView2 (Win 10/11 auto)

Cửa sổ có title bar, minimize/maximize, close icon — như app thường.
Đóng cửa sổ → Flask + scheduler dừng.

Logic chính:
1. Bootstrap data dir (Application Support / APPDATA)
2. Khởi Flask server trong background thread
3. Đợi port 5050 sẵn sàng
4. Tạo native window pointing tới localhost:5050
5. Blocking webview.start()
6. User close window → process exit
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time


# ─── Data dir helpers ─────────────────────────────────────────────────────

def _writable_data_dir() -> str:
    """User data dir (.env, cache, rules, log)."""
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.path.expanduser("~/.config")
    app_dir = os.path.join(base, "EyePlusAds")
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def _bundled_resource_dir() -> str:
    """Folder chứa templates + .env.example bundle trong PyInstaller."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _open_folder(path: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _show_native_dialog(title: str, msg: str) -> None:
    """Native dialog: osascript trên Mac, MessageBoxW trên Win."""
    try:
        if sys.platform == "darwin":
            escaped = msg.replace('"', '\\"').replace("\n", "\\n")
            subprocess.run([
                "osascript", "-e",
                f'display dialog "{escaped}" with title "{title}" buttons {{"OK"}} default button "OK"'
            ], timeout=120)
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0)
        else:
            print(f"[{title}] {msg}")
    except Exception as e:
        print(f"[{title}] {msg}\n(dialog failed: {e})")


def _bootstrap() -> str:
    """Setup data dir + copy .env.example nếu lần đầu chạy."""
    data_dir = _writable_data_dir()
    os.chdir(data_dir)
    sys.path.insert(0, data_dir)

    env_path = os.path.join(data_dir, ".env")
    if not os.path.exists(env_path):
        bundle_example = os.path.join(_bundled_resource_dir(), ".env.example")
        if os.path.exists(bundle_example):
            try:
                shutil.copy(bundle_example, env_path)
                shutil.copy(bundle_example, os.path.join(data_dir, ".env.example"))
                _open_folder(data_dir)
                _show_native_dialog(
                    "EyePlus Ads — Setup",
                    f"App vừa tạo file .env tại:\n{data_dir}\n\n"
                    "Vui lòng:\n"
                    "1. Mở file .env vừa hiện trong Finder/Explorer\n"
                    "2. Điền 3 FB_TOKEN_BM1/2/3 (xin từ admin)\n"
                    "3. Lưu file\n"
                    "4. Mở lại app",
                )
                sys.exit(0)
            except Exception as e:
                print(f"⚠️  Copy .env fail: {e}")
    return data_dir


# ─── Wait Flask server sẵn sàng ───────────────────────────────────────────

def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    """Đợi tới khi port chấp nhận connection."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.2)
    return False


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    data_dir = _bootstrap()
    print(f"📂 Data dir: {data_dir}")

    # KHÔNG check license tại startup — login page handle khi user nhập key.
    # Background scheduler vẫn check mỗi 6h sau đăng nhập (xem app.py).

    # Start Flask in background daemon thread
    def _run_flask():
        try:
            from app import main as app_main
            app_main(open_browser=False)
        except Exception as e:
            print(f"❌ Flask crash: {e}")
            import traceback; traceback.print_exc()

    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()

    port = int(os.getenv("PORT", "5050"))
    print(f"⏳ Đợi server sẵn sàng tại http://localhost:{port}...")
    if not _wait_for_port(port, timeout=30):
        _show_native_dialog(
            "EyePlus Ads — Lỗi khởi động",
            f"Server không khởi động trong 30 giây.\n\nKiểm tra log:\n{data_dir}/app.log",
        )
        sys.exit(1)
    print(f"✅ Server ready")

    # Create native window
    try:
        import webview
    except ImportError as e:
        print(f"⚠️  pywebview chưa cài: {e}")
        print(f"   Fallback: mở browser")
        import webbrowser
        webbrowser.open(f"http://localhost:{port}")
        # Block main thread cho tới khi Flask thread chết
        flask_thread.join()
        return

    window = webview.create_window(
        title="EyePlus Ads Monitor",
        url=f"http://localhost:{port}",
        width=1440,
        height=900,
        min_size=(900, 600),
        resizable=True,
        confirm_close=False,
        background_color="#0a0e1a",   # match dark theme bg
    )

    # webview.start() blocks until window closed
    webview.start(debug=False)

    # Window closed → exit (daemon Flask thread sẽ tự chết)
    print("👋 Window closed — exit")


if __name__ == "__main__":
    main()
