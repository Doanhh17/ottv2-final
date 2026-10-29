# Thiết kế và yêu cầu hệ thống

Dành cho người đọc mã nguồn hoặc thuyết trình bài tập lớn. Tài liệu này tập trung vào kiến trúc và cách xử lý; cách cài nằm ở CAI_DAT/TRIEN_KHAI_VPS/MAY_CHAM, quy định sinh viên nằm ở QUY_DINH_SINH_VIEN, luật và JSON của bot nằm ở VIET_BOT.

## 1. Phạm vi và yêu cầu chính

Hệ thống có **một máy quản lý** và một hoặc nhiều **máy chấm**. Máy quản lý giữ dữ liệu có thẩm quyền; máy chấm do ban tổ chức quản lý và cấp khóa. Sinh viên không tự đăng ký máy chấm. VPS hiện tại chạy website và dịch vụ máy chấm riêng thường trực 4 trận; PC bật thêm sẽ cùng nhận việc. Hai dịch vụ có thể dừng/khởi động độc lập. Cách chạy local có thể để một tiến trình quản lý kiêm chấm một trận.

| Yêu cầu | Cách đáp ứng |
| --- | --- |
| Tài khoản và phân quyền | Sinh viên và quản trị có chức năng riêng; kiểm quyền phía API |
| Đăng ký/đổi thông tin | Google Form, đối chiếu email trường/MSSV và lưu biên nhận xử lý |
| Bài nộp có phiên bản | Tải GitHub theo commit, kiểm ZIP và READY rồi mới nhận bot |
| Ghép trận tự động | Ưu tiên hai người cùng tìm, Elo gần; sau đó xét rộng hơn hoặc chọn bot phòng thủ |
| Lượt và điểm | Giữ/dùng/hoàn lượt bằng giao dịch; kết quả có biên nhận để không tính Elo trùng |
| Chạy bot có giới hạn | Isolate đo CPU/RAM và hạn chế mạng/tệp; kiểm phản hồi bằng thời gian thực |
| Thêm năng lực chấm | Mỗi máy 1–12 luồng, khóa riêng cho từng luồng, nhận việc qua HTTPS |
| Xem lại và giữ kết quả | Log chứa bàn ban đầu và nước đi; replay dựng lại trên máy sinh viên |
| Điều hành | Khóa/mở khóa sinh viên, điều khiển ghép/cập nhật, đổi bản đồ |
| Phục hồi | Giữ phiên bản/bản đồ đã chốt, gửi lại kết quả không ghi trùng, sao lưu có mã băm |

Mục tiêu số tài khoản đăng ký khác năng lực phục vụ đồng thời. Phần đã đo và giới hạn nghiệm thu chỉ công bố tại [KIEM_THU.md](KIEM_THU.md).

## 2. Kiến trúc và giao tiếp mạng

```text
Sinh viên ── HTTPS ──> Nginx ── HTTP loopback ──> Máy quản lý
    │                                               │
    ├── Forms / Docs / Sheets / Drive                ├── SQLite, bot, log
    └── Replay HTML trên máy                        ├── GitHub theo commit
                                                    ├── Apps Script qua HTTPS
                                                    └── HTTPS RPC <── Máy chấm 1, 2, ...
                                                                        └── Isolate: bot A, bot B
```

- **Trình duyệt → máy quản lý:** GET đọc trang/dữ liệu; POST JSON cho thao tác ghi. Nginx kết thúc TLS, giới hạn tốc độ rồi chuyển vào Python ở 127.0.0.1:8765.
- **Máy quản lý → Google:** Apps Script thực thi dưới chủ tài nguyên; yêu cầu có chữ ký HMAC, thời điểm và mã chống lặp. Không gửi khóa này cho trình duyệt hoặc Worker.
- **Máy quản lý → GitHub:** chốt commit, tải ZIP của commit đó và kiểm trước khi phát hành phiên bản bot.
- **Máy chấm → máy quản lý:** máy chấm chủ động hỏi việc qua POST /api/worker, không cần mở cổng nhận từ Internet. Xác thực Bearer riêng; client từ chối chuyển hướng để tránh gửi khóa sang host khác.
- **Bot ↔ máy chấm:** stdin/stdout theo dòng JSON; stderr dành cho chẩn đoán. Hai tiến trình bot sống suốt trận, chỉ bên đến lượt nhận trạng thái để trả nước.

