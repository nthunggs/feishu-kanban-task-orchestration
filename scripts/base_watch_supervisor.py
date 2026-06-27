#!/usr/bin/env python3
"""
飞书任务表监听 - supervisor（cron 每分钟跑一次）

职责：
1. 检查 tmux session 是否存在；不在则启动
   (lark-cli event +subscribe ... | base_event_listener.py)
2. 启动时调一次 /drive/v1/files/{token}/subscribe 把目标表挂上事件订阅
3. 不重复启动（tmux has-session 幂等）

无 LLM 消耗，纯 shell + REST。所有配置从 config.yaml 读取。
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
# 实时事件 EventKey（lark-cli v1.0.19+ `event consume <key>`）
EVENT_KEY  = CFG["feishu"].get("event_key", "drive.file.bitable_record_changed_v1")
ENV        = env_with_overrides()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tmux_alive():
    r = subprocess.run(["tmux", "has-session", "-t", TMUX_NAME],
                       capture_output=True, env=ENV)
    return r.returncode == 0


def lark_subscribe():
    """订阅 bitable 文件事件（幂等，重复调 OK）"""
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
    """启动 tmux session，里面跑 lark-cli | listener"""
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
