# Cài website lên VPS

Dành cho **VPS Ubuntu 24.04 mới**. Website hiện tại đã được cài; nếu chỉ bật lại, sao lưu hoặc xử lý lỗi thì dùng [VAN_HANH.md](VAN_HANH.md), không làm lại tài liệu này trên dữ liệu đang chạy.

Website chạy trên **ottv2-central-v2 — D2as_v4, 2 vCPU/8 GiB**, IP web **20.194.2.4**. Cách vào máy đang chạy nằm ở đầu VAN_HANH. Những lệnh cài mới bên dưới dành cho một VM trắng khác; thay IP ví dụ bằng IP VM mới đó.

Cách triển khai: **VPS 8 GiB giữ website, dữ liệu và một máy chấm thường trực 4 trận; PC bổ sung khi đông người**. Xem cách kiểm và bật/tắt ở đầu [MAY_CHAM.md](MAY_CHAM.md). Tài liệu này dành cho việc dựng một VPS chưa có ứng dụng.

## 1. Chuẩn bị thông tin và chuyển mã

Ví dụ dưới dùng thông tin bản triển khai của nhóm:

| Thông tin | Giá trị |
| --- | --- |
| Người dùng và IP SSH của VM trắng ví dụ | azureuser@IP_VPS_MOI |
| Khóa SSH trên Windows | Downloads/ottv2-key.pem |
| Tên miền | ottv2-nhom12.koreacentral.cloudapp.azure.com |
| Repository nguồn | https://github.com/Doanhh17/ottv2-final.git |

Nếu dựng VPS khác, thay các giá trị bằng thông tin máy đó. Giữ khóa SSH riêng; khóa này khác khóa máy chấm và mật khẩu website.

Trong **PowerShell máy quản lý**, nơi có khóa SSH:

```powershell
ssh -o IdentitiesOnly=yes -i "$env:USERPROFILE\Downloads\ottv2-key.pem" azureuser@IP_VPS_MOI
```

**Cần thấy:** dấu nhắc SSH trên VM vừa tạo. Lần đầu SSH cần đối chiếu fingerprint với thông tin nhà cung cấp. Nếu báo `publickey`, kiểm đúng khóa và quyền đọc tệp; không gửi nội dung PEM khi hỏi hỗ trợ.

## 2. Đặt mã nguồn trên VPS

Tất cả khối bash còn lại chạy trong **cửa sổ SSH vào VPS**:

```bash
sudo apt update
sudo apt install -y python3 git nginx snapd ufw nano curl ca-certificates
git clone https://github.com/Doanhh17/ottv2-final.git ~/ottv2-source
cd ~/ottv2-source
sha256sum -c SHA256SUMS
```

**Cần thấy:** mọi dòng `OK`. `~/ottv2-source` phải là thư mục mới; không chép đè website đang chạy.

```bash
sudo adduser --system --group --home /var/lib/ottv2 --shell /usr/sbin/nologin ottv2
sudo install -d /opt/ottv2/app
sudo cp -a ottv2 tests scripts web practice replay examples maps deploy google config docs README.md SHA256SUMS .gitignore /opt/ottv2/app/
sudo chown -R root:root /opt/ottv2/app
sudo chmod -R u=rwX,go=rX /opt/ottv2/app
sudo install -d -m 700 -o ottv2 -g ottv2 /var/lib/ottv2
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.demo --seed 1
```

**Cần thấy:** demo in nước đi và `RESULT`. `ottv2` là tài khoản dịch vụ Linux, không phải tài khoản sinh viên. Nếu bị Permission denied khi đọc mã, kiểm lệnh chmod và quyền các thư mục cha. Mã ở `/opt/ottv2/app`; dữ liệu sẽ ở `/var/lib/ottv2/data`.

## 3. Chuẩn bị Isolate và bộ nhớ

Trên **VPS**, làm bước 2–4 của [MOI_TRUONG.md](MOI_TRUONG.md), rồi trở lại đây. Không chạy bước cài WSL của Windows trên VPS.

