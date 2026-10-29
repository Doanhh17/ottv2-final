# Luật thi đấu và hướng dẫn viết bot

Đọc mục 1–3 để hiểu luật và giới hạn, mục 4 để biết cách nhận/trả dữ liệu, rồi lấy main.py mẫu ở mục 6 và tự kiểm theo mục 7. Quy định tài khoản, đăng ký, cập nhật, lượt đấu, mật khẩu và lưu log nằm trong [QUY_DINH_SINH_VIEN.md](QUY_DINH_SINH_VIEN.md); sinh viên cần tuân thủ cả hai.

## 1. Bàn cờ và quân

Bàn 9×9 có cột `a`–`i`, hàng `1`–`9`; tọa độ viết thường, ví dụ `e4`. Hai bên A và B được thể hiện bằng đỏ và xanh. Mỗi bên bắt đầu với 3 búa, 3 bao, 3 kéo. Mỗi trận gồm một ván; bên đi trước được chọn ngẫu nhiên.

| Tên JSON | Tên quân | Ăn được |
| --- | --- | --- |
| `rock` | Búa | `scissors` |
| `paper` | Bao | `rock` |
| `scissors` | Kéo | `paper` |

Mỗi bên có một ô đích trong `goals`. `goals.A` là ô A cần đến để thắng, tương tự B. Không suy ra đích từ màu/quân đang ở đó; đọc dữ liệu trạng thái. Bản đồ có thể thay đổi giữa các đợt thi đấu.

## 2. Nước đi hợp lệ

Mỗi lượt chọn một quân của mình và đi đúng một ô theo một trong tám hướng. Điều kiện:

1. Ô xuất phát có quân thuộc bên đến lượt.
2. Ô đến ở trong bàn, khác ô xuất phát và cách tối đa một đơn vị ở mỗi trục.
3. Ô đến không phải chướng ngại vật.
4. Ô đến trống, hoặc có quân đối phương mà quân đang đi khắc chế.

Không nhảy qua ô, đứng yên, đi vào quân đồng minh, ăn quân cùng loại hoặc đi vào quân khắc chế mình. Nước đi chỉ có hai trường `from` và `to`.

### Thắng, thua và hòa

- Quân đi vào đích của bên mình mà không bị bom loại thì bên đó thắng.
- Một bên mất hết bất kỳ loại quân nào thì thua.
- Đi vào bom: quân di chuyển bị loại và bom biến mất; không gây nổ các ô xung quanh.
- Khi cùng trạng thái gồm vị trí/loại quân, tập bom và bên đến lượt xuất hiện lần thứ ba, trận hòa.
- Mỗi bên được đi tối đa 25 nước hợp lệ. Sau nước thứ 50 của cả trận, nếu chưa thắng/thua theo luật bàn cờ thì đếm quân còn trên bàn: bên nhiều quân hơn thắng, bằng quân thì hòa. Mỗi quân có giá trị như nhau; quân mất do bom không còn được tính. Thắng ở nước cuối vẫn được xét trước việc so quân. Nước sai luật không tăng bộ đếm và không cấp lại thời gian.
- Không có nước bỏ lượt. Nếu không có nước hợp lệ, bên đến lượt vẫn chịu hạn phản hồi/CPU.

Engine là bộ phận kiểm luật trước khi đổi bàn. Sau một nước hợp lệ, Engine thực hiện ăn quân/bom, kiểm thắng đích, kiểm mất loại quân, đổi lượt. Nếu đã đủ 50 nước thì so quân; nếu chưa đủ thì xét lặp ba lần. Lặp trước mốc 50 vẫn hòa, kể cả khi một bên đang hơn quân. Nếu xét mất loại, bên vừa đi được kiểm trước đối phương. Cấu hình bản đồ không cho bom chồng quân/đích, vật cản chồng đối tượng khác hoặc hai đích trùng nhau.

## 3. Môi trường và thời gian

Bot là chương trình Python chạy liên tục suốt trận, không khởi động lại mỗi lượt. Chỉ nộp `main.py`, dùng thư viện chuẩn. Không import gói `ottv2`, mở mạng, đọc tệp ngoài sandbox hoặc phụ thuộc đường dẫn máy cá nhân.

| Giới hạn | Quy định |
| --- | --- |
| Khởi động | 5 giây thời gian thực để xuất READY |
| CPU thi đấu | Tổng 7 giây CPU trong các lượt của chính bot |
| Phản hồi | 2,5 giây thực cho một lượt, không cấp lại khi gửi nước sai luật |
| Bộ nhớ | 64 MiB, áp dụng từ khởi động và cả ngoài lượt |
| Dòng phản hồi | JSON UTF-8 có newline; tối đa 65.536 ký tự đã giải mã, gồm newline |

