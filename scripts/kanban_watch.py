#!/usr/bin/env python3
"""
kanban 状态监听器（轮询，建议 cron 3 分钟跑一次）

no_agent 模式：检测 kanban 任务状态变化 → 直接写回飞书表
有变化才写；无变化则无输出（cron no_agent 不耗 token）。

状态映射：
  kanban done       → 飞书「进展=已完成」+「复核状态=待复核」+ 交付内容
  kanban blocked    → 飞书「进展=已停滞」
  kanban running    → 仅首次通知 worker 已 spawn
  kanban [QUESTION] → 写「澄清状态=待澄清」+「澄清记录」
  kanban [ANSWER]   → 写「澄清状态=已澄清」

所有配置从 config.yaml 读取（base_token / table_id / tenant / state_file）。
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

WRITE_IDENTITY = get_security()["write_identity"]   # "bot" 推荐 / "user"

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
        "--as", WRITE_IDENTITY, "--limit", "200",
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
    chain = normalize_text(rec.get("Kanban链"))
    ids = extract_chain_ids(chain)
    if ids: return ids[-1]
    detail = normalize_text(rec.get("任务详情"))
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

                is_revision = bool(meta.get("is_revision"))
                digest = summary[:1500] if summary else f"kanban {kid} 已完成"

                updates = {
                    "进展":         "已完成",
                    "实际完成时间": NOW + ":00",
                    "最新进展记录": f"[{HM}] kanban {kid} 已完成",
                    "任务情况总结": digest,
                    "复核状态":     "已修正" if is_revision else "待复核",
                }

                url = (meta.get("deliverable_url") or
                       meta.get("doc_url") or
                       meta.get("feishu_doc_url") or "")
                if not url and summary:
                    m = re.search(r"https?://[^\s)]*feishu\.cn/docx/\S+", summary)
                    if m: url = m.group(0).rstrip(").,;")
                if url:
                    updates["交付内容"] = url
                    updates["最新进展记录"] += f"，交付物 {url}"

                notes = meta.get("self_check_notes") or ""
                if notes:
                    updates["自检备注"] = notes[:2000]
                elif meta.get("self_check_passed") is None:
                    updates["自检备注"] = "(worker 未提交 self_check_notes)"

                chain_text = normalize_text(rec.get("Kanban链"))
                new_chain  = update_chain_after_complete(chain_text, kid, is_revision)
                if new_chain != chain_text:
                    updates["Kanban链"] = new_chain

            elif kstatus == "blocked":
                updates = {
                    "进展": "已停滞",
                    "最新进展记录": f"[{HM}] kanban {kid} 被 worker 标记 blocked，需人工介入",
                }
            elif kstatus == "running" and prev_kstatus in ("ready", "todo", ""):
                updates = {
                    "最新进展记录": f"[{HM}] worker 已 spawn,任务进入运行",
                }

            if updates:
                update_feishu(rid, updates)

        # QA comment 检测
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
            log_lines = [normalize_text(rec.get("澄清记录"))] if normalize_text(rec.get("澄清记录")) else []
            for kind, content, author in new_qa:
                line = f"[{HM}] {'Q' if kind=='Q' else 'A'}({author}):{content}"
                log_lines.append(line)

            clarify_updates = {"澄清记录": "\n".join(log_lines)[:5000]}
            cur_clarify = normalize_select(rec.get("澄清状态"))
            has_new_Q = any(k == "Q" for k, _, _ in new_qa)
            has_new_A = any(k == "A" for k, _, _ in new_qa)
            if has_new_Q:
                clarify_updates["澄清状态"] = "待澄清"
            elif has_new_A and cur_clarify == "待澄清":
                clarify_updates["澄清状态"] = "已澄清"

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
