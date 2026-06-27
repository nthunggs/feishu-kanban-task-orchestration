#!/usr/bin/env python3
"""
Giám sát lắng nghe bảng task Lark - supervisor (cron chạy mỗi phút)

Nhiệm vụ:
1. Kiểm tra tmux session còn sống không; chưa có thì khởi động
   (lark-cli event +subscribe ... | base_event_listener.py)
2. Khi khởi động gọi /drive/v1/files/{token}/subscribe để đăng ký sự kiện cho bảng
3. Không khởi động trùng (tmux has-session idempotent)

Không tốn LLM, chỉ shell + REST. Mọi cấu hình đọc từ config.yaml.
"""

import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import load_config, env_with_overrides

CFG        = load_config()
BASE_TOKEN = CFG["feishu"]["base_token"]
TMUX_NAME  = CFG["paths"]["tmux_name"]
LISTENER   = CFG["paths"]["listener"]
LOG_FILE   = CFG["paths"]["log_file"]
# EventKey real-time (lark-cli v1.0.19+ `event consume <key>`)
EVENT_KEY  = CFG["feishu"].get("event_key", "drive.file.bitable_record_changed_v1")
ENV        = env_with_overrides()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tmux_alive():
    r = subprocess.run(["tmux", "has-session", "-t", TMUX_NAME],
                       capture_output=True, env=ENV)
    return r.returncode == 0


def lark_subscribe():
    """Đăng ký sự kiện file bitable (idempotent, gọi lại OK)"""
    cmd = ["lark-cli", "api", "POST",
           f"/open-apis/drive/v1/files/{BASE_TOKEN}/subscribe",
           "--params", json.dumps({"file_type": "bitable"}),
           "--as", "bot"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=ENV)
    try:
        data = json.loads(r.stdout)
        if data.get("code") == 0:
            log("drive subscribe ok")
            return True
        log(f"drive subscribe fail: {data}")
    except Exception as e:
        log(f"drive subscribe parse error: {e}; out={r.stdout[:200]}")
    return False


def start_tmux():
    """Khởi động tmux session, bên trong chạy lark-cli | listener"""
    try:
        open(LOG_FILE, "w").close()
    except Exception:
        pass

    inner = (
        f"LARK_CLI_NO_PROXY=1 lark-cli event consume {EVENT_KEY} "
        f"--as bot --quiet 2>&1 "
        f"| tee -a {LOG_FILE} "
        f"| python3 {LISTENER}"
    )
    cmd = ["tmux", "new-session", "-d", "-s", TMUX_NAME, inner]
    r = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
    if r.returncode == 0:
        log(f"tmux session '{TMUX_NAME}' started")
        return True
    log(f"tmux start failed: {r.stderr.strip()}")
    return False


def main():
    if tmux_alive():
        log(f"tmux session '{TMUX_NAME}' already running, OK")
        return

    log("tmux session missing, restarting...")
    lark_subscribe()
    start_tmux()


if __name__ == "__main__":
    main()
