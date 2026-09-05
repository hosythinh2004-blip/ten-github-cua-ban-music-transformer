# Suno Audio Converter

Ứng dụng Windows có giao diện để nhận link audio công khai / link chia sẻ Suno, tải nguồn audio khi URL cho phép truy cập và chuyển sang **MP3** hoặc **WAV**.

## Tính năng
- Dán một hoặc nhiều link (mỗi dòng một link).
- Hỗ trợ `https://suno.com/song/<UUID>` và link rút gọn `https://suno.com/s/...` khi link công khai có thể resolve được.
- Hỗ trợ URL audio trực tiếp như `.m4a`, `.mp3`, `.wav`, `.aac`.
- Chuyển sang MP3 320 kbps hoặc WAV PCM 16-bit/44.1 kHz bằng FFmpeg.
- Chọn thư mục lưu ngay trong app.
- Không cần cài FFmpeg thủ công: dùng binary đi kèm `imageio-ffmpeg`.
- Có log trạng thái và xử lý nhiều link tuần tự.

> Lưu ý: công cụ không vượt qua đăng nhập, DRM, paywall hoặc cơ chế kiểm soát truy cập. Chỉ dùng với nội dung bạn có quyền tải/xử lý hoặc URL audio công khai mà máy bạn truy cập được.

## Chạy bằng Python
Yêu cầu Python 3.11+ trên Windows.

```bash
pip install -r requirements.txt
python app.py
```

## Tạo file EXE
GitHub Actions trong repo sẽ build file `SunoAudioConverter.exe`. Vào **Actions → Build Windows EXE → Run workflow**, sau khi chạy xong tải artifact `SunoAudioConverter-Windows`.

## Cách dùng
1. Mở app.
2. Dán link Suno hoặc URL audio, mỗi dòng một link.
3. Chọn MP3 hoặc WAV.
4. Chọn thư mục lưu.
5. Bấm **BẮT ĐẦU CHUYỂN ĐỔI**.

Nếu Suno thay đổi cấu trúc link/API công khai, resolver có thể cần cập nhật.