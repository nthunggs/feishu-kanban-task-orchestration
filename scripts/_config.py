#!/usr/bin/env python3
"""
Bộ nạp cấu hình (dùng chung cho mọi script)

Tìm config.yaml theo thứ tự ưu tiên:
1. Đường dẫn trỏ bởi biến môi trường FKTO_CONFIG
2. ../config.yaml cùng thư mục script
3. ~/.hermes/feishu-kanban-task-orchestration/config.yaml
4. /etc/feishu-kanban-task-orchestration/config.yaml

Nếu không có, lùi về config.example.yaml (giá trị placeholder khiến script no-op, tránh thao tác nhầm).
"""

import os
import sys
from pathlib import Path

try:
    import yaml  # PyYAML
except ImportError:
    print("[_config] thiếu PyYAML, chạy `pip install pyyaml`", file=sys.stderr)
    raise


_CACHE = None


def _candidate_paths():
    here = Path(__file__).resolve().parent
    candidates = []
    if os.environ.get("FKTO_CONFIG"):
        candidates.append(Path(os.environ["FKTO_CONFIG"]).expanduser())
    candidates.append(here.parent / "config.yaml")
    candidates.append(Path("~/.hermes/feishu-kanban-task-orchestration/config.yaml").expanduser())
    candidates.append(Path("/etc/feishu-kanban-task-orchestration/config.yaml"))
    candidates.append(here.parent / "config.example.yaml")  # dự phòng
    return candidates


def _expand_paths(cfg):
    """Mở rộng ~ và biến môi trường trong mục paths thành đường dẫn tuyệt đối (cho macOS).

    Script gốc gọi Path(cfg["paths"][...]) trực tiếp không mở rộng ~, trên macOS sẽ
    tạo thư mục literal './~/...'. Xử lý tập trung tại đây một lần.
    """
    paths = cfg.get("paths")
    if isinstance(paths, dict):
        for k, v in paths.items():
            if isinstance(v, str) and ("~" in v or "$" in v):
                paths[k] = str(Path(os.path.expandvars(v)).expanduser())
    return cfg


def load_config():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    for p in _candidate_paths():
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                _CACHE = yaml.safe_load(f) or {}
            _CACHE["_loaded_from"] = str(p)
            _expand_paths(_CACHE)
            return _CACHE
    raise FileNotFoundError("config.yaml not found; copy config.example.yaml -> config.yaml rồi chạy lại")


def env_with_overrides():
    """Trả về dict env kèm config.env để tiến trình con dùng"""
    cfg = load_config()
    env = dict(os.environ)
    for k, v in (cfg.get("env") or {}).items():
        env[str(k)] = str(v)
    return env


def get_security():
    """Đọc mục security, có giá trị mặc định an toàn nhất khi thiếu."""
    cfg = load_config()
    sec = cfg.get("security") or {}
    return {
        "write_identity":         sec.get("write_identity", "bot"),
        "allowed_operators":      list(sec.get("allowed_operators") or []),
        "require_spawn_approval": bool(sec.get("require_spawn_approval", True)),
        "treat_table_text_as_data": bool(sec.get("treat_table_text_as_data", True)),
    }
