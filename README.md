# Suno Audio Converter v3

Ứng dụng Windows nhận **link bài hát Suno**, đọc metadata công khai, tìm **file audio trực tiếp công khai** nếu trang có cung cấp, sau đó chuyển đổi bằng FFmpeg sang **MP3 / WAV / M4A**.

## Quy trình
1. **URL Resolution**
   - Nhận link `https://suno.com/song/<UUID>` hoặc `https://suno.com/s/...`.
   - Trích UUID bằng Regex.
   - Đọc metadata công khai từ trang: title, artist (nếu có), duration (nếu có), cover art.
   - Tìm các URL audio trực tiếp được nhúng công khai trong HTML/metadata.

2. **Kiểm tra nguồn audio**
   - Tải thử từng nguồn audio trực tiếp.
   - Kiểm tra bằng FFmpeg để chắc chắn đó là file audio hoàn chỉnh, không phải HTML/JSON/placeholder/segment lỗi.
   - **Không gọi endpoint quyền/DRM, không lấy khóa mã hóa, không giải mã luồng được bảo vệ.**

3. **Transcoding**
   - MP3 320 kbps CBR (`libmp3lame`).
   - WAV PCM 16-bit / 44.1 kHz.
   - M4A AAC 256 kbps + `faststart`.
   - Tùy chọn normalize âm lượng, mono và cắt đoạn theo thời gian.
   - Sau khi tạo, giải mã kiểm tra lại toàn bộ file đầu ra bằng FFmpeg.

4. **Batch / Cache / ZIP**
   - Hàng đợi nhiều link.
   - Trạng thái từng bài: Chờ → Phân tích → Tải audio → Mã hóa → Hoàn tất.
   - Smart cache để không chuyển đổi lại cùng link + cùng thiết lập.
   - Có thể lưu cover art.
   - Đóng gói tất cả file hoàn tất thành một file ZIP.

## Phạm vi hỗ trợ
Công cụ chỉ xử lý **nguồn audio trực tiếp công khai** mà trang Suno cung cấp và máy người dùng được phép truy cập. Nếu bài chỉ cung cấp playback segment hoặc luồng được bảo vệ, ứng dụng sẽ báo `Không có audio public` thay vì cố giải mã hoặc vượt cơ chế truy cập.

## Chạy bằng Python
Yêu cầu Python 3.12 trên Windows.

```bash
pip install -r requirements.txt
python app_v3.py
```

## Build EXE
GitHub Actions build `SunoAudioConverter.exe`.

Vào **Actions → Build Windows EXE** và tải artifact `SunoAudioConverter-Windows`.
