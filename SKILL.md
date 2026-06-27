---
name: feishu-kanban-task-orchestration
description: Bộ hệ thống điều phối task hoàn chỉnh, biến "Feishu bitable" thành nguồn task, Hermes Kanban thành bus thực thi, orchestrator/worker profiles thành người thực thi. Bao gồm lắng nghe sự kiện Feishu thời gian thực (WebSocket via lark-cli), tự động ghi trạng thái kanban trở lại, đồng bộ hai chiều, quy ước field danh sách. Thông báo tức thì khi người dùng tạo/sửa task trong bảng task Feishu; ghi tiến độ, sản phẩm giao, trạng thái duyệt, QA trở lại Feishu khi task kanban tiến triển.
---

# Skill điều phối task Feishu ↔ Hermes Kanban

## Đây là gì

Một template hợp tác đa Agent biến "Feishu bitable" thành UI quản lý task, biến Hermes Kanban thành hàng đợi thực thi, biến orchestrator/worker profile thành thực thể thực thi.

```
[Người gõ chữ trong bảng Feishu] ──sự kiện thời gian thực──▶ [base_event_listener] ──thông báo──▶ [orchestrator profile]
                                                                      │
                                                              spawn worker
                                                                      ▼
[Bảng Feishu tự cập nhật] ◀──ghi trở lại polling 3min── [kanban_watch] ◀──trạng thái──── [Hermes Kanban]
```

Giám sát không tiêu tốn token LLM (cron + no_agent), lắng nghe sự kiện với độ trễ cỡ giây.

## Thành phần cốt lõi

| File | Công dụng | Cách kích hoạt |
|---|---|---|
| `scripts/base_event_listener.py` | Phân giải sự kiện WebSocket Feishu, lấy record, gửi DM | stdin pipe trong tmux |
| `scripts/base_watch_supervisor.py` | Giữ sống tmux + drive subscribe API | cron mỗi 60s |
| `scripts/kanban_watch.py` | Ghi trở lại kanban → bảng Feishu | cron mỗi 3 phút |
| `templates/feishu-base-schema.json` | Định nghĩa field của bảng task 20 field | tạo bảng một lần |
| `templates/cron-jobs.yaml` | Danh sách định nghĩa cronjob hermes | đăng ký cronjob hermes |
| `config.example.yaml` | Nơi tập trung toàn bộ tham số | sao chép thành config.yaml |

## Điều kiện tiên quyết

- Hermes Agent đã cài đặt (`hermes` CLI khả dụng)
- `lark-cli` đã cài đặt và hoàn tất liên kết OAuth (tham khảo skill `lark-shared`)
- Hermes app đã đăng ký các sự kiện `drive.file.bitable_record_changed_v1`, `im.message.receive_v1`, và đã tích `bitable:app:readonly` trong "danh tính ứng dụng"
- Hệ thống đã cài `tmux`, `python3`, `pyyaml`

## Khởi động nhanh (5 bước)

### 1. Tạo bảng task Feishu

Tham khảo `templates/feishu-base-schema.json` (20 field), dùng `lark-cli base +table-create` hoặc giao diện Feishu để tạo. Sau khi tạo xong chạy một lần:

```bash
lark-cli api GET /open-apis/bitable/v1/apps/<base_token>/tables/<table_id>/fields --as bot
```

Chép `field_id` của từng field vào mục `field_ids:` trong `config.yaml`.

### 2. Viết config.yaml

```bash
cp config.example.yaml config.yaml
# Sửa config.yaml, điền base_token / table_id / chat_id / field ID
```

### 3. Triển khai script

```bash
mkdir -p ~/.hermes/scripts ~/.hermes/cron/state
cp scripts/*.py ~/.hermes/scripts/
chmod +x ~/.hermes/scripts/*.py
```

`config.yaml` mặc định sẽ được nạp từ `../config.yaml` so với thư mục script, cũng có thể đặt tại `~/.hermes/feishu-kanban-task-orchestration/config.yaml`, hoặc dùng biến môi trường `FKTO_CONFIG=/path/to/config.yaml` để chỉ định.

### 4. Đăng ký task cron

Tham khảo `templates/cron-jobs.yaml` để đăng ký bằng hermes cron:

```bash
hermes cron add --no-agent --schedule "* * * * *"   --script ~/.hermes/scripts/base_watch_supervisor.py --name "Giám sát lắng nghe sự kiện Feishu"
hermes cron add --no-agent --schedule "*/3 * * * *" --script ~/.hermes/scripts/kanban_watch.py --name "Ghi trở lại Kanban→Feishu"
```

### 5. Kiểm thử

- Thêm/sửa một dòng trong bảng Feishu → sẽ nhận được DM trong vòng 1~3 giây
- Chạy một task kanban, đánh dấu done → trong 3 phút Tiến độ của dòng tương ứng trong bảng Feishu chuyển thành "Đã hoàn thành"

## Giải thích chi tiết luồng dữ liệu

Xem chi tiết `docs/architecture.md`. Các quy ước then chốt:

- Field **Chuỗi Kanban** ghi chuỗi kanban id của task: `t_a1b2c3d4 ← ✅t_e5f6g7h8`
- Field **Nhật ký làm rõ** là log Q/A nhiều vòng dạng append-only
- **Trạng thái duyệt** mặc định "Chờ duyệt" → sau khi người dùng duyệt đổi thành "Đạt" / "Có vấn đề" / "Đã sửa"
- worker gửi `[QUESTION]` / `[ANSWER]` trong comment kanban để kích hoạt luồng làm rõ

## Các bẫy đã biết (bắt buộc đọc)

Xem chi tiết `docs/setup-guide.md`. Hai cái dễ vướng nhất:

1. **Đăng ký sự kiện loại drive là hai bước**: tích chọn loại sự kiện trong trang quản trị Feishu mới chỉ là bước một, mỗi bảng còn phải gọi `POST /open-apis/drive/v1/files/{token}/subscribe?file_type=bitable` để gắn tường minh, nếu không sẽ chỉ nhận được sự kiện IM, không nhận được sự kiện bitable.
2. **"danh tính người dùng" ≠ "danh tính ứng dụng"**: quản lý quyền trong trang quản trị Feishu có hai cột, đăng ký sự kiện qua `--as bot` bắt buộc phải tích cột **danh tính ứng dụng**, sửa xong còn phải vào "phát hành ứng dụng" để tạo lại phiên bản và đưa lên online.

## Repo

Mã nguồn: https://github.com/erencoding/feishu-kanban-task-orchestration

## License

MIT
