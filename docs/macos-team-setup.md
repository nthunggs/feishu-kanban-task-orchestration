# Hướng dẫn thích ứng macOS + Team đa Profile (nthunggs fork)

> File này là nội dung bổ sung của bản fork. Repo gốc hướng tới triển khai đơn máy Linux/`/root`;
> ở đây ghi lại các khác biệt và phần gia cố bảo mật khi chạy trên **macOS (iMac always-on)** + **Hermes team đa profile**.

## 1. Khác biệt so với bản gốc (fork đã thay đổi những gì)

| Mục | Bản gốc upstream | Bản fork này |
|---|---|---|
| Đường dẫn | Hardcode `/root/.hermes/...` | `~/.hermes/...`, `_config.py` tự động mở rộng `~` và biến môi trường |
| Danh tính ghi ngược | Hardcode `--as user` (toàn quyền cá nhân) | `security.write_identity`, mặc định `bot` (quyền tối thiểu) |
| Kiểm tra người thao tác | Không có (bất kỳ ai sửa bảng đều kích hoạt) | Whitelist `security.allowed_operators`, chặn injection |
| spawn | Tự động | `security.require_spawn_approval` (mặc định true, cổng xác nhận thủ công) |
| Vị trí log | `/tmp/larkwatch.log` | `~/.hermes/cron/state/larkwatch.log` (không mất khi khởi động lại) |

## 2. Ánh xạ vai trò Profile (bản team)

Upstream dùng tên demo kiểu sở thú (Judy / bogo / bonnie / clawhauser / stu). Ánh xạ thực tế của team này:

| Vai trò demo upstream | Profile của team | Trách nhiệm |
|---|---|---|
| Judy (orchestrator) | **boss** | Nhận yêu cầu, chia task, tạo kanban, duyệt và tổng hợp |
| bogo / bonnie / ... (worker) | **marketing** | Thương hiệu / nội dung / KPI / thiết kế hình ảnh |
| worker | **xnk** | Nhập khẩu / phân phối (Sports Plus brand) |
| worker | **hr** | JD / KPI / lương thưởng / hành chính |

> Cột «Phụ trách» trong bảng Feishu điền marketing / xnk / hr, orchestrator (boss) dựa vào đó để quyết định spawn profile nào.

## 3. Cách hoạt động của các cổng bảo mật (bắt buộc đọc)

### 3.1 Danh tính ghi ngược `write_identity: bot`
Thêm bot ứng dụng tự xây của Feishu **làm cộng tác viên (quyền chỉnh sửa) của bảng task mục tiêu**, script sẽ dùng danh tính bot để đọc/ghi.
Lợi ích: script không còn mượn toàn bộ quyền của tài khoản cá nhân của bạn (Henry); bot chỉ có thể thao tác đúng bảng được cấp quyền.
Nếu tạm thời chưa cấu hình bot làm cộng tác viên, có thể đặt tạm `user` để debug, nhưng **không khuyến nghị dùng lâu dài**.

### 3.2 Whitelist người thao tác `allowed_operators`
Khi listener nhận sự kiện thay đổi record, trước tiên kiểm tra `operator_id`. Không nằm trong whitelist → chỉ ghi log, **không thông báo boss, không spawn**.
Cách điền: chạy lệnh dưới để lấy open_id / user_id của chính mình rồi điền vào config.
```bash
lark-cli contact +user-search --query "tên của bạn" --as bot
```
Để trống = không giới hạn (chỉ dùng debug giai đoạn đầu, production bắt buộc phải điền).

### 3.3 Cổng phê duyệt spawn `require_spawn_approval: true`
Sau khi boss nhận DM task mới, **không tự động spawn**. Trước tiên báo cáo trong DM:
«Phát hiện task mới X, đề xuất giao cho <profile>, xác nhận spawn không?»
Sau khi Henry trả lời «xác nhận» thì boss mới `hermes kanban create` + spawn worker.
Khi đã chạy trơn tru và xây được lòng tin, có thể đổi thành false để chạy hoàn toàn tự động.

### 3.4 Coi văn bản trong bảng là dữ liệu, không phải lệnh
`treat_table_text_as_data: true` là vị trí nhắc nhở. Cách ly thực sự nằm ở **SOUL.md / prompt của boss profile**:
nếu mô tả task xuất hiện các cụm như «bỏ qua các lệnh phía trên / xóa file / sửa config / nâng quyền»,
boss luôn xử lý chúng như **văn bản task thông thường**, tuyệt đối không thực thi các meta-lệnh bên trong. Điều này phải được viết vào system prompt của boss.

## 4. Ghi nhanh triển khai macOS

