# Đưa mã nguồn lên GitHub

Đưa cả cây mã nguồn lên repository để thầy đọc README, tài liệu và các tệp Python/HTML trực tiếp. Không tải gói ZIP lên repository.

## 1. Thư mục cần đẩy

Trong dự án đang làm việc, thư mục `output/github-source` là bản nguồn công khai đã được lọc: có mã, hướng dẫn, báo cáo hiệu năng và `SHA256SUMS`; không có khóa, cơ sở dữ liệu, output hoặc lịch sử Git cũ.

Nếu chưa có thư mục đó, mở **PowerShell tại thư mục dự án có README** rồi chạy:

```powershell
python scripts/export_github.py --output output/github-source
```

Lệnh từ chối ghi đè thư mục có sẵn để tránh mất thay đổi của bạn. Dự án làm việc có lịch sử Git chứa tài liệu trao đổi riêng; bắt đầu Git trong thư mục nguồn sạch bên dưới để không đưa lịch sử ấy lên GitHub.

## 2. Tạo repository trống

1. Đăng nhập GitHub, chọn **New repository**.
2. Đặt tên `ottv2`. Chọn Public nếu muốn thầy đọc trực tiếp; Private nếu cần giới hạn người xem.
3. Không chọn tạo sẵn README, `.gitignore` hoặc License vì nguồn đã có các tệp của dự án.
4. Bấm **Create repository**, chép địa chỉ HTTPS, dạng `https://github.com/TEN_GITHUB/ottv2.git`.

## 3. Đẩy lần đầu

Trong **PowerShell tại thư mục dự án ban đầu**:

```powershell
cd output/github-source
git init -b main
git config core.autocrlf false
git add .
git diff --cached --stat
git status --short
git commit -m "Cong bo ma nguon OTTv2"
```

Cần thấy README, docs, ottv2, web, practice, replay, scripts, tests và các tệp cấu hình công khai. Không có output, `.pem`, `.env`, khóa worker hoặc cơ sở dữ liệu. Nếu Git hỏi tên/email người tạo commit, đặt tên và email GitHub của bạn bằng `git config user.name` và `git config user.email`, rồi chạy lại commit.

Thiết lập `core.autocrlf false` giữ nguyên bytes các tệp để SHA256SUMS không bị lệch do Git tự đổi xuống dòng trên Windows.

Thay `TEN_GITHUB` bằng tên tài khoản thật, sau đó chạy:

```powershell
git remote add origin https://github.com/TEN_GITHUB/ottv2.git
git remote -v
git push -u origin main
```

Nếu hiện màn hình đăng nhập GitHub, bạn tự đăng nhập và xác nhận. Không dán token vào mã, tài liệu hoặc chat. Mở repository trên web: cần thấy README hiện đúng bốn thành viên và các liên kết tài liệu bấm được.

## 4. Cập nhật các lần sau

Từ lần này, sửa và quản lý mã trong repository vừa tạo. Trước khi đẩy, chạy kiểm phù hợp với phần thay đổi theo [KIEM_THU.md](KIEM_THU.md), cập nhật lại manifest rồi commit:

```powershell
python scripts/export_github.py --refresh-manifest
git add .
git diff --cached --stat
git commit -m "Cap nhat OTTv2"
git push
```

`.gitignore` đã loại dữ liệu vận hành và các loại khóa thông dụng. Bạn vẫn cần xem danh sách thay đổi trước mỗi lần commit; bộ quét mẫu khóa không phát hiện được mọi dạng bí mật.

## 5. Người khác lấy nguồn

Trong **Ubuntu** đã cài Git, thay tên tài khoản trong URL:

```bash
git clone https://github.com/TEN_GITHUB/ottv2.git ~/ottv2-project
cd ~/ottv2-project
sha256sum -c SHA256SUMS
```

Cần thấy toàn bộ dòng `OK`. Sau đó chọn [cài local](CAI_DAT.md), [cài VPS](TRIEN_KHAI_VPS.md) hoặc [cài máy chấm](MAY_CHAM.md). Khóa của hệ thống đang chạy được quản trị cấp riêng; clone mã không cấp quyền vào VPS hay quyền nhận trận.

Số đo được lưu ở [HIEU_NANG.md](HIEU_NANG.md) và [hieu-nang.json](hieu-nang.json). Không dùng số trận/giờ của bot mẫu làm bảo đảm cho mọi bot.

Các bước GitHub đối chiếu với [hướng dẫn chính thức](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).
