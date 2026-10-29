# Đấu thử main.py trên máy của bạn

Đấu thử giúp kiểm mã trước khi dùng lượt thi đấu chính thức. Bạn không cần tài khoản hoặc bật máy chấm. Kết quả không ghi Elo, lịch sử hoặc hạn mức của giải.

## 1. Tải và mở

1. Trên website OTTv2, bấm **Đấu thử với máy** ở trang đăng nhập, Thi đấu, Bot của tôi hoặc Tài liệu.
2. Tải tệp **OTTv2-dau-thu.html** rồi mở bằng Chrome hoặc Edge trên máy của bạn.
3. Giữ kết nối Internet lúc đầu để tải Python cho trình duyệt. Tệp HTML chứa sẵn giao diện, bản đồ và mã ba đối thủ; bộ chạy Python được tải riêng từ Pyodide. Đây không phải bộ Python offline tự chứa hoàn toàn.

## 2. Lấy đúng bản đồ và ba đối thủ

1. Ở màn hình đăng nhập hoặc trang **Thi đấu** của tài khoản sinh viên, bấm **Sao chép cấu hình đấu thử**. Nút này không nằm ở Bot của tôi, Tài liệu hoặc trang quản trị.
2. Trong HTML đã mở, bấm **Dán cấu hình từ website**.
3. Dán cấu hình, bấm **Áp dụng cấu hình**. Kiểm tên và hình bàn cờ.

Nếu trình duyệt không cho ghi clipboard, website hiện ô cấu hình đã chọn sẵn; nhấn Ctrl+C để sao chép. Cấu hình gồm bản đồ hiện tại, luật và mã ba cấp bot do quản trị đặt. Khi ban tổ chức thay bản đồ hoặc đối thủ, sao chép lại cấu hình; không cần sửa HTML. Nếu thay cấu trúc luật, tải HTML mới theo thông báo của ban tổ chức.

## 3. Đấu thử

1. Chọn **main.py** của bạn, tối đa **64 KiB trong trang đấu thử**. Giới hạn này dành riêng cho trình duyệt; giới hạn bài nộp chính thức nằm trong quy định sinh viên.
2. Chọn **Sơ cấp**, **Trung cấp** hoặc **Cao cấp**, và bên đi trước.
3. Bấm **Bắt đầu đấu thử**. Lần tải Python đầu tiên có thể mất 1–2 phút. Bàn cờ tự chạy từng nước và báo kết quả; không cần dán log JSON. Trên màn hình laptop, bàn cờ/nút điều khiển nằm cùng một khung; chỉ danh sách nước đi cuộn riêng. Ô dán cấu hình mở trong hộp thoại, không đẩy bàn cờ xuống.
4. Muốn ngắt, bấm **Dừng**. Muốn thử lại, chọn cấp độ rồi bấm bắt đầu.

Đối thủ mặc định gồm bot chọn nước đơn giản, bot tìm kiếm độ sâu 2 và bot tìm kiếm độ sâu 3 có cách đánh phòng thủ. Quản trị có thể thay mã, nên tên cấp độ chỉ là nhãn do người tổ chức chọn, không phải chứng nhận sức mạnh.

## 4. Khác biệt với thi đấu chính thức

| Nội dung | HTML đấu thử | Máy chấm chính thức |
| --- | --- | --- |
| Nơi chạy | Hai luồng riêng trong trình duyệt máy bạn | Isolate trên máy chấm |
| Ngân sách | 7 giây **thời gian thực** của các lượt mỗi bot | 7 giây **CPU** trong lượt của mỗi bot |
| Phản hồi | Tối đa 2,5 giây thực/lượt; READY tối đa 5 giây sau khi Python tải xong | Tối đa 2,5 giây thực/lượt; READY tối đa 5 giây |
| Số nước | 25 mỗi bên; đủ 50 nước xét số quân | Cùng luật bàn cờ |
| RAM | Không áp được trần 64 MiB của giải | 64 MiB/bot qua Isolate |
| Điểm, lượt | Không tính | Tính theo kết quả hợp lệ |

Máy chậm có thể khiến bot thua thời gian khi đấu thử; không dùng kết quả này làm thước đo CPU chính thức. Trình duyệt hỗ trợ Python chuẩn và cách đọc stdin thông thường: `for line in sys.stdin`, `sys.stdin.readline()` hoặc `input()`, kể cả qua hàm main. Bot đọc stdin bằng cơ chế đặc biệt, luồng hoặc thư viện ngoài có thể cần chỉnh để thử tại đây. Trận chính thức chạy mã main.py gốc, không qua bộ chuyển đổi của HTML.

Chỉ mở mã bạn tin cậy. Web Worker giúp bàn cờ không bị treo khi bot tính lâu; đây không phải Isolate và không thay thế cơ chế bảo vệ của máy chấm. Isolate là mã nguồn sandbox thường dùng để chấm thi lập trình, giúp cách ly tiến trình, mạng và giới hạn tài nguyên; vẫn cần cấu hình máy chủ và cập nhật hệ thống đúng cách.

Nếu bot lỗi, mở **Lỗi và thông tin từ bot**. Kiểm cú pháp, READY và mỗi dòng JSON, dùng flush, không in lời chào vào stdout. Hướng dẫn đầy đủ: [VIET_BOT.md](VIET_BOT.md). Bộ Python trình duyệt dùng [Pyodide](https://pyodide.org/en/0.27.7/usage/webworker.html).