CPU chỉ tính thời gian bộ xử lý thực hiện công việc của bot; thời gian thực là số giây chờ trên đồng hồ, vẫn trôi khi bot ngủ hoặc chờ dữ liệu. Nếu muốn đi đủ 25 lượt, trung bình mỗi lượt chỉ có khoảng 0,28 giây CPU; bot nên dành thời gian nhiều hơn cho thế khó và ít hơn cho nước rõ ràng. Dù còn CPU, một lượt vẫn phải trả lời trong 2,5 giây thực. Trả lời nhanh không được cộng điểm; thắng/thua theo luật bàn cờ hoặc lỗi/hết giờ. CPU ngoài lượt được phép tính trước và không trừ đồng hồ 7 giây. Bot chỉ được xuất nước khi nhận trạng thái cho lượt mình, không gửi sẵn nhiều nước.

Phải dự phòng cho xuất/nhận dữ liệu và tải máy, không tính đến sát 2,5 giây. `time` là ảnh chụp trước khi gửi trạng thái, không phải số luôn chính xác đến lúc bot đọc xong. Phiên còn có giới hạn bảo vệ tổng của Worker, mô tả trong [thiết kế hệ thống](THIET_KE.md#5-giới-hạn-và-các-lớp-bảo-vệ).

## 4. Giao thức stdin/stdout

Mỗi thông điệp nằm trên **một dòng JSON**. Mã JSON trình bày nhiều dòng trong tài liệu chỉ để dễ đọc; khi gửi dùng `json.dumps(...)` tạo một dòng.

### Khởi động

`stdin` là nơi bot nhận dữ liệu từ máy chấm; `stdout` là nơi bot trả lời; `stderr` dành cho thông báo kiểm tra lỗi. Trong Python, `input()` hoặc `sys.stdin` đọc stdin, `print()` mặc định ghi stdout.

Bot gửi đúng một dòng trước khi nhận trạng thái:

```json
{"protocol_version":1,"type":"ready"}
```

Không thêm khóa, lời chào hoặc debug vào dòng READY. Dùng `print(..., flush=True)` để dữ liệu không nằm lại trong bộ đệm.

### Trạng thái nhận vào

| Trường | Ý nghĩa |
| --- | --- |
| `protocol_version` | Phiên bản giao thức, hiện là số nguyên 1 |
| `type` | Chuỗi `state` |
| `you` | Bên của bot, `A` hoặc `B` |
| `turn` | Bên đang đến lượt |
| `board` | Tọa độ → đối tượng `{side, kind}`; ô trống không có trong ánh xạ |
| `goals` | Đích cần đến của A và B |
| `bombs` | Danh sách ô còn bom |
| `obstacles` | Danh sách ô bị chặn |
| `time` | Ngân sách và thời gian còn lại do máy chấm cung cấp |

Ví dụ cấu trúc thời gian:

```json
{
  "cpu_budget_seconds": 10.0,
  "cpu_remaining_seconds": {"A": 9.8, "B": 9.9},
  "response_limit_seconds": 2.5,
  "response_remaining_seconds": 2.499
}
```

Tất cả đơn vị thời gian là giây. Đọc CPU còn lại qua `state["time"]["cpu_remaining_seconds"][state["you"]]`. Khi tự thử bằng trạng thái tối giản không có `time`, có thể dùng `state.get("time")` để kiểm tra trước khi đọc đồng hồ.

Giao thức không gửi danh sách nước hợp lệ hoặc lịch sử toàn trận. Bot tự tìm nước hợp lệ từ bàn hiện tại và có thể lưu thông tin trong bộ nhớ giữa các lượt. Engine giữ lịch sử riêng để quyết định lặp ba lần.

### Nước trả về

```json
{"from":"b1","to":"b2"}
```

Phải có đúng hai khóa `from`, `to`, giá trị là tọa độ hợp lệ. Chỉ xuất một nước cho mỗi trạng thái nhận được. Thông báo kiểm tra lỗi ghi vào `stderr`, ví dụ `print("depth=3", file=sys.stderr, flush=True)`.

## 5. Xử lý lỗi

| Lý do | Điều kiện | Kết quả |
| --- | --- | --- |
| `format_error` | JSON sai, thiếu/thừa trường, kiểu/tọa độ sai hoặc dòng không hoàn chỉnh | Bot thua |
| Nước sai luật | JSON đúng nhưng không hợp lệ với bàn hiện tại | Giữ bàn/lượt, gửi lại trạng thái; tiếp tục trừ giờ trong cùng lượt |
| `cpu_timeout` | Hết CPU thi đấu của bot | Bot thua |
| `response_timeout` | Không trả nước hợp lệ trong 2,5 giây thực | Bot thua |
| `runtime_error` | Tiến trình dừng/lỗi, có bằng chứng trạng thái chạy tương ứng | Bot thua |
| `memory_limit` | Máy chấm xác nhận bot vượt bộ nhớ cho phép | Bot thua |
| `startup_timeout` | Không READY trước hạn khởi động | Thua trước nước đầu nếu đối thủ sẵn sàng |
| `startup_format_error` | READY sai giao thức | Như trên |
| `startup_runtime_error` | Lỗi chương trình lúc khởi động | Như trên |
| `startup_memory_limit` | Vượt bộ nhớ lúc khởi động | Như trên |

Nếu máy chấm gặp lỗi, thiếu dữ liệu đo tài nguyên hoặc cả hai bot không khởi động được, hệ thống giữ thông tin để kiểm tra và thử lại; không tự chọn một bên thắng. READY thành công chỉ xác nhận bot khởi động đúng, không bảo đảm thuật toán luôn trả nước hợp lệ.

## 6. Mã main.py mẫu

Bot dưới đây chọn nước hợp lệ đầu tiên. Đây là mẫu giao tiếp, không phải thuật toán thi đấu mạnh. Nếu không có nước hợp lệ, bot không xuất `pass`; nó tiếp tục chờ và chịu hạn phản hồi.

```python
import json
import sys

BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def choose(state):
    side = state["you"]
    board = state["board"]
    blocked = set(state["obstacles"])
    for start, piece in sorted(board.items()):
        if piece["side"] != side:
            continue
        x, y = ord(start[0]) - 97, int(start[1]) - 1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if (dx == dy == 0) or not (0 <= nx < 9 and 0 <= ny < 9):
                    continue
                end = chr(97 + nx) + str(ny + 1)
                if end in blocked:
                    continue
                target = board.get(end)
                if target and (target["side"] == side or
                               BEATS[piece["kind"]] != target["kind"]):
                    continue
                return {"from": start, "to": end}
    return None


print(json.dumps({"protocol_version": 1, "type": "ready"}), flush=True)
for line in sys.stdin:
    state = json.loads(line)
    move = choose(state)
    if move is not None:
        print(json.dumps(move), flush=True)
```

Mã mẫu có sẵn ở `examples/greedy/main.py`. Muốn thử thuật toán khác, thay phần chọn nước; bài nộp vẫn phải chứa toàn bộ mã trong một `main.py`.

## 7. Tự kiểm tra

### Kiểm cú pháp và READY

```bash
python3 -m py_compile main.py
python3 main.py
```

Mong đợi không lỗi cú pháp, sau đó một dòng READY. Chương trình chờ đầu vào là bình thường; Ctrl+C để dừng. Chỉ chạy trực tiếp mã do mình kiểm soát. Máy chủ phải dùng sandbox cho bài nộp.

### Một lần nhận trạng thái

Tại thư mục dự án, tạo trạng thái thử từ Engine rồi truyền vào bot mẫu:

```bash
mkdir -p output
python3 -c 'import json; from ottv2.engine import sample_game; from ottv2.protocol import state_message; print(json.dumps(state_message(sample_game())))' > output/bot-state.json
python3 examples/greedy/main.py < output/bot-state.json
```

Mong đợi READY rồi một dòng nước đi JSON. Để thử mã vừa viết, thay đường dẫn `examples/greedy/main.py` bằng đường dẫn tới `main.py` đó. File thử này không có thông số đồng hồ, dùng để kiểm giao tiếp cơ bản.

### Đấu trong Isolate

Sau khi cấu hình Isolate và dừng máy chấm khác:

```bash
sudo systemctl start isolate.service
python3 -m ottv2.cpu_trial --bot-a-file examples/greedy/main.py --bot-b-file examples/greedy/main.py --seed 1 --result-file output/bot-trial.json
```

Mong đợi READY cho hai bên, các nước đi, `RESULT`, `CLEANUP` và tệp JSON. Tệp kết quả phải dùng tên mới nếu đã tồn tại. Có thể dùng một bot mẫu làm đối thủ bằng cách chỉ thay một đường dẫn.

Cách đưa bài lên GitHub, cập nhật trên website và lưu trận: [quy định và cách sử dụng](QUY_DINH_SINH_VIEN.md).

## Xem kết quả và replay offline

Trên website, vào **Kết quả**. Mỗi trận hiển thị đối thủ và lý do thắng/thua/hòa. Bấm **Sao chép log** ở trận muốn xem, rồi tải [trình replay offline](https://drive.google.com/file/d/1lfaBDrBbYPJRhikBKEGlKnfBxuunlYFm/view).

Mở tệp HTML đã tải bằng trình duyệt. Ban đầu chỉ có bàn cờ, chưa có trận hoặc đồng hồ CPU. Mở **Dán JSON**, dán log và bấm **Mở JSON đã dán**; hoặc dùng **Mở tệp JSON** nếu đã lưu log thành tệp. Có thể ngắt mạng để xem. Các nút về đầu/cuối, trước/sau, thanh tua và phím mũi tên chọn nước; phím cách phát/dừng, tốc độ 0,5×–4× chỉ thay đổi tốc độ xem.

Đồng hồ và giới hạn lấy từ từng bản ghi. Trận của giải dùng 7 giây CPU/bot, 2,5 giây phản hồi/lượt và 50 nước tổng. Nếu mở log có giới hạn khác, replay ghi rõ **Giới hạn khác luật hiện hành** và giữ nguyên số đo; log thiếu số đo CPU từng nước sẽ không được tự ước lượng. Số quân còn lại được đếm tại nước đang xem.
