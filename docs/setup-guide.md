# Hướng dẫn triển khai

## 0. Dependency tiên quyết

- Máy chủ Linux (bất kỳ môi trường nào chạy được cron và tmux)
- Python 3.9+, `pip install pyyaml`
- `tmux` (`apt install tmux` / `yum install tmux`)
- Hermes Agent đã cài và `hermes` CLI dùng được
- `lark-cli` đã cài và hoàn tất binding (tham khảo [lark-shared](https://hermes-agent.nousresearch.com/))

## 1. Chuẩn bị quyền ở backend Feishu (dễ vướng bẫy nhất)

Vào https://open.feishu.cn/app tìm app của bạn, làm ba việc dưới đây:

### 1.1 Thêm quyền

Vào «Quản lý quyền», **tick cả hai cột**:

| Quyền | Danh tính người dùng | Danh tính ứng dụng |
|---|---|---|
| `bitable:app:readonly` | ✓ | ✓ |
| `im:message` (gửi tin nhắn) | ✓ | ✓ |
| `drive:drive:readonly` hoặc `drive:file:readonly` | ✓ | ✓ |

⚠️ **«Danh tính người dùng» và «danh tính ứng dụng» là hai cột quyền khác nhau**. Đăng ký sự kiện đi qua `--as bot` thì bắt buộc tick cột **danh tính ứng dụng**. Đây là nguyên nhân phổ biến nhất của lỗi 99991672 permission denied.

### 1.2 Đăng ký sự kiện

Vào «Đăng ký sự kiện», đăng ký hai sự kiện này:

- `drive.file.bitable_record_changed_v1` (thay đổi record bảng nhiều chiều)
- `im.message.receive_v1` (nhận tin nhắn IM, tùy chọn, dùng để phản hồi khi bị @ nhắc tên)

### 1.3 Tạo lại phiên bản và phát hành

Sau khi sửa xong quyền và đăng ký sự kiện, bắt buộc vào «Phát hành ứng dụng» **tạo lại một phiên bản mới** và lên sóng, nếu không thay đổi sẽ không có hiệu lực.

---

## 2. Tạo bảng task

Tham khảo `templates/feishu-base-schema.json`, dùng `lark-cli` hoặc giao diện Feishu để tạo một bảng 20 cột.

Tạo xong thì chạy:

```bash
lark-cli api GET \
  /open-apis/bitable/v1/apps/<base_token>/tables/<table_id>/fields \
  --as bot
```

Chép từng `field.field_id` vào mục `field_ids:` của `config.yaml`.

---

## 3. Đăng ký tường minh file bitable (chỗ dễ vướng bẫy thứ hai)

Chỉ tick loại sự kiện ở backend Feishu là **chưa đủ**. Sự kiện loại drive còn cần đăng ký tường minh cho từng bảng:

```bash
lark-cli api POST \
  /open-apis/drive/v1/files/<base_token>/subscribe \
  --params '{"file_type":"bitable"}' \
  --as bot
```

Trả về `{"code":0, "msg":"Success"}` là thành công. `base_watch_supervisor.py` khi khởi động tmux sẽ tự động gọi API này một lần, nên chỉ cần supervisor cron đã chạy thì không phải gọi thủ công.

⚠️ Nếu bỏ sót bước này, listener chỉ nhận được sự kiện IM, **không nhận được bất kỳ sự kiện bitable nào**. Đây là bẫy mà tài liệu lark-cli không nói rõ.

---

## 4. Cài đặt script

```bash
mkdir -p ~/.hermes/scripts ~/.hermes/cron/state ~/.hermes/feishu-kanban-task-orchestration
cp scripts/_config.py scripts/*.py ~/.hermes/scripts/
chmod +x ~/.hermes/scripts/*.py
cp config.example.yaml ~/.hermes/feishu-kanban-task-orchestration/config.yaml
# Sửa ~/.hermes/feishu-kanban-task-orchestration/config.yaml, điền giá trị thực tế
```

`_config.py` sẽ tìm config.yaml theo thứ tự này:

1. Biến môi trường `$FKTO_CONFIG`
2. `../config.yaml` cùng cấp với script
3. `~/.hermes/feishu-kanban-task-orchestration/config.yaml`
4. `/etc/feishu-kanban-task-orchestration/config.yaml`

---

## 5. Lần đầu khởi động supervisor thủ công để test

```bash
python3 ~/.hermes/scripts/base_watch_supervisor.py
# Sẽ thấy:
# [HH:MM:SS] tmux session missing, restarting...
# [HH:MM:SS] drive subscribe ok
# [HH:MM:SS] tmux session 'larkwatch' started

tmux ls
# Sẽ thấy: larkwatch: 1 windows ...

tail -f /tmp/larkwatch.log
# Sẽ thấy log kết nối của lark-cli (connected to wss://...)
# Sau đó vào bảng Feishu thêm một dòng, trong vài giây listener sẽ parse được sự kiện
```

---

## 6. Đăng ký cron

Tham khảo `templates/cron-jobs.yaml`:

```bash
hermes cron add --no-agent --schedule "* * * * *" \
  --script ~/.hermes/scripts/base_watch_supervisor.py \
  --name "Giám sát lắng nghe sự kiện Feishu (giữ tmux sống)"

hermes cron add --no-agent --schedule "*/3 * * * *" \
  --script ~/.hermes/scripts/kanban_watch.py \
  --name "Kanban → ghi ngược Feishu"
```

Cũng có thể tạo bằng hermes UI, kết quả như nhau.

---

## 7. Khắc phục sự cố

### tmux session không khởi động được

```bash
tmux kill-session -t larkwatch 2>/dev/null
python3 ~/.hermes/scripts/base_watch_supervisor.py
tmux ls
```

Nếu vẫn không khởi động được, phần lớn là bản thân `lark-cli` có vấn đề:

```bash
LARK_CLI_NO_PROXY=1 lark-cli event +subscribe \
  --as bot --event-types drive.file.bitable_record_changed_v1
# Xem thông báo lỗi
```

### Nhận được sự kiện IM nhưng không nhận được sự kiện bitable

99% là chưa làm bước 3 đăng ký tường minh. Gọi lại thủ công API drive subscribe một lần nữa.

### bot báo 99991672 permission denied

Quay lại bước 1.1, xác nhận cột **danh tính ứng dụng** đã tick `bitable:app:readonly`, và bước 1.3 đã tạo lại phiên bản và lên sóng.

### kanban_watch không ghi ngược được

```bash
# Chạy trực tiếp một lần để xem lỗi
python3 ~/.hermes/scripts/kanban_watch.py
# Kiểm tra hermes kanban CLI có xuất được JSON không
hermes kanban list --tenant <tenant của bạn> --json | head -20
```

### lark-cli đi qua proxy báo [WARN] proxy detected

Đặt `env.LARK_CLI_NO_PROXY: "1"` (trong config.yaml đã đặt sẵn mặc định).
