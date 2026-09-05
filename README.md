# Suno Audio Converter v4

Ứng dụng Windows tích hợp **API nội bộ chạy trên localhost**. Luồng xử lý:

`Link Suno → API nội bộ → metadata + nguồn audio trực tiếp công khai → FFmpeg → MP3 / WAV / M4A → lưu file / ZIP`

## Điểm mới v4
- Chỉ cần chạy **một file `SunoAudioConverter.exe`**.
- Ứng dụng tự khởi động FastAPI trên `127.0.0.1` bằng một cổng trống.
- Giao diện desktop gọi chính API nội bộ để phân tích link và xử lý audio.
- Có nút **API DOCS** để mở Swagger của server đang chạy.
- Phân tích metadata trước khi xử lý: title, artist, duration, cover, số nguồn public.
- Thử lần lượt tất cả nguồn audio trực tiếp công khai thay vì dừng ở nguồn đầu tiên bị lỗi.
- Chẩn đoán riêng: HTTP 401/403, HTML/JSON, placeholder, `audio/mp4` sai header, fMP4 fragment thiếu `moov/init`, FFmpeg không đọc được.
- Cache theo UUID + định dạng + normalize + mono + khoảng cắt.
- Batch queue, dark UI, progress tải file, lưu cover, phát file, mở thư mục và đóng ZIP.

## API nội bộ
### Metadata
```http
GET /api/suno-info?url=https://suno.com/song/<UUID>
```

Trả về các trường chính:
- `id`
- `title`
- `artist`
- `cover_url`
- `duration`
- `source_count`

### Chuyển đổi audio public
```http
GET /api/proxy-audio?id=<UUID>&format=mp3
GET /api/proxy-audio?id=<UUID>&format=wav
GET /api/proxy-audio?id=<UUID>&format=m4a
```

Tùy chọn:
- `normalize=true|false`
- `mono=true|false`
- `start=<giây>`
- `end=<giây>`

Ví dụ:
```bash
curl -L -o baihat.mp3 "http://127.0.0.1:8000/api/proxy-audio?id=5e0db2d1-38ca-4c19-9587-c7cc87750e0e&format=mp3&normalize=false&mono=false"
```

### Cache
```http
GET /api/cache-info
DELETE /api/cache
```

### Health
```http
GET /health
```

## Định dạng đầu ra
- **MP3 320 kbps CBR** — `libmp3lame`, 44.1 kHz.
- **WAV PCM 16-bit / 44.1 kHz**.
- **M4A AAC 256 kbps** + `faststart`.

## Chạy bằng Python
```bash
pip install -r requirements.txt
python app_v4.py
```

Nếu chỉ muốn chạy API độc lập:
```bash
uvicorn api_server:app --host 127.0.0.1 --port 8000
```

## Build EXE
GitHub Actions build `SunoAudioConverter.exe` từ `app_v4.py` và nhúng các dependency cần thiết của FastAPI/Uvicorn/FFmpeg.

Vào **Actions → Build Windows EXE** và tải artifact `SunoAudioConverter-Windows-v4`.

## Phạm vi xử lý
Công cụ xử lý **file audio trực tiếp công khai** mà trang Suno cung cấp và máy người dùng được phép truy cập. Nó không lấy khóa, không tự sinh token để vượt quyền truy cập, không gọi endpoint bản quyền/DRM và không giải mã luồng được bảo vệ. Nếu nguồn chỉ là playback segment hoặc dữ liệu được bảo vệ, API trả chẩn đoán cụ thể thay vì tạo file hỏng.
