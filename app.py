import json
import re
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urlparse

import imageio_ffmpeg
import requests

APP_NAME = "Suno Audio Converter"
APP_VERSION = "1.2.0"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".webm"}


def safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:120] or "audio"


def guess_name_from_url(url: str) -> str:
    return safe_name(Path(urlparse(url).path).stem or "audio")


def source_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".m4a"):
        return "M4A"
    if path.endswith(".mp3"):
        return "MP3"
    if path.endswith(".wav"):
        return "WAV"
    if path.endswith(".aac"):
        return "AAC"
    return "audio"


class Resolver:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Referer": "https://suno.com/",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def resolve(self, raw_url: str):
        url = raw_url.strip().strip("'\"")
        if not url:
            raise ValueError("Link trống")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url

        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]
        suffix = Path(parsed.path).suffix.lower()

        if suffix in AUDIO_EXTS:
            return [url], guess_name_from_url(url)
        if host in {"suno.com", "www.suno.com"}:
            return self._resolve_suno(url)
        return self._resolve_audio_from_page(url)

    def _resolve_suno(self, url: str):
        clip_id = self._extract_uuid(url)
        final_url = url
        page_html = ""

        if not clip_id and "/s/" in urlparse(url).path:
            clip_id, final_url = self._resolve_short_link(url)

        try:
            r = self.session.get(
                final_url,
                timeout=20,
                allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            final_url = r.url
            content_type = (r.headers.get("Content-Type") or "").lower()
            if "text" in content_type or not content_type:
                page_html = r.text
            clip_id = clip_id or self._extract_clip_id_from_page(page_html) or self._extract_uuid(final_url)
        except requests.RequestException:
            pass

        if not clip_id:
            raise RuntimeError(
                "Không lấy được ID bài hát từ link Suno. Hãy mở link trên trình duyệt và thử lại."
            )

        candidates = []
        title = clip_id

        meta_urls = [
            f"https://studio-api-prod.suno.com/api/clip/{clip_id}",
            f"https://studio-api.prod.suno.com/api/clip/{clip_id}",
            f"https://studio-api.suno.ai/api/clip/{clip_id}",
        ]
        for api_url in meta_urls:
            try:
                r = self.session.get(
                    api_url,
                    timeout=15,
                    allow_redirects=False,
                    headers={"Accept": "application/json"},
                )
                if not r.ok:
                    continue
                data = r.json()
                if not isinstance(data, dict):
                    continue

                title = safe_name(data.get("title") or title)

                media_urls = data.get("media_urls") or data.get("mediaUrls") or []
                if isinstance(media_urls, list):
                    m4a, other = [], []
                    for item in media_urls:
                        if not isinstance(item, dict):
                            continue
                        media_url = item.get("url")
                        content_type = str(item.get("content_type") or item.get("contentType") or "").lower()
                        if not self._is_usable_audio_url(media_url):
                            continue
                        if "m4a" in content_type or urlparse(media_url).path.lower().endswith(".m4a"):
                            m4a.append(media_url)
                        else:
                            other.append(media_url)
                    candidates.extend(m4a)
                    candidates.extend(other)

                for key in ("audio_url", "audioUrl", "stream_audio_url", "streamAudioUrl"):
                    value = data.get(key)
                    if self._is_usable_audio_url(value):
                        candidates.append(value)
            except (requests.RequestException, json.JSONDecodeError, ValueError):
                continue

        if page_html:
            candidates.extend(self._extract_audio_urls_from_html(page_html))

        canonical_url = f"https://suno.com/song/{clip_id}"
        try:
            r = self.session.get(
                canonical_url,
                timeout=15,
                allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            if r.ok:
                page_title = self._extract_title_from_html(r.text)
                if page_title:
                    title = safe_name(page_title)
                candidates.extend(self._extract_audio_urls_from_html(r.text))
        except requests.RequestException:
            pass

        # Public playback CDN fallback. It is only used if earlier candidates fail validation.
        candidates.append(f"https://cdn1.suno.ai/{clip_id}.mp3")
        candidates = self._dedupe([u for u in candidates if self._is_usable_audio_url(u)])
        if not candidates:
            raise RuntimeError("Không tìm thấy nguồn audio công khai cho bài Suno này.")
        return candidates, title

    def _resolve_short_link(self, url: str):
        seen = [url]
        final_url = url
        for method in ("head", "get"):
            try:
                if method == "head":
                    r = self.session.head(url, timeout=15, allow_redirects=True)
                else:
                    r = self.session.get(
                        url,
                        timeout=20,
                        allow_redirects=True,
                        stream=True,
                        headers={"Accept": "text/html,application/xhtml+xml"},
                    )
                final_url = r.url
                for response in list(r.history) + [r]:
                    seen.append(response.url or "")
                    seen.append(response.headers.get("Location") or "")
                    if getattr(response, "request", None) is not None:
                        seen.append(response.request.url or "")
                r.close()
                for text in seen:
                    clip_id = self._extract_uuid(text)
                    if clip_id:
                        return clip_id, final_url
            except requests.RequestException:
                continue
        return None, final_url

    def _resolve_audio_from_page(self, url: str):
        try:
            r = self.session.get(url, timeout=20, allow_redirects=True)
            r.raise_for_status()
            urls = self._extract_audio_urls_from_html(r.text)
            if not urls:
                raise RuntimeError("Trang không công khai URL audio mà tool có thể đọc.")
            return urls, guess_name_from_url(r.url)
        except requests.RequestException as exc:
            raise RuntimeError(f"Không đọc được trang: {exc}") from exc

    def _extract_audio_urls_from_html(self, html: str):
        if not html:
            return []
        decoded = (
            html.replace("\\u0026", "&")
            .replace("\\u002F", "/")
            .replace("\\/", "/")
            .replace("&amp;", "&")
        )
        patterns = [
            r'<meta[^>]+property=["\']og:audio(?::url)?["\'][^>]+content=["\'](https?://[^"\']+)',
            r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:audio(?::url)?["\']',
            r'"url"\s*:\s*"(https?://[^\"]+\.m4a(?:\?[^\"]*)?)"',
            r'"audio_url"\s*:\s*"(https?://[^\"]+)',
            r'"audioUrl"\s*:\s*"(https?://[^\"]+)',
            r'"stream_audio_url"\s*:\s*"(https?://[^\"]+)',
            r'"streamAudioUrl"\s*:\s*"(https?://[^\"]+)',
            r'(https?://[^\"\'<>\s]+\.(?:m4a|mp3|wav|aac|flac|ogg|opus)(?:\?[^\"\'<>\s]*)?)',
        ]
        found = []
        for pattern in patterns:
            for match in re.finditer(pattern, decoded, re.I):
                audio_url = match.group(1).replace("\\u0026", "&").replace("\\/", "/")
                if self._is_usable_audio_url(audio_url):
                    found.append(audio_url)
        found = self._dedupe(found)
        return sorted(found, key=lambda u: 0 if urlparse(u).path.lower().endswith(".m4a") else 1)

    @staticmethod
    def _extract_title_from_html(html: str):
        patterns = [
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            r'<title>(.*?)</title>',
        ]
        for pattern in patterns:
            m = re.search(pattern, html or "", re.I | re.S)
            if m:
                value = re.sub(r"\s+", " ", m.group(1)).strip()
                value = re.sub(r"\s*[|·-]\s*Suno.*$", "", value, flags=re.I)
                if value:
                    return value
        return None

    @staticmethod
    def _extract_clip_id_from_page(html: str):
        if not html:
            return None
        patterns = [
            rf'/song/({UUID_PATTERN})',
            rf'"clip_id"\s*:\s*"({UUID_PATTERN})"',
            rf'"id"\s*:\s*"({UUID_PATTERN})"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.I)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _extract_uuid(text: str):
        m = UUID_RE.search(text or "")
        return m.group(0) if m else None

    @staticmethod
    def _is_usable_audio_url(url):
        if not isinstance(url, str) or not url.strip():
            return False
        value = url.strip().replace("\\/", "/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        low = value.lower()
        if "/api/forbidden" in low or low.endswith("/forbidden") or "silence.mp3" in low:
            return False
        return True

    @staticmethod
    def _dedupe(values):
        out, seen = [], set()
        for value in values:
            key = value.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out


class ConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x690")
        self.minsize(780, 590)
        self.configure(bg="#121417")
        self.resolver = Resolver()
        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.output_format = tk.StringVar(value="mp3")
        self.is_running = False
        self._build_ui()

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#121417")
        style.configure("TLabel", background="#121417", foreground="#e8eaed", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), foreground="#ffffff")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), foreground="#9aa0a6")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=8)
        style.configure("TRadiobutton", background="#121417", foreground="#e8eaed", font=("Segoe UI", 10))

        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="SUNO AUDIO CONVERTER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text=f"v{APP_VERSION} • nguồn lỗi sẽ tự bỏ qua → thử nguồn tiếp theo → MP3 hoặc WAV",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 16))

        ttk.Label(root, text="Link đầu vào (mỗi dòng một link)").pack(anchor="w")
        self.links = tk.Text(
            root,
            height=10,
            bg="#1b1f24",
            fg="#f1f3f4",
            insertbackground="white",
            relief="flat",
            padx=12,
            pady=10,
            font=("Consolas", 10),
            wrap="word",
        )
        self.links.pack(fill="x", pady=(6, 14))

        options = ttk.Frame(root)
        options.pack(fill="x", pady=(0, 12))
        ttk.Label(options, text="Định dạng:").pack(side="left")
        ttk.Radiobutton(options, text="MP3 320 kbps", variable=self.output_format, value="mp3").pack(side="left", padx=(12, 6))
        ttk.Radiobutton(options, text="WAV 16-bit / 44.1 kHz", variable=self.output_format, value="wav").pack(side="left", padx=6)

        folder_row = ttk.Frame(root)
        folder_row.pack(fill="x", pady=(0, 14))
        ttk.Label(folder_row, text="Thư mục lưu:").pack(side="left")
        self.folder_entry = tk.Entry(
            folder_row,
            textvariable=self.output_dir,
            bg="#1b1f24",
            fg="#f1f3f4",
            insertbackground="white",
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=10, ipady=7)
        ttk.Button(folder_row, text="CHỌN THƯ MỤC", command=self.choose_folder).pack(side="right")

        self.start_btn = ttk.Button(root, text="BẮT ĐẦU CHUYỂN ĐỔI", command=self.start)
        self.start_btn.pack(fill="x", pady=(0, 12))
        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 12))
        ttk.Label(root, text="Nhật ký").pack(anchor="w")
        self.log_box = tk.Text(
            root,
            height=13,
            bg="#0d0f12",
            fg="#c7d0d9",
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_box.pack(fill="both", expand=True, pady=(6, 0))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def log(self, text: str):
        def _append():
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, _append)

    def set_progress(self, value: float):
        self.after(0, lambda: self.progress.configure(value=max(0, min(100, value))))

    def start(self):
        if self.is_running:
            return
        urls = [line.strip() for line in self.links.get("1.0", "end").splitlines() if line.strip()]
        if not urls:
            messagebox.showwarning(APP_NAME, "Hãy dán ít nhất một link.")
            return

        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được thư mục lưu:\n{exc}")
            return

        fmt = self.output_format.get()
        self.is_running = True
        self.start_btn.config(state="disabled")
        self.progress.configure(value=0)
        threading.Thread(target=self._worker, args=(urls, out_dir, fmt), daemon=True).start()

    def _worker(self, urls, out_dir: Path, fmt: str):
        ok_count = 0
        try:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            self.log(f"FFmpeg: {ffmpeg}")
            total = len(urls)

            for index, url in enumerate(urls, 1):
                self.set_progress(((index - 1) / total) * 100)
                self.log(f"[{index}/{total}] Đang xử lý: {url}")
                try:
                    candidates, title = self.resolver.resolve(url)
                    self.log(f"  ✓ Tìm thấy {len(candidates)} nguồn audio, sẽ xác minh từng nguồn bằng FFmpeg")
                    output = self._unique_output(out_dir, title, fmt)
                    converted = False
                    last_error = None

                    with tempfile.TemporaryDirectory(prefix="suno_converter_") as tmp:
                        source = Path(tmp) / "source_audio"

                        for source_index, audio_url in enumerate(candidates, 1):
                            kind = source_kind(audio_url)
                            try:
                                if source.exists():
                                    source.unlink()
                                if output.exists():
                                    output.unlink()

                                self.log(f"    • Nguồn {source_index}/{len(candidates)}: {kind}")
                                info = self._download(audio_url, source)
                                self.log(
                                    f"      ↳ Đã tải {info['size_mb']:.2f} MB, "
                                    f"Content-Type: {info['content_type'] or 'không rõ'}"
                                )

                                # Quan trọng: tải được chưa có nghĩa là audio hợp lệ.
                                # Chỉ coi nguồn thành công nếu FFmpeg đọc và tạo output được.
                                self._convert(ffmpeg, source, output, fmt)
                                if not output.exists() or output.stat().st_size < 4096:
                                    raise RuntimeError("FFmpeg không tạo được file đầu ra hợp lệ")

                                self.log(f"      ✓ FFmpeg xác nhận nguồn {kind} hợp lệ")
                                converted = True
                                break
                            except Exception as exc:
                                last_error = exc
                                if output.exists():
                                    try:
                                        output.unlink()
                                    except OSError:
                                        pass
                                short_error = str(exc).replace("\n", " ")
                                if len(short_error) > 350:
                                    short_error = short_error[-350:]
                                self.log(f"      ↳ Nguồn lỗi, tự thử nguồn tiếp theo: {short_error}")

                    if not converted:
                        raise RuntimeError(f"Tất cả {len(candidates)} nguồn đều không hợp lệ. Lỗi cuối: {last_error}")

                    self.log(f"  ✓ Đã lưu: {output}")
                    ok_count += 1
                except Exception as exc:
                    self.log(f"  ✗ Lỗi: {exc}")

                self.set_progress((index / total) * 100)

            self.log(f"Hoàn tất: {ok_count}/{total} file thành công.")
            self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Hoàn tất: {ok_count}/{total} file thành công."))
        finally:
            self.is_running = False
            self.after(0, lambda: self.start_btn.config(state="normal"))

    def _download(self, url: str, dest: Path):
        headers = {
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
        }
        content_type = ""
        final_url = url
        with self.resolver.session.get(
            url,
            headers=headers,
            stream=True,
            timeout=(20, 120),
            allow_redirects=True,
        ) as r:
            final_url = r.url or url
            if "/api/forbidden" in final_url.lower():
                raise RuntimeError("Suno chuyển nguồn sang /api/forbidden")
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").lower()
            if any(x in content_type for x in ("text/html", "application/json", "text/plain")):
                raise RuntimeError(f"Nguồn trả về {content_type}, không phải file audio")

            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        if not dest.exists() or dest.stat().st_size < 4096:
            raise RuntimeError("File tải về rỗng hoặc quá nhỏ")

        # Bắt các response giả M4A/MP4 trước khi đưa vào FFmpeg.
        kind = source_kind(final_url)
        if kind == "M4A":
            with open(dest, "rb") as f:
                header = f.read(32)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise RuntimeError("URL ghi M4A nhưng dữ liệu tải về không có cấu trúc M4A/MP4 hợp lệ")

        return {
            "content_type": content_type,
            "size_mb": dest.stat().st_size / (1024 * 1024),
            "final_url": final_url,
        }

    @staticmethod
    def _convert(ffmpeg: str, source: Path, output: Path, fmt: str):
        common = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn"]
        if fmt == "mp3":
            cmd = common + ["-codec:a", "libmp3lame", "-b:a", "320k", str(output)]
        else:
            cmd = common + ["-codec:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(output)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "FFmpeg lỗi không xác định").strip()
            raise RuntimeError(err[-1000:])

    @staticmethod
    def _unique_output(folder: Path, title: str, fmt: str):
        base = safe_name(title)
        candidate = folder / f"{base}.{fmt}"
        n = 2
        while candidate.exists():
            candidate = folder / f"{base} ({n}).{fmt}"
            n += 1
        return candidate


if __name__ == "__main__":
    ConverterApp().mainloop()
