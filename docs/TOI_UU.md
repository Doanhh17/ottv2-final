# Những phần hệ thống đã tối ưu

Tài liệu giải thích **đã làm gì, giảm tải ở đâu và còn giới hạn nào**. Thông số đo và cách kiểm nằm trong [KIEM_THU.md](KIEM_THU.md); đây không phải lời hứa về số người dùng khi chưa đo.

### Chi phí của bản triển khai hiện tại

Nhóm đang tự chi trả **0đ cho hạ tầng web Azure**, sử dụng tín dụng từ quyền lợi [Azure for Students](https://learn.microsoft.com/azure/education-hub/about-azure-for-students). Đây là chi phí trong điều kiện ưu đãi hiện tại; tài nguyên Azure vẫn tiêu thụ tín dụng theo mức sử dụng. Các PC chấm là thiết bị sẵn có; con số trên chưa tính điện, mạng và khấu hao thiết bị. Khi tự triển khai, người tổ chức chọn hạ tầng và theo dõi ngân sách của mình. Các cơ chế dưới đây giúp tiết kiệm tài nguyên cả khi dùng ưu đãi sinh viên lẫn khi thuê máy theo giá thông thường.

## 1. Chuyển việc đọc và xem lại ra ngoài VPS

| Phần việc | Cách xử lý hiện tại | Tác dụng |
| --- | --- | --- |
| Đăng ký/đổi thông tin | Google Forms, máy quản lý nhận khoảng 1 phút/lần | Không giữ một yêu cầu web chờ xử lý Form |
| Hướng dẫn và mã mẫu | Docs/Drive phục vụ đọc và tải | VPS không phải phục vụ nội dung tài liệu dài |
| Bảng xếp hạng | Xuất toàn bảng sang Google Sheets khoảng 2 phút/lần | Website chỉ đọc Elo cá nhân |
| Replay | Một tệp HTML tải về; dựng bàn, tua và phát ở máy sinh viên | Không gửi ảnh từng nước hoặc chạy phát lại trên VPS |
| Đấu thử | HTML nhận main.py của sinh viên và cấu hình bản đồ/3 bot từ web; tính toán trong Web Worker trên máy sinh viên | Không tạo trận thử trên VPS hoặc chiếm luồng Isolate chính thức |
| Log trận | Chỉ tải khi bấm Sao chép log; giao diện hiện 10 trận gần nhất | Không tải mọi replay khi vừa mở trang |

Log chi tiết giữ 7 ngày; sinh viên tự lưu JSON muốn giữ lâu. HTML replay chứa sẵn mã và ảnh, không tự gọi mạng; trước khi mở JSON chỉ hiển thị bàn cờ.

### Google Apps Script nối Google với VPS như thế nào?

Dự án dùng **Google Apps Script**, cụ thể là [google/Code.gs](../google/Code.gs), triển khai thành Web app chạy bằng tài khoản Google của người tổ chức. Bộ đồng bộ [ottv2/google_sync.py](../ottv2/google_sync.py) trên VPS gọi địa chỉ Web app qua HTTPS. Yêu cầu có chữ ký xác thực HMAC, thời điểm và mã chỉ dùng một lần; Apps Script kiểm trước khi đọc Forms hoặc ghi Sheets. Khóa đặt riêng ở VPS và thuộc tính của Apps Script, không đưa vào mã nguồn hoặc ZIP.

1. **Nhận yêu cầu từ Forms:** sinh viên gửi Form đăng ký hoặc Form đổi mật khẩu/GitHub. Khoảng 1 phút/lần, VPS nhờ Apps Script đọc hồ sơ. VPS kiểm thông tin và lưu tài khoản hoặc thay đổi vào SQLite; mật khẩu trên VPS được băm. Apps Script không tự quyết định quyền tham gia hoặc điểm Elo.
2. **Xác nhận xử lý:** sau khi lưu, VPS gửi biên nhận cho Apps Script. Script giữ thông tin xử lý không chứa mật khẩu và xóa câu trả lời Form đã xác nhận. Nếu kết nối đứt, hai bên dùng mã phản hồi/biên nhận để thử lại mà không áp dụng hồ sơ hai lần.
3. **Xuất bảng điểm:** khoảng 2 phút/lần, VPS tổng hợp điểm chính thức rồi gửi cho Apps Script ghi Google Sheets. Sinh viên đọc bảng điểm trực tiếp từ Sheets, nên mỗi lần mở bảng không phải tải cả danh sách từ VPS.
4. **Đọc tài liệu và tải HTML:** sinh viên mở Google Docs hoặc tải tệp từ Drive bằng liên kết trực tiếp. Những lượt này do Docs/Drive phục vụ, không đi qua Apps Script hay VPS. Replay/đấu thử tính toán tiếp trên máy sinh viên.

Lợi ích là VPS không phải tự xây phần thu biểu mẫu, phục vụ bảng điểm cho từng người hoặc truyền tài liệu dài. VPS vẫn xử lý tài khoản, hàng đợi, luật và Elo; máy chấm Isolate vẫn chấm trận chính thức. Chu kỳ 1/2 phút là lịch kiểm khi kết nối hoạt động, không bảo đảm mọi hồ sơ xong ngay nếu có nhiều người gửi hoặc Google/mạng gặp lỗi. Cách cấu hình Web app và quyền Google: [VAN_HANH.md](VAN_HANH.md).


## 2. Giảm số lần hỏi trạng thái

Giao diện tự kiểm trạng thái khoảng **1 phút/lần**, thêm tối đa 1 giây ngẫu nhiên để tránh nhiều máy hỏi cùng lúc. Với 4.000 phiên cùng mở trang cần theo dõi, khoảng 67 yêu cầu/giây; đây là tính toán theo cấu hình, không phải kết quả đo tải mới. Tab ẩn không hỏi; đăng xuất thì dừng. Trang Thi đấu/Kết quả theo dõi cả trận phòng thủ; Bot của tôi theo dõi yêu cầu cập nhật; Trạng thái admin theo dõi máy chấm. Có thể bấm Làm mới để đọc ngay. API `/api/status` chỉ trả phần cần thiết; lịch sử tải lại khi có kết quả mới.

Nhịp cố định này không cần đếm phiên hoặc giữ cache số phiên để chọn thời gian chờ. Bấm Tìm trận/Cập nhật bot vẫn gửi ngay; việc ghép/chấm chạy riêng, không phải đợi nhịp kiểm của trình duyệt. Nếu trạng thái thay đổi giữa hai nhịp, người dùng có thể thấy chậm gần một phút.

Kiểm tra nền không làm mờ hoặc khóa nút đang dùng. Giao diện chỉ thay chữ, ảnh bàn cờ hoặc danh sách khi nội dung hiển thị đổi; không dựng lại menu, tải lại cùng ảnh hoặc xóa vùng log đang chọn sau mỗi nhịp. Phản hồi nền đến muộn không ghi đè thao tác mới của người dùng.

Mỗi lần đọc trạng thái dùng cùng một giao dịch SQLite để lấy ghép trận/hạn mức, giảm số lần mở dữ liệu và tránh hai thông tin lệch nhau. Log được tải riêng theo yêu cầu.

## 3. Giữ kết nối và giới hạn đúng loại yêu cầu

| Đường yêu cầu | Giới hạn Nginx hiện hành |
| --- | --- |
| Trạng thái `/api/status` | 2 yêu cầu/giây/khóa phiên; 400 yêu cầu/giây/IP |
| Máy chấm `/api/worker` | 40 yêu cầu/giây/IP, đợt ngắn tối đa 80 |
| Đăng nhập | 2 yêu cầu/giây toàn website |
| Đường thông thường | 10 yêu cầu/giây/IP |

Ứng dụng vẫn xác thực phiên/khóa và kiểm tài khoản bị khóa. Giới hạn riêng cho trạng thái/máy chấm giúp nhiều người hoặc PC dùng chung mạng trường. Nginx giữ kết nối 45 giây; cấu hình cho phép 4.096 kết nối mỗi worker và giới hạn tệp 16.384. Đây là giới hạn cấu hình, không có nghĩa mọi mức tải đều đã đạt.

## 4. Tải bot GitHub theo commit và dùng cache có giới hạn

Khi xử lý yêu cầu Cập nhật bot, ứng dụng đọc commit mới nhất của nhánh mặc định bằng API rồi tải ZIP công khai từ codeload đúng commit đó. Lần cập nhật tiếp theo đọc lại commit mới nhất. Cache phân biệt theo repository và commit, giữ tối đa **16 ZIP, tổng 8 MiB**, tránh tải lặp cùng nội dung. Khi GitHub giới hạn tốc độ, yêu cầu được giữ và thử lại vào lúc phù hợp; bot đang hợp lệ vẫn được dùng. Trận đã ghép giữ phiên bản được chốt cho trận.

GitHub token chỉ là cấu hình tùy chọn cho hạn mức API. Nếu dùng, token đặt riêng ngoài nguồn, chỉ gửi đến API GitHub, không gửi đến codeload và không đóng trong ZIP. Cách cấu hình ở [TRIEN_KHAI_VPS.md](TRIEN_KHAI_VPS.md).

## 5. Chia việc chấm và luôn có năng lực nền

VPS **D2as_v4, 2 vCPU/8 GiB** chạy website và một dịch vụ chấm độc lập **4 trận song song**. Dịch vụ tự bật cùng VPS; khi ít trận có thể dùng riêng VPS. Các PC bổ sung khi cần: local 6, Sinh 12, Hải 6, Dương 12 chỗ. Tổng tối đa 40 chỗ cấu hình; thời gian xong phụ thuộc bot và CPU thực tế.

Máy chấm nhận việc qua HTTPS bằng khóa riêng, không cần Google. Hai bot của mỗi trận dùng sandbox riêng; dùng chung CPU khi chọn `shared`. Giới hạn RAM và CPU theo từng bot, deadline theo từng lượt. Một máy mất liên lạc không làm mất toàn bộ hàng đợi; việc chưa hoàn tất được giao lại trong giới hạn thử.

## 6. Tránh mất kết quả và tính điểm trùng

Máy chấm lưu `outbox.json` trước khi gửi kết quả. Mất mạng thì gửi lại cùng bản ghi; chỉ bỏ khi được xác nhận hoặc quyền nhận việc hết hiệu lực. Máy quản lý kiểm lại lịch sử theo luật và ghi biên nhận theo mã trận/mã băm, nên gửi lại không cộng Elo lần nữa.

Trận đã ghép giữ bản đồ, phiên bản bot và giới hạn của chính trận đó. Cập nhật bot hoặc đổi bản đồ không làm trận đang chấm dùng lẫn dữ liệu.

## 7. Giảm thao tác vận hành

Mỗi PC cài môi trường và dịch vụ một lần, sau đó tạo **nút Desktop** bằng `scripts/install_worker_desktop.ps1`. Những lần sau bấm đúp đúng nút; chương trình giữ khóa, profile và không mở pool thứ hai. VPS không cần Desktop và không tự bật máy tính từ xa.

Nguồn và tài liệu vận hành được quản lý tập trung, có SHA256SUMS để kiểm tính nguyên vẹn. Khóa và dữ liệu vận hành đặt riêng. Người tổ chức cài thêm PC theo [MAY_CHAM.md](MAY_CHAM.md), tạo nút Desktop một lần và dùng lại những ngày sau.

Số đo hiện hành tập trung tại [HIEU_NANG.md](HIEU_NANG.md), có [JSON gốc](hieu-nang.json) để đối chiếu. Độ trễ API, độ trễ qua Internet và tốc độ chấm là ba phép đo khác nhau; công thức ước tính được ghi riêng.

## 8. Giữ dữ liệu và chỉ dẫn cùng một cấu hình

Quản trị lưu bản đồ và ba mã bot đấu thử trong cùng giao dịch; lỗi một phần thì không thay gì. Nút Sao chép chỉ có ở màn hình đăng nhập và trang Thi đấu của sinh viên; nút xuất một gói công khai, giúp sinh viên lấy đúng bản đồ và đối thủ hiện tại. Cú pháp mã được kiểm bằng AST, không chạy trên VPS khi lưu. Bộ đấu thử dùng thời gian thực vì trình duyệt không đo được CPU/áp RAM như Isolate; xem [DAU_THU.md](DAU_THU.md).

Một tài khoản chỉ có một phiên 30 phút, giúp tránh hai nơi cùng thao tác một bot. Hết phiên sinh viên chờ 7 phút, đăng xuất sớm và admin không có khoảng chờ. Việc kiểm phiên/tài khoản khóa diễn ra trên máy chủ, không chỉ ẩn nút trong giao diện.

## 9. Xử lý cập nhật bot ở nền và hạn chế việc trùng

Khi sinh viên bấm Cập nhật bot, API ghi yêu cầu vào SQLite và trả trạng thái đang chờ. Vòng xử lý nền mới tải GitHub và kiểm bot; một yêu cầu HTTP không phải giữ kết nối suốt công việc đó. Mỗi tài khoản chỉ có một yêu cầu ở trạng thái chờ/đang xử lý, được ràng buộc cả trong cơ sở dữ liệu. Khoảng cách 3 phút giữa hai yêu cầu được nhận giúp hạn chế gửi liên tục.

Nếu GitHub báo giới hạn tốc độ, yêu cầu được giữ lại cùng thời điểm thử tiếp; các yêu cầu chưa tới thời điểm được bỏ qua khi chọn việc. Việc này giảm thử lại vô ích. Tải hoặc kiểm bot lỗi thì giữ phiên bản hợp lệ đang dùng. Hàng đợi vẫn có thể dài khi nhiều sinh viên cập nhật cùng lúc; đây là cách điều tiết và giữ trạng thái, không làm mất thời gian tải/kiểm thực tế.

Đối chiếu mã: [request_update/process_update](../ottv2/portal_data.py), [vòng xử lý nền](../ottv2/local_arena.py), [ràng buộc một yêu cầu](../ottv2/accounts.py), [hạn cập nhật](../ottv2/update_gate.py).

## 10. Kiểm soát dung lượng log bằng các lượt dọn nhỏ

Log chi tiết được giữ 7 ngày; điểm và tóm tắt trận tiếp tục được lưu. Khoảng 60 giây một lần, máy quản lý kiểm tối đa 100 bản ghi đủ điều kiện, giúp giới hạn số mục cần xử lý trong một lượt. Cách này tránh để một lần kiểm dọn phải duyệt toàn bộ lịch sử khi giải kéo dài.

Chỉ bản ghi hoàn tất, có biên nhận bền vững và đúng mã băm mới được dọn khi hết hạn. Tệp phục hồi của trận chưa xong và tệp nghi vấn được giữ lại để xử lý. Sinh viên cần tự lưu JSON nếu muốn giữ replay lâu hơn. Các mốc 7 ngày/60 giây/100 bản ghi là chính sách dung lượng và khối lượng mỗi lượt; chưa có phép đo riêng để khẳng định chúng giảm bao nhiêu phần trăm tài nguyên.

Đối chiếu mã: [log_retention.py](../ottv2/log_retention.py); cách vận hành ở [VAN_HANH.md](VAN_HANH.md).