Khi Isolate đã cài xong, kiểm bằng tài khoản dịch vụ:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 scripts/isolate_smoke.py
sudo -u ottv2 python3 scripts/isolate_memory_check.py
sudo -u ottv2 python3 scripts/isolate_network_check.py
sudo -u ottv2 python3 scripts/isolate_filesystem_check.py
sudo -u ottv2 python3 scripts/isolate_wall_check.py
free -h
df -h /
```

**Cần thấy:** các phép kiểm `OK`, có RAM/dung lượng trống. Các phép kiểm có hạn riêng để thử lỗi; không thay giới hạn thi đấu.

VPS phải có đủ RAM khả dụng cho website và bốn trận. Swap không thay cho RAM và không làm tăng sức tính CPU.

## 4. Tạo dữ liệu giải

ZIP nguồn không chứa tài khoản, điểm hoặc khóa Google. Phần này chỉ chạy khi thư mục dữ liệu giải chưa có.

Trên **VPS**:

```bash
cd /opt/ottv2/app
sudo -u ottv2 mkdir -p /var/lib/ottv2/data/bots
sudo -u ottv2 python3 -m ottv2.match_queue --db /var/lib/ottv2/data/queue.sqlite3 init --store /var/lib/ottv2/data/bots --map maps/nine-piece.json --ratings /var/lib/ottv2/data/elo.sqlite3
sudo -u ottv2 python3 -m ottv2.accounts --db /var/lib/ottv2/data/accounts.sqlite3 init --queue /var/lib/ottv2/data/queue.sqlite3
sudo -u ottv2 python3 -m ottv2.accounts --db /var/lib/ottv2/data/accounts.sqlite3 add admin --admin --name 'Quản trị viên'
```

Đặt mật khẩu admin riêng từ 12 ký tự khi được hỏi hai lần. **Cần thấy:** dữ liệu được tạo và `Đã lưu`. Kho bot ban đầu rỗng; sinh viên cần đăng ký/cập nhật bot. Không dùng prepare_local với mật khẩu mẫu cho giải công khai.

Nếu cần phục hồi một bộ dữ liệu đã sao lưu, dùng [VAN_HANH → Sao lưu và khôi phục](VAN_HANH.md#4-sao-lưu-và-khôi-phục). Không vừa khởi tạo giải vừa chép dữ liệu vào cùng một thư mục.

## 5. Trỏ DNS và mở cổng

Trong trang nhà cung cấp, trỏ tên miền về IP VPS. Với Azure Public IP, đặt DNS name label để nhận tên dạng `<nhãn>.koreacentral.cloudapp.azure.com`. Trên **VPS**:

```bash
getent ahostsv4 ottv2-nhom12.koreacentral.cloudapp.azure.com
```

**Cần thấy:** IP đúng bằng IP của VM vừa tạo. Tên miền đang dùng của nhóm trỏ về 20.194.2.4; không trỏ nó sang một VM thử khi chưa chuyển giải. Chưa đúng thì sửa/chờ DNS trước khi xin HTTPS.

Cho phép SSH/HTTP/HTTPS ở cả tường lửa nhà cung cấp và Ubuntu. Ví dụ dùng SSH cổng 22:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Nếu SSH dùng cổng khác, thay 22 trước khi bật ufw. Giữ phiên hiện tại, thử mở thêm một phiên SSH để kiểm. Không mở cổng 8765 ra Internet; ứng dụng chỉ nghe nội bộ, Nginx phục vụ 80/443.

## 6. Cài dịch vụ website và HTTPS

Trên **VPS mới**:

```bash
cd /opt/ottv2/app
OTTV2_DOMAIN='ottv2-nhom12.koreacentral.cloudapp.azure.com'
python3 scripts/render_deploy.py --domain "$OTTV2_DOMAIN" --output /tmp/ottv2-config --distributed
sudo install -m 644 /tmp/ottv2-config/ottv2.service /etc/systemd/system/ottv2.service
sudo install -m 600 /tmp/ottv2-config/ottv2.env /etc/ottv2.env
sudo install -m 644 /tmp/ottv2-config/ottv2-proxy.conf /etc/nginx/snippets/ottv2-proxy.conf
sudo install -m 644 /tmp/ottv2-config/ottv2.conf /etc/nginx/sites-available/ottv2
sudo ln -s /etc/nginx/sites-available/ottv2 /etc/nginx/sites-enabled/ottv2
printf 'server_names_hash_bucket_size 128;\n' | sudo tee /etc/nginx/conf.d/ottv2-server-names.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

