#!/usr/bin/env python3
"""
Bộ theo dõi trạng thái kanban (polling, khuyến nghị cron 3 phút/lần)

Chế độ no_agent: phát hiện kanban đổi trạng thái → ghi ngược vào bảng Lark
Có thay đổi mới ghi; không đổi thì không output (cron no_agent không tốn token).

Ánh xạ trạng thái:
  kanban done       → bảng Lark「Tiến độ=Đã hoàn thành」+「Trạng thái duyệt=Chờ duyệt」+ Nội dung giao
  kanban blocked    → bảng Lark「Tiến độ=Đình trệ」
  kanban running    → chỉ báo lần đầu khi worker đã spawn
  kanban [QUESTION] → ghi「Trạng thái làm rõ=Chờ làm rõ」+「Nhật ký làm rõ」
  kanban [ANSWER]   → ghi「Trạng thái làm rõ=Đã làm rõ」

Mọi cấu hình đọc từ config.yaml (base_token / table_id / tenant / state_file).
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import load_config, get_security

CFG        = load_config()
TENANT     = CFG["kanban"]["tenant"]
BASE_TOKEN = CFG["feishu"]["base_token"]
TABLE_ID   = CFG["feishu"]["table_id"]
STATE_FILE = Path(CFG["paths"]["state_file"])

WRITE_IDENTITY = get_security()["write_identity"]   # khuyến nghị "bot" / "user"

KANBAN_ID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")
HM  = datetime.now().strftime("%H:%M")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = r.stdout.strip()
        m = re.search(r"\{", out)
        return json.loads(out[m.start():]) if m else {}
    except Exception:
        return {}


def normalize_text(v):
    if v is None: return ""
    if isinstance(v, str): return v
    if isinstance(v, list):
        return "".join(item.get("text") or item.get("link") or "" for item in v if isinstance(item, dict))
    if isinstance(v, dict):
        return v.get("text") or v.get("link") or ""
    return str(v)


def normalize_select(v):
    if v is None: return ""
    if isinstance(v, list) and v:
        item = v[0]
        if isinstance(item, dict): return item.get("text") or item.get("name") or ""
        return str(item)
    if isinstance(v, dict): return v.get("text") or v.get("name") or ""
    return str(v)


def load_state():
    if not STATE_FILE.exists(): return {}
    try: return json.loads(STATE_FILE.read_text())
    except Exception: return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def list_feishu_records():
    r = run([
        "lark-cli", "base", "+record-list",
        "--base-token", BASE_TOKEN, "--table-id", TABLE_ID,
        "--as", WRITE_IDENTITY, "--limit", "200", "--format", "json",
    ])
    if not r.get("ok"): return {}
    data   = r.get("data", {})
    fields = data.get("fields", [])
    rows   = data.get("data", [])
    ids    = data.get("record_id_list", [])
    out = {}
    for i, row in enumerate(rows):
        rec = {"_id": ids[i]}
        for j, f in enumerate(fields):
            rec[f] = row[j]
        out[ids[i]] = rec
    return out


def update_feishu(rid, fields: dict) -> bool:
    r = run([
        "lark-cli", "base", "+record-upsert",
        "--base-token", BASE_TOKEN, "--table-id", TABLE_ID,
        "--record-id", rid, "--as", WRITE_IDENTITY,
        "--json", json.dumps(fields, ensure_ascii=False),
    ])
    return r.get("ok", False)


def extract_chain_ids(chain_text):
    return KANBAN_ID_RE.findall(chain_text or "")


def get_active_kanban_id(rec):
    chain = normalize_text(rec.get("Chuỗi Kanban"))
    ids = extract_chain_ids(chain)
    if ids: return ids[-1]
    detail = normalize_text(rec.get("Chi tiết task"))
    ids = extract_chain_ids(detail)
    if ids: return ids[-1]
    return None


def list_kanban_tasks():
    r = subprocess.run(
        ["hermes", "kanban", "list", "--tenant", TENANT, "--json"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try: return json.loads(r.stdout.strip())
    except Exception: return []


def show_kanban_task(kid):
    r = subprocess.run(
        ["hermes", "kanban", "show", kid, "--json"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try: return json.loads(r.stdout.strip())
    except Exception: return {}


def update_chain_after_complete(chain_text, kid, is_revision=False):
    marker = f"✅{kid}" if not is_revision else f"✏️{kid}"
    sep = " ← "
    return chain_text + sep + marker


def main():
    state  = load_state()
    feishu = list_feishu_records()
    kanban = list_kanban_tasks()
    kanban_by_id = {t.get("id"): t for t in kanban}

    for rid, rec in feishu.items():
        kid = get_active_kanban_id(rec)
        if not kid: continue

        ktask = kanban_by_id.get(kid)
        if not ktask:
            full = show_kanban_task(kid)
            ktask = full.get("task") if isinstance(full, dict) and "task" in full else full
            if not ktask: continue

        kstatus = ktask.get("status", "")
        prev    = state.get("kanban", {}).get(kid, {})
        prev_kstatus = prev.get("status", "")

        if kstatus != prev_kstatus:
            updates = {}
            if kstatus == "done":
                result = ktask.get("result")
                meta, summary = {}, ""
                if isinstance(result, dict):
                    meta    = result.get("metadata") or {}
                    summary = result.get("summary") or ""
                elif isinstance(result, str):
                    summary = result

                # Tương thích hermes v1: result trong `kanban list --json` thường null,
                # summary thật nằm ở latest_summary / result của `kanban show`.
                if not summary or not meta:
                    full = show_kanban_task(kid)
                    if isinstance(full, dict):
                        fresult = (full.get("task") or {}).get("result") or full.get("result")
                        if isinstance(fresult, dict):
                            meta    = meta or (fresult.get("metadata") or {})
                            summary = summary or (fresult.get("summary") or "")
                        elif isinstance(fresult, str):
                            summary = summary or fresult
                        summary = summary or (full.get("latest_summary") or "")

                is_revision = bool(meta.get("is_revision"))
                digest = summary[:1500] if summary else f"kanban {kid} Đã hoàn thành"

                updates = {
                    "Tiến độ":         "Đã hoàn thành",
                    "Thời gian hoàn thành": NOW + ":00",
                    "Nhật ký tiến độ": f"[{HM}] kanban {kid} Đã hoàn thành",
                    "Tóm tắt task": digest,
                    "Trạng thái duyệt":     "Đã sửa" if is_revision else "Chờ duyệt",
                }

                url = (meta.get("deliverable_url") or
                       meta.get("doc_url") or
                       meta.get("feishu_doc_url") or "")
                if not url and summary:
                    m = re.search(r"https?://[^\s)]*feishu\.cn/docx/\S+", summary)
                    if m: url = m.group(0).rstrip(").,;")
                if url:
                    updates["Nội dung giao"] = url
                    updates["Nhật ký tiến độ"] += f"，giao phẩm {url}"

                notes = meta.get("self_check_notes") or ""
                if notes:
                    updates["Ghi chú tự kiểm"] = notes[:2000]
                elif meta.get("self_check_passed") is None:
                    updates["Ghi chú tự kiểm"] = "(worker chưa nộp ghi chú tự kiểm)"

                chain_text = normalize_text(rec.get("Chuỗi Kanban"))
                new_chain  = update_chain_after_complete(chain_text, kid, is_revision)
                if new_chain != chain_text:
                    updates["Chuỗi Kanban"] = new_chain

            elif kstatus == "blocked":
                updates = {
                    "Tiến độ": "Đình trệ",
                    "Nhật ký tiến độ": f"[{HM}] kanban {kid} bị worker đánh dấu blocked, cần người xử lý",
                }
            elif kstatus == "running" and prev_kstatus in ("ready", "todo", ""):
                updates = {
                    "Nhật ký tiến độ": f"[{HM}] worker đã spawn, task bắt đầu chạy",
                }

            if updates:
                update_feishu(rid, updates)

        # phát hiện comment QA
        prev_qa  = prev.get("qa_count", 0)
        full     = show_kanban_task(kid)
        comments = (full.get("comments") or []) if isinstance(full, dict) else []
        qa = []
        for c in comments:
            text = c.get("text") or c.get("body") or ""
            s = text.strip()
            if s.startswith("[QUESTION]"):
                qa.append(("Q", s[len("[QUESTION]"):].strip(), c.get("author") or ""))
            elif s.startswith("[ANSWER]"):
                qa.append(("A", s[len("[ANSWER]"):].strip(), c.get("author") or ""))

        if len(qa) > prev_qa:
            new_qa = qa[prev_qa:]
            log_lines = [normalize_text(rec.get("Nhật ký làm rõ"))] if normalize_text(rec.get("Nhật ký làm rõ")) else []
            for kind, content, author in new_qa:
                line = f"[{HM}] {'Q' if kind=='Q' else 'A'}({author}):{content}"
                log_lines.append(line)

            clarify_updates = {"Nhật ký làm rõ": "\n".join(log_lines)[:5000]}
            cur_clarify = normalize_select(rec.get("Trạng thái làm rõ"))
            has_new_Q = any(k == "Q" for k, _, _ in new_qa)
            has_new_A = any(k == "A" for k, _, _ in new_qa)
            if has_new_Q:
                clarify_updates["Trạng thái làm rõ"] = "Chờ làm rõ"
            elif has_new_A and cur_clarify == "Chờ làm rõ":
                clarify_updates["Trạng thái làm rõ"] = "Đã làm rõ"

            if clarify_updates:
                update_feishu(rid, clarify_updates)

        state.setdefault("kanban", {})[kid] = {
            "status":   kstatus,
            "qa_count": len(qa),
            "title":    ktask.get("title", ""),
        }

    save_state(state)


if __name__ == "__main__":
    main()
