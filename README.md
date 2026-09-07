# SunoFlow

Công cụ web nhỏ gọn để lấy audio từ **liên kết Suno công khai** và chuyển đổi sang MP3 hoặc WAV. Tệp nguồn và tệp đầu ra chỉ được lưu trong thư mục tạm, sau đó được xoá ngay khi phản hồi hoàn tất.

## Yêu cầu

- Node.js 20+
- [FFmpeg](https://ffmpeg.org/) có trong `PATH` (hoặc đặt biến `FFMPEG_PATH`)

## Chạy ứng dụng

```bash
npm start
```

Mở `http://localhost:3000`. Có thể đổi cổng bằng biến môi trường `PORT`.

## Kiểm thử

```bash
npm test
```

> Chỉ tải xuống và chuyển đổi nội dung mà bạn sở hữu hoặc được chủ sở hữu cho phép. Ứng dụng chỉ hỗ trợ nội dung Suno công khai, không vượt qua đăng nhập hay biện pháp bảo vệ truy cập.