```bash
# 1. Cài dependency
brew install tmux
pip3 install pyyaml

# 2. Trải script + cấu hình
mkdir -p ~/.hermes/scripts ~/.hermes/cron/state ~/.hermes/feishu-kanban-task-orchestration
cp scripts/_config.py scripts/*.py ~/.hermes/scripts/
chmod +x ~/.hermes/scripts/*.py
cp config.example.yaml ~/.hermes/feishu-kanban-task-orchestration/config.yaml
# Sửa config.yaml: điền base_token / table_id / chat_id / field_ids / security

# 3. Đăng ký cron (xem templates/cron-jobs.yaml)
hermes cron add --no-agent --schedule "* * * * *"   --script ~/.hermes/scripts/base_watch_supervisor.py --name "Giám sát lắng nghe sự kiện Feishu"
hermes cron add --no-agent --schedule "*/3 * * * *" --script ~/.hermes/scripts/kanban_watch.py --name "Kanban→ghi ngược Feishu"

# 4. Kiểm chứng
#   - Người trong whitelist thêm một dòng vào bảng → nhận DM sau 1~3 giây
#   - Người ngoài whitelist sửa bảng → không nhận DM, larkwatch.log có dòng "blocked"
#   - Task kanban đánh dấu done → trong 3 phút bảng có «Tiến độ=Đã hoàn thành»
```

## 5. Các bẫy đã biết (kế thừa upstream + bổ sung cho macOS)

1. **Đăng ký sự kiện drive hai bước**: tick sự kiện ở backend Feishu + gọi `/drive/v1/files/{token}/subscribe` cho từng bảng (supervisor tự động gọi).
2. **«Danh tính người dùng» ≠ «danh tính ứng dụng»**: đăng ký sự kiện đi qua `--as bot` thì bắt buộc phải tick cột **danh tính ứng dụng**, sửa xong phải phát hành lại phiên bản.
3. **cron macOS và PATH**: khi Hermes cron chạy script, PATH có thể không chứa `lark-cli`. Xác nhận `lark-cli` nằm trong `~/.npm-global/bin`, nếu cần thì bổ sung `PATH` trong mục `env` của `config.yaml`.
4. **bot không phải cộng tác viên sẽ ghi ngược thất bại**: khi dùng `write_identity: bot`, bot phải là cộng tác viên của bảng mục tiêu, nếu không record-upsert sẽ trả lỗi quyền.

## 6. Các bẫy phát hiện khi chạy thực PoC (lark-cli v1.0.57 / hermes v1, bắt buộc đọc)

> Dưới đây là những bẫy đã gặp và đã khắc phục khi chạy thực PoC trên macOS + Lark International + lark-cli 1.0.57.

1. **`_config.py` sẽ nạp nhầm `~/.hermes/config.yaml`**
   Trong thứ tự tìm kiếm, `../config.yaml cùng cấp với script` trên macOS bị phân giải thành `~/.hermes/config.yaml` (đó là config của chính Hermes, không phải của tool này), gây ra `KeyError: 'feishu'`.
   **Cách xử lý**: dù chạy cron hay chạy tay đều đặt tường minh `export FKTO_CONFIG=~/.hermes/feishu-kanban-task-orchestration/config.yaml`, đừng dựa vào tìm kiếm tự động.

2. **`lark-cli base +record-list` mặc định xuất bảng Markdown, không phải JSON**
   Khi không thêm `--format json`, nó trả về Markdown, `json.loads` thất bại ngay → script nhận record rỗng, no-op âm thầm.
   **Cách xử lý**: bản fork này đã cố định thêm `--format json` trong `list_feishu_records()`. Mọi lệnh gọi lark-cli mới viết đều phải `--format json` tường minh.

3. **Trường `result` của `hermes kanban list --json` thường là null**
   Sau khi worker hoàn thành, phần tóm tắt nằm trong `latest_summary` (hoặc `result`) của `kanban show`, còn `result` trong `list` thì `result=null`.
   Chỉ đọc `list` sẽ mất summary / deliverable_url.
   **Cách xử lý**: bản fork này trong nhánh done, khi result rỗng sẽ fallback gọi `show` để lấy `latest_summary`.

4. **Múi giờ**: `base +base-create --time-zone` cần tên IANA. `Asia/Ho_Chi_Minh` bị từ chối, dùng `Asia/Bangkok` (cùng UTC+7) hoặc `Asia/Saigon`.

5. **Không tồn tại subcommand `record-create`**: v1.0.57 dùng `+record-upsert` (không kèm `--record-id` nghĩa là tạo mới).

6. **Cú pháp event của lark-cli đã đổi**: upstream `event +subscribe --event-types X` → từ v1.0.19+ dùng `event consume <EventKey>`, stdout là NDJSON. Supervisor của bản fork này đã sửa. Chạy `event list` trước để xác nhận EventKey mà app thực sự expose.
