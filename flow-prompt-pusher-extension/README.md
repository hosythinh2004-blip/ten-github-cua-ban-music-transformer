# Flow Prompt Pusher

Chrome Extension hỗ trợ đẩy nhiều prompt vào Google Flow/Veo bằng chính phiên Google đang đăng nhập trên trình duyệt.

## Mục tiêu
- Không dùng Gemini/Veo API.
- Không yêu cầu API key.
- Không đăng nhập thay người dùng và không lưu mật khẩu/cookie.
- Chỉ thao tác ô prompt và nút Generate trên trang Google Flow đang mở.
- 1 dòng = 1 prompt.

## Cách dùng
1. Đăng nhập Google Flow bình thường tại `https://flow.google/`.
2. Mở project bạn muốn tạo video.
3. Cài model, tỷ lệ, ảnh/ingredient/reference trực tiếp trong Flow như bình thường.
4. Cài extension bằng Chrome Developer Mode:
   - Mở `chrome://extensions/`.
   - Bật **Developer mode**.
   - Chọn **Load unpacked** và chọn thư mục `flow-prompt-pusher-extension`.
5. Quay lại Flow, panel **FLOW PROMPT PUSHER** sẽ xuất hiện bên phải.
6. Dán prompt, mỗi dòng một prompt.
7. Bấm **Tự nhận diện**. Nếu Flow đổi giao diện và nhận diện sai, dùng:
   - **Chọn ô Prompt** rồi click ô prompt của Flow.
   - **Chọn nút Generate** rồi click nút Generate của Flow.
8. Chọn khoảng cách giữa các prompt và bấm **BẮT ĐẦU**.

## Hành vi
Extension sẽ lần lượt:
1. Điền prompt hiện tại vào ô prompt.
2. Bấm Generate.
3. Chờ khoảng thời gian cấu hình và/hoặc chờ nút Generate sẵn sàng.
4. Chuyển sang prompt tiếp theo.

Có nút Tạm dừng / Tiếp tục / Dừng.

## Lưu ý
- Model, ảnh tham chiếu, ingredient, Frames và các cài đặt Veo vẫn do bạn chọn trực tiếp trong Flow trước khi bắt đầu.
- Extension không cố vượt quota, credit, CAPTCHA, xác minh tài khoản hoặc giới hạn của Google Flow.
- Giao diện Flow có thể thay đổi; vì vậy extension có chế độ chọn thủ công ô prompt và nút Generate để giảm phụ thuộc selector cố định.
