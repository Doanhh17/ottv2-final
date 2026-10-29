# Sinh viên: quy định và cách sử dụng OTTv2

Đọc tài liệu này để đăng ký, nộp bot, thi đấu và giữ kết quả. Luật cờ, định dạng JSON và mã main.py mẫu chỉ trình bày ở [VIET_BOT.md](VIET_BOT.md).

Website: [ottv2-nhom12.koreacentral.cloudapp.azure.com](https://ottv2-nhom12.koreacentral.cloudapp.azure.com). Sau khi đăng nhập, mở **Tài liệu** để xem hướng dẫn và tải mã mẫu/replay; mở **Bảng xếp hạng** trong menu để xem điểm cả lớp. Website chỉ hiện Elo cá nhân; bảng điểm cả lớp ở Google Sheets.

Mỗi bot có **7 giây CPU cho cả trận, 2,5 giây phản hồi mỗi lượt và 64 MiB RAM**. Mỗi bên tối đa **25 nước hợp lệ**; đủ 50 nước mà chưa có kết quả theo luật bàn cờ thì bên còn nhiều quân hơn thắng, bằng quân thì hòa. Quân mất do bom không được tính. Lặp bàn ba lần trước mốc 50 vẫn hòa. Thắng ở nước cuối vẫn được tính thắng.

## 1. Đăng ký và đăng nhập

1. Ở trang đăng nhập, bấm **Đăng ký tài khoản**.
2. Dùng email Google của trường có đuôi `@vnu.edu.vn`. Form lấy email đã xác minh; điền đúng MSSV, họ tên và link kho GitHub công khai.
3. Đặt mật khẩu riêng cho OTTv2, dài 12–256 ký tự. Không dùng mật khẩu Google.
4. Gửi Form, chờ hệ thống nhận yêu cầu rồi đăng nhập bằng MSSV và mật khẩu vừa đặt.

**Cần thấy:** tên tài khoản và Elo ở đầu trang. Hệ thống nhận Form khoảng **1 phút/lần**; gửi Form thành công chưa có nghĩa tài khoản đã được tạo. Đông người gửi hoặc lỗi mạng có thể chậm hơn.

Mỗi email/MSSV chỉ có một tài khoản. Không gửi lại Form đăng ký để đổi thông tin. Hồ sơ sai, trùng hoặc không đủ điều kiện sẽ bị từ chối; liên hệ ban tổ chức nếu chưa đăng nhập được sau khi kiểm đúng thông tin.

Ban tổ chức có thể cấp tài khoản trực tiếp. Tài khoản đó dùng trên cùng website và lên bảng điểm như tài khoản Form; nếu chưa gắn email đã xác minh thì nhờ ban tổ chức hỗ trợ đổi thông tin.

Mỗi tài khoản chỉ đăng nhập **một nơi** tại một thời điểm, kể cả admin. Phiên kéo dài **30 phút** tính từ lúc đăng nhập. Sinh viên phải chờ **7 phút sau khi phiên tự hết hạn** mới đăng nhập lại; chủ động Đăng xuất trước hạn thì được vào lại ngay. Admin hết phiên được đăng nhập lại ngay. Đóng tab không phải đăng xuất.

Muốn kiểm bot trước khi dùng lượt chính thức, tải **Đấu thử với máy** ngay ở trang đăng nhập. Đọc [cách đấu thử](DAU_THU.md); dùng **Sao chép cấu hình đấu thử** để lấy bản đồ và ba bot hiện tại.

## 2. Chuẩn bị bài nộp GitHub

| Yêu cầu bắt buộc | Cách làm |
| --- | --- |
| Kho công khai, có ít nhất một commit | Link dạng `https://github.com/ten-nguoi-dung/ten-kho`, không dùng link `/blob/` hoặc `/tree/` |
| Một tệp Python | Đặt `main.py` ngay ở gốc kho; toàn bộ thuật toán nằm trong tệp đó |
| Môi trường | Python từ 3.12, chỉ thư viện chuẩn; không import gói ottv2 hoặc gói pip |
| Tự chứa mã | Không đọc tệp phụ/đường dẫn cá nhân và không tải mã hoặc dữ liệu từ mạng khi bot chạy |
| Dung lượng | main.py 1 byte–5 MiB; ZIP toàn kho tối đa 5 MiB, giải nén tối đa 20 MiB, không quá 256 mục |
| Tệp phụ | Được có README/tài liệu nhỏ; không có thêm tệp `.py` khác |

Không đưa ảnh/video lớn, môi trường ảo, mật khẩu hoặc token vào kho. Không can thiệp bot khác hoặc tìm cách vượt giới hạn của máy chấm. Quy định điểm học phần, làm nhóm và đạo văn do giảng viên công bố riêng.

## 3. Đưa bản bot mới lên hệ thống

1. Đưa main.py lên GitHub và **commit** để lưu mã.
2. Mở **Bot của tôi**, kiểm link kho rồi bấm **Cập nhật bot**.
3. Chờ trạng thái **Bot đã sẵn sàng**; trang tự cập nhật trạng thái, không cần F5.
4. Khi bot đã hợp lệ mới sang Thi đấu.

Hai yêu cầu cập nhật được nhận phải cách nhau **3 phút**, kể cả lần trước thất bại. Đẩy mã lên GitHub chưa tự thay bot trên hệ thống. Muốn đổi kho thì dùng Form ở mục 7.

Hệ thống chốt mã tại một commit và kiểm bot báo sẵn sàng. Bản mới lỗi thì giữ bản tốt trước; chưa có bản tốt thì chưa được đấu. Trận đã ghép giữ bot/bản đồ đã chốt, không đổi giữa trận. Bot báo sẵn sàng chỉ chứng minh khởi động đúng, không bảo đảm mọi nước về sau hợp lệ.

**Nếu lỗi:** kho trống thì thêm commit; kiểm main.py ở gốc, kho công khai và dung lượng toàn kho. Lỗi giao tiếp/thời gian xem VIET_BOT.md. Không bấm liên tục để bỏ thời gian chờ.

## 4. Tìm trận và tính lượt

Mở **Thi đấu → Tìm trận**. Chưa ghép được thì có thể Hủy tìm và chưa dùng lượt. Đã ghép thì không hủy; đóng trình duyệt không dừng trận.

| Nội dung | Quy định |
| --- | --- |
| Hạn mức mỗi ngày Việt Nam | 3 lượt tấn công và 3 lượt phòng thủ, độc lập; ngày mới bắt đầu 00:00 UTC+7 |
| Tấn công | Chính bạn bấm Tìm trận |
| Phòng thủ | Hệ thống chọn bot của bạn làm đối thủ, kể cả khi bạn đã đăng xuất/đóng website |
| Hai người cùng tìm | Mỗi bên dùng một lượt tấn công; được gặp lại trong lượt còn lại |
| A tìm, B phòng thủ | Dùng một lượt tấn công của A và một lượt phòng thủ của B; cùng chiều A→B tối đa một lần/ngày |
| B tìm lại A | Chiều B→A độc lập, vẫn được phép trong ngày đó |
| Một bot | Chỉ có một công việc thi đấu tại một thời điểm; không tự chọn đối thủ |

Hệ thống ưu tiên hai người đang tìm và Elo gần nhau; nếu chưa có sẽ xét rộng hơn hoặc chọn bot đủ điều kiện làm bên phòng thủ. Một bot có tối đa ba lượt phòng thủ từ ba người mời khác nhau/ngày. Bộ chỉ có ba tài khoản thì mỗi người chỉ có hai người khác để mời đấu.

Lượt được giữ khi ghép và tính đã dùng khi hoàn tất, theo ngày tạo trận. Bot thua vì lỗi vẫn tính lượt và Elo. Lỗi hệ thống được thử lại; nếu hết số lần thử vẫn không có kết quả hợp lệ thì hoàn lượt, không ghi Elo.

Sau khi ghép, kết quả **dự kiến có trong tối đa 10 phút**, gồm thời gian xếp hàng. Trang tự báo đã ghép, chờ chấm, đang chấm và đã chấm; không cần F5. Trạng thái tự kiểm khoảng 1 phút/lần; bấm Làm mới nếu muốn xem ngay. Quá tải/mất mạng có thể lâu hơn; quá 10 phút sẽ có thông báo chậm. Không tạo lại trận; có thể đóng trang rồi xem Kết quả sau. Thời gian xếp hàng không trừ đồng hồ CPU của bot.

## 5. Xem kết quả và điểm

Vào **Kết quả** để xem đối thủ, thắng/thua/hòa và lý do. Website hiện Elo của bạn ngay sau khi xử lý trận. Vào **Bảng xếp hạng** trong menu để mở Google Sheets; bảng được xuất khoảng **2 phút/lần**.

Elo bắt đầu **1000**, K=32. Hai người bằng điểm thì thắng/thua lần lượt +16/−16; hòa không đổi. Mỗi kết quả chỉ tính điểm một lần. Đổi bot, kho hoặc bản đồ không đặt lại Elo.

Tài khoản Form, tài khoản do quản trị tạo và tài khoản mẫu vai trò sinh viên đều có trên bảng; quản trị viên không được xuất. Có tên trên bảng không đồng nghĩa đã xác minh email.

## 6. Tải replay và giữ log

**Sinh viên phải tải replay HTML về máy trước khi thi đấu.** Google Drive chỉ lưu tệp; chọn **Tải xuống**, rồi mở HTML bằng trình duyệt trên máy. Website không tự theo dõi bạn đã tải hay chưa.

1. Ở trang đăng nhập hoặc **Tài liệu**, bấm **Tải replay**, tải và giữ tệp HTML.
2. Sau trận, vào **Kết quả → Sao chép log**. Nếu không được ghi clipboard, dùng ô văn bản hiện ra rồi nhấn Ctrl+C.
3. Mở HTML đã tải: ban đầu chỉ có bàn cờ, không có trận mẫu. Bấm **Dán JSON**, dán log và bấm **Mở JSON đã dán**. Dùng nút trước/tiếp hoặc tự phát để xem nước đi.
4. Muốn giữ lâu, lưu log thành tệp `.json` UTF-8; replay cũng mở được tệp đó.

**Cần thấy:** đúng hai bên, bàn đầu/cuối và kết quả. Có HTML và log rồi thì mất mạng vẫn xem lại được. Log chứa đầy đủ quân, đích, bom, ô cản; đổi bản đồ trong cùng định dạng không cần tải replay lại.

Website chỉ hiện **10 trận gần nhất**; log chi tiết giữ **7 ngày sau khi hoàn tất**. Trận có thể ra khỏi danh sách trước khi đủ 7 ngày nếu bạn đấu nhiều. Tự lưu sớm; hết hạn log vẫn giữ điểm/tóm tắt nhưng không lấy lại log từ hệ thống. Chỉ tải lại replay khi ban tổ chức thông báo bản mới.

## 7. Đổi mật khẩu, quên mật khẩu hoặc đổi link GitHub

Mở Form **Đổi / quên mật khẩu hoặc link GitHub** từ trang đăng nhập hoặc mục Tài khoản. Dùng đúng email trường đã đăng ký và đúng MSSV.

| Muốn đổi gì? | Chọn trong Form | Điền giá trị mới |
| --- | --- | --- |
| Đổi hoặc quên mật khẩu | Đổi / quên mật khẩu | Mật khẩu 12–256 ký tự; để trống link |
| Đổi kho bot | Đổi link GitHub | Link kho; để trống mật khẩu |
| Đổi cả hai | Đổi cả mật khẩu và link GitHub | Điền cả hai; chỉ áp dụng khi toàn bộ hợp lệ |

Chờ xử lý khoảng 1 phút/lần. Đổi mật khẩu kết thúc các phiên đăng nhập đang có, cần đăng nhập lại. Chỉ đổi link giữ mật khẩu và phiên; vào Bot của tôi bấm Cập nhật bot khi hết thời gian chờ. Đổi link không tự tải mã, không hoàn lượt và không đặt lại điểm.

Tài khoản cấp thủ công chưa có email xác minh thì liên hệ ban tổ chức; Form không tự lấy lại tài khoản đó. Không gửi mật khẩu trong tin nhắn hoặc ảnh chụp lỗi.

## 8. Bản đồ, khóa tài khoản và hỗ trợ

Bàn cờ ở trang đăng nhập/Thi đấu là **bản đồ khởi đầu hiện tại**, không phải hình trực tiếp của trận đang chấm. Bot phải đọc bàn, đích, bom và ô cản từ trạng thái, không mặc định vị trí giống mã mẫu.

Ban tổ chức có thể đổi bản đồ, tạm dừng ghép, tạm dừng cập nhật bot hoặc khóa tài khoản. Trận đã ghép vẫn dùng cấu hình đã chốt. Khi tài khoản bị khóa, phiên đăng nhập kết thúc và không được đăng nhập/tạo trận/cập nhật mới; dữ liệu vẫn giữ. Mở khóa thì đăng nhập lại bằng mật khẩu hiện tại; đổi mật khẩu không tự mở khóa.

| Vấn đề | Kiểm tra trước |
| --- | --- |
| Chưa đăng nhập được | Đúng MSSV/mật khẩu/email trường, đã chờ đồng bộ, tài khoản có bị khóa không |
| Không tìm trận được | Bot đã hợp lệ, còn lượt, không chờ/bận, giải đang mở |
| Chờ mãi chưa có kết quả | Xem trạng thái trận; có thể máy chấm/mạng đang chậm, không gửi lại trận |
| Sheet chưa đổi Elo | Chờ lịch xuất khoảng 2 phút rồi tải lại Sheet |
| Không lấy được log đã hết hạn | Kiểm 10 trận gần nhất và thời hạn 7 ngày; dùng bản đã lưu riêng |

Khi báo lỗi, gửi MSSV, mã trận/yêu cầu cập nhật, thời điểm và thông báo. Giữ nguyên log làm bằng chứng. Không gửi mật khẩu, cookie hoặc khóa máy chấm. Sinh viên không được tự đăng ký máy chấm để quyết định kết quả.
