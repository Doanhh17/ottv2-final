# OTTv2 — Thi đấu bot Búa, Bao, Kéo

Bài tập lớn môn **Lập Trình Mạng**, giảng viên **Hồ Đắc Phương**, **Nhóm 12**.

| STT | MSSV | Họ và tên | Vai trò |
| :---: | --- | --- | --- |
| 1 | | Lê Bá Tùng Dương | Thành viên |
| 2 | 24020071 | Nguyễn Đăng Doanh | Nhóm Trưởng |
| 3 | | Nguyễn Đức Hải | Thành viên |
| 4 | | Trương Gia Sinh | Thành viên |

## 1. Hệ thống làm gì?

Sinh viên viết bot trong một tệp `main.py`, đặt ở gốc repo GitHub công khai rồi thi đấu qua website. Máy quản lý nhận bài, ghép trận và ghi Elo; các máy chấm chạy bot trong Isolate, gửi kết quả về. Sinh viên có thể tải HTML đấu thử về máy để kiểm thuật toán và dùng HTML replay để xem lại trận.

**Website:** [ottv2-nhom12.koreacentral.cloudapp.azure.com](https://ottv2-nhom12.koreacentral.cloudapp.azure.com).

**Đọc trước:** [Hướng dẫn sinh viên](docs/QUY_DINH_SINH_VIEN.md) · [Luật và cách viết main.py](docs/VIET_BOT.md) · [Mục lục toàn bộ tài liệu](docs/README.md).

## 2. Những thông số cần biết

| Nội dung | Quy định đang áp dụng | Đọc chi tiết |
| --- | --- | --- |
| Bài nộp | Chỉ đọc `main.py` ở gốc repo GitHub Public; Python chuẩn, không cần gói pip | [Viết bot](docs/VIET_BOT.md) |
| Bàn cờ | 9×9; mỗi bên 3 búa, 3 bao, 3 kéo | [Luật](docs/VIET_BOT.md) |
| Thời gian và RAM | 7 giây CPU/bot cho cả trận; 2,5 giây thực/lượt; 64 MiB/bot; READY tối đa 5 giây | [Tài nguyên bot](docs/VIET_BOT.md#3-môi-trường-và-thời-gian) |
| Giới hạn nước | 25 nước hợp lệ/bên; đủ 50 nước thì bên còn nhiều quân hơn thắng, bằng quân hòa | [Luật](docs/VIET_BOT.md) |
| Lượt mỗi ngày | 3 lượt tấn công + 3 lượt phòng thủ, theo ngày Việt Nam; bot offline vẫn có thể phòng thủ | [Cách tính lượt](docs/QUY_DINH_SINH_VIEN.md) |
| Đăng ký/đổi thông tin | Kiểm Google Forms khoảng **1 phút/lần** | [Tài khoản](docs/QUY_DINH_SINH_VIEN.md) |
| Cập nhật bot | Hai yêu cầu được nhận cách nhau ít nhất **3 phút**; commit lên GitHub chưa tự đổi bot | [Nộp bot](docs/QUY_DINH_SINH_VIEN.md) |
| Bảng xếp hạng | Xuất Google Sheets khoảng **2 phút/lần**; Elo cá nhân được ghi sau trận | [Điểm và kết quả](docs/QUY_DINH_SINH_VIEN.md) |
| Trạng thái web | Tự kiểm khoảng **1 phút/lần**; có nút Làm mới, không phải F5 liên tục | [Sử dụng website](docs/QUY_DINH_SINH_VIEN.md) |
| Phiên đăng nhập | Một nơi/tài khoản, 30 phút; sinh viên chờ 7 phút sau khi tự hết phiên, đăng xuất sớm được vào lại ngay; admin không chờ | [Tài khoản](docs/QUY_DINH_SINH_VIEN.md) |
| Lịch sử và log | Hiện 10 trận gần nhất; log giữ 7 ngày, cần tự lưu để giữ lâu | [Replay](docs/QUY_DINH_SINH_VIEN.md) |

Các chu kỳ trên là lịch kiểm khi dịch vụ hoạt động. Nhiều hồ sơ hoặc lỗi mạng có thể làm xử lý chậm hơn; thời gian chờ chấm dự kiến trong 10 phút sau khi ghép, không phải cam kết mọi tải đều đạt.

## 3. Công nghệ sử dụng và vai trò

| Công nghệ | Dùng vào việc gì? |
| --- | --- |
| Python, SQLite | API, tài khoản/mật khẩu băm, ghép trận, luật, hạn mức, Elo và biên nhận chống ghi trùng |
| Isolate 2.7, cgroup v2 | Sandbox mã nguồn thường dùng trong chấm thi lập trình; cách ly bot, hạn chế mạng/tệp và CPU/RAM trên máy chấm |
| HTML, CSS, JavaScript | Website và trình replay; cập nhật đúng phần dữ liệu thay đổi, không tải lại cả giao diện |
| Pyodide, Web Worker | Chạy Python trong HTML đấu thử trên máy sinh viên; hai bot ở hai luồng riêng |
| Nginx, systemd, Ubuntu | HTTPS, giới hạn tốc độ, chạy website/máy chấm bằng dịch vụ |
| **Google Apps Script** | **Web app trung gian nối VPS với Google Forms và Sheets**: đọc hồ sơ Forms, nhận xác nhận xử lý và ghi bảng xếp hạng |
| Google Docs, Drive | Phục vụ hướng dẫn, mã mẫu và các HTML tải về qua liên kết trực tiếp |
| GitHub | Lấy mã theo commit cố định; kiểm bài trước khi nhận phiên bản bot |

Cụ thể, [ottv2/google_sync.py](ottv2/google_sync.py) trên VPS gọi [google/Code.gs](google/Code.gs), là Web app Apps Script chạy bằng tài khoản người tổ chức. Yêu cầu HTTPS có chữ ký HMAC để xác thực máy quản lý. Script đọc hai Forms khoảng 1 phút/lần; VPS kiểm và lưu tài khoản/thông tin mới rồi xác nhận để Script xóa câu trả lời đã xử lý, chỉ giữ biên nhận không có mật khẩu. Khoảng 2 phút/lần, VPS gửi bảng Elo chính thức cho Script ghi Sheets.

**Docs/Drive không đi qua Apps Script hoặc VPS ở mỗi lượt đọc/tải.** Máy chấm cũng không dùng Apps Script để chấm. Khóa Google và khóa máy chấm đặt riêng ngoài nguồn/ZIP. Các lớp bảo vệ giúp giảm rủi ro, không thay thế việc cấu hình và cập nhật máy chủ đúng.

Đọc kỹ cơ chế, lợi ích và giới hạn trong [Những phần đã tối ưu](docs/TOI_UU.md); kiến trúc/API ở [Thiết kế hệ thống](docs/THIET_KE.md).

## 4. Dành cho sinh viên

1. Đăng ký bằng email trường trong Form; chờ đồng bộ rồi đăng nhập bằng MSSV.
2. Đặt `main.py` trên GitHub Public, vào Bot của tôi bấm Cập nhật bot và chờ sẵn sàng.
3. Sang Thi đấu, bấm Tìm trận. Trang tự cập nhật 1 phút/lần; bấm Làm mới nếu cần xem ngay.
4. Xem Kết quả, sao chép log và tự lưu. Bảng điểm cả lớp ở mục Bảng xếp hạng, cập nhật 2 phút/lần.

[Tải HTML đấu thử](https://drive.google.com/file/d/1r4dwmC-EvzwlE1Q-Z2Lzzpv7KNFr78jx/view) · [Cách đấu thử main.py](docs/DAU_THU.md) · [Tải replay](https://drive.google.com/file/d/1lfaBDrBbYPJRhikBKEGlKnfBxuunlYFm/view).

HTML đấu thử cần mạng để tải Python ban đầu; đo 7 giây **thời gian thực**, không đo CPU hoặc áp RAM như Isolate. Chọn main.py và cấp bot để xem trực tiếp; nút Sao chép cấu hình ở màn hình đăng nhập/Thi đấu giúp lấy bản đồ và ba đối thủ hiện tại. Replay có HTML và log rồi thì xem offline.

## 5. Cài đặt và quản trị

| Công việc | Hướng dẫn chính |
| --- | --- |
| Cài WSL, Ubuntu và Isolate lần đầu | [MOI_TRUONG.md](docs/MOI_TRUONG.md) |
| Chạy thử trên máy cá nhân | [CAI_DAT.md](docs/CAI_DAT.md) |
| Triển khai website HTTPS | [TRIEN_KHAI_VPS.md](docs/TRIEN_KHAI_VPS.md) |
| Thêm máy chấm, tạo nút Desktop và bật hằng ngày | [MAY_CHAM.md](docs/MAY_CHAM.md) |
| Đổi bản đồ và ba mã bot đấu thử | [BAN_DO.md](docs/BAN_DO.md) |
| Nối Google, tài khoản, sao lưu, xử lý sự cố | [VAN_HANH.md](docs/VAN_HANH.md) |
| Kiểm thử và số đo chịu tải | [KIEM_THU.md](docs/KIEM_THU.md) |
| Số đo hiệu năng và cách hiểu kết quả | [HIEU_NANG.md](docs/HIEU_NANG.md) |
| Đưa mã nguồn lên GitHub | [GITHUB.md](docs/GITHUB.md) |

VPS D2as_v4 2 vCPU/8 GiB chạy website và 4 luồng chấm thường trực. Các PC cấu hình local/Sinh/Hải/Dương lần lượt 6/12/6/12 luồng; chỉ nhận thêm việc khi bật và có kết nối. Số luồng không phải số trận hoàn tất mỗi giây. Cài PC một lần, những lần sau bấm nút Desktop theo MAY_CHAM; không dán lại lệnh cài mỗi ngày.

Đổi mật khẩu admin bằng dòng lệnh **trên VPS mới**, không dùng Form hoặc máy chấm:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.accounts --db /var/lib/ottv2/data/accounts.sqlite3 password admin
```

Nhập mật khẩu mới hai lần; không hiện ký tự lúc gõ là bình thường. Cần thấy `Đã lưu`, sau đó đăng nhập lại. Không ghi mật khẩu vào mã hoặc đối số lệnh.

## 6. Mã nguồn trên GitHub

`ottv2/`: xử lý hệ thống · `web/`: website · `practice/`: HTML đấu thử · `replay/`: HTML xem lại · `google/`: Apps Script · `maps/`, `examples/`: bản đồ/bot mẫu · `deploy/`, `scripts/`: triển khai/vận hành · `tests/`: kiểm tự động · `docs/`: hướng dẫn.

Repository chứa nguồn hiện hành, `SHA256SUMS` và tài liệu; dữ liệu tài khoản, khóa và output nằm ngoài Git. Làm theo [GITHUB.md](docs/GITHUB.md) để đẩy lần đầu, cập nhật và clone. Mã dựng HTML được giữ để tạo lại đúng tệp tải xuống. Số đo thực tế, phạm vi và công thức ước tính ở [HIEU_NANG.md](docs/HIEU_NANG.md); kết quả kiểm chức năng ở [KIEM_THU.md](docs/KIEM_THU.md).
