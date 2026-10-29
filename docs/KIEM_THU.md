# Kiểm thử và số đo hệ thống

Tài liệu này ghi kết quả kiểm có bằng chứng và phạm vi của từng phép đo. Cấu hình thi đấu thống nhất: **7 giây CPU/bot, 2,5 giây thực/lượt, 25 nước mỗi bên, RAM 64 MiB**.

## 1. Kết quả kiểm chức năng

| Nhóm kiểm | Kết quả đã xác nhận |
| --- | --- |
| Python trên Ubuntu/Isolate | 533 ca được kiểm qua lượt toàn bộ và lượt chạy lại 32 ca; sau sửa kỳ vọng của bài kiểm, không còn lỗi chưa xử lý |
| HTTP, tài khoản, Google, bản đồ/đấu thử và bộ bật/cập nhật máy chấm | Có trong bộ Python; dùng dữ liệu thử riêng, không tạo tài khoản hoặc trận thử trong giải thật |
| JavaScript | 97/97 bài đạt cho portal, lịch sử, replay, Google bridge và bộ đấu thử |
| Isolate | Khởi động, RAM, mạng, hệ thống tệp và hạn thời gian thực đạt |
| Máy chấm VPS | 4/4 luồng online, dịch vụ tự bật cùng VPS |
| HTTPS/Google | Health thành công; Google ready=true, bridge phiên bản 3 |
| Gói nguồn | Đối chiếu CRC ZIP, SHA256SUMS và các tệp đã cài |

Các bài Python kiểm luật, giới hạn 50 nước, so quân, Elo một lần, quyền tài khoản, hạn mức, phục hồi kết quả chưa gửi, giữ bot hợp lệ khi cập nhật lỗi và máy chấm nhiều luồng. Kiểm bổ sung cho nút Desktop xác nhận không mở thêm pool, giữ profile/khóa và chọn đúng mức 6 trận của local.

Lượt toàn bộ chạy 533 ca trong 125,373 giây: 529 đạt, bốn ca còn dùng giả định không đúng chính sách hiện hành. Đã sửa ba kỳ vọng về hạn cập nhật bot thành 180 giây và sửa ca đăng nhập để kiểm từ chối phiên thứ hai, đăng xuất rồi vào lại. Chạy lại đủ ba nhóm liên quan: 32/32 đạt trên Ubuntu trong 3,250 giây. Không đổi luật hoặc cơ chế đăng nhập để làm vừa bài kiểm. Trước khi kiểm Isolate đã chờ hết việc thật và dừng dịch vụ tranh sandbox; kiểm xong bật lại dịch vụ và khôi phục điều khiển nhận việc.

Kiểm HTTP có đăng nhập/phân quyền, cookie an toàn, CSRF, khóa/mở sinh viên, đặt mật khẩu sinh viên, chặn đổi mật khẩu admin qua web, thêm sinh viên, điều khiển ghép/cập nhật, nhận trận và gửi kết quả qua RPC, đọc lịch sử đúng chủ, đăng xuất. Các thao tác thử dùng dữ liệu riêng.

Replay được kiểm dán JSON, mở tệp, tua tới đầu/cuối, phát/dừng, tốc độ, tái dựng bàn, đếm quân và đồng hồ CPU đo thật. Khi chưa mở JSON chỉ có bàn đầu, không có trận mẫu hoặc đồng hồ giả. HTML tự chứa ảnh/mã, không tự gọi mạng. Đã xem bố cục qua trình duyệt tại localhost; chưa kiểm mở trực tiếp file:// trong trình duyệt tự động do chính sách URL.

Các bài Google tự động dùng mô phỏng. Kiểm bridge/Sheet thật không thay cho việc thử mọi lựa chọn Form bằng email trường. Không suy từ bộ kiểm mã rằng toàn bộ dịch vụ bên ngoài luôn sẵn sàng.

Bộ đấu thử được đối chiếu engine Python qua 150 ván với bàn chuẩn và các vị trí khác nhau. Ba bot chạy bằng Python WebAssembly 3.12.7 qua Pyodide: mỗi cấp đạt READY và trả ba nước hợp lệ liên tiếp; kiểm trọn ván với bot mẫu kết thúc ở 15, 50 và 50 nước. Kiểm thêm READY sai, dừng hai luồng, không cấp lại thời gian khi nước sai và nhập cấu hình cùng lúc. Đây là kiểm bộ chạy/JavaScript, chưa chứng minh mọi main.py hoặc mọi trình duyệt đều tương thích; việc mở trực tiếp file:// bị trình duyệt tự động chặn nên chưa nghiệm thu thao tác đó bằng công cụ.