**Cần thấy:** `syntax is ok`, `test is successful`. Nếu `/tmp/ottv2-config` hoặc liên kết website đã có, kiểm lần cài trước; không xóa để ép chạy lại. Biến OTTV2_DOMAIN chỉ tồn tại trong phiên SSH đang mở, cần đặt lại nếu đổi cửa sổ.

```bash
sudo snap install --classic certbot
sudo /snap/bin/certbot --nginx -d "$OTTV2_DOMAIN" --redirect
sudo /snap/bin/certbot renew --dry-run --no-random-sleep-on-renew
sudo systemctl daemon-reload
sudo systemctl enable --now ottv2.service
curl --fail --show-error "https://$OTTV2_DOMAIN/health"
sudo systemctl status ottv2.service --no-pager
```

Certbot hỏi email quản trị và điều khoản khi đăng ký chứng chỉ. **Cần thấy:** cấp/gia hạn thử thành công, service `active (running)`, health có `"ok": true`. Mở trình duyệt bằng HTTPS. Nếu máy có Certbot từ trước, dùng bản đã cài sau khi kiểm hướng dẫn chính thức, không cài chồng.

**Nếu lỗi:** 502 thì xem `sudo journalctl -u ottv2.service -n 60 --no-pager`; lỗi chứng chỉ thì kiểm DNS, cổng 80/443 và nginx -t. Nếu hệ thống từ chối mật khẩu mẫu, đổi đúng các tài khoản mẫu còn dùng mật khẩu đó. Kiểm chứng chỉ HTTPS trước khi đăng nhập.

## 7. Nối Google và bật máy chấm VPS

