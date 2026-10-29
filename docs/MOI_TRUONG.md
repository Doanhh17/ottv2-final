# Cài WSL, Ubuntu và Isolate

Chỉ làm một lần trên mỗi máy mới. Máy đã cài và kiểm đạt thì dùng tiếp, không cài lại mỗi lần bật máy chấm.

**WSL** giúp Windows chạy Linux. **Ubuntu** là hệ điều hành Linux chạy trong WSL. **Isolate** là chương trình cách ly bot và giới hạn tài nguyên. Một lệnh ở bước 1 cài cả WSL và Ubuntu; không cần tải chúng ở hai nơi khác nhau.

Máy đã chạy Ubuntu 24.04 trực tiếp hoặc VPS Ubuntu bắt đầu từ bước 2. Không chạy các lệnh WSL trên VPS.

## 1. Cài và mở Ubuntu trên Windows

Trên **máy Windows cần cài**, mở PowerShell bằng **Run as administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

**Cần thấy:** tải và cài WSL/Ubuntu hoàn tất. Nếu có yêu cầu khởi động lại Windows, lưu công việc và khởi động lại. Mốc tải 100% chưa đủ để kết luận việc cài đã hoàn tất.

Mở **PowerShell thường** sau đó và chạy:

```powershell
wsl --list --verbose
```

**Cần thấy:** có dòng `Ubuntu-24.04`, cột `VERSION` bằng `2`. Không thấy biểu tượng trong Start vẫn có thể mở trực tiếp:

```powershell
wsl -d Ubuntu-24.04
```

Lần đầu mở, chờ giải nén và tạo tên người dùng/mật khẩu Ubuntu theo câu hỏi trên màn hình. Tự chọn tên; không thêm `-u doanh` trên máy mới chưa có tài khoản đó. Nếu bản cài thực tế tên `Ubuntu`, dùng đúng tên được lệnh list hiển thị; các hướng dẫn của dự án dùng tên `Ubuntu-24.04`.

**Nếu lỗi:**

| Hiện tượng | Cách xử lý |
| --- | --- |
| Danh sách không có bản Linux nào | Nếu đang được yêu cầu khởi động lại thì làm việc đó trước; sau đó chạy lại lệnh cài Ubuntu ở trên |
| Ubuntu hiện VERSION 1 | Trong PowerShell chạy `wsl --set-version Ubuntu-24.04 2` rồi kiểm lại |
| `HCS_E_HYPERV_NOT_INSTALLED` | Kiểm Task Manager → Performance → CPU → Virtualization; bật ảo hóa trong BIOS/UEFI và tính năng Virtual Machine Platform theo hướng dẫn Microsoft |
| Gõ mật khẩu không hiện chữ/dấu sao | Bình thường; nhập xong nhấn Enter |

Không dùng `wsl --unregister` để chữa lỗi mở Ubuntu: lệnh đó xóa bản Linux cùng dữ liệu của nó.

Từ đây, lệnh `bash` chạy trong **Ubuntu**, không dán vào cửa sổ còn dấu nhắc `PS C:\...>`. Gõ `exit` để thoát về PowerShell. Đường dẫn `C:\Users\...` của Windows tương ứng `/mnt/c/Users/...` trong Ubuntu.

## 2. Cài công cụ trong Ubuntu

Trong **Ubuntu của máy đang chuẩn bị**:

```bash
sudo apt update
sudo apt install -y python3 git unzip build-essential pkg-config libcap-dev libseccomp-dev libsystemd-dev nano curl ca-certificates
python3 --version
ps -p 1 -o comm=
stat -fc %T /sys/fs/cgroup
```

**Cần thấy:** Python từ 3.12, `systemd`, `cgroup2fs`. Ứng dụng Python và bot mẫu không cần `pip install`.

Nếu Ubuntu trong WSL chưa có systemd, mở `sudo nano /etc/wsl.conf`, giữ các mục đang có và thêm/sửa duy nhất một mục:

```ini
[boot]
systemd=true
```

Trong nano: Ctrl+O, Enter để lưu; Ctrl+X để thoát. Lưu công việc trong các phiên WSL khác, rồi chạy ở **PowerShell trên chính máy này**:

