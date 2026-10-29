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

Kho mã nguồn và bộ tài liệu đi kèm hướng dẫn người tổ chức **tự cài đặt, triển khai và vận hành một hệ thống giải đấu riêng**: chuẩn bị môi trường, dựng website, kết nối máy chấm và thiết lập các dịch vụ Google. Người triển khai có thể chọn VPS Ubuntu và các máy chấm phù hợp với điều kiện của mình. Tên miền, tên máy và tài nguyên Google của nhóm là cấu hình tham khảo; khi dựng một hệ thống độc lập, cần thiết lập địa chỉ, tài khoản và khóa riêng cho hệ thống đó.

**Bản triển khai của nhóm:** [ottv2-nhom12.koreacentral.cloudapp.azure.com](https://ottv2-nhom12.koreacentral.cloudapp.azure.com). Cách tự triển khai bắt đầu từ [mục lục tài liệu](docs/README.md).

**Đọc trước:** [Hướng dẫn sinh viên](docs/QUY_DINH_SINH_VIEN.md) · [Luật và cách viết main.py](docs/VIET_BOT.md) · [Mục lục toàn bộ tài liệu](docs/README.md).

### Góc nhìn người xây dựng và tổ chức giải

Tôi xây dựng OTTv2 để tổ chức giải đấu bot cho sinh viên: người chơi có thể tự đăng ký, cập nhật bài, tìm đối thủ và xem kết quả; người quản lý có thể điều hành giải, đổi bản đồ, quản lý tài khoản và theo dõi máy chấm. Bài toán của tôi là vận hành được các công việc đó với ngân sách phù hợp, tận dụng máy tính sẵn có và giảm thao tác thủ công khi số người tham gia tăng.

Tôi chia hệ thống theo tính chất công việc: VPS giữ dữ liệu và điều phối; các máy chấm xử lý bot; Google phục vụ biểu mẫu, bảng điểm và tài liệu; máy sinh viên xử lý đấu thử và replay. Cách chia này giúp dùng tài nguyên đúng chỗ, bổ sung sức chấm khi cần và kiểm soát chi phí thuê máy chủ.

### Chi phí triển khai web hiện tại

**Chi phí hạ tầng web mà nhóm đang tự chi trả: 0đ**, nhờ sử dụng quyền lợi **Azure for Students**. Chi phí sử dụng tài nguyên Azure hiện được bù bằng tín dụng của gói sinh viên. Mức 0đ này áp dụng trong phạm vi ưu đãi còn hiệu lực và đủ tín dụng; người tự triển khai cần dự toán theo gói và hạ tầng mình chọn. Thông tin chương trình: [Azure for Students](https://learn.microsoft.com/azure/education-hub/about-azure-for-students).

### Tôi tối ưu chi phí và vận hành như thế nào?

| Lựa chọn của tôi | Lợi ích khi tổ chức giải | Đọc kỹ cơ chế |
| --- | --- | --- |
| Một VPS duy trì website và 4 chỗ chấm; bật thêm các PC sẵn có khi đông người | Có năng lực chấm thường trực, bổ sung công suất bằng thiết bị của nhóm | [Phân chia máy chấm](docs/TOI_UU.md#5-chia-việc-chấm-và-luôn-có-năng-lực-nền) · [Cài và bật máy](docs/MAY_CHAM.md) |
| Kết nối Google Forms/Sheets bằng Apps Script; hướng dẫn và HTML tải trực tiếp từ Docs/Drive | Giảm phần biểu mẫu và nội dung mà VPS phải phục vụ cho từng sinh viên | [Phần việc đưa ra dịch vụ ngoài](docs/TOI_UU.md#1-chuyển-việc-đọc-và-xem-lại-ra-ngoài-vps) |
| Đấu thử Python và xem replay trên máy sinh viên | Việc luyện bot và xem lại trận sử dụng tài nguyên của người xem, giảm việc chấm thử trên VPS | [Đấu thử](docs/DAU_THU.md) · [Cách giảm tải đọc/xem](docs/TOI_UU.md#1-chuyển-việc-đọc-và-xem-lại-ra-ngoài-vps) |
| Kiểm trạng thái mỗi phút, dừng hỏi khi tab ẩn, chỉ cập nhật phần giao diện đổi và tải log khi cần | Giảm yêu cầu lặp, dữ liệu truyền và hiện tượng nháy giao diện | [Giảm số lần hỏi trạng thái](docs/TOI_UU.md#2-giảm-số-lần-hỏi-trạng-thái) |
| Khi cập nhật bot, lấy commit mới nhất rồi chốt phiên bản; cache nội dung tải có giới hạn | Dùng đúng bài đã nhận, giảm tải lặp từ GitHub và kiểm soát RAM cache | [Tải bot và cache](docs/TOI_UU.md#4-tải-bot-github-theo-commit-và-dùng-cache-có-giới-hạn) |
| Giới hạn tài nguyên bot, lưu kết quả chưa gửi và chống ghi Elo trùng | Kiểm soát việc chiếm tài nguyên, phục hồi khi mất mạng và giảm công xử lý sự cố | [Luật và tài nguyên](docs/VIET_BOT.md) · [Giữ kết quả](docs/TOI_UU.md#6-tránh-mất-kết-quả-và-tính-điểm-trùng) |
| Đưa cập nhật bot vào hàng đợi, chỉ giữ một yêu cầu đang xử lý cho mỗi sinh viên | Trang trả trạng thái sớm, hạn chế gửi trùng và tách việc tải/kiểm bot khỏi yêu cầu HTTP | [Công việc nền](docs/TOI_UU.md#9-xử-lý-cập-nhật-bot-ở-nền-và-hạn-chế-việc-trùng) |
| Dọn log hết hạn theo lô, giữ điểm và tóm tắt trận | Kiểm soát dung lượng lưu trữ và khối lượng kiểm dọn mỗi lượt | [Quản lý log](docs/TOI_UU.md#10-kiểm-soát-dung-lượng-log-bằng-các-lượt-dọn-nhỏ) |

Tôi kiểm các lựa chọn bằng số đo: API nội bộ hoàn thành 6.500/6.500 yêu cầu qua ba mức tải; ở mức 200 yêu cầu/giây, P95 là 3,361 ms. VPS chấm 8 trận bot mẫu sâu 3 với 4 trận song song trong 16,960 giây. Các mẫu này có điều kiện đo riêng, chưa chứng minh 4.000 sinh viên thi đấu đồng thời. **Số liệu, dữ liệu JSON và giới hạn phép đo:** [HIEU_NANG.md](docs/HIEU_NANG.md). **Toàn bộ tối ưu, cách thực hiện và đánh đổi:** [TOI_UU.md](docs/TOI_UU.md).

Việc giảm chi phí đi cùng lựa chọn vận hành: trạng thái nền có thể hiển thị chậm gần một phút, Google có hạn mức riêng, PC bổ sung cần được bật và giữ kết nối. Tôi theo dõi hàng đợi, lỗi chấm và số đo thực tế để quyết định khi nào cần thêm tài nguyên. Kiến trúc chi tiết ở [THIET_KE.md](docs/THIET_KE.md); thao tác của người tổ chức ở [VAN_HANH.md](docs/VAN_HANH.md).

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
| GitHub | Khi cập nhật, lấy commit mới nhất của nhánh mặc định rồi chốt phiên bản để tải, kiểm và thi đấu |

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

VPS D2as_v4 2 vCPU/8 GiB chạy website và 4 luồng chấm thường trực. Các PC cấu hình local/Sinh/Hải/Dương lần lượt 6/12/6/12 luồng; chỉ nhận thêm việc khi bật và có kết nối. Số luồng không phải số trận hoàn tất mỗi giây. Cài PC một lần, những lần sau bấm nút Desktop theo MAY_CHAM; không dán lại lệnh cài mỗi ngày.

Đổi mật khẩu admin bằng dòng lệnh **trên VPS mới**, không dùng Form hoặc máy chấm:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.accounts --db /var/lib/ottv2/data/accounts.sqlite3 password admin
```

Nhập mật khẩu mới hai lần; không hiện ký tự lúc gõ là bình thường. Cần thấy `Đã lưu`, sau đó đăng nhập lại. Không ghi mật khẩu vào mã hoặc đối số lệnh.

## 6. Mã nguồn và tài liệu

`ottv2/`: xử lý hệ thống · `web/`: website · `practice/`: HTML đấu thử · `replay/`: HTML xem lại · `google/`: Apps Script · `maps/`, `examples/`: bản đồ/bot mẫu · `deploy/`, `scripts/`: triển khai/vận hành · `tests/`: kiểm tự động · `docs/`: hướng dẫn.

Repository của dự án: [Doanhh17/ottv2-final](https://github.com/Doanhh17/ottv2-final). Nguồn hiện hành đi kèm `SHA256SUMS` và tài liệu; dữ liệu tài khoản, khóa và output đặt riêng. Mã dựng HTML được giữ để tạo lại đúng tệp tải xuống. Số đo thực tế, phạm vi và công thức ước tính ở [HIEU_NANG.md](docs/HIEU_NANG.md); kết quả kiểm chức năng ở [KIEM_THU.md](docs/KIEM_THU.md).
