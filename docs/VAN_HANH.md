# Vận hành website và dữ liệu giải

Dành cho quản trị viên sau khi cài xong. Các lệnh chính dưới đây chạy **qua SSH trên VPS**, tại `/opt/ottv2/app`. Cài mới xem TRIEN_KHAI_VPS; bật/cài máy chấm xem MAY_CHAM; viết JSON bản đồ xem BAN_DO.

VPS hiện tại là **ottv2-central-v2**, IP web **20.194.2.4**, RAM 8 GiB. Trên Windows giữ khóa SSH, mở PowerShell tại thư mục nguồn có README.md rồi chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps.ps1 connect
```

Phải thấy `azureuser@ottv2-central-v2`. Máy cầu nối 20.214.142.35 phục vụ SSH vào VPS qua mạng nội bộ; website và dữ liệu giải ở ottv2-central-v2. Không quản lý dữ liệu giải trên máy cầu nối. Lệnh kết nối cần cả hai VM đang chạy; PC máy chấm chỉ kết nối HTTPS, không cần SSH. VPS không mở SSH công khai.

## 1. Nhận biết dữ liệu trước khi sửa

| Vị trí trên VPS | Nội dung |
| --- | --- |
| /opt/ottv2/app | Mã nguồn, tài liệu, mẫu cấu hình |
| /var/lib/ottv2/data/accounts.sqlite3 | Tài khoản, phiên đăng nhập, biên nhận Google |
| /var/lib/ottv2/data/queue.sqlite3 | Hàng đợi, lượt, bản đồ đang dùng, máy chấm và công việc |
| /var/lib/ottv2/data/elo.sqlite3 | Điểm và biên nhận tính điểm |
| /var/lib/ottv2/data/bots | Phiên bản bot đã nhận |
| /var/lib/ottv2/data/matches | Log trận |
| /var/lib/ottv2/data/google-client.json | URL và khóa Google riêng |
| /etc/ottv2.env | Địa chỉ HTTPS công khai của ứng dụng |
| /var/lib/ottv2/private-workers | Bản khóa đã cấp cho từng máy chấm |

Dữ liệu của giải công khai nằm trên VPS theo bảng trên. Máy chạy thử theo CAI_DAT dùng `output/local-demo`, tách biệt với giải. PC máy chấm chỉ giữ cấu hình và kết quả chưa gửi; không tạo cơ sở dữ liệu giải trên PC.

## 2. Điều hành trên website

Đăng nhập quản trị; dùng ba trang:

| Trang | Việc làm |
| --- | --- |
| Điều hành | Tạm dừng/mở ghép trận; tạm dừng/mở nhận cập nhật bot; đổi bản đồ |
| Sinh viên | Tạo tài khoản, đặt lại mật khẩu sinh viên, khóa/mở khóa |
| Trạng thái | Xem kết nối Google, máy chấm và công việc lỗi; bấm Làm mới để kiểm lại |

**Tạm dừng / Nhận trận trở lại** điều khiển ghép trận mới. **Tạm dừng cập nhật bot / Cho cập nhật bot trở lại** điều khiển nhận yêu cầu tải mã từ GitHub. Bot đang có vẫn thi đấu; yêu cầu cập nhật được nhận trước đó vẫn xử lý tiếp. Hai việc này khác khóa tài khoản.

Khi khóa sinh viên, phiên đăng nhập kết thúc, không nhận đăng nhập/trận/cập nhật mới và hủy yêu cầu tìm còn chờ. Trận đã ghép hoặc cập nhật đã bắt đầu vẫn hoàn tất. Mở khóa giữ nguyên bot, điểm, lượt và lịch sử; sinh viên đăng nhập lại bằng mật khẩu hiện tại.

Tài khoản tạo ở website VPS dùng được từ mọi máy. Tài khoản tạo ở website local chỉ nằm trong dữ liệu local cho đến khi chuyển dữ liệu. Tài khoản thủ công chưa có email Google xác minh thì quản trị hỗ trợ đổi thông tin; không coi email tự nhập là đã xác minh.

### Đổi mật khẩu quản trị

Admin không đổi mật khẩu qua website/Form. Trên **VPS**:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.accounts --db /var/lib/ottv2/data/accounts.sqlite3 password admin
```

Nhập mật khẩu mới hai lần. **Cần thấy:** `Đã lưu`; các phiên đăng nhập bị kết thúc. Nếu quản trị có tên khác admin thì thay đúng tên đó. Không ghi mật khẩu vào đối số lệnh.

### Đổi GitHub cho tài khoản cấp thủ công