```powershell
wsl --shutdown
wsl -d Ubuntu-24.04
```

Lệnh shutdown dừng mọi phiên WSL trên máy đó. Kiểm lại systemd/cgroup trước khi sang bước 3. VPS Ubuntu không dùng `/etc/wsl.conf`.

## 3. Cài Isolate

Trong **Ubuntu**, dùng người dùng thường có sudo:

```bash
git clone https://github.com/ioi/isolate.git ~/ottv2-isolate-src
cd ~/ottv2-isolate-src
git checkout --detach 8f185bb37f3f23e29b33b0c7727c91c13429abe3
make isolate isolate-cg-keeper default.cf systemd/isolate.service
sudo make install
sudo adduser --disabled-login --no-create-home --home /nonexistent --shell /usr/sbin/nologin --comment "" isolate
sudo systemctl daemon-reload
sudo systemctl enable --now isolate.service
isolate --version
isolate --check-config
isolate --cg --print-cg-root
grep '^isolate:' /etc/subuid /etc/subgid
```

**Cần thấy:** Isolate 2.7, cấu hình không lỗi, một đường dẫn cgroup và hai dải ID cho `isolate`. Commit cố định ở trên cũng được ghi tại `config/isolate-source.json` trong mã nguồn.

Nếu thư mục nguồn hoặc tài khoản isolate đã tồn tại, kiểm lần cài trước thay vì chạy lại lệnh clone/adduser. Không sao chép dải ID của tài khoản khác. Isolate được upstream cài với quyền cần thiết để tạo sandbox; ứng dụng và bot vẫn chạy bằng người dùng thường.

**Nếu lỗi:** thiếu thư viện thì kiểm bước 2; thiếu `/run/isolate/cgroup` thì chạy `sudo systemctl restart isolate.service`; dịch vụ lỗi thì xem:

```bash
sudo journalctl -u isolate.service -n 40 --no-pager
```

## 4. Chọn CPU cho hai hộp kiểm khởi động

Chỉ làm trên **máy mới chưa có bot đang chạy**. Máy quản lý cần hai hộp 0/1 để kiểm bot báo sẵn sàng. Máy chấm nhiều trận sẽ tạo thêm cấu hình ở MAY_CHAM.md.

Trong **Ubuntu**:

```bash
python3 - <<'PY'
import os
from pathlib import Path
import re

cpus = sorted(os.sched_getaffinity(0))
lines = [line for line in Path('/usr/local/etc/isolate').read_text().splitlines()
         if not re.match(r'^\s*box[01]\.cpus\s*=', line)]
lines += [f'box0.cpus = {cpus[0]}', f'box1.cpus = {cpus[1 % len(cpus)]}']
with (Path.home() / 'ottv2-isolate-ready.conf').open('x') as stream:
    stream.write('\n'.join(lines) + '\n')
print('DA TAO CAU HINH')
PY
sudo cp --no-clobber /usr/local/etc/isolate /usr/local/etc/isolate.before-ready
sudo install -m 644 ~/ottv2-isolate-ready.conf /usr/local/etc/isolate
isolate --check-config
```

**Cần thấy:** `DA TAO CAU HINH`, không có lỗi cấu hình. Máy một nhân cho hai hộp dùng chung nhân đó. Nếu tệp đề xuất/bản sao lưu đã có, kiểm lần cài trước; không ghi đè cấu hình máy đang chấm.

## 5. Làm gì tiếp?

- Chạy thử cả website trên máy cá nhân: [CAI_DAT.md](CAI_DAT.md).
- Cài website lên VPS: trở lại [TRIEN_KHAI_VPS.md](TRIEN_KHAI_VPS.md).
- Chỉ nối máy chấm: trở lại [MAY_CHAM.md](MAY_CHAM.md).

Nguồn tra cứu khi cài môi trường: [Cài WSL](https://learn.microsoft.com/en-us/windows/wsl/install), [lệnh WSL](https://learn.microsoft.com/en-us/windows/wsl/basic-commands), [systemd trong WSL](https://learn.microsoft.com/en-us/windows/wsl/systemd), [Isolate](https://github.com/ioi/isolate).
