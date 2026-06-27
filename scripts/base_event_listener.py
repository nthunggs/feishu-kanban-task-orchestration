#!/usr/bin/env python3
"""
Bộ lắng nghe sự kiện bảng task Lark (real-time)

Đọc stdin (output của lark-cli event consume), parse từng dòng NDJSON.
Với sự kiện drive.file.bitable_record_changed_v1:
- Lấy record đầy đủ (tên cột người đọc được)
- Gửi thông báo DM qua IM

Do base_watch_supervisor.py khởi động trong tmux, là pipe nhận output lark-cli.
Tất cả base_token / table_id / chat_id đọc từ config.yaml.
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
WRITE_IDENTITY    = SEC["write_identity"]        # khuyến nghị "bot" / "user"
ALLOWED_OPERATORS = set(SEC["allowed_operators"]) # rỗng = không giới hạn (chỉ cảnh báo)


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
    """Chuẩn hóa giá trị cột (select/user/link/text) thành chuỗi 1 dòng"""
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
    """Lấy đầy đủ các cột của 1 record (kèm tên cột)"""
    r = lark_api("GET",
                 f"/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{record_id}",
                 identity="bot")
    if r.get("code") != 0:
        return {}
    return r.get("data", {}).get("record", {}).get("fields", {})


def summarize(fields):
    title  = normalize(fields.get("Mô tả task")) or normalize(fields.get("Chi tiết task")) or "(Không tiêu đề)"
    status = normalize(fields.get("Tiến độ")) or normalize(fields.get("Trạng thái duyệt")) or ""
    person = normalize(fields.get("Phụ trách")) or normalize(fields.get("Quản lý")) or ""
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
    """Trích field ID đã đổi từ before/after của action"""
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

    # Cổng an toàn: operator ngoài whitelist → chỉ ghi log, không báo orchestrator, không spawn.
    # Chặn 'ai cũng gõ lệnh vào bảng để inject'. Whitelist rỗng = không giới hạn (để debug ban đầu).
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
            line = f"➕ Task mới：{title}"
            if person:
                line += f"({person})"
            lines.append(line)

        elif action == "record_edited":
            changed = changed_field_names(act)
            line = f"✏️ {title}"
            if status:
                line += f" → {status}"
            if changed:
                line += f"  [{len(changed)} cột]"
            lines.append(line)

        elif action == "record_deleted":
            lines.append(f"🗑️ Xóa：{rid}")

    if not lines:
        return None
    return f"📋 Bảng task thay đổi({operator})\n" + "\n".join(lines)


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
