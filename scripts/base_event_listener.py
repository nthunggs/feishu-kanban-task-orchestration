#!/usr/bin/env python3
"""
飞书任务表事件监听器（real-time）

读取 stdin（lark-cli event +subscribe 的输出），逐行解析 NDJSON 事件。
对 drive.file.bitable_record_changed_v1 事件：
- 拉取完整 record（人类可读字段名）
- 通过 IM 发送飞书 DM 通知

由 base_watch_supervisor.py 在 tmux 内启动，作为 lark-cli 的下游 pipe。
所有 base_token / table_id / chat_id 从 config.yaml 读取。
"""

import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import load_config, env_with_overrides, get_security

CFG       = load_config()
BASE_TOKEN  = CFG["feishu"]["base_token"]
TABLE_ID    = CFG["feishu"]["table_id"]
FEISHU_CHAT = CFG["feishu"]["chat_id"]
SEEN_FILE   = Path(CFG["paths"]["seen_file"])
ENV         = env_with_overrides()

SEC               = get_security()
WRITE_IDENTITY    = SEC["write_identity"]        # "bot" 推荐 / "user"
ALLOWED_OPERATORS = set(SEC["allowed_operators"]) # 空 = 不限制（仅告警）


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def lark_api(method, path, params=None, data=None, identity="bot", timeout=15):
    cmd = ["lark-cli", "api", method, path, "--as", identity]
    if params:
        cmd += ["--params", json.dumps(params)]
    if data:
        cmd += ["--data", json.dumps(data)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV)
        return json.loads(r.stdout) if r.stdout else {}
    except Exception as e:
        log(f"API error {path}: {e}")
        return {}


def normalize(v):
    """把字段值（select / user / link / text）规范成单行字符串"""
    if v is None:
        return ""
    if isinstance(v, list) and v:
        item = v[0]
        if isinstance(item, dict):
            return item.get("text") or item.get("name") or str(item)[:80]
        return str(item)
    if isinstance(v, dict):
        return v.get("text") or v.get("name") or ""
    return str(v)[:120]


def fetch_record(record_id):
    """拉单条记录的完整字段（带字段名）"""
    r = lark_api("GET",
                 f"/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{record_id}",
                 identity="bot")
    if r.get("code") != 0:
        return {}
    return r.get("data", {}).get("record", {}).get("fields", {})


def summarize(fields):
    title  = normalize(fields.get("任务描述")) or normalize(fields.get("任务详情")) or "(无标题)"
    status = normalize(fields.get("进展")) or normalize(fields.get("复核状态")) or ""
    person = normalize(fields.get("负责人")) or normalize(fields.get("管理者")) or ""
    return title[:60], status, person


def send_feishu(text):
    cmd = ["lark-cli", "api", "POST", "/open-apis/im/v1/messages",
           "--params", json.dumps({"receive_id_type": "chat_id"}),
           "--as", WRITE_IDENTITY,
           "--data", json.dumps({
               "receive_id": FEISHU_CHAT,
               "msg_type": "text",
               "content": json.dumps({"text": text}, ensure_ascii=False),
           })]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=ENV)
    except Exception as e:
        log(f"send_feishu error: {e}")


def load_seen():
    if SEEN_FILE.exists():
        try:
            return list(json.loads(SEEN_FILE.read_text()).get("event_ids", []))[-500:]
        except Exception:
            pass
    return []


def save_seen(seen):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps({"event_ids": seen[-500:]}, ensure_ascii=False))


def changed_field_names(action):
    """从 action 的 before/after 提取变更字段 ID"""
    before = {f["field_id"]: f.get("field_value", "") for f in action.get("before_value", [])}
    after  = {f["field_id"]: f.get("field_value", "") for f in action.get("after_value", [])}
    return [fid for fid in after if before.get(fid) != after.get(fid)]


def handle_event(ev):
    body = ev.get("event", {})
    if body.get("table_id") != TABLE_ID:
        return None

    actions = body.get("action_list", [])
    if not actions:
        return None

    operator = body.get("operator_id", {}).get("user_id", "?")

    # 安全门：operator 不在白名单 → 只记日志，不通知 orchestrator、不触发 spawn。
    # 防「任意人在表里打字注入指令」。空白名单 = 不限制（兼容初始调试）。
    if ALLOWED_OPERATORS and operator not in ALLOWED_OPERATORS:
        log(f"⚠️ blocked: operator {operator} not in allowed_operators; ignoring event")
        return None

    lines = []

    for act in actions:
        action = act.get("action", "")
        rid    = act.get("record_id", "")
        fields = fetch_record(rid) if action != "record_deleted" else {}
        title, status, person = summarize(fields)

        if action == "record_added":
            line = f"➕ 新任务：{title}"
            if person:
                line += f"({person})"
            lines.append(line)

        elif action == "record_edited":
            changed = changed_field_names(act)
            line = f"✏️ {title}"
            if status:
                line += f" → {status}"
            if changed:
                line += f"  [{len(changed)}个字段]"
            lines.append(line)

        elif action == "record_deleted":
            lines.append(f"🗑️ 删除：{rid}")

    if not lines:
        return None
    return f"📋 任务表变更({operator})\n" + "\n".join(lines)


def main():
    seen = load_seen()
    seen_set = set(seen)
    log(f"listener started; {len(seen_set)} prior events known; config={CFG.get('_loaded_from')}")

    for raw in sys.stdin:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue

        header     = ev.get("header", {})
        event_id   = header.get("event_id")
        event_type = header.get("event_type")
        if event_type != "drive.file.bitable_record_changed_v1":
            continue
        if not event_id or event_id in seen_set:
            continue

        seen_set.add(event_id)
        seen.append(event_id)
        save_seen(seen)

        msg = handle_event(ev)
        if msg:
            send_feishu(msg)
            log(f"sent: {msg.splitlines()[0]}")


if __name__ == "__main__":
    main()