Trên **VPS**, thay MSSV/link trong đoạn dưới bằng thông tin cần sửa:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 - <<'PY'
from ottv2.accounts import set_repository
set_repository('/var/lib/ottv2/data/accounts.sqlite3', 'student04', 'https://github.com/ten/kho-bot')
print('Đã lưu link GitHub.')
PY
```

**Cần thấy:** lưu thành công. Sinh viên vẫn phải bấm Cập nhật bot; thay link không tự tải mã. Sinh viên có email xác minh dùng Form theo QUY_DINH_SINH_VIEN.

## 3. Kiểm dịch vụ và xử lý lỗi

Trên **VPS**:

```bash
sudo systemctl status ottv2.service --no-pager
sudo nginx -t
curl --fail --show-error https://ottv2-nhom12.koreacentral.cloudapp.azure.com/health
sudo journalctl -u ottv2.service -n 50 --no-pager
```

**Cần thấy:** service active, nginx hợp lệ, health có `"ok": true`. Xem thông báo gần thời điểm lỗi, không chỉ nhìn website có mở được hay không.

| Hiện tượng | Kiểm tra |
| --- | --- |
| 502 hoặc health nội bộ lỗi | Trạng thái/journal ottv2.service, quyền đọc mã và dữ liệu |
| HTTPS lỗi | DNS đúng IP, cổng 80/443, chứng chỉ và nginx -t; không bỏ kiểm chứng chỉ |
| Chưa có kết quả | Trạng thái máy chấm và hàng đợi; lệnh trong MAY_CHAM |
| Google retry | URL /exec, khóa khớp, phiên bản deployment, quyền Forms/Sheets và Verified email |
| Bot cập nhật lỗi | Link GitHub, commit, main.py ở gốc, giới hạn ZIP và READY |
| Điểm Sheet chậm | Lịch xuất 2 phút và lần Google thành công gần nhất |

Muốn khởi động lại riêng website, dùng `sudo systemctl restart ottv2.service`, rồi kiểm health. Máy chấm ngoài sẽ thử kết nối lại. Không tạo dữ liệu mới hoặc sửa SQLite bằng tay để chữa lỗi.

## 4. Sao lưu và khôi phục

### Sao lưu VPS đang chạy

Trên **VPS**, tạm dừng ghép và xem trạng thái:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.queue_admin --db /var/lib/ottv2/data/queue.sqlite3 pause --reason 'Sao luu'
sudo -u ottv2 python3 -m ottv2.queue_admin --db /var/lib/ottv2/data/queue.sqlite3 status
```

Chờ `busy_players: 0` và không còn job pending/running. Dừng công cụ quản trị khác đang ghi. VPS hiện tại có máy chấm riêng, phải dừng cả dịch vụ này trước khi sao lưu.

```bash
sudo systemctl stop ottv2-vps-worker.service
sudo systemctl stop ottv2.service
sudo install -d -m 700 -o ottv2 -g ottv2 /var/lib/ottv2/backups
sudo -u ottv2 python3 -m ottv2.backup create /var/lib/ottv2/data /var/lib/ottv2/backups/arena-01.tar.gz
```

**Cần thấy:** `backup_created`. Mỗi lần dùng tên mới, không đặt bản sao trong thư mục data. Bản sao có tài khoản, bot, điểm, log còn lưu và khóa Google nếu có; quyền 600, giữ thêm một bản ở nơi riêng ngoài VPS.

Khi đã sao lưu xong:

```bash
sudo systemctl start ottv2.service
sudo systemctl start ottv2-vps-worker.service
curl --fail --show-error https://ottv2-nhom12.koreacentral.cloudapp.azure.com/health
```

Xác nhận `worker_vps` có 4 luồng trực tuyến rồi mở lại ghép bằng queue_admin với lệnh `resume`. Nếu còn lỗi, giữ tạm dừng để xử lý.

### Sao lưu bản local để chuyển máy

Trong **Ubuntu máy nguồn**, vào thư mục mã và tạm dừng hàng đợi đang dùng. Ví dụ bộ local-demo:

```bash
cd ~/ottv2-project
python3 -m ottv2.queue_admin --db output/local-demo/queue.sqlite3 pause --reason 'Chuyen may'
python3 -m ottv2.queue_admin --db output/local-demo/queue.sqlite3 status
```

Chờ hết trận, Ctrl+C dừng website và máy chấm chạy tay. Sau đó:

```bash
mkdir -p ~/ottv2-backups
chmod 700 ~/ottv2-backups
python3 -m ottv2.backup create output/local-demo ~/ottv2-backups/chuyen-may-01.tar.gz
```

**Cần thấy:** `backup_created`. Chỉ sao lưu đúng thư mục dữ liệu mà ứng dụng đang dùng. Chuyển bản sao qua SSH khi có yêu cầu chuyển hệ thống, không đưa vào ZIP nguồn.

