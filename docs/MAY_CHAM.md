# Cài máy chấm và bật bằng Desktop

**Lần đầu:** cài Ubuntu, Isolate, mã nguồn, khóa riêng và dịch vụ; cuối cùng tạo nút trên Desktop. **Những lần sau:** bấm đúp nút đó, nhập mật khẩu Ubuntu nếu được hỏi. Không dán lại các lệnh cài đặt.

## 1. Chọn đúng máy và nút bật

| Máy | Mã xác thực | Số trận song song | Tên chọn khi tạo nút |
| --- | --- | ---: | --- |
| Máy local | worker-local-pool | 6 | worker_local |
| Máy Sinh | worker-pc02 | 12 | worker_sinh |
| Máy Hải | worker-pc03 | 6 | worker_hai |
| Máy Dương | worker-pc04 | 12 | worker_duong |
| VPS | worker-vps | 4 | Không cần nút Desktop |

VPS chạy thường trực 4 trận bằng `ottv2-vps-worker.service`, tự bật cùng VPS và tự chạy lại nếu tiến trình lỗi. Bình thường có thể chỉ dùng VPS; đông người thì bật thêm PC. Các máy cùng nhận việc từ một hàng đợi; không cần đổi URL hoặc cấp lại khóa mỗi ngày. Máy local hiện được chọn chạy cùng VPS.

Bốn PC có 36 chỗ chấm; cộng VPS là tối đa 40 chỗ khi đủ máy online. Đây là số chỗ cấu hình, không phải cam kết thời gian chờ. Luật chung: **7 giây CPU/bot, 2,5 giây thực/lượt, 25 nước mỗi bên, 64 MiB RAM**.

## 2. Đã cài xong: chỉ bật nút Desktop

1. Cắm nguồn và bật mạng; không để Windows ngủ trong lúc chấm.
2. Bấm đúp `OTTv2 - worker_sinh.cmd` hoặc nút đúng máy trong bảng. Máy local dùng `OTTv2 - worker_local.cmd`.
3. Nếu hỏi mật khẩu, nhập mật khẩu **Ubuntu**, nhấn Enter. Khi gõ không hiện ký tự là bình thường.
4. Cần thấy `active (running)` nếu dùng dịch vụ; hoặc `pool_started` nếu chạy trong cửa sổ. Quản trị mở **Trạng thái** để kiểm số luồng online.

Nút bật giữ nguyên khóa và giới hạn riêng của máy. Bấm lại khi dịch vụ đã chạy không tạo thêm dịch vụ. Nếu chương trình chấm chạy trong cửa sổ, giữ cửa sổ đó mở; dừng bằng Ctrl+C và chờ hoàn tất dọn dẹp.

Máy dùng dịch vụ có thể đóng cửa sổ xem trạng thái, nhưng vẫn phải giữ Windows thức. Muốn dừng dịch vụ, mở Ubuntu và chạy:

```bash
sudo systemctl stop ottv2-worker-pool.service
```

Không thấy nút Desktop thì làm mục 7. Chưa cài máy thì bắt đầu mục 3.

## 3. Lần đầu: chuẩn bị Ubuntu và Isolate

Trên máy Windows cần cài, làm [MOI_TRUONG.md](MOI_TRUONG.md). Tài liệu đó hướng dẫn cài WSL, Ubuntu 24.04, Isolate và kiểm môi trường. Mốc tải 100% không thay cho việc mở Ubuntu và tạo người dùng.

Mở Ubuntu từ PowerShell:

```powershell
wsl -d Ubuntu-24.04
```

Máy Ubuntu trực tiếp bỏ bước WSL. Trong **Ubuntu**, kiểm:

```bash
free -h
isolate --version
isolate --check-config
systemctl is-active isolate.service
```

Cần thấy Isolate 2.7, cấu hình hợp lệ và `active`. RAM khả dụng tối thiểu: 1.280 MiB cho 4 trận, 1.664 MiB cho 6 trận, 2.816 MiB cho 12 trận. Công thức là `512 + 192 × số trận` MiB; đủ RAM chưa bảo đảm bot nặng trả lời kịp hạn.

## 4. Đặt mã nguồn trên máy chấm

Trên **Windows**, clone repository theo [GITHUB.md](GITHUB.md). Giữ thư mục có README và scripts để tạo nút Desktop ở mục 7.

