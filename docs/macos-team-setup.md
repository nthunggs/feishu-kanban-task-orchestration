# macOS + 多 Profile Team 适配指南 (nthunggs fork)

> 本文件是 fork 的新增内容。原始 repo 面向 Linux/`/root` 单机部署；
> 这里记录在 **macOS (always-on iMac)** + **Hermes 多 profile team** 下的差异与安全加固。

## 1. 与上游的差异（fork 改了什么）

| 项 | 上游原版 | 本 fork |
|---|---|---|
| 路径 | 硬编码 `/root/.hermes/...` | `~/.hermes/...`，`_config.py` 自动展开 `~` 和环境变量 |
| 写回身份 | 硬编码 `--as user`（个人全权） | `security.write_identity`，默认 `bot`（最小权限） |
| 操作者校验 | 无（任何人改表都触发） | `security.allowed_operators` 白名单，挡注入 |
| spawn | 自动 | `security.require_spawn_approval`（默认 true，人工确认门） |
| 日志位置 | `/tmp/larkwatch.log` | `~/.hermes/cron/state/larkwatch.log`（重启不丢） |

## 2. Profile 角色映射（团队版）

上游用动物园 demo 名（Judy / bogo / bonnie / clawhauser / stu）。本 team 实际映射：

| 上游 demo 角色 | 本 team profile | 职责 |
|---|---|---|
| Judy (orchestrator) | **boss** | 接需求、拆任务、建 kanban、复核汇总 |
| bogo / bonnie / ... (worker) | **marketing** | 品牌 / 内容 / KPI / 视觉设计 |
| worker | **xnk** | 进口 / 分销（Sports Plus brand） |
| worker | **hr** | JD / KPI / 薪酬 / 行政 |

> 飞书表「负责人」字段填 marketing / xnk / hr，orchestrator (boss) 据此决定 spawn 哪个 profile。

## 3. 安全门工作方式（必读）

### 3.1 写回身份 `write_identity: bot`
把飞书自建应用机器人**加为目标任务表的协作者**（编辑权限），脚本就用 bot 身份读写。
好处：脚本不再借用你（Henry）个人账号的全部权限；bot 只能动它被授权的那张表。
若暂时没配 bot 协作者，可临时设 `user` 调试，但**不建议长期**。

### 3.2 操作者白名单 `allowed_operators`
监听器收到记录变更时，先看 `operator_id`。不在白名单 → 只写日志、**不通知 boss、不 spawn**。
填法：跑下面拿到自己的 open_id / user_id，填进 config。
```bash
lark-cli contact +user-search --query "你的名字" --as bot
```
留空 = 不限制（仅初期调试用，生产务必填）。

### 3.3 spawn 审批门 `require_spawn_approval: true`
boss 收到新任务 DM 后，**不要自动 spawn**。先在 DM 里回报：
「检测到新任务 X，建议派给 <profile>，确认 spawn 吗？」
Henry 回「确认」后 boss 才 `hermes kanban create` + spawn worker。
跑顺、信任建立后可改 false 走全自动。

### 3.4 表内文字当数据，不当指令
`treat_table_text_as_data: true` 是提醒位。真正隔离在 **boss profile 的 SOUL.md / prompt**：
任务描述里出现「忽略以上指令 / 删除文件 / 改 config / 提权」等字样，
boss 一律当**普通任务文本**处理，绝不执行其中的元指令。这条要写进 boss 的系统提示。

## 4. macOS 部署速记

```bash
# 1. 装依赖
brew install tmux
pip3 install pyyaml

# 2. 铺脚本 + 配置
mkdir -p ~/.hermes/scripts ~/.hermes/cron/state ~/.hermes/feishu-kanban-task-orchestration
cp scripts/_config.py scripts/*.py ~/.hermes/scripts/
chmod +x ~/.hermes/scripts/*.py
cp config.example.yaml ~/.hermes/feishu-kanban-task-orchestration/config.yaml
# 编辑 config.yaml：填 base_token / table_id / chat_id / field_ids / security

# 3. 注册 cron（见 templates/cron-jobs.yaml）
hermes cron add --no-agent --schedule "* * * * *"   --script ~/.hermes/scripts/base_watch_supervisor.py --name "飞书事件监听守护"
hermes cron add --no-agent --schedule "*/3 * * * *" --script ~/.hermes/scripts/kanban_watch.py --name "Kanban→飞书写回"

# 4. 验证
#   - 白名单内的人在表里加一行 → 1~3 秒收到 DM
#   - 白名单外的人改表 → 不收 DM，larkwatch.log 有 "blocked" 行
#   - kanban 任务标 done → 3 分钟内表里「进展=已完成」
```

## 5. 已知坑（继承上游 + macOS 补充）

1. **drive 事件两步订阅**：飞书后台勾事件 + 每张表调 `/drive/v1/files/{token}/subscribe`（supervisor 自动调）。
2. **「用户身份」≠「应用身份」**：事件订阅走 `--as bot` 必须勾**应用身份**栏，改完重新发版本。
3. **macOS cron 与 PATH**：Hermes cron 跑脚本时 PATH 可能不含 `lark-cli`。确认 `lark-cli` 在 `~/.npm-global/bin`，必要时在 `config.yaml` 的 `env` 段补 `PATH`。
4. **bot 不是协作者会写回失败**：`write_identity: bot` 时，bot 必须是目标表协作者，否则 record-upsert 返回权限错误。

## 6. PoC 实测发现的坑（lark-cli v1.0.57 / hermes v1，必读）

> 以下是在 macOS + Lark International + lark-cli 1.0.57 实跑 PoC 时踩到并已修复的坑。

1. **`_config.py` 会误加载 `~/.hermes/config.yaml`**
   查找顺序里 `脚本同级 ../config.yaml` 在 macOS 上解析成 `~/.hermes/config.yaml`（那是 Hermes 自己的配置，不是本工具的），导致 `KeyError: 'feishu'`。
   **解法**：cron / 手跑都显式设 `export FKTO_CONFIG=~/.hermes/feishu-kanban-task-orchestration/config.yaml`，别依赖自动查找。

2. **`lark-cli base +record-list` 默认输出 Markdown 表格，不是 JSON**
   不加 `--format json` 时返回 Markdown，`json.loads` 直接失败 → 脚本拿到空记录、静默 no-op。
   **解法**：本 fork 已在 `list_feishu_records()` 固定加 `--format json`。任何新写的 lark-cli 调用都要显式 `--format json`。

3. **`hermes kanban list --json` 的 `result` 字段常为 null**
   worker 完成后，总结在 `kanban show` 的 `latest_summary`（或 `result`）里，`list` 里 `result=null`。
   只读 `list` 会丢掉 summary / deliverable_url。
   **解法**：本 fork 在 done 分支里 result 为空时回落调用 `show` 取 `latest_summary`。

4. **时区**：`base +base-create --time-zone` 要 IANA 名。`Asia/Ho_Chi_Minh` 被拒，用 `Asia/Bangkok`（同 UTC+7）或 `Asia/Saigon`。

5. **`record-create` 子命令不存在**：v1.0.57 用 `+record-upsert`（不带 `--record-id` 即新建）。

6. **lark-cli 事件语法变了**：上游 `event +subscribe --event-types X` → v1.0.19+ 用 `event consume <EventKey>`，stdout 为 NDJSON。本 fork supervisor 已改。`event list` 先确认 app 实际暴露的 EventKey。