### Kiểm bản sao bằng cách khôi phục sang thư mục mới

Trên **VPS**:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.backup restore /var/lib/ottv2/backups/arena-01.tar.gz /var/lib/ottv2/restored-check-01
```

**Cần thấy:** `backup_restored`, `sessions_revoked: true`. Đích phải chưa tồn tại; bản gốc không đổi. Công cụ kiểm mã băm và đổi đường dẫn nội bộ; tài khoản/mật khẩu vẫn giữ. Không tự cho website dùng bản khôi phục khi chưa kiểm xong, không bật đồng bộ Google ở cả bản gốc và bản thử.

Khóa và outbox của từng máy chấm **không nằm trong bản sao data này**. Giữ riêng cấu hình khóa và thư mục worker-pool trên đúng máy chấm sau khi dừng máy đó.

## 5. Kết nối Google

Chỉ máy quản lý kết nối Google. Nếu dữ liệu đã có google-client.json hợp lệ thì dùng cấu hình đang có; phần dưới chỉ dành cho việc thiết lập kết nối Google.

### 5.1. Chuẩn bị Forms, Sheets và tệp tải

Tài khoản người tổ chức sở hữu:

| Tài nguyên | Cách dùng |
| --- | --- |
| Form đăng ký | Thu email **Đã xác minh**; bốn câu trả lời ngắn bắt buộc |
| Form đổi mật khẩu/link | Thu email **Đã xác minh**; câu hỏi theo bảng dưới |
| Sheet quản lý riêng | Tab DangKy và MatKhau; chỉ người tổ chức được đọc |
| Sheet xếp hạng | Tab XepHang; chia sẻ chỉ xem cho sinh viên |
| Docs và Drive | Hướng dẫn, main.py mẫu, replay HTML; chia sẻ chỉ đọc/tải |

Form đăng ký cần đúng thứ tự và tên:

1. `Mã sinh viên`
2. `Họ tên`
3. `Link GitHub`
4. `Mật khẩu riêng cho OTTv2 (12–256 ký tự; không dùng mật khẩu Google)`

Form đổi thông tin cần đúng thứ tự và tên:

1. `Mã sinh viên`: câu trả lời ngắn bắt buộc.
2. `Nội dung cần thay đổi`: trắc nghiệm bắt buộc, ba lựa chọn `Đổi / quên mật khẩu`, `Đổi link GitHub`, `Đổi cả mật khẩu và link GitHub`.
3. `Mật khẩu mới cho OTTv2 (12–256 ký tự; không dùng mật khẩu Google)`: câu trả lời ngắn, không bắt buộc ở cấp Form.
4. `Link GitHub mới`: câu trả lời ngắn, không bắt buộc ở cấp Form.

Máy quản lý kiểm ô nào phải có theo lựa chọn. Đặt kiểm độ dài mật khẩu bằng biểu thức `^[\s\S]{12,256}$`. Không giới hạn Form đổi thông tin ở một phản hồi/người.

**Không liên kết Form với Sheet phản hồi thô**, vì sẽ lưu thêm bản mật khẩu. Apps Script ghi hồ sơ/trạng thái không chứa mật khẩu vào tab quản lý; tiêu đề/thứ tự cột nằm ở OTT.header, OTT.resetHeader, OTT.rankHeader trong mã.

### 5.2. Đặt mã Apps Script và lấy URL

1. Tạo Apps Script dưới tài khoản sở hữu các tài nguyên.
2. Chép `google/Code.gs` và manifest `google/appsscript.json` từ gói nguồn.
3. Điền ID thực tế vào OTT.form, OTT.resetForm, OTT.registration, OTT.ranking.
4. Triển khai **Web app**, chạy dưới tài khoản chủ, cho phép ứng dụng gọi URL qua mạng. Mọi POST xử lý dữ liệu vẫn phải qua xác thực khóa của OTTv2.
5. Lấy URL kết thúc bằng `/exec`. URL `/dev` không dùng cho máy chủ.

Khi đổi chủ tổ chức, cập nhật cả `config/google-resources.json` và liên kết trong `web/portal.html`. Một thay đổi trong JSON không tự thay hằng OTT hoặc HTML. Khi sửa mã Apps Script, cập nhật deployment lên phiên bản mới; chỉ lưu trình soạn thảo chưa đổi mã Web app đang phục vụ.

### 5.3. Tạo khóa kết nối trên VPS

Chỉ tạo khi chưa có google-client.json cần giữ. Trên **VPS**:

```bash
read -r -p 'URL Apps Script ket thuc bang /exec: ' OTTV2_GOOGLE_URL
sudo -u ottv2 python3 - "$OTTV2_GOOGLE_URL" <<'PY'
import json
from pathlib import Path
import secrets
import sys

