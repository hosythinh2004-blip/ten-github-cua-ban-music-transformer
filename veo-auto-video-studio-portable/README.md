# VEO AUTO VIDEO STUDIO Portable

Bản Windows portable: tải về, chạy `VEO-Auto-Video-Studio.exe`, không cần cài Node/Python/npm.

## Lần đầu sử dụng
1. Mở EXE.
2. Trình duyệt tự mở giao diện tại `http://127.0.0.1:3188`.
3. Nhập Gemini API key có quyền sử dụng Veo.
4. Dán prompt: **mỗi dòng = 1 video**.
5. Chọn ảnh tham chiếu / model / tỉ lệ / chất lượng.
6. Bấm **GENERATE ALL**.

API key chỉ lưu trên máy tại `%APPDATA%\VeoAutoVideoStudio\config.json`.
Video được lưu vào `Documents\VEO Auto Video Studio`.

## Tính năng
- 1 dòng = 1 prompt
- Veo 3.1 Fast / Veo 3.1 Quality
- 16:9 / 9:16
- 720p / 1080p / 4K
- 4 / 6 / 8 giây
- tối đa 3 ảnh tham chiếu cho cả batch
- chạy 1–3 job song song
- Pause / Resume / Retry Error
- tự tải MP4 về máy
- không cần npm install trên máy người dùng

Ứng dụng dùng Gemini API chính thức. Quota/billing của Gemini API tách biệt với credit trong giao diện Google Flow.