Giao diện tự hỏi trạng thái để hiện ghép trận và kết quả. Nhịp cố định 60 giây cộng 0–1 giây ngẫu nhiên; không cần đếm/cache số phiên. Với 4.000 phiên cùng mở trang cần theo dõi, khoảng 67 yêu cầu/giây theo tính toán cấu hình. Không gửi chồng yêu cầu, bỏ qua tab ẩn và dừng khi đăng xuất. Trang Thi đấu/Kết quả vẫn kiểm khi chưa có trận để nhận trận phòng thủ mới; trang Bot kiểm khi còn cập nhật/thời gian chờ. Trang Trạng thái admin tự làm mới và đưa luồng rảnh đang kết nối lên trước. Kiểm tra nền không khóa nút, không dựng lại menu/danh sách hoặc gán lại cùng ảnh. Chỉ các phần hiển thị có thay đổi mới được cập nhật; phản hồi nền quá cũ bị bỏ khi người dùng đã thao tác mới. Mỗi lần đọc trạng thái lấy hạn mức và trạng thái ghép trận trong cùng một giao dịch SQLite để hai thông tin khớp nhau. Log chỉ tải khi bấm sao chép. Đọc tài liệu, bảng điểm và phát replay được chuyển sang Google/máy sinh viên để giảm lượng dữ liệu qua VPS.

Riêng API đọc trạng thái có giới hạn 2 yêu cầu/giây cho mỗi khóa phiên và 400 yêu cầu/giây/IP, giúp nhiều sinh viên dùng chung mạng trường. Ứng dụng vẫn kiểm tra phiên và tài khoản bị khóa ở mọi yêu cầu; chuỗi giống khóa không đủ để đăng nhập. Các đường khác giữ giới hạn riêng: đăng nhập 2 yêu cầu/giây toàn website, máy chấm 40 yêu cầu/giây/IP, đường thông thường 10 yêu cầu/giây/IP. Kết nối HTTPS được giữ 45 giây để các lượt kiểm tra trạng thái có thể dùng lại kết nối.

## 3. Mô-đun và dữ liệu

| Nhóm mã | Trách nhiệm |
| --- | --- |
| engine, map_config, protocol | Luật cờ, kiểm bản đồ, trạng thái gửi bot |
| isolated_session, cpu_budget, local_runner | Tiến trình Isolate, đồng hồ và chạy một trận |
| accounts, portal_api, portal_data | Tài khoản, quyền, API và dữ liệu từng trang |
| bot_store, github_update, ready_check | Phiên bản mã, tải GitHub và kiểm khởi động |
| match_queue, daily_quota, elo_store | Ghép trận, hạn mức và điểm |
| cluster, remote_worker, worker_pool | Giao việc phân tán, gửi kết quả, nhiều luồng chấm |
| google_sync, google/Code.gs | Kết nối Form/Sheet và xử lý biên nhận |
| result_log, match_archive, log_retention, backup | Bản ghi, giới hạn lưu và sao lưu/khôi phục |
| web, replay | Giao diện website và trình xem lại offline |

Dữ liệu giải được tách khỏi mã nguồn:

```text
data/
├── accounts.sqlite3
├── queue.sqlite3
├── elo.sqlite3
├── bots/<bot_id>/current.json
├── bots/<bot_id>/versions/<sha256>.py
├── matches/<match_id>.json
└── google-client.json       Chỉ có khi nối Google, tệp riêng quyền 600
```

Tên sổ điểm/đường dẫn thực tế lấy từ cấu hình queue; các hướng dẫn cài của dự án dùng elo.sqlite3. Accounts giữ tài khoản, phiên, yêu cầu cập nhật, email xác minh và biên nhận Google. Queue giữ bản đồ, hàng chờ, job, lượt và registry/lease máy chấm. Elo giữ điểm cùng biên nhận kết quả.

Mật khẩu băm PBKDF2-SHA256 với salt riêng. Khóa tài khoản ghi đồng thời trạng thái tài khoản và chặn ghép trong một giao dịch SQLite ATTACH, dùng rollback journal. Không xóa tài khoản khi khóa.

