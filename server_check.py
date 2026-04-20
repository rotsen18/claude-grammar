#!/usr/bin/env python3
import fcntl
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR
from settings import DASHBOARD_HOST, DASHBOARD_PORT

PID_FILE = DATA_DIR / "dashboard.pid"
LOG_FILE = DATA_DIR / "dashboard.log"
LOCK_FILE = DATA_DIR / "dashboard.lock"


def is_server_running() -> bool:
    try:
        import requests

        response = requests.get(
            f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/health",
            timeout=2,
        )
        return response.status_code == 200
    except Exception:
        return False


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


CHROME_BINARY_MACOS = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# First-launch size only. Position intentionally omitted — Chrome persists
# per-URL window bounds once the window exists, and its own logic handles
# display changes (external monitor plug/unplug, resolution shifts) far
# better than any coords we'd try to save ourselves.
DASHBOARD_WINDOW_WIDTH = 1056
DASHBOARD_WINDOW_HEIGHT = 321


def _dashboard_app_arg() -> str:
    return f"--app=http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"


def _window_size_arg() -> str:
    return f"--window-size={DASHBOARD_WINDOW_WIDTH},{DASHBOARD_WINDOW_HEIGHT}"


def _dashboard_url() -> str:
    return f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"


def is_dashboard_window_open() -> bool:
    # macOS: ask Chrome directly via AppleScript — `pgrep` can't see the
    # --app= flag because already-running Chrome handles the new-window
    # request internally without spawning a process carrying that arg.
    if sys.platform == "darwin":
        try:
            script = (
                'tell application "Google Chrome" to '
                'count (windows whose URL of active tab starts with "'
                + _dashboard_url() + '")'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=3,
            )
            count = int((result.stdout or "0").strip() or "0")
            return count > 0
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["pgrep", "-f", _dashboard_app_arg()],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _find_chrome_binary() -> str | None:
    candidates = [
        CHROME_BINARY_MACOS,
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def open_dashboard_window() -> None:
    # If the dashboard window is already up (e.g. we're coming back from a
    # server restart and Chrome kept the tab), leave it exactly where the
    # user parked it. Re-asserting bounds here would snap the window back
    # to the hardcoded default on every restart.
    if is_dashboard_window_open():
        return
    time.sleep(1.5)
    if is_dashboard_window_open():
        return

    chrome_binary = _find_chrome_binary()
    url = _dashboard_url()

    if chrome_binary:
        try:
            # Direct binary launch — `open -a ... --args` does NOT forward
            # args to an already-running Chrome instance, so the --app=
            # flag gets ignored and a plain tab opens. The binary directly
            # handles the arg correctly every time.
            subprocess.Popen(
                [chrome_binary, _dashboard_app_arg(), _window_size_arg()],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception as exc:
            print(f"Chrome launch failed, falling back to default browser: {exc}", file=sys.stderr)

    # No Chromium-family browser installed (or launch failed) — use the
    # system default browser. User sees the dashboard in a regular tab
    # instead of a sized app-window, but at least they see it.
    try:
        webbrowser.open(url)
        print(f"Opened dashboard in default browser: {url}")
    except Exception as exc:
        print(f"Failed to open browser: {exc}. Visit {url} manually.", file=sys.stderr)


def start_server() -> bool:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if not is_pid_alive(old_pid):
                PID_FILE.unlink()
        except (ValueError, FileNotFoundError):
            try:
                PID_FILE.unlink()
            except FileNotFoundError:
                pass

    project_dir = Path(__file__).parent
    log_handle = open(LOG_FILE, "a")

    process = subprocess.Popen(
        ["uv", "run", "--project", str(project_dir), "python", "-m", "dashboard.app"],
        cwd=str(project_dir),
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )

    PID_FILE.write_text(str(process.pid))

    for _ in range(20):
        time.sleep(0.3)
        if is_server_running():
            return True
    return False


def main() -> None:
    try:
        if is_server_running():
            open_dashboard_window()
            sys.exit(0)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOCK_FILE, "w") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                print(f"Could not acquire startup lock: {exc}", file=sys.stderr)
                sys.exit(0)

            if is_server_running():
                open_dashboard_window()
                sys.exit(0)

            if start_server():
                print(f"Grammar dashboard running at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
                open_dashboard_window()
            else:
                print("Warning: Grammar dashboard failed to start", file=sys.stderr)
    except Exception as exc:
        print(f"server_check error: {exc}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