Trong **Ubuntu máy chấm**, thay `TEN_GITHUB` bằng tên tài khoản sở hữu repository rồi chạy:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/TEN_GITHUB/ottv2.git ~/ottv2-source
cd ~/ottv2-source
sha256sum -c SHA256SUMS
```

Cần thấy tất cả dòng `OK`. `ottv2-source` phải là thư mục chưa tồn tại. Tiếp tục:

```bash
sudo adduser --system --group --home /var/lib/ottv2 --shell /usr/sbin/nologin ottv2
sudo install -d /opt/ottv2/app
sudo cp -a ottv2 tests scripts web practice replay examples maps deploy google config docs README.md SHA256SUMS .gitignore /opt/ottv2/app/
sudo chown -R root:root /opt/ottv2/app
sudo chmod -R u=rwX,go=rX /opt/ottv2/app
sudo install -d -m 700 -o ottv2 -g ottv2 /var/lib/ottv2/worker-pool
```

`ottv2` là người dùng dịch vụ Linux. Máy chấm chỉ nhận việc, không tạo tài khoản sinh viên hoặc cơ sở dữ liệu giải trên máy này.

## 5. Nhận khóa riêng từ quản trị

Mỗi máy dùng đúng một JSON riêng. **Nếu quản trị đã cấp khóa, chỉ lấy đúng tệp đó; không đăng ký lại.** Ví dụ dưới dành cho Sinh (`worker-pc02`); Hải thay thành pc03, Dương thay thành pc04.

### 5.1. Quản trị kết nối VPS

Trên **Windows quản trị**, mở PowerShell tại thư mục mã nguồn có README:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps.ps1 connect
```

Cần thấy `azureuser@ottv2-central-v2`. Công cụ đi qua máy cầu nối SSH của nhóm. Chỉ máy quản trị cần khóa SSH; PC chấm chỉ dùng HTTPS và JSON riêng.

### 5.2. Chỉ cấp khi máy chưa có khóa

Trong **SSH trên VPS**:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.worker_pool register --db /var/lib/ottv2/data/queue.sqlite3 --host-id worker-pc02 --url https://ottv2-nhom12.koreacentral.cloudapp.azure.com --slots 12 --output /var/lib/ottv2/private-workers/worker-pc02-pool.json
```

Cần thấy `pool_registered`. Nếu tên đã tồn tại thì kiểm đúng bộ khóa đã cấp, không xóa hoặc ghi đè khóa để chữa lỗi tải.

### 5.3. Chép ra vị trí tải và chờ xác nhận

Vẫn trong **SSH trên VPS**, chạy riêng dòng này:

```bash
sudo install -m 600 -o "$(id -un)" -g "$(id -gn)" /var/lib/ottv2/private-workers/worker-pc02-pool.json /home/azureuser/worker-pc02-pool.json && test -s /home/azureuser/worker-pc02-pool.json && echo JSON_READY
```

**Phải thấy `JSON_READY` và dấu nhắc trở lại**, rồi mới gõ `exit`. Nếu chưa có xác nhận, chưa tải tệp và không dán thêm lệnh vào lúc lệnh đang chạy.

Trong **PowerShell Windows quản trị**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps.ps1 download -RemotePath /home/azureuser/worker-pc02-pool.json -LocalPath "$env:USERPROFILE\Downloads\worker-pc02-pool.json"
```

Cần thấy tải xong 100%. Chuyển JSON sang đúng PC bằng USB hoặc kênh riêng. Không gửi nội dung khóa lên chat/GitHub/Drive công khai. Không chuyển khóa SSH hoặc khóa Google sang PC chấm.

### 5.4. Cài JSON trên PC

Trong **Ubuntu máy chấm**, dùng `explorer.exe .` để chép JSON vào home. Chạy từng lệnh:

```bash
chmod 600 ~/worker-pc02-pool.json
sudo test ! -e /etc/ottv2-worker-pool.json && sudo install -o ottv2 -g ottv2 -m 600 ~/worker-pc02-pool.json /etc/ottv2-worker-pool.json && echo DA_CAI_KHOA
sudo -u ottv2 test -r /etc/ottv2-worker-pool.json && echo DOC_DUOC_KHOA
curl --fail --show-error https://ottv2-nhom12.koreacentral.cloudapp.azure.com/health
```

Cần thấy `DA_CAI_KHOA`, `DOC_DUOC_KHOA`, JSON có `"ok": true`. Tệp đích đã tồn tại thì kiểm đúng khóa đang cài; không ghi đè để thử.

## 6. Cài dịch vụ theo số trận của máy