## 4. Từ cập nhật mã đến kết quả trận

### Cập nhật bot

Máy quản lý kiểm link kho, lấy commit cố định, giới hạn tải/giải nén, đường dẫn và số tệp Python. Chỉ main.py được nhập vào kho phiên bản sau khi kiểm READY trong Isolate. Tệp nguồn đặt tên theo SHA-256; current.json chỉ đổi sau khi bản mới đạt. Nếu tải/khởi động lỗi thì giữ bot tốt trước đó.

### Ghép trận và lượt

Máy quản lý ưu tiên hai người đang chờ, xét độ chênh Elo theo các mức 100, 200, 400 rồi rộng hơn; chỉ khi chưa có hai người cùng tìm trận mới chọn bot phòng thủ hợp lệ. Job chốt người chơi, vai trò tính lượt, phiên bản, bản đồ, seed và bên đi trước. Bảng busy ngăn một bot vào hai job cùng lúc.

Lượt tấn công/phòng thủ được giữ lúc ghép. Hoàn tất thì chuyển thành đã dùng; lỗi hệ thống hết số lần thử thì trả lượt. Chiều mời đấu A→B độc lập B→A; trận hai bên cùng tìm không dùng lần mời đấu với bot phòng thủ. Chi tiết hạn mức theo [quy định sinh viên](QUY_DINH_SINH_VIEN.md#4-tìm-trận-và-tính-lượt).

### Giao việc có thời hạn và gửi lại

Mỗi luồng được một **lease**, tức quyền chấm tạm thời cho một job. Lease đầu 180 giây; heartbeat khoảng 10 giây có thể gia hạn nhưng tối đa 300 giây từ lúc nhận. Mất liên lạc/hết hạn thì giao lại cùng job bằng token mới. Token hết hiệu lực hoặc sai máy bị từ chối. Tổng tối đa ba lần thử; hết số lần thì job failed, hoàn lượt và không ghi Elo.

Worker lưu kết quả vào **outbox**, tức tệp chờ xác nhận, trước khi gửi. Mạng mất hoặc phản hồi bị mất thì gửi lại đúng bản đó; chỉ bỏ outbox sau khi được xác nhận hoặc lease bị bác bỏ dứt khoát. Máy quản lý phát lại mọi nước bằng Engine, kiểm kết quả/bàn cuối và đối chiếu map/nguồn đã chốt.

### Ghi điểm và phục hồi

Elo dùng điểm kỳ vọng `E_A = 1 / (1 + 10 ** ((R_B - R_A) / 400))` và K=32. Kết quả A là 1, 0,5 hoặc 0 tương ứng thắng/hòa/thua; điểm được lưu dưới dạng số thực, có thể làm tròn khi hiển thị. Điểm đầu 1000. Biên nhận gắn match_id và mã băm log bảo đảm một kết quả chỉ được áp dụng một lần.

Nếu máy quản lý dừng giữa lưu log, ghi Elo và hoàn tất job, lần mở lại dùng log/biên nhận để hoàn tất phần còn thiếu. Không chấm lại bằng bản đồ hoặc bot mới. Khôi phục dữ liệu hủy phiên đăng nhập và làm hết hiệu lực lease đang chạy; mật khẩu/bot/điểm vẫn giữ.

## 5. Giới hạn và các lớp bảo vệ

Giới hạn thi đấu chính thức là 7 giây CPU mỗi bot, 2,5 giây phản hồi/lượt và 25 nước hợp lệ mỗi bên (50 nước cả trận), mô tả tại [VIET_BOT.md](VIET_BOT.md#3-môi-trường-và-thời-gian). CPU ngoài lượt không trừ đồng hồ thi đấu. Phiên bot còn có giới hạn bảo vệ tổng 140 giây thực và 145 giây CPU; chạm giới hạn bảo vệ không tự đồng nghĩa hết đồng hồ CPU thi đấu.

Giới hạn được chốt trong từng job. Job mới dùng luật phiên bản 6: 7 giây CPU/bot, phản hồi 2,5 giây/lượt, `max_valid_moves: 50` và `move_limit_tiebreak: "piece_count"`. Sau nước 50, nếu chưa thắng đích hoặc mất loại quân, bên còn nhiều quân hơn thắng với lý do `material_advantage`; bằng quân thì hòa với lý do `move_limit`. Lặp bàn ba lần trước mốc này vẫn hòa. Máy quản lý phát lại đủ lịch sử để kiểm tra trước khi nhận điểm và tính Elo theo kết quả đó.

Khi nhận việc, máy chấm gửi header `X-OTTV2-Rules-Version: 6`. Máy quản lý chỉ giao trận cho máy hỗ trợ đúng giới hạn đã chốt. Trang quản trị báo khi máy chấm cần cập nhật. Thời gian dừng dịch vụ cho phép 330 giây để gửi xong kết quả; không xóa khóa hoặc outbox khi cập nhật.

Pool dùng hộp 2/3 đến 24/25 cho tối đa 12 trận, giữ 0/1 để kiểm READY. Mỗi luồng có khóa và outbox riêng. Khóa tệp ngăn hai pool/Worker đơn dùng chung máy. shared cho bot dùng chung CPU; dedicated cần CPU riêng cho các hộp và phải kiểm tài nguyên tương ứng. Số luồng là số chỗ nhận trận, không phải bảo đảm tốc độ.

Các lớp bảo vệ gồm: phân quyền API; kiểm Host/Origin/CSRF cho thao tác trình duyệt; giới hạn thử đăng nhập và tốc độ Nginx; giới hạn ZIP/path/kích thước JSON; tệp bí mật không thuộc gói nguồn; bot bị cách ly mạng/tệp; biên nhận ngăn xử lý trùng. Mọi đường dẫn đọc log/bot phải nằm trong thư mục cho phép.

Máy chấm là thiết bị được quản trị tin cậy. Phát lại luật không chứng minh bằng mật mã rằng một máy tùy ý đã đo CPU/RAM đúng. Bản hiện tại dùng một máy quản lý; không có tự chuyển sang máy quản lý dự phòng, lịch sao lưu tự động hay bảo đảm chống mọi dạng tấn công.

## 6. HTTP API

GET trang, health, logo, SVG, HTML replay/đấu thử và cấu hình đấu thử công khai không yêu cầu phiên. Cấu hình chỉ xuất bản đồ, ba mã đối thủ và luật; không xuất khóa hay dữ liệu tài khoản. Trang có dữ liệu cá nhân chỉ hiển thị sau khi API xác thực. Hash bản đồ không khớp ở đường dẫn SVG trả lỗi để tránh dùng ảnh sai bản đồ.

| Phương thức | Đường dẫn | Dữ liệu gửi | Quyền |
| --- | --- | --- | --- |
| POST | `/api/login` | `identity`, `password` | Chưa cần phiên |
| GET | `/assets/uet-logo.png` | Không | Logo công khai; trình duyệt lưu bộ nhớ đệm một ngày |
| GET | `/api/me` | Không | Đăng nhập |
| GET | `/api/status` | Không | Trạng thái ngắn của tài khoản đã đăng nhập; không đọc replay |
| GET | `/api/history` | Không | Mười trận gần nhất của chính tài khoản |
| GET | `/api/admin/map-guide` | Không | Quản trị; tải hướng dẫn JSON bản đồ |
| POST | `/api/logout` | `{}` | Đăng nhập + CSRF |
| POST | `/api/update` | `{}` | Sinh viên + CSRF |
| POST | `/api/find` | `{}` | Sinh viên + CSRF |
| POST | `/api/cancel` | `{}` | Sinh viên + CSRF |
| GET | `/api/logs/<match_id>` | Không | Người tham gia trận hoặc quản trị |
| GET | `/replay.html`, `/practice.html` | Không | Công khai; tải HTML |
| GET | `/api/practice-config` | Không | Công khai; bản đồ/ba bot/luật, không có khóa hoặc tài khoản |
| POST | `/api/admin/pause` | `paused`, `reason` | Quản trị + CSRF |
| POST | `/api/admin/updates` | `locked` | Quản trị + CSRF |
| POST | `/api/admin/account-lock` | `identity`, `locked`, `reason` | Quản trị + CSRF; khóa/mở khóa sinh viên |
| POST | `/api/admin/block` | `identity`, `blocked`, `reason` | Quản trị + CSRF |
| POST | `/api/admin/map` | `map`: chuỗi JSON bản đồ, `practice_bots`: ba mã bot đấu thử | Quản trị + CSRF |
| POST | `/api/admin/student` | `identity`, `display_name`, `password`, `repository` tùy chọn | Quản trị + CSRF |
| POST | `/api/admin/password` | `identity`, `password` | Quản trị + CSRF; tài khoản đích là sinh viên |

Thêm `POST /api/worker` cho máy chấm khi chạy distributed: Bearer key, không dùng cookie/CSRF. HTTP chỉ cho loopback khi thử tại chỗ; liên máy dùng HTTPS. Giới hạn body Worker 16 MiB.

Sinh viên đổi mật khẩu hoặc link GitHub qua Google Form; không có API trình duyệt đổi trực tiếp hai thông tin này. Admin không đổi mật khẩu qua web/Form. GET /standings chuyển HTTP 303 sang Sheet; /api/me chỉ trả Elo của chính tài khoản. GET /api/admin/map-guide chỉ cho admin tải hướng dẫn JSON.

Máy chủ HTTP xử lý tối đa 8 yêu cầu cùng lúc, giữ tối đa 32 yêu cầu đã nhận; đầy thì trả 503 để bên gọi thử lại. Đây là giới hạn tài nguyên, không phải cam kết 4.000 phiên đồng thời. Mỗi thao tác SQLite vẫn có giao dịch riêng; khóa giao việc và tính điểm giữ nguyên. Backend vẫn chỉ nghe loopback sau Nginx.

Giao diện lấy `/api/me` khi mở trang hoặc thao tác, sau đó dùng `/api/status` trong lúc chờ. Khi có trận mới hoặc trận kết thúc, nó mới lấy lại `/api/history`. Danh sách dùng tóm tắt đã ghi cùng kết quả; JSON replay đầy đủ chỉ đọc khi người dùng yêu cầu sao chép/tải.

Mỗi lần cập nhật GitHub vẫn kiểm commit hiện tại, nhưng tải ZIP trực tiếp từ codeload theo commit cố định. Cache trong RAM chỉ giữ tối đa 16 ZIP công khai và 8 MiB tổng cộng. Token tùy chọn chỉ gửi đến api.github.com, không gửi sang máy tải ZIP. Nếu GitHub báo hết hạn mức, hàng đợi cập nhật giữ yêu cầu và chờ thời điểm được phép thử lại; không tính thêm lượt bấm và không thay bot tốt bằng bản lỗi. Hướng dẫn cài token riêng ở TRIEN_KHAI_VPS.

## 7. Tài liệu tham khảo

- [Isolate](https://github.com/ioi/isolate), [Python subprocess](https://docs.python.org/3/library/subprocess.html), [SQLite transactions](https://www.sqlite.org/lang_transaction.html).
- [GitHub commit](https://docs.github.com/en/rest/commits/commits#get-a-commit), [GitHub ZIP archive](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip).
- [Apps Script Web Apps](https://developers.google.com/apps-script/guides/web).

## Phiên và bộ đấu thử trong trình duyệt

Mỗi tài khoản có một phiên hợp lệ tại một thời điểm. Đăng nhập đồng thời được chốt trong giao dịch ghi SQLite; cùng mật khẩu vẫn không tạo phiên thứ hai. Phiên 30 phút, sinh viên chờ 7 phút sau thời điểm hết hạn, admin không chờ. Đăng xuất sớm thu hồi phiên mà không tạo thời gian chờ; đổi mật khẩu sau khi phiên đã hết không bỏ thời gian chờ.

Bản đồ và ba mã đấu thử được kiểm rồi lưu trong cùng một giao dịch khi hàng đợi đã tạm dừng. Mã chỉ qua ast.parse, không chạy khi lưu. HTML tải về nhúng bản đồ/luật/ba đối thủ; sinh viên dùng cấu hình sao chép để thay cả gói. Hai bot Python chạy trong hai Web Worker qua Pyodide, dùng bộ chuyển vòng stdin sang chờ thông điệp bất đồng bộ. Máy chấm Isolate và kết quả chính thức không sử dụng bộ chuyển này. Trình duyệt chỉ đo thời gian thực; không giả lập số đo CPU hoặc trần RAM chính thức.