Kiểm trực tiếp giao diện HTML qua localhost với `examples/greedy/main.py`: cả ba cấp hoàn tất ở 15/50/50 nước. Ở 1280×720 và 1024×600, kích thước trang bằng khung nhìn, bàn cờ/nút điều khiển không bị đẩy xuống dưới; danh sách nước đi cuộn riêng. Cấu hình sai báo lỗi và giữ ván đang xem. Nút Dừng kết thúc hai bot và cho chọn lại cấp độ. HTML trên Drive đã được tải lại và đối chiếu SHA-256 với mã nguồn.

Kiểm riêng cập nhật nền xác nhận: các nút không bị khóa trong lúc chờ phản hồi; menu, ảnh bàn cờ và dòng kết quả giữ nguyên khi dữ liệu không đổi; không mất log đang chọn; phản hồi nền đến muộn không ghi đè thao tác Tìm trận; heartbeat không dựng lại danh sách máy chấm. Nút sao chép cấu hình chỉ nằm ở trang đăng nhập và Thi đấu của sinh viên.

## 2. Hiệu năng hiện hành

Các số đo HTTP, API với 4.000 tài khoản giả và máy chấm 4 trận song song được tập trung tại [HIEU_NANG.md](HIEU_NANG.md). Dữ liệu đo ở [hieu-nang.json](hieu-nang.json). Tài liệu hiệu năng tách số đo thực tế khỏi công thức ước tính, không suy từ API nhanh rằng mọi luồng thi đấu đồng thời đều đạt.

## 3. Tự chạy kiểm thử

Dùng máy thử có Ubuntu và Isolate. Nếu dùng máy đang chấm, tạm dừng nhận trận/cập nhật, chờ hết pending/running, dừng đúng pool/dịch vụ trước. Bộ kiểm không được tranh sandbox với trận thật.

Trong **Ubuntu**, tại thư mục có README:

```bash
sudo systemctl start isolate.service
python3 -m unittest discover -s tests -v
```

Cần thấy tổng kết `OK`. Trong **PowerShell hoặc Ubuntu** đã có Node.js, tại thư mục mã nguồn:

```text
node --test tests/*.test.cjs
```

Cần thấy `fail 0`. Node.js chỉ dùng kiểm JavaScript, không bắt buộc trên máy chấm Python. Để kiểm riêng bộ bật máy chấm/cập nhật/đóng gói:

```bash
python3 -m unittest tests.test_worker_launch tests.test_worker_update tests.test_deploy -v
```

Kiểm xong phải bật lại đúng dịch vụ, xác nhận online rồi mở lại hàng đợi nếu đã tạm dừng. Xem lệnh bật/tắt ở [MAY_CHAM.md](MAY_CHAM.md).

## 4. Đo thêm máy chấm

Chỉ đo trên máy không có pool đang chạy. Trong Ubuntu, tại thư mục mã:

```bash
python3 scripts/benchmark_worker.py --slots 1 2 4 --matches 16 --output /tmp/ottv2-worker-measure.json
```

Tệp kết quả phải chưa tồn tại. So thời gian cả đợt, số trận quá hạn và dọn sandbox. Chọn số chỗ mà bot vẫn trả lời kịp hạn; không chỉ nhìn số trận kết thúc hoặc số luồng CPU. Muốn đo 6/12 chỗ, thay danh sách slots và kiểm tài nguyên trước.

## 5. Danh sách kiểm bằng tài khoản thử

| Việc kiểm | Kết quả cần thấy |
| --- | --- |
| Sinh viên/admin đăng nhập | Đúng menu và quyền; không đọc được dữ liệu người khác |
| Đăng ký/đổi thông tin qua Form | Đối chiếu email/MSSV, xử lý và báo trạng thái đúng |
| Bot hợp lệ và bot lỗi | Bản tốt được dùng; bản lỗi không thay bài đang chạy |
| Ghép trận, hoàn tất, làm mới | Kết quả, lượt và Elo chỉ ghi một lần |
| Sao chép log, mở/dán JSON | Đúng bàn đầu/cuối, CPU, quân và kết quả |
| Khóa/mở khóa sinh viên | Phiên đăng nhập bị thu hồi khi khóa; dữ liệu giữ nguyên |
| Docs/Drive/Sheet | Đúng liên kết, quyền đọc/tải và tên mục |
| Desktop máy chấm | Bấm một lần mở đúng máy, không sinh thêm pool |
| Mất mạng/khởi động lại | Giữ kết quả chưa gửi, kết nối lại, không tính điểm trùng |
