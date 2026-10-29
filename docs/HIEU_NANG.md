# Hiệu năng đã đo

Đo ngày **30/10/2026, giờ Việt Nam**, trên VPS **D2as_v4, 2 vCPU, RAM 8 GiB**. Luật đang áp dụng: **7 giây CPU/bot, 2,5 giây thực/lượt, 25 nước/bên, 64 MiB/bot**.

Kết quả máy đọc được nằm trong [hieu-nang.json](hieu-nang.json). Tệp này không có mật khẩu, cookie, khóa hoặc thông tin tài khoản thật. Chỉ giữ bộ số đo hiện hành; không kèm các thư mục thử và bản dựng trung gian.

## 1. Người dùng truy cập từ Windows qua Internet

Đọc luân phiên trang chủ, health và ảnh bàn cờ của website thật. Không đăng nhập hoặc tạo trận.

| Chỉ số | Kết quả |
| --- | ---: |
| Tốc độ phát yêu cầu | 3 yêu cầu/giây |
| Kết quả | 90/90 HTTP 200 |
| Thời gian cả đợt | 30,092 giây |
| Trung vị / P95 / chậm nhất | 357,57 / 845,75 / 1.358,76 ms |

**P95** nghĩa là 95% yêu cầu không chậm hơn mức đó. Số đo này có đường truyền Windows–VPS và bắt tay HTTPS, nên khác độ trễ xử lý API nội bộ. Đây là kiểm truy cập nhẹ, chưa tìm tải tối đa của website công khai.

## 2. API trạng thái có đăng nhập

Tạo **4.000 tài khoản giả, 4.000 phiên riêng và 2.000 trận chờ giả**. Bộ phát tải và API là hai tiến trình riêng trên cùng VPS; gọi `/api/status` qua HTTP loopback. Dữ liệu thử ở thư mục tạm, được xóa sau đo. Không thay tài khoản, điểm hoặc hàng đợi thật.

| Tốc độ đặt | Yêu cầu thành công | Thời gian đợt | P95 | Chậm nhất | RAM đỉnh tiến trình API |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 25/giây | 500/500 | 19,964 giây | 3,104 ms | 4,434 ms | 31,38 MiB |
| 100/giây | 2.000/2.000 | 19,995 giây | 3,139 ms | 10,157 ms | 31,38 MiB |
| 200/giây | 4.000/4.000 | 20,003 giây | 3,361 ms | 27,228 ms | 32,83 MiB |

Tổng **6.500/6.500 HTTP 200**, không trả nhầm người dùng. 60 lần kiểm HTTPS health của website thật trong các đợt đều thành công. Ở đợt 200/giây, tiến trình API dùng 8,643 giây CPU trong khoảng 20 giây thực; không tính đó là tổng CPU của cả VPS.

**Kết luận có thể dùng:** API đọc trạng thái đã qua mức 200 yêu cầu/giây trong mẫu này. Chưa tìm giới hạn tối đa. Phép đo không gồm Nginx/TLS cho API, Internet của sinh viên, băm mật khẩu, gửi Forms, cập nhật GitHub, tải log hoặc phát tải HTTP đồng thời với bot nặng. Không dùng kết quả này để tuyên bố 4.000 người thao tác/chấm cùng lúc đều đạt.

## 3. Máy chấm thường trực trên VPS

Hai bot mẫu `depth3-alpha` và `depth3-guardian`, **8 trận, tối đa 4 trận song song**, chạy qua Isolate theo luật hiện hành. Chờ hết việc thật rồi tạm nhường sandbox VPS cho phép đo; website và pool local vẫn hoạt động. Đã bật lại pool VPS và khôi phục trạng thái nhận việc sau đo.

| Chỉ số | Kết quả |
| --- | ---: |
| Hoàn tất cả đợt | **16,960 giây** |
| Thắng đích / hòa đủ nước / thắng hơn quân | 4 / 2 / 2 |
| Trận quá hạn phản hồi | 0 |
| Lượt phản hồi chậm nhất | **0,652 giây** |
| Dọn sandbox đầy đủ | 8/8 |
| Tốc độ quy đổi từ mẫu ngắn | Khoảng **1.698 trận/giờ** |

1.698 là `8 / 16,960 × 3.600`, không phải đã chạy liên tục một giờ. Bot dùng sâu hơn hoặc dùng hết CPU có thể chậm hơn nhiều. Không nhân thẳng kết quả VPS với số luồng PC: CPU các máy khác nhau. Chưa đo phối hợp đủ các PC, chưa đo P95 web khi đồng thời phát tải API và chấm bot nặng.

## 4. Tính tải theo cấu hình

| Nội dung | Cách tính và ý nghĩa |
| --- | --- |
| 4.000 phiên kiểm trạng thái mỗi phút | `4.000 / 60 ≈ 66,67 yêu cầu/giây` trung bình; chưa tính thao tác khác hoặc đợt truy cập dồn cùng lúc |
| Chỗ chấm khi tất cả máy online | VPS 4 + local 6 + Sinh 12 + Hải 6 + Dương 12 = 40; đây là chỗ cấu hình, không phải trận/giây |
| Đợt giả định 2.000 trận, chỉ theo mẫu VPS | `2.000 / 1.698,2 ≈ 1,18 giờ` nếu tốc độ giữ nguyên; đây là phép tính tham khảo, không phải đã chấm 2.000 trận theo đợt đo này |

Ưu tiên giữ ổn định: theo dõi thời gian chờ hàng đợi, lỗi quá hạn và tài nguyên máy; bật thêm PC khi cần. Tăng số luồng chỉ hữu ích nếu CPU vẫn trả lời kịp 2,5 giây. Không hạ giới hạn CPU/đổi luật chỉ để làm đẹp số đo.

## 5. Đo lại khi thay máy hoặc thuật toán

**Đọc nhẹ website**, từ PowerShell/Ubuntu có Python, chọn tên kết quả chưa tồn tại:

```text
python scripts/measure_portal_reads.py --url https://ottv2-nhom12.koreacentral.cloudapp.azure.com --requests 90 --concurrency 8 --rps 3 --output output/public-performance.json
```

**API có dữ liệu giả**, trong Ubuntu, tại repository, không dùng các cổng thử 18765–18770 cho việc khác:

```bash
python3 scripts/benchmark_portal.py --students 4000 --output output/api-performance.json
```

Phép đo chỉ mở loopback, tự tạo/dọn dữ liệu giả, dừng khi health website thật lỗi. Các đợt phát 25/100/200 yêu cầu/giây; không chạy nhiều bộ phát cùng lúc. 4000 tài khoản chỉ là dữ liệu thử, không phải tài khoản có thể đăng nhập website thật.

**Máy chấm**, trong Ubuntu có Isolate: tạm dừng nhận trận/cập nhật, chờ hết việc, dừng đúng pool trên máy đo trước. Không để benchmark tranh sandbox với trận thật. Sau đó:

```bash
python3 scripts/benchmark_worker.py --slots 4 --matches 8 --output output/worker-performance.json
```

Kiểm kết quả, bật lại pool rồi mở nhận việc theo [MAY_CHAM.md](MAY_CHAM.md). Khi công bố số mới, thay số đo hiện hành và JSON tương ứng, ghi đủ máy/luật/tải/phạm vi; không chỉ chép con số trận/giờ. Cơ chế giảm tải giải thích ở [TOI_UU.md](TOI_UU.md).
