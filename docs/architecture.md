# Mô tả kiến trúc

## Ba vai trò

```
┌────────────────────────┐    ┌────────────────────────┐    ┌────────────────────────┐
│      Feishu bitable    │    │     Hermes Kanban      │    │   Profile (Agents)     │
│   (UI quản lý task)    │    │   (hàng đợi thực thi)  │    │   (người thực thi)     │
│                        │    │                        │    │                        │
│  - Mô tả task          │    │  status:               │    │  Judy (orchestrator)   │
│  - Tiến độ             │    │    todo/ready/         │    │  bogo / bonnie /       │
│  - Trạng thái duyệt    │    │    running/done/       │    │  clawhauser / stu      │
│  - Nhật ký làm rõ      │    │    blocked             │    │  (workers)             │
│  - Chuỗi Kanban        │    │  comments:             │    │                        │
│  - Nội dung giao       │    │    [QUESTION]/[ANSWER] │    │                        │
└──────────┬─────────────┘    └──────────┬─────────────┘    └──────────┬─────────────┘
           │                             │                             │
           │  drive event WebSocket      │  spawn/result               │  spawn worker
           │  (cỡ giây)                  │  (tức thì)                   │
           ▼                             ▼                             │
┌────────────────────────┐    ┌────────────────────────┐               │
│ base_event_listener    │    │   kanban_watch         │◀──────────────┘
│  - Phân giải NDJSON    │    │  - Polling 3 phút       │
│  - Lấy record          │    │  - Ghi trở lại status/QA│
│  - Gửi Feishu DM       │    │  - Nối ✅ vào cuối chuỗi │
└──────────┬─────────────┘    └──────────┬─────────────┘
           │                             │
           ▼                             ▼
   Thông báo orchestrator           Bảng Feishu tự cập nhật
```

## Luồng dữ liệu: người tạo task → worker hoàn thành

1. **Người điền một dòng trong bảng Feishu**
   - Mô tả task / Phụ trách / Mức ưu tiên

2. **Sự kiện WebSocket kích hoạt**
   - lark-cli event +subscribe nhận NDJSON event
   - base_event_listener phân giải → lấy record → gửi Feishu DM

3. **orchestrator nhận thông báo**
   - Xem tóm tắt task trong DM → tra toàn văn bảng Feishu
   - Quyết định spawn worker nào → `hermes kanban create` để tạo task kanban (lark-cli/hermes v1: add đã đổi tên thành create)
   - Ghi kanban id vào field "Chuỗi Kanban" của bảng Feishu

4. **worker chạy task**
   - kanban status: ready → running
   - Trong quá trình chạy gặp chỗ chưa rõ → comment `[QUESTION] xxx`
   - Hoàn thành → kanban status: done + result.summary + result.metadata.deliverable_url

5. **kanban_watch ghi trạng thái trở lại Feishu**
   - done → Tiến độ=Đã hoàn thành, Trạng thái duyệt=Chờ duyệt, Nội dung giao=URL
   - QUESTION → Trạng thái làm rõ=Chờ làm rõ, nối thêm vào Nhật ký làm rõ
   - Nối ✅<kid> vào cuối chuỗi

6. **Người duyệt trong bảng Feishu**
   - Trạng thái duyệt: Chờ duyệt → Đạt / Có vấn đề
   - Thay đổi quay lại orchestrator qua sự kiện WebSocket một lần nữa
   - Khi "Có vấn đề", orchestrator spawn task sửa đổi (nối ✏️<kid> vào cuối chuỗi)

## Tại sao dùng tmux mà không dùng systemd

Đã thực nghiệm:

- `python subprocess.Popen(lark-cli, ...)` → lark-cli thoát trong vòng 0.1 giây
- `nohup setsid lark-cli ...` → tiến trình bị SIGKILL/SIGTERM
- `systemd-run --scope ...` → service khởi lên nhưng socket đứt ngay lập tức
- `systemd unit` → tương tự systemd-run, kết nối không ổn định

Chỉ có tmux hoạt động tốt, lý do:

- tmux server là tiến trình độc lập, PTY độc lập, không chịu ảnh hưởng tín hiệu từ tiến trình cha
- lark-cli có vẻ nhạy cảm với việc stdin/stdout có phải TTY hay không
- Sau khi tmux session detach, server vẫn tiếp tục chạy, pipeline không bị SIGHUP

## Tại sao kết hợp "sự kiện thời gian thực + polling 3 phút"

| Kênh | Độ trễ | Độ chi tiết thông tin | Hướng kích hoạt | Ghi chú |
|---|---|---|---|---|
| Feishu WebSocket | cỡ giây | diff mức field | Feishu → cục bộ | Chỉ bao phủ thay đổi phía Feishu |
| kanban_watch | 3 phút | trạng thái mức task | kanban → Feishu | Bao phủ thay đổi phía kanban |
| Hermes internal events | tức thì | sự kiện task | kanban → orchestrator | Dùng cho orchestrator tự động phản hồi |

Ba kênh mỗi kênh phụ trách một đoạn: thao tác thủ công của người trên Feishu đi qua WebSocket (nhanh), trạng thái kanban đi qua polling (đơn giản đáng tin cậy), giữa các agent đi qua Hermes internal events (không cần API bên ngoài).

## Tập trung hóa cấu hình

Tất cả tham số có thể thay đổi đều nằm trong `config.yaml`:

- Feishu: base_token / table_id / chat_id / field ID
- Kanban: tenant
- Đường dẫn: state file / log file / tên tmux

Script nạp qua `_config.py`, không viết hardcode. Người fork skill này chỉ cần sửa config.yaml là được.

## Quy ước Profile (cài đặt tham khảo, có thể đổi)

Một bộ tôi tự dùng:

| Profile | Vai trò | Model |
|---|---|---|
| Judy | orchestrator (quyết định giao cho ai) | claude-opus v.v. loại đắt |
| bogo | worker - nghiên cứu tổng quát | trung bình |
| bonnie | worker - viết lách | trung bình |
| clawhauser | worker - quy trình / workflow | rẻ |
| stu | worker - việc vặt | rẻ |

worker profile ack trong system prompt của chính nó:

- Nhận task kanban → status: ready → running
- Chưa rõ thì bổ sung một comment `[QUESTION]` → status: ready, chờ [ANSWER]
- Hoàn thành → status: done + deliverable URL + self_check_notes

## Quy tắc cứng khi duyệt

Trước khi duyệt bắt buộc phải chạy kiểm tra PRE-FLIGHT (xem skill kanban-orchestrator).
Trong phản hồi chỉ cần có chứa "đề xuất / lần sau", trạng thái bắt buộc phải đặt là "Có vấn đề" (không được là "Đạt"), nếu không worker sẽ tưởng là ổn, lần sau vẫn mắc lỗi cũ.
