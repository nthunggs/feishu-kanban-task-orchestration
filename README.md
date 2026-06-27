# Feishu Kanban Task Orchestration

Real-time **Feishu (Lark) bitable** + **Hermes Kanban** task orchestration system. Multi-agent worker profiles, real-time event listening, automatic bidirectional sync.

A Skill package for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

> **🍴 nthunggs fork — bản thích ứng cho macOS + team đa profile**
> Fork này bổ sung thích ứng macOS và tăng cường bảo mật trên nền upstream: tự động mở rộng đường dẫn `~`, ghi dữ liệu trở lại bằng danh tính bot quyền tối thiểu,
> thêm whitelist người thao tác và cổng phê duyệt spawn. Xem chi tiết tại **[docs/macos-team-setup.md](docs/macos-team-setup.md)**.

## Là gì

Lắp ráp "Feishu bitable làm bảng task, Hermes Kanban làm hàng đợi task, nhiều worker profile làm người thực thi" thành một pipeline tự động hóa:

- **Tạo một dòng mới trong bảng task Feishu** → đẩy thông báo ≤3 giây → orchestrator phân rã → spawn worker
- **worker hoàn thành task** → tự động ghi trở lại bảng Feishu trong ≤3 phút (tiến độ/sản phẩm giao/trạng thái duyệt/nhật ký làm rõ)
- **worker để lại `[QUESTION]`** → tự động đồng bộ vào field "Nhật ký làm rõ" của Feishu, chờ người dùng trả lời `[ANSWER]`
- Nhiều agent chạy song song (mặc định cấu hình 1 orchestrator + 4 worker profile)

Không tiêu tốn LLM liên tục (lắng nghe qua kết nối dài WebSocket, ghi trở lại qua cron polling, đều là script `no_agent`).

## Luồng dữ liệu

```
Feishu bitable (bảng task)
   │ ↑
   │ │ Ghi trở lại (kanban_watch.py, cron 3 min)
   │ ↓
   │ ├─ Tiến độ / Thời gian hoàn thành / Tóm tắt task
   │ ├─ Nội dung giao (feishu doc URL)
   │ ├─ Trạng thái duyệt / Phản hồi duyệt
   │ └─ Nhật ký làm rõ ([QUESTION]/[ANSWER])
   │
   │ Đẩy thời gian thực (base_event_listener.py, WebSocket)
   ↓
Feishu DM (người dùng) → gọi orchestrator
   │
   ↓
Hermes Kanban
   │
   ├─→ orchestrator profile (như Judy) phân rã + phân phối
   └─→ worker profiles (như bogo / bonnie / clawhauser / stu) thực thi
```

## Bắt đầu nhanh

