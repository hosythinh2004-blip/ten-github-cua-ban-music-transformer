import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import requests

import app_v14 as base

base.APP_VERSION = "1.5.0"


def _looks_like_standalone_audio(path: Path, content_type: str):
    with open(path, "rb") as f:
        head = f.read(64)
    ctype = (content_type or "").lower()

    if "mp4" in ctype or "m4a" in ctype:
        # Standalone MP4/M4A normally exposes an ISO-BMFF box near the start.
        if b"ftyp" not in head[:32] and b"styp" not in head[:32]:
            return False, "Phản hồi ghi audio/mp4 nhưng không có header MP4/M4A; có thể là playback segment hoặc dữ liệu trung gian, không phải file độc lập"
    elif "mpeg" in ctype or "mp3" in ctype:
        if not (head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)):
            return False, "Phản hồi ghi audio/mpeg nhưng không có header MP3 hợp lệ"
    return True, ""


class ConverterApp(base.ConverterApp):
    def _build_ui(self):
        super()._build_ui()
        self.title(base.APP_NAME)

    def _download(self, url: str, dest: Path):
        headers = {
            "User-Agent": base.UA,
            "Referer": "https://suno.com/",
            "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
        }
        try:
            with self.resolver.session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(20, 120),
                allow_redirects=True,
            ) as r:
                final_url = r.url or url
                if r.status_code in (401, 403):
                    raise base.SunoAccessBlocked(
                        f"HTTP {r.status_code} từ {urlparse(final_url).netloc}"
                    )
                r.raise_for_status()
                ctype = (r.headers.get("Content-Type") or "").lower()
                if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                    raise RuntimeError(f"Nguồn trả về {ctype}, không phải file audio")

                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except requests.RequestException as exc:
            raise RuntimeError(f"Lỗi mạng khi đọc nguồn: {exc}") from exc

        size = dest.stat().st_size if dest.exists() else 0
        if size < 16384:
            raise RuntimeError(
                f"Nguồn chỉ có {size} byte: file quá nhỏ/placeholder, không phải bài hát hoàn chỉnh"
            )

        ok, reason = _looks_like_standalone_audio(dest, ctype)
        if not ok:
            raise RuntimeError(reason)

        return {
            "size_mb": size / (1024 * 1024),
            "content_type": ctype,
            "host": urlparse(final_url).netloc,
        }

    def _worker(self, mode, items, out_dir: Path, fmt: str):
        ok_count = 0
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.log(f"FFmpeg: {ffmpeg}")
        try:
            total = len(items)
            for i, item in enumerate(items, 1):
                self.set_progress(((i - 1) / total) * 100)
                try:
                    if mode == "local":
                        path = Path(item)
                        self.log(f"[{i}/{total}] File: {path}")
                        if not path.exists() or path.stat().st_size < 4096:
                            raise RuntimeError("File nguồn không tồn tại hoặc quá nhỏ")
                        output = self._unique_output(out_dir, path.stem, fmt)
                        self._convert_and_validate(ffmpeg, path, output, fmt)
                    else:
                        self.log(f"[{i}/{total}] Link: {item}")
                        candidates, title, is_suno = self.resolver.resolve(item)
                        output = self._unique_output(out_dir, title, fmt)
                        converted = False
                        saw_access_block = False
                        errors = []

                        with tempfile.TemporaryDirectory(prefix="suno_converter_") as tmp:
                            source = Path(tmp) / "source_audio"
                            for n, audio_url in enumerate(candidates, 1):
                                try:
                                    if source.exists():
                                        source.unlink()
                                    if output.exists():
                                        output.unlink()

                                    self.log(
                                        f"  • Nguồn {n}/{len(candidates)}: {base.source_kind(audio_url)} • {urlparse(audio_url).netloc}"
                                    )
                                    info = self._download(audio_url, source)
                                    self.log(
                                        f"    ↳ {info['size_mb']:.2f} MB • {info['content_type'] or 'không rõ'} • {info['host']}"
                                    )
                                    self._convert_and_validate(ffmpeg, source, output, fmt)
                                    converted = True
                                    break
                                except base.SunoAccessBlocked as exc:
                                    saw_access_block = True
                                    errors.append(f"access: {exc}")
                                    self.log(f"    ↳ Bị từ chối truy cập: {exc}")
                                except Exception as exc:
                                    errors.append(str(exc))
                                    self.log(f"    ↳ Không phải file audio độc lập hợp lệ: {str(exc)[-350:]}")

                        if not converted:
                            if is_suno:
                                if saw_access_block:
                                    raise base.SunoAccessBlocked(
                                        "Có nguồn trả 401/403. Đây là từ chối truy cập ở máy chủ Suno; ứng dụng không thể biến phản hồi đó thành file audio."
                                    )
                                raise RuntimeError(
                                    "Trang Suno có trả về tài nguyên playback, nhưng không có tài nguyên nào là file audio độc lập mà FFmpeg có thể chuyển đổi. "
                                    "Đây là lỗi nhận dạng/kiểu nguồn của tool, không phải bằng chứng rằng bạn đã hết lượt download."
                                )
                            raise RuntimeError("Không có nguồn audio hợp lệ: " + (errors[-1] if errors else "không rõ"))

                    self.last_output = output
                    self.after(0, lambda: self.play_btn.config(state="normal"))
                    self.log(f"  ✓ Đã tạo file phát được: {output}")
                    ok_count += 1
                except base.SunoAccessBlocked as exc:
                    self.log(f"  ✗ TRUY CẬP BỊ TỪ CHỐI: {exc}")
                except Exception as exc:
                    self.log(f"  ✗ Lỗi: {exc}")
                self.set_progress((i / total) * 100)

            self.log(f"Hoàn tất: {ok_count}/{total} file thành công.")
            self.after(0, lambda: base.messagebox.showinfo(base.APP_NAME, f"Hoàn tất: {ok_count}/{total} file thành công."))
        finally:
            self.is_running = False
            self.after(0, lambda: self.link_btn.config(state="normal"))
            self.after(0, lambda: self.local_btn.config(state="normal"))


if __name__ == "__main__":
    ConverterApp().mainloop()
