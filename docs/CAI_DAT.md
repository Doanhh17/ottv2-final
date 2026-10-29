# Chạy thử website trên máy cá nhân

Dành cho người tổ chức muốn chạy một trận mẫu trước khi dùng VPS. Làm xong tài liệu này, bạn mở được website tại `http://localhost:8765`, đăng nhập, xem kết quả và replay. Không cần tài khoản Google hoặc VPS cho lần thử này.

## 1. Chuẩn bị máy

Hoàn thành [MOI_TRUONG.md](MOI_TRUONG.md) trên máy đang dùng. Trong Windows, mở Ubuntu bằng PowerShell:

```powershell
wsl -d Ubuntu-24.04
```

**Cần thấy:** dấu nhắc dạng `ten@may:~$`. Nếu chưa mở được Ubuntu, xử lý ở MOI_TRUONG trước; không dán lệnh Linux vào PowerShell.

## 2. Đưa mã nguồn vào Ubuntu

Trong **Ubuntu**, lấy nguồn dự án:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/Doanhh17/ottv2-final.git ~/ottv2-project
cd ~/ottv2-project
sha256sum -c SHA256SUMS
```

**Cần thấy:** tất cả dòng đều `OK`, thư mục hiện tại chứa README.md, ottv2, scripts. Nếu thư mục `~/ottv2-project` đã có bản đang dùng, chọn thư mục clone mới, không ghi đè.

Từ đây, mọi lệnh Ubuntu trong tài liệu chạy tại `~/ottv2-project`.

## 3. Chuẩn bị một chỗ chấm thử

Trong **Ubuntu**, khi chưa có máy chấm khác đang chạy:

```bash
cd ~/ottv2-project
python3 -m ottv2.worker_pool plan --slots 1 --cpu-mode shared --output output/isolate-local.conf
sudo cp --no-clobber /usr/local/etc/isolate /usr/local/etc/isolate.before-local
sudo install -m 644 output/isolate-local.conf /usr/local/etc/isolate
sudo systemctl start isolate.service
isolate --check-config
python3 scripts/isolate_smoke.py
```

**Cần thấy:** `slots: 1`, `cpu_mode: shared`, phép kiểm Isolate báo `OK`. Bot được dùng chung CPU; không cần một nhân riêng cho mỗi bot. Nếu plan báo thiếu RAM/CPU thì đọc mức yêu cầu trước khi tiếp tục. Nếu tệp cấu hình đã tồn tại, kiểm bản trước thay vì tạo đè.

## 4. Tạo bộ tài khoản và bot mẫu

Trong **Ubuntu**:

```bash
python3 -m ottv2.prepare_local --demo --directory output/local-demo
```

**Cần thấy:** `Đã chuẩn bị bản thử`. Lệnh chuẩn bị ba bot mẫu và bốn tài khoản mới dưới đây:

| Tài khoản | Vai trò | Mật khẩu mẫu |
| --- | --- | --- |
| admin | Quản trị viên | OTTv2-demo-2026 |
| student01 | Sinh viên | OTTv2-demo-2026 |
| student02 | Sinh viên | OTTv2-demo-2026 |
| student03 | Sinh viên | OTTv2-demo-2026 |

Nếu tài khoản đã có và đã đổi mật khẩu, lệnh không đặt lại mật khẩu. Đây là bộ dữ liệu trình diễn trong `output/local-demo`, không dùng mật khẩu mẫu để công khai giải đấu.

## 5. Bật website

Trong **Ubuntu**:

```bash
python3 -m ottv2.local_arena --db output/local-demo/queue.sqlite3 --accounts output/local-demo/accounts.sqlite3 --port 8765
```

**Cần thấy:** `started`. Giữ cửa sổ đó mở, rồi dùng trình duyệt Windows vào **http://localhost:8765**.

Nếu trang không mở, mở một cửa sổ Ubuntu khác và kiểm:

```bash
curl --fail --show-error http://127.0.0.1:8765/health
```

**Cần thấy:** JSON có `"ok": true`. Nếu `Address already in use`, cổng 8765 đã có chương trình chạy; dùng `ss -ltnp 'sport = :8765'` để xác định và dừng đúng phiên đang chiếm cổng, không mở thêm bản thứ hai.

## 6. Thử một trận và replay

1. Đăng nhập student01, mở **Thi đấu**, bấm **Tìm trận**.
2. Hệ thống có thể chọn bot mẫu khác làm bên phòng thủ. Nếu chưa ghép, dùng cửa sổ ẩn danh đăng nhập student02 và bấm Tìm trận.
3. Mở **Kết quả**, đợi trang tự kiểm tra hoặc bấm Làm mới. Cần thấy trận hoàn tất và Elo thay đổi một lần.
4. Mở tệp `replay/index.html` bằng trình duyệt. Có thể dùng Explorer để chép riêng HTML ra Windows, hoặc tải replay từ Drive trong Tài liệu.
5. Bấm **Sao chép log**, dán vào replay, kiểm bàn đầu/cuối và nút trước/tiếp. Sau khi có HTML và log, ngắt mạng vẫn xem lại được.

Hai tài khoản cần hai cửa sổ trình duyệt độc lập; hai tab thường dùng chung phiên đăng nhập. Cách thao tác và quy định thi đấu nằm trong [hướng dẫn sinh viên](QUY_DINH_SINH_VIEN.md).

Nếu trận chưa xong, kiểm ở **Ubuntu thứ hai**:

```bash
cd ~/ottv2-project
python3 -m ottv2.queue_admin --db output/local-demo/queue.sqlite3 status
```

`waiting` là số người chờ ghép; `busy_players` là số bot đã có trận. Xem thêm cửa sổ chạy website để biết lỗi chấm.

## 7. Dừng và bật lại hôm sau

Nhấn **Ctrl+C** ở cửa sổ website, chờ chương trình dừng. Dữ liệu được giữ. Lần sau mở Ubuntu và chạy:

```bash
cd ~/ottv2-project
sudo systemctl start isolate.service
python3 -m ottv2.local_arena --db output/local-demo/queue.sqlite3 --accounts output/local-demo/accounts.sqlite3 --port 8765
```

Không tạo lại dữ liệu mỗi ngày. Bản local này không tự lấy tài khoản hoặc khóa Google của website VPS. Nếu máy chỉ cần chấm cho website đã host, dùng [MAY_CHAM.md](MAY_CHAM.md); không bật local_arena để thay cho máy chấm.

Chạy local đạt rồi mới sang [TRIEN_KHAI_VPS.md](TRIEN_KHAI_VPS.md). Các lỗi WSL/Isolate tra ở [MOI_TRUONG.md](MOI_TRUONG.md).