Kết nối Google theo [VAN_HANH → Kết nối Google](VAN_HANH.md#5-kết-nối-google). Khi có google-client.json hợp lệ cạnh accounts.sqlite3, ứng dụng dùng cấu hình đó. Không đưa khóa Google lên GitHub hoặc chuyển cho PC chấm.

Sau khi website HTTPS hoạt động, trên **VPS chưa cấp worker-vps**, chạy lần lượt:

```bash
cd /opt/ottv2/app
sudo install -d -m 700 -o ottv2 -g ottv2 /var/lib/ottv2/private-workers
sudo -u ottv2 python3 -m ottv2.worker_pool register --db /var/lib/ottv2/data/queue.sqlite3 --host-id worker-vps --url https://ottv2-nhom12.koreacentral.cloudapp.azure.com --slots 4 --output /var/lib/ottv2/private-workers/worker-vps-pool.json
sudo install -m 600 -o ottv2 -g ottv2 /var/lib/ottv2/private-workers/worker-vps-pool.json /etc/ottv2-vps-worker.json
sudo -u ottv2 python3 -m ottv2.worker_pool label --db /var/lib/ottv2/data/queue.sqlite3 --host-id worker-vps --name worker_vps --max-slots 4
sudo -u ottv2 python3 -m ottv2.worker_pool plan --slots 4 --cpu-mode shared --output /var/lib/ottv2/isolate-vps-plan.conf
sudo install -m 644 /var/lib/ottv2/isolate-vps-plan.conf /usr/local/etc/isolate
isolate --check-config
sudo install -m 644 deploy/ottv2-vps-worker.service /etc/systemd/system/ottv2-vps-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now ottv2-vps-worker.service
sudo systemctl status ottv2-vps-worker.service --no-pager
```

Cần thấy `pool_registered`, cấu hình Isolate hợp lệ, dịch vụ `active (running)` và 4 luồng worker_vps online trong trang quản trị. Khóa hoặc tệp đã có thì kiểm cấu hình đang dùng, không đăng ký đè. Dịch vụ tự bật khi VPS khởi động; không cần nút Desktop cho VPS.

Các PC bổ sung làm theo [MAY_CHAM.md](MAY_CHAM.md). Mỗi PC có JSON riêng, cài một lần và dùng nút Desktop từ những lần sau.

## 8. Kiểm trước khi công bố

Trên **VPS**:

```bash
cd /opt/ottv2/app
sudo -u ottv2 python3 -m ottv2.cluster --db /var/lib/ottv2/data/queue.sqlite3 status
sudo -u ottv2 python3 -m ottv2.queue_admin --db /var/lib/ottv2/data/queue.sqlite3 status
```

**Cần thấy:** máy chấm enabled và online, không có lỗi. Nếu đang tạm dừng, chỉ mở lại sau khi máy chấm đã trực tuyến:

```bash
sudo -u ottv2 python3 -m ottv2.queue_admin --db /var/lib/ottv2/data/queue.sqlite3 resume
```

Dùng tài khoản thử để kiểm đăng ký, cập nhật GitHub, một trận hoàn tất, điểm chỉ tính một lần, sao chép log và xem replay offline. Kiểm Form đổi thông tin, Google Sheet và các link bằng tài khoản sinh viên. Các trận thử vẫn tính lượt/điểm.

Sau đó kiểm website tự bật lại bằng `sudo systemctl restart ottv2.service` và mở health lần nữa. Kiểm thử chức năng cụ thể theo [KIEM_THU.md](KIEM_THU.md), công việc hằng ngày theo [VAN_HANH.md](VAN_HANH.md).

## 9. Tải bot từ GitHub khi đông sinh viên

Bot được tải bằng ZIP công khai. Ứng dụng gọi API một lần để chốt commit, sau đó tải ZIP đúng commit từ codeload; không tải theo nhánh đang thay đổi. Nếu nhiều người dùng cùng commit/repo, cache ZIP giúp giảm tải lặp lại. Khi GitHub giới hạn tốc độ, yêu cầu được giữ trong hàng đợi và tự thử lại, bản bot tốt vẫn được giữ.

Không có token vẫn dùng được, nhưng API công khai thường chỉ cho 60 yêu cầu/giờ trên một IP. Xác thực thường nâng lên 5.000 yêu cầu/giờ, vẫn có các giới hạn phụ; xem [quy định GitHub](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api). Token là phần tùy chọn cho tải cập nhật lớn, không phải mật khẩu sinh viên.

Nếu quản trị chọn dùng token, tạo token chỉ đọc tài nguyên công khai, không cấp quyền ghi hay quyền kho riêng. Nhập trực tiếp trong SSH trên VPS:

```bash
cd /opt/ottv2/app
sudo python3 scripts/configure_github.py
```

Chương trình hỏi kín, không hiện token khi nhập. Không ghi token vào chat, dòng lệnh hoặc repository. Khi đã báo lưu xong, chạy riêng:

```bash
sudo systemctl restart ottv2.service
```

Token nằm ngoài nguồn ở `/etc/ottv2-github.token`, quyền 600; cấu hình chỉ giữ đường dẫn. Đến ngày hết hạn phải thay token. Không có token không ảnh hưởng việc đăng nhập hoặc chấm các bot đã tải.

## 10. Đổi kích thước VPS

Máy hiện tại **ottv2-central-v2** dùng D2as_v4, 2 vCPU/8 GiB. Giá Azure được duyệt ngày 29/10/2026: 0,118 USD/giờ, khoảng 86,14 USD/730 giờ phần máy, chưa gồm đĩa/IP/lưu lượng. 

Muốn đổi tiếp, mở **ottv2-central-v2 → Size**, kiểm giá và hạn mức thực tế của subscription trước khi áp dụng. Ubuntu phải nhận đủ RAM sau thay đổi. Máy cầu nối SSH cũng phát sinh phí khi đang chạy; tính cả tài nguyên này vào chi phí vận hành.

Trước khi đổi: tạm dừng nhận trận/cập nhật, chờ hết trận đang chấm, sao lưu và chuẩn bị thời gian bảo trì. Sau khi đổi: kiểm IP/tên miền, Nginx, dịch vụ OTTv2, Google và máy chấm kết nối lại; đối chiếu số trận/điểm với trước bảo trì trước khi mở giải. Nâng RAM VPS không tự tăng năng lực của các PC chấm.