path = Path('/var/lib/ottv2/data/google-client.json')
settings = {'url': sys.argv[1].strip(), 'secret': secrets.token_hex(32)}
with path.open('x', encoding='utf-8') as stream:
    path.chmod(0o600)
    json.dump(settings, stream)
print('DA TAO TEP KET NOI')
PY
sudo -u ottv2 nano /var/lib/ottv2/data/google-client.json
```

**Cần thấy:** tạo tệp riêng quyền 600. Trong nano, sao chép giá trị secret vào Script Property **OTTV2_SECRET** của Apps Script. Không dán khóa vào chat, GitHub hoặc tài liệu. Nếu tệp đã tồn tại, giữ nguyên để kiểm, không xóa để sinh khóa khác.

Kiểm trực tiếp cả hai Form thực sự dùng email **Đã xác minh**, rồi đặt thêm Script Properties:

| Thuộc tính | Giá trị |
| --- | --- |
| OTTV2_VERIFIED_EMAIL | reviewed-verified |
| OTTV2_RESET_VERIFIED_EMAIL | reviewed-verified |

Mã Apps Script không tự chứng minh chế độ Verified thay cho chủ Form; chỉ đặt hai xác nhận sau khi kiểm giao diện. Chạy **checkSetup** trong Apps Script và cấp quyền Forms/Sheets cho mã dưới tài khoản chủ. Cần hoàn tất không có lỗi.

### 5.4. Bật và kiểm đồng bộ

Trên **VPS**:

```bash
sudo systemctl restart ottv2.service
sudo journalctl -u ottv2.service -n 40 --no-pager
```

**Cần thấy:** `google_synced`, hoặc trang Trạng thái admin báo lần đồng bộ thành công. Form đăng ký/đổi thông tin được đọc khoảng 1 phút/lần; mỗi lượt tối đa 10 hồ sơ mỗi loại. Bảng điểm có lịch riêng khoảng 2 phút/lần. Mất mạng/đông yêu cầu có thể chậm hơn.

Dùng tài khoản thử có email trường để gửi Form, chờ xử lý rồi kiểm đăng nhập/đổi thông tin. Không chỉ kiểm bằng tài khoản chủ Google. Máy chủ lưu biên nhận trước khi xác nhận xóa phản hồi chứa mật khẩu; thử lại sau mất mạng không tạo tài khoản hoặc tính thay đổi trùng.

Chỉ chia sẻ Sheet xếp hạng và tài liệu tải cho sinh viên. Giữ riêng Sheet hồ sơ, khóa và bản sao lưu; chỉ một máy quản lý đồng bộ một bộ Google tại một thời điểm.

## 6. Log và mã nguồn

Máy quản lý tự kiểm dọn log khoảng 60 giây/lần: chỉ log hoàn tất quá 7 ngày, có mã băm/biên nhận đúng mới được dọn. Điểm và tóm tắt trận giữ nguyên. Tệp lỗi/lạ để lại để kiểm tra; không cần đặt lịch xóa thủ công khi hệ thống đã chạy bình thường.

Lấy nguồn từ [repository dự án](https://github.com/Doanhh17/ottv2-final), kiểm trong Ubuntu bằng `sha256sum -c SHA256SUMS`. Mọi dòng cần `OK` trên cây nguồn chưa sửa. Khi cần chuyển một bản cập nhật cho máy chấm đã cài dịch vụ, công cụ `scripts/build_release.py` tạo gói nguồn nội bộ theo giao thức cập nhật ở [MAY_CHAM.md](MAY_CHAM.md). Khóa và bản sao lưu data riêng ở mục 4 được giữ ngoài repository.

## Phiên đăng nhập và cấu hình đấu thử

Mọi tài khoản chỉ có một phiên, dài 30 phút từ lúc đăng nhập. Sinh viên hết phiên phải chờ 7 phút; đăng xuất trước hạn được vào lại ngay. Admin hết phiên được vào lại ngay. Đóng trình duyệt không thu hồi phiên: nếu báo đang đăng nhập nơi khác, dùng Đăng xuất ở nơi đó hoặc đợi hết phiên. Đổi mật khẩu thu hồi phiên; nếu phiên đã tự hết hạn thì không xóa thời gian chờ của sinh viên.

Đổi bản đồ và mã của ba bot đấu thử tại Điều hành theo [BAN_DO.md](BAN_DO.md). Lưu đồng thời, cần tạm dừng nhận trận trước. Nút Sao chép cấu hình cho sinh viên lấy bản đồ/ba bot/luật; không chứa dữ liệu tài khoản hay khóa. Trạng thái admin tự làm mới, xếp luồng đang rảnh và còn kết nối lên đầu, luồng mất kết nối ở dưới.
