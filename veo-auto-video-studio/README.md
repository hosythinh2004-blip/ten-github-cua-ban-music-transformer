# VEO AUTO VIDEO STUDIO

Web app chạy local để tạo video Veo theo hàng đợi. Quy tắc nhập chính: **1 dòng = 1 prompt = 1 video**.

## Có gì trong V1
- Dán 1–100 prompt, mỗi dòng là một job riêng.
- Queue trạng thái Ready / Waiting / Generating / Done / Error.
- Chạy tuần tự hoặc 2–3 job song song.
- Upload tối đa 3 ảnh tham chiếu và áp dụng cho toàn bộ batch.
- Veo 3.1 Fast hoặc Veo 3.1 Quality.
- 16:9 / 9:16, 720p / 1080p / 4K, 4 / 6 / 8 giây.
- Tự ép 8 giây khi dùng reference image hoặc 1080p/4K, đúng giới hạn Veo 3.1.
- Retry job lỗi, pause/resume hàng đợi, xem video sau khi hoàn tất.
- Video được tải về thư mục `output/` của app.

## Kết nối Veo
App dùng **Gemini API chính thức**, không lấy cookie đăng nhập Flow và không điều khiển giao diện Veo bằng Selenium.

1. Tạo Gemini API key tại Google AI Studio.
2. Copy `.env.example` thành `.env`.
3. Điền:
   ```env
   GEMINI_API_KEY=...
   PORT=3188
   ```
4. Chạy:
   ```bash
   npm install
   npm start
   ```
5. Mở `http://localhost:3188`.

### Windows
Có thể double-click `start.bat`. Lần đầu file sẽ tạo `.env`; điền API key rồi chạy lại.

## Lưu ý quan trọng
Đây là ứng dụng sử dụng Veo API. Nó **không thể dùng quota/credit của giao diện Google Flow chỉ bằng phiên đăng nhập trình duyệt**. Billing/quota áp dụng theo Gemini API key của bạn.
