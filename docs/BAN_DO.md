# Hướng dẫn quản trị tạo bản đồ JSON

Tài liệu này dành cho quản trị viên. Nội dung hướng dẫn và JSON mẫu nằm trong tệp này, không hiển thị trực tiếp trên trang web.

Bản Google Docs: [Hướng dẫn bản đồ JSON](https://docs.google.com/document/d/1Pa-BfF-t86ggMAvzNxm4k0___yKCi8uq_54iXA-G9fY/edit). Sau khi đăng nhập quản trị, mở đường dẫn **Hướng dẫn bản đồ JSON** ở **Điều hành** hoặc **Tài liệu**. Mở **Điều hành** để áp dụng bản đồ.

## 1. Các phần của tệp

| Trường | Điền gì |
| --- | --- |
| `schema_version` | Số nguyên `1`; giữ nguyên |
| `name` | Tên bản đồ, 1–80 ký tự |
| `board` | Tọa độ và quân đang đứng trên ô đó |
| `goals` | Hai đích khác nhau; A phải đến `goals.A`, B phải đến `goals.B` |
| `bombs` | Danh sách ô bom; không có thì dùng `[]` |
| `obstacles` | Danh sách ô cản; không có thì dùng `[]` |

Tọa độ viết thường, cột `a`–`i`, hàng `1`–`9`. Ví dụ `e4` là cột e, hàng 4. Chỉ khai báo ô có quân trong `board`; ô trống không cần ghi.

## 2. JSON mẫu đầy đủ

Sao chép toàn bộ nội dung, sửa tên và tọa độ, rồi lưu UTF-8 với đuôi `.json`, chẳng hạn `maps/ban-do-moi.json`.

```json
{
  "schema_version": 1,
  "name": "ban-do-moi",
  "board": {
    "a1": {"side": "A", "kind": "rock"},
    "a9": {"side": "B", "kind": "scissors"},
    "b1": {"side": "A", "kind": "paper"},
    "b9": {"side": "B", "kind": "paper"},
    "c1": {"side": "A", "kind": "scissors"},
    "c9": {"side": "B", "kind": "rock"},
    "d1": {"side": "A", "kind": "rock"},
    "d9": {"side": "B", "kind": "scissors"},
    "e1": {"side": "A", "kind": "paper"},
    "e9": {"side": "B", "kind": "paper"},
    "f1": {"side": "A", "kind": "scissors"},
    "f9": {"side": "B", "kind": "rock"},
    "g1": {"side": "A", "kind": "rock"},
    "g9": {"side": "B", "kind": "scissors"},
    "h1": {"side": "A", "kind": "paper"},
    "h9": {"side": "B", "kind": "paper"},
    "i1": {"side": "A", "kind": "scissors"},
    "i9": {"side": "B", "kind": "rock"}
  },
  "goals": {"A": "i9", "B": "a1"},
  "bombs": ["d5", "f5"],
  "obstacles": ["e5"]
}
```

## 3. Điều kiện để được nhận

- Đúng sáu trường trên, không thiếu hoặc thêm trường.
- Đúng 18 quân: mỗi bên A/B có 3 búa `rock`, 3 bao `paper`, 3 kéo `scissors`.
- Một tọa độ chỉ khai báo một lần; `side` chỉ là `A` hoặc `B`.
- Hai đích khác nhau. Đích có thể có quân đối phương, nhưng không có quân của chính bên cần đến đích đó lúc bắt đầu.
- Bom không chồng quân hoặc đích. Ô cản không chồng quân, bom hoặc đích. Không lặp ô trong danh sách bom/ô cản.
- Dùng dấu ngoặc kép, không chèn chú thích và không thêm dấu phẩy sau phần tử cuối. Tệp tối đa 65.536 byte.

Không bắt buộc bản đồ đối xứng, nhưng người tổ chức cần thử nhiều trận để kiểm tra sự cân bằng trước khi dùng chính thức.

## 4. Kiểm tệp trước khi áp dụng

Trong Ubuntu, tại thư mục mã nguồn:

```bash
python3 -m ottv2.map_config maps/ban-do-moi.json
```

**Cần thấy:** `MAP OK`, `pieces=18` và mã SHA-256. Nếu báo `MAP ERROR`, sửa JSON trước; lệnh kiểm không thay bản đồ đang thi đấu.

| Thông báo | Cách sửa |
| --- | --- |
| `duplicate map key` | Có tọa độ hoặc tên trường bị lặp |
| `map must start with 18 pieces` | Thừa/thiếu quân trong board |
| `each side needs 3 rock, 3 paper, 3 scissors` | Đếm lại từng loại quân của mỗi bên |
| `overlap` hoặc `own winning goal` | Kiểm ô trùng và quân đặt trên đích của chính mình |
| `valid UTF-8 JSON` | Kiểm dấu ngoặc, dấu phẩy, dấu ngoặc kép và mã hóa tệp |

## 5. Đổi bản đồ qua website

1. Đăng nhập quản trị, mở **Điều hành** và bấm **Tạm dừng** nhận trận.
2. Dán toàn bộ JSON vào ô **Đổi bản đồ**.
3. Điền mã main.py vào đủ ba ô **sơ cấp, trung cấp, cao cấp**. Các ô được điền sẵn mã đang dùng; giữ nguyên nếu chỉ đổi bản đồ. Mỗi mã tối đa 64 KiB và phải đúng cú pháp Python.
4. Bấm **Lưu bản đồ và 3 bot đấu thử**. Bản đồ và cả ba mã chỉ được lưu cùng nhau khi tất cả hợp lệ.
5. Xem lại hình bàn cờ: quân, đích, bom và ô cản phải đúng.
6. Bấm **Nhận trận trở lại**.

Trận đã ghép giữ bản đồ đã chốt; trận ghép sau dùng bản đồ vừa áp dụng. Đổi bản đồ không xóa điểm hoặc hoàn lượt đã dùng. Log chứa toàn bộ bàn ban đầu nên replay offline vẫn đọc được các trận dùng bản đồ mới.

Quản lý tài khoản, hai loại nút tạm dừng và sao lưu: [VAN_HANH.md](VAN_HANH.md).

## 6. Cấu hình cho HTML đấu thử

Nút **Sao chép cấu hình đấu thử** ở màn hình đăng nhập và trang Thi đấu của sinh viên xuất đúng bản đồ hiện tại, ba mã bot và giới hạn trận. Đây là dữ liệu công khai, không chứa khóa máy chấm, tài khoản hay mật khẩu. Sinh viên dán vào **Dán cấu hình từ website** trong HTML rồi bấm **Áp dụng cấu hình**. Mã đối thủ chạy trên máy sinh viên; lưu tại đây không chạy mã đó trên VPS.

Gói cấu hình có cấu trúc ngoài gồm `schema_version: 1`, `type: "ottv2_practice_config"`, `map` (JSON sáu trường ở trên), `bots` (ba chuỗi mã `beginner`, `intermediate`, `advanced`), `limits` và `config_sha256`. Không nhét ba mã vào bên trong `map`. Nên lấy gói bằng nút Sao chép thay vì tự viết các dấu xuống dòng trong chuỗi JSON. Có thể dán nguyên gói vào ô bản đồ ở Điều hành; giao diện tách bản đồ và điền ba ô mã cho quản trị kiểm trước khi lưu.

Nếu chưa Tạm dừng mà lưu, website báo: **Chưa đổi bản đồ: bạn cần bấm Tạm dừng nhận trận trước.** Không thay dữ liệu nào. Mã được kiểm cú pháp trước khi ghi, chưa chứng minh bot chơi đúng: hãy đấu thử từng cấp trên bản đồ vừa chọn trước khi mở giải. Bot cần tự chứa trong một main.py, dùng Python chuẩn, in READY rồi đọc stdin/trả JSON có flush theo [VIET_BOT.md](VIET_BOT.md).
