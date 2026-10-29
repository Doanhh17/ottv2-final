# Đọc tài liệu OTTv2 từ đâu?

Chọn đúng công việc trong bảng. Không cần đọc hết thư mục này theo thứ tự tên tệp.

| Bạn cần làm gì? | Đọc tài liệu nào? |
| --- | --- |
| Sinh viên đăng ký, cập nhật bot, tìm trận, xem điểm, đổi thông tin | [Quy định và cách sử dụng](QUY_DINH_SINH_VIEN.md) |
| Sinh viên học luật cờ và viết main.py | [Luật và giao thức bot](VIET_BOT.md) |
| Sinh viên tải HTML đấu thử main.py, đọc cấu hình bản đồ và ba bot | [Đấu thử trên máy sinh viên](DAU_THU.md) |
| Cài WSL, Ubuntu và Isolate lần đầu | [Chuẩn bị môi trường](MOI_TRUONG.md) |
| Chạy website và một trận thử trên máy cá nhân | [Chạy local](CAI_DAT.md) |
| Mở website HTTPS cho sinh viên truy cập | [Cài VPS](TRIEN_KHAI_VPS.md) |
| Nối máy chấm mới, bật hằng ngày hoặc cập nhật phiên bản | [Máy chấm](MAY_CHAM.md) |
| Nối Google, quản lý tài khoản, sao lưu và xử lý sự cố | [Vận hành](VAN_HANH.md) |
| Viết hoặc thay bản đồ | [Bản đồ JSON](BAN_DO.md) |
| Đọc kiến trúc và cách hệ thống xử lý dữ liệu | [Thiết kế](THIET_KE.md) |
| Chạy kiểm thử hoặc xem kết quả đã đo | [Kiểm thử](KIEM_THU.md) |
| Xem độ trễ, tốc độ máy chấm và dữ liệu đo | [Hiệu năng](HIEU_NANG.md) |
| Giải thích đã tối ưu gì và tác dụng của từng phần | [Tối ưu hệ thống](TOI_UU.md) |

## Người cài mới đi theo đường nào?

- **Chưa có gì, muốn chạy thử:** MOI_TRUONG → CAI_DAT.
- **Đã chạy thử, muốn công khai website:** TRIEN_KHAI_VPS → MAY_CHAM → VAN_HANH.
- **Chỉ thêm máy chấm:** MAY_CHAM; tài liệu sẽ chỉ chỗ cần cài môi trường. Không tạo lại website hoặc giải đấu.
- **Đã cài xong, chỉ muốn bật máy chấm:** đọc ngay phần đầu MAY_CHAM. Không làm lại các bước cài.

## Phân biệt ba cửa sổ trước khi nhập lệnh

| Cửa sổ | Dấu nhắc thường thấy | Dùng để làm gì? |
| --- | --- | --- |
| PowerShell Windows | `PS C:\...>` | Cài/mở WSL, chuyển tệp, kết nối SSH |
| Ubuntu trên máy cá nhân hoặc máy chấm | `ten@may:~$` | Cài Isolate, chạy chương trình Python |
| SSH vào VPS | `azureuser@ottv2-central-v2:~$` | Cài và quản lý website trên VPS |

Mỗi khối lệnh cài đặt đều ghi nơi chạy và kết quả cần thấy. `sudo` hỏi mật khẩu **Ubuntu của máy đang dùng**; lúc gõ không hiện ký tự là bình thường. Mật khẩu OTTv2 chỉ dùng để đăng nhập website.

Website hiện tại: [ottv2-nhom12.koreacentral.cloudapp.azure.com](https://ottv2-nhom12.koreacentral.cloudapp.azure.com). Sau khi đăng nhập, mở **Bảng xếp hạng** trong menu để xem điểm; vào **Tài liệu** để đọc hướng dẫn, lấy mã mẫu và tải replay. Các tệp ở đây là hướng dẫn đi kèm mã nguồn nộp bài.

Đấu thử và replay có nút tải ngay ngoài trang đăng nhập. Đấu thử cần mạng để tải Python lúc đầu; replay đã có HTML và log thì xem offline.
