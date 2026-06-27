# Feishu Kanban Task Orchestration

Real-time **Feishu (Lark) bitable** + **Hermes Kanban** task orchestration system. Multi-agent worker profiles, real-time event listening, automatic bidirectional sync.

A Skill package for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **🍴 nthunggs fork — macOS + 多 profile team 适配版**
> 本 fork 在上游基础上做了 macOS 适配与安全加固：路径自动展开 `~`、写回改用 bot 最小权限身份、
> 新增操作者白名单与 spawn 审批门。详见 **[docs/macos-team-setup.md](docs/macos-team-setup.md)**。

## 是什么

把"飞书多维表格当任务面板，Hermes Kanban 当任务队列，多个 worker profile 当执行者"组装成一套自动化流水线：

- **新建一行飞书任务表** → ≤3 秒推送通知 → orchestrator 拆解 → spawn worker
- **worker 完成任务** → ≤3 分钟自动写回飞书表（进展/交付物/复核状态/澄清记录）
- **worker 留 `[QUESTION]`** → 自动同步到飞书"澄清记录"字段，等用户回复 `[ANSWER]`
- 多 agent 并行（默认配 1 个 orchestrator + 4 个 worker profile）

零 LLM 持续消耗（监听走 WebSocket 长连，写回走 cron 轮询，都是 `no_agent` 脚本）。

## 数据流

```
飞书多维表格（任务表）
   │ ↑
   │ │ 写回（kanban_watch.py，3 min cron）
   │ ↓
   │ ├─ 进展 / 实际完成时间 / 任务情况总结
   │ ├─ 交付内容（feishu doc URL）
   │ ├─ 复核状态 / 复核反馈
   │ └─ 澄清记录（[QUESTION]/[ANSWER]）
   │
   │ 实时推送（base_event_listener.py，WebSocket）
   ↓
飞书 DM（用户）→ 调用 orchestrator
   │
   ↓
Hermes Kanban
   │
   ├─→ orchestrator profile（如 Judy）拆解 + 分派
   └─→ worker profiles（如 bogo / bonnie / clawhauser / stu）执行
```

## 快速开始

### 1. 前置依赖

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 已安装并配置租户
- [lark-cli](https://github.com/larksuite/cli) v1.0.19+ 已安装并绑定到飞书自建应用
- `tmux` 可用
- 飞书自建应用具备：
  - **应用身份**权限：`bitable:app:readonly`、`im:message:send_as_bot`
  - 事件订阅（长连接模式）：`drive.file.bitable_record_changed_v1`
  - 应用机器人是目标多维表格的协作者

> 详细的飞书后台配置步骤见 [docs/setup-guide.md](docs/setup-guide.md)。

### 2. 创建任务表

按 [`templates/feishu-base-schema.json`](templates/feishu-base-schema.json) 在飞书新建一张多维表格，包含 20 个字段（任务描述、负责人、进展、复核状态…）。

或者复用已有表格，把字段名对齐到模板即可（field_id 不需要相同，脚本会按字段名解析）。

### 3. 配置

复制 `config.example.yaml` 为 `config.yaml`，填入：

```yaml
feishu:
  base_token: "你的多维表格 app_token"
  table_id:   "你的任务表 table_id"
  chat_id:    "你的 P2P 飞书 DM chat_id"

hermes:
  tenant: "你的 hermes 租户名"
  orchestrator_profile: "Judy"
  worker_profiles: ["bogo", "bonnie", "clawhauser", "stu"]

paths:
  state_dir: "/root/.hermes/cron/state"
  log_file:  "/tmp/larkwatch.log"
  tmux_session: "larkwatch"
```

### 4. 部署

```bash
bash scripts/install.sh
```

会做三件事：
1. 把脚本拷到 `~/.hermes/scripts/`
2. 调用 `/drive/v1/files/{token}/subscribe` 订阅多维表格事件
3. 注册两个 cron job：
   - `飞书任务表监听守护`（每 60 s，supervisor，保活 tmux + 重订阅）
   - `任务状态同步`（每 3 min，kanban → 飞书写回）

### 5. 验证

在飞书任务表里加一行 → ≤3 秒应收到飞书 DM 通知。
完成一个 kanban 任务 → ≤3 分钟应看到任务表"进展"字段更新。

## 仓库结构

```
.
├── README.md
├── SKILL.md                    # Hermes skill 入口
├── LICENSE
├── config.example.yaml
├── scripts/
│   ├── base_event_listener.py     # 实时事件解析 + 飞书 DM 通知
│   ├── base_watch_supervisor.py   # tmux 保活 + 自动重订阅
│   ├── kanban_watch.py            # kanban → 飞书写回（3 min 轮询）
│   └── install.sh                 # 一键安装
├── templates/
│   ├── feishu-base-schema.json    # 任务表字段定义（20 字段，含枚举选项）
│   ├── cron-jobs.yaml             # cron 清单
│   └── profile-prompts/           # orchestrator/worker prompt 模板
│       ├── orchestrator.md
│       └── worker.md
└── docs/
    ├── setup-guide.md          # 飞书后台权限/事件订阅图文步骤
    ├── architecture.md         # 架构数据流图 + 关键设计决策
    ├── multi-agent-profiles.md # Judy + 4 worker 编排约定
    └── troubleshooting.md      # 常见问题
```

## 设计要点

### 为什么要 tmux？

实测 `lark-cli event +subscribe` 在 systemd / Python subprocess 下 0.1 s 必死（即使加 `start_new_session=True` / `KillMode=process`），唯一可用方案是 tmux。

详见 [docs/architecture.md](docs/architecture.md) 的 "Process persistence pitfalls"。

### 为什么 drive 事件需要两步订阅？

仅在飞书后台勾选事件类型不够，**还要显式调 API 把具体文件挂到事件订阅**：

```bash
lark-cli api POST /open-apis/drive/v1/files/{file_token}/subscribe \
  --params '{"file_type":"bitable"}' --as bot
```

否则 lark-cli 即便 `Connected`，也只能收到 IM 类事件，收不到 drive 事件。

详见 [docs/setup-guide.md](docs/setup-guide.md)。

### 为什么 kanban → 飞书走轮询而不是事件？

Hermes Kanban 是本地状态机，没有外部事件总线。3 分钟轮询足够覆盖人类决策周期（用户看到通知→上下文切换→评估→反馈），无需更高频。

## 已踩过的坑

完整列表见 [docs/troubleshooting.md](docs/troubleshooting.md)。摘要：

| 现象 | 原因 |
|------|------|
| `99991672 action_scope_required` | 权限只勾了"用户身份"，没勾"应用身份"；或新版本未发布 |
| WebSocket Connected 但不收 drive 事件 | 没调 `/drive/files/{token}/subscribe` |
| lark-cli 启动后立即 exit code 2 | 已有另一个 `event +subscribe` 实例占着单例锁 |
| 进程在 systemd 下 0.1 s 必死 | 改用 tmux |
| 事件载荷里没有字段名 | 用 record-get API 拉详情时按字段名匹配 |

## License

MIT — see [LICENSE](LICENSE).

## Origin

This skill is extracted from a working production deployment in [tongche](https://github.com/tongche) tenant. Generalized and parameterized for public reuse.

If you build something on top of this, I'd love to hear about it. Issues & PRs welcome.