### 1. Phụ thuộc cần có trước

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) đã cài đặt và cấu hình tenant
- [lark-cli](https://github.com/larksuite/cli) v1.0.19+ đã cài đặt và liên kết với ứng dụng tự xây Feishu
- `tmux` khả dụng
- Ứng dụng tự xây Feishu có:
  - Quyền **danh tính ứng dụng**: `bitable:app:readonly`, `im:message:send_as_bot`
  - Đăng ký sự kiện (chế độ kết nối dài): `drive.file.bitable_record_changed_v1`
  - Robot ứng dụng là cộng tác viên của bitable mục tiêu

> Các bước cấu hình chi tiết trong trang quản trị Feishu xem [docs/setup-guide.md](docs/setup-guide.md).

### 2. Tạo bảng task

Theo [`templates/feishu-base-schema.json`](templates/feishu-base-schema.json) tạo một bitable mới trên Feishu, gồm 20 field (Mô tả task, Phụ trách, Tiến độ, Trạng thái duyệt…).

Hoặc tái sử dụng bảng đã có, chỉ cần căn chỉnh tên field theo template (field_id không cần giống nhau, script sẽ phân giải theo tên field).

### 3. Cấu hình

Sao chép `config.example.yaml` thành `config.yaml`, điền vào:

```yaml
feishu:
  base_token: "app_token bitable của bạn"
  table_id:   "table_id bảng task của bạn"
  chat_id:    "chat_id Feishu DM P2P của bạn"

hermes:
  tenant: "tên tenant hermes của bạn"
  orchestrator_profile: "Judy"
  worker_profiles: ["bogo", "bonnie", "clawhauser", "stu"]

paths:
  state_dir: "/root/.hermes/cron/state"
  log_file:  "/tmp/larkwatch.log"
  tmux_session: "larkwatch"
```

### 4. Triển khai

```bash
bash scripts/install.sh
```

Sẽ làm ba việc:
1. Sao chép script vào `~/.hermes/scripts/`
2. Gọi `/drive/v1/files/{token}/subscribe` để đăng ký sự kiện bitable
3. Đăng ký hai cron job:
   - `Giám sát lắng nghe bảng task Feishu` (mỗi 60 s, supervisor, giữ sống tmux + đăng ký lại)
   - `Đồng bộ trạng thái task` (mỗi 3 min, ghi trở lại kanban → Feishu)

### 5. Kiểm thử

Thêm một dòng vào bảng task Feishu → trong ≤3 giây sẽ nhận được thông báo Feishu DM.
Hoàn thành một task kanban → trong ≤3 phút sẽ thấy field "Tiến độ" của bảng task được cập nhật.

## Cấu trúc repo

```
.
├── README.md
├── SKILL.md                    # Điểm vào skill Hermes
├── LICENSE
├── config.example.yaml
├── scripts/
│   ├── base_event_listener.py     # Phân giải sự kiện thời gian thực + thông báo Feishu DM
│   ├── base_watch_supervisor.py   # Giữ sống tmux + tự động đăng ký lại
│   ├── kanban_watch.py            # Ghi trở lại kanban → Feishu (polling 3 min)
│   └── install.sh                 # Cài đặt một lệnh
├── templates/
│   ├── feishu-base-schema.json    # Định nghĩa field bảng task (20 field, kèm tùy chọn enum)
│   ├── cron-jobs.yaml             # Danh sách cron
│   └── profile-prompts/           # Template prompt orchestrator/worker
│       ├── orchestrator.md
│       └── worker.md
└── docs/
    ├── setup-guide.md          # Các bước minh họa quyền/đăng ký sự kiện trong trang quản trị Feishu
    ├── architecture.md         # Sơ đồ luồng dữ liệu kiến trúc + các quyết định thiết kế then chốt
    ├── multi-agent-profiles.md # Quy ước điều phối Judy + 4 worker
    └── troubleshooting.md      # Các vấn đề thường gặp
```

## Điểm thiết kế chính

### Tại sao cần tmux?

Thực nghiệm cho thấy `lark-cli event +subscribe` chết chắc trong 0.1 s khi chạy dưới systemd / Python subprocess (kể cả khi thêm `start_new_session=True` / `KillMode=process`), phương án duy nhất khả dụng là tmux.

Xem chi tiết "Process persistence pitfalls" trong [docs/architecture.md](docs/architecture.md).

### Tại sao sự kiện drive cần đăng ký hai bước?

Chỉ tích chọn loại sự kiện trong trang quản trị Feishu là chưa đủ, **còn phải gọi API tường minh để gắn file cụ thể vào đăng ký sự kiện**:

```bash
lark-cli api POST /open-apis/drive/v1/files/{file_token}/subscribe \
  --params '{"file_type":"bitable"}' --as bot
```

Nếu không, dù lark-cli báo `Connected`, cũng chỉ nhận được sự kiện loại IM, không nhận được sự kiện drive.

Xem chi tiết [docs/setup-guide.md](docs/setup-guide.md).

### Tại sao kanban → Feishu dùng polling chứ không dùng sự kiện?

Hermes Kanban là máy trạng thái cục bộ, không có bus sự kiện bên ngoài. Polling 3 phút đủ bao phủ chu kỳ ra quyết định của con người (người dùng thấy thông báo → chuyển ngữ cảnh → đánh giá → phản hồi), không cần tần suất cao hơn.

## Những cái bẫy đã gặp

Danh sách đầy đủ xem [docs/troubleshooting.md](docs/troubleshooting.md). Tóm tắt:

| Hiện tượng | Nguyên nhân |
|------|------|
| `99991672 action_scope_required` | Quyền chỉ tích "danh tính người dùng", chưa tích "danh tính ứng dụng"; hoặc phiên bản mới chưa phát hành |
| WebSocket Connected nhưng không nhận sự kiện drive | Chưa gọi `/drive/files/{token}/subscribe` |
| lark-cli khởi động xong là exit code 2 ngay | Đã có một instance `event +subscribe` khác đang giữ khóa singleton |
| Tiến trình chết chắc trong 0.1 s dưới systemd | Đổi sang dùng tmux |
| Payload sự kiện không có tên field | Khi gọi API record-get lấy chi tiết thì khớp theo tên field |

## License

MIT — see [LICENSE](LICENSE).

## Origin

This skill is extracted from a working production deployment in [tongche](https://github.com/tongche) tenant. Generalized and parameterized for public reuse.

If you build something on top of this, I'd love to hear about it. Issues & PRs welcome.