Ví dụ **Sinh: 12 trận**. Hải dùng `worker_hai` và số 6; Dương dùng `worker_duong` và số 12; local dùng `worker_local` và số 6. Làm trong **Ubuntu**, khi chưa có chương trình chấm chạy:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.worker_pool configure --config /etc/ottv2-worker-pool.json --name worker_sinh --max-slots 12
sudo -u ottv2 python3 -m ottv2.worker_pool plan --slots 12 --cpu-mode shared --output /var/lib/ottv2/isolate-worker-plan.conf
sudo install -m 644 /var/lib/ottv2/isolate-worker-plan.conf /usr/local/etc/isolate
isolate --check-config
sudo -u ottv2 python3 scripts/isolate_smoke.py
sudo -u ottv2 python3 scripts/isolate_memory_check.py
sudo -u ottv2 python3 scripts/isolate_network_check.py
sudo -u ottv2 python3 scripts/isolate_filesystem_check.py
sudo -u ottv2 python3 scripts/isolate_wall_check.py
```

Cần thấy tên/số chỗ đúng và các phép kiểm `OK`. Sau đó cài dịch vụ:

```bash
sudo install -m 644 deploy/ottv2-worker-pool.service /etc/systemd/system/ottv2-worker-pool.service
sudo systemctl daemon-reload
sudo systemctl enable --now ottv2-worker-pool.service
sudo systemctl status ottv2-worker-pool.service --no-pager
```

Cần thấy `active (running)`. Số chỗ thực tế được giới hạn bằng profile ở bước configure; dịch vụ không tự tăng lên toàn bộ số khóa. Trong trang admin **Trạng thái**, cần thấy đúng máy và số luồng online. Không tạo trận thử bằng tài khoản sinh viên nếu không muốn tính lượt/điểm.

## 7. Tạo nút Desktop một lần

Trên **Windows của chính PC đó**, mở PowerShell tại thư mục repository đã clone có README. Ví dụ cho Sinh:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_worker_desktop.ps1 -Name worker_sinh
```

Hải thay tên bằng `worker_hai`, Dương dùng `worker_duong`, local dùng `worker_local`. Cần thấy **DA TAO NUT DESKTOP** và tệp `OTTv2 - tên_máy.cmd` trên Desktop. Công cụ dùng đúng thư mục Desktop của Windows, kể cả Desktop nằm trong OneDrive.

Từ lần sau chỉ làm mục 2. Không cần mở tài liệu để dán lại lệnh. Nút dùng Ubuntu-24.04; kiểm tên bằng `wsl --list --verbose` nếu không mở được.

## 8. Cập nhật nguồn khi ban tổ chức thông báo

Chỉ làm khi cần áp dụng gói nguồn được thông báo, không làm hằng ngày. Chép ZIP hiện hành vào home Ubuntu thành `ottv2-source.zip`. Với máy dùng dịch vụ, trong Ubuntu chạy:

```bash
sudo python3 /opt/ottv2/app/scripts/update_worker.py --archive ~/ottv2-source.zip
```

Trình cập nhật kiểm mã băm, dừng dịch vụ nhẹ nhàng, giữ khóa/kết quả chưa gửi, cấu hình đúng tên máy và bật lại. Cần thấy `DA CAP NHAT` và `active (running)`. Chạy lại mục 7 để nút Desktop khớp gói nguồn. Máy đã dùng `~/ottv2-local` tiếp tục dùng nút local; không cài thêm một pool thứ hai trên cùng máy.

## 9. Kiểm lỗi và giữ kết quả

| Hiện tượng | Việc cần làm |
| --- | --- |
| Không có Ubuntu trong Start | Mở bằng `wsl -d Ubuntu-24.04`; kiểm MOI_TRUONG |
| Không có nút Desktop | Làm mục 7 từ thư mục mã Windows có scripts |
| Chưa có dịch vụ/khóa | Hoàn tất mục 4–6, không chỉ tải WSL |
| JSON No such file khi tải | Kiểm mục 5.3 đã có JSON_READY, không cấp lại khóa |
| Pool đã chạy/hộp bận | Không mở hai pool; kiểm đúng cửa sổ hoặc dịch vụ đang chạy |
| HTTP 401/403 | Kiểm đúng JSON và trạng thái khóa trên VPS |
| HTTP 429/503 | Kiểm mạng/tải; giữ kết quả chưa gửi để chương trình thử lại |
| CPU/RAM hoặc ghim CPU không khớp | Dừng đúng pool, kiểm lại số chỗ ở configure và plan |
| Cannot open /run/isolate/cgroup | Kiểm `sudo systemctl restart isolate.service` |

Kết quả chưa được VPS xác nhận nằm trong `outbox.json` dưới thư mục từng luồng. Giữ khóa và thư mục `/var/lib/ottv2/worker-pool` hoặc thư mục mà lệnh run đã dùng. Không xóa outbox để ép nhận trận. Dừng nhẹ sẽ chờ dọn sandbox/gửi kết quả; không tắt cưỡng bức tiến trình đang chấm.

Muốn thu hồi một máy, cho máy xử lý hết kết quả chờ rồi dừng; quản trị chạy revoke đúng bộ khóa trên VPS. Muốn chọn số chỗ phù hợp, dùng `scripts/benchmark_worker.py` trên máy thử không có pool hoạt động và xem [KIEM_THU.md](KIEM_THU.md).
