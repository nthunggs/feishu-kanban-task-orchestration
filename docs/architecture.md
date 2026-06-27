# 架构说明

## 三个角色

```
┌────────────────────────┐    ┌────────────────────────┐    ┌────────────────────────┐
│      飞书多维表格      │    │     Hermes Kanban      │    │   Profile (Agents)     │
│   (任务管理 UI)        │    │   (执行队列)           │    │   (执行人)             │
│                        │    │                        │    │                        │
│  - 任务描述            │    │  status:               │    │  Judy (orchestrator)   │
│  - 进展                │    │    todo/ready/         │    │  bogo / bonnie /       │
│  - 复核状态            │    │    running/done/       │    │  clawhauser / stu      │
│  - 澄清记录            │    │    blocked             │    │  (workers)             │
│  - Kanban 链           │    │  comments:             │    │                        │
│  - 交付内容            │    │    [QUESTION]/[ANSWER] │    │                        │
└──────────┬─────────────┘    └──────────┬─────────────┘    └──────────┬─────────────┘
           │                             │                             │
           │  drive event WebSocket      │  spawn/result               │  spawn worker
           │  (秒级)                     │  (立即)                      │
           ▼                             ▼                             │
┌────────────────────────┐    ┌────────────────────────┐               │
│ base_event_listener    │    │   kanban_watch         │◀──────────────┘
│  - 解析 NDJSON         │    │  - 3 分钟轮询           │
│  - 拉记录              │    │  - 状态/QA 写回         │
│  - 发飞书 DM           │    │  - 链尾追加 ✅          │
└──────────┬─────────────┘    └──────────┬─────────────┘
           │                             │
           ▼                             ▼
   通知 orchestrator                飞书表自动更新
```

## 数据流：人创建任务 → worker 完成

1. **人在飞书表填一行**
   - 任务描述 / 负责人 / 重要紧急程度

2. **WebSocket 事件触发**
   - lark-cli event +subscribe 收到 NDJSON event
   - base_event_listener 解析 → 拉记录 → 发飞书 DM

3. **orchestrator 收到通知**
   - 看 DM 里的任务概要 → 查飞书表全文
   - 决定 spawn 哪个 worker → `hermes kanban create` 创建 kanban 任务（lark-cli/hermes v1：add 已改名 create）
   - 把 kanban id 写到飞书表「Kanban链」字段

4. **worker 跑任务**
   - kanban status: ready → running
   - 跑过程中遇到不清晰 → comment `[QUESTION] xxx`
   - 完成 → kanban status: done + result.summary + result.metadata.deliverable_url

5. **kanban_watch 把状态写回飞书**
   - done → 进展=已完成、复核状态=待复核、交付内容=URL
   - QUESTION → 澄清状态=待澄清、澄清记录追加
   - 链尾追加 ✅<kid>

6. **人在飞书表复核**
   - 复核状态：待复核 → 通过 / 有问题
   - 改动通过 WebSocket 事件再次回到 orchestrator
   - 「有问题」时 orchestrator spawn 修订任务（链尾追加 ✏️<kid>）

## 为什么用 tmux 而不是 systemd

实测过：

- `python subprocess.Popen(lark-cli, ...)` → lark-cli 在 0.1 秒内退出
- `nohup setsid lark-cli ...` → 进程被 SIGKILL/SIGTERM
- `systemd-run --scope ...` → service 起来但 socket 立刻断
- `systemd unit` → 类似 systemd-run，连接不稳

只有 tmux 工作得很好，原因：

- tmux server 是独立进程，独立 PTY，不受父进程信号影响
- lark-cli 似乎对 stdin/stdout 是不是 TTY 敏感
- tmux session detach 后 server 继续跑，pipeline 不会被 SIGHUP

## 为什么混合「实时事件 + 3 分钟轮询」

| 通道 | 延迟 | 信息粒度 | 触发方向 | 备注 |
|---|---|---|---|---|
| 飞书 WebSocket | 秒级 | 字段级 diff | 飞书 → 本地 | 只覆盖飞书侧变化 |
| kanban_watch | 3 分钟 | 任务级状态 | kanban → 飞书 | 覆盖 kanban 侧变化 |
| Hermes 内部 events | 立即 | 任务事件 | kanban → orchestrator | 用于 orchestrator 自动响应 |

三个通道各管一段：飞书的人工动作走 WebSocket(快)，kanban 状态走轮询(简单可靠)，agent 之间走 Hermes 内部事件(无需外部 API)。

## 配置中心化

所有可变参数都在 `config.yaml`：

- 飞书：base_token / table_id / chat_id / 字段 ID
- Kanban：tenant
- 路径：state file / log file / tmux 名

脚本通过 `_config.py` 加载，不写硬编码。fork 这个 skill 的人只改 config.yaml 即可。

## Profile 约定（参考实现，可改）

我自己用的一套：

| Profile | 角色 | 模型 |
|---|---|---|
| Judy | orchestrator(决定派给谁) | claude-opus 等贵的 |
| bogo | worker - 通用调研 | 中等 |
| bonnie | worker - 写作 | 中等 |
| clawhauser | worker - 流程 / 工作流 | 便宜 |
| stu | worker - 杂务 | 便宜 |

worker profile 在自己的 system prompt 里 ack：

- 收到 kanban 任务 → status: ready → running
- 不清楚要补一句 `[QUESTION]` comment → status: ready，等 [ANSWER]
- 完成 → status: done + deliverable URL + self_check_notes

## 复核硬规则

复核前必须跑 PRE-FLIGHT 检查（见 kanban-orchestrator skill）。
反馈里只要含「建议 / 下次」，状态必须置「有问题」（不能是「通过」），否则 worker 会以为没事，下次还犯同样错。
