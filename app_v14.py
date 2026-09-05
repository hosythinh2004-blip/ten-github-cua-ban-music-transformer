import json
import os
import re
import subprocess
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urlparse

import imageio_ffmpeg
import requests

APP_NAME = "Suno Audio Converter"
APP_VERSION = "1.4.0"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".webm"}
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)


class SunoAccessBlocked(RuntimeError):
    pass


def safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:120] or "audio"


def source_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext, label in ((".m4a", "M4A"), (".mp3", "MP3"), (".wav", "WAV"), (".aac", "AAC")):
        if path.endswith(ext):
            return label
    return "audio"


class Resolver:
    """Chỉ đọc các URL audio mà trang/metadata công khai cung cấp; không vượt kiểm soát truy cập."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        })

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
            return [url], safe_name(Path(parsed.path).stem or "audio"), False

        if host in {"suno.com", "www.suno.com"}:
            candidates, title = self._resolve_suno(url)
            return candidates, title, True

        r = self.session.get(url, timeout=20, allow_redirects=True)
        r.raise_for_status()
        candidates = self._extract_audio_urls(r.text)
        if not candidates:
            raise RuntimeError("Trang không công khai URL audio mà ứng dụng có thể đọc.")
        return candidates, safe_name(Path(urlparse(r.url).path).stem or "audio"), False

    def _resolve_suno(self, url: str):
        final_url = url
        html = ""
        clip_id = self._extract_uuid(url)

        try:
            r = self.session.get(
                url,
                timeout=20,
                allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            final_url = r.url
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text" in ctype or not ctype:
                html = r.text
            clip_id = clip_id or self._extract_uuid(final_url) or self._extract_clip_id_from_page(html)
        except requests.RequestException:
            pass

        if not clip_id:
            raise RuntimeError("Không lấy được ID bài hát từ link Suno.")

        title = clip_id
        candidates = []

        # Metadata công khai: chỉ dùng URL thật được trả về, không tự tạo/đoán URL CDN.
        for api_url in (
            f"https://studio-api-prod.suno.com/api/clip/{clip_id}",
            f"https://studio-api.prod.suno.com/api/clip/{clip_id}",
        ):
            try:
                r = self.session.get(api_url, timeout=15, allow_redirects=False, headers={"Accept": "application/json"})
                if not r.ok:
                    continue
                data = r.json()
                if not isinstance(data, dict):
                    continue
                title = safe_name(data.get("title") or title)

                media_urls = data.get("media_urls") or data.get("mediaUrls") or []
                if isinstance(media_urls, list):
                    for item in media_urls:
                        if isinstance(item, dict):
                            value = item.get("url")
                            if self._usable_url(value):
                                candidates.append(value)

                for key in ("audio_url", "audioUrl", "stream_audio_url", "streamAudioUrl"):
                    value = data.get(key)
                    if self._usable_url(value):
                        candidates.append(value)
            except (requests.RequestException, json.JSONDecodeError, ValueError):
                continue

        if html:
            candidates.extend(self._extract_audio_urls(html))

        # Thử trang canonical để lấy title/URL public nếu có.
        try:
            r = self.session.get(
                f"https://suno.com/song/{clip_id}",
                timeout=15,
                allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            if r.ok:
                title2 = self._extract_title(r.text)
                if title2:
                    title = safe_name(title2)
                candidates.extend(self._extract_audio_urls(r.text))
        except requests.RequestException:
            pass

        candidates = self._dedupe([x for x in candidates if self._usable_url(x)])
        if not candidates:
            raise SunoAccessBlocked(
                "Suno không cung cấp URL audio công khai cho link này. "
                "Hãy tải bài bằng chức năng Download của Suno theo quyền tài khoản, "
                "sau đó dùng nút CHỌN FILE TRÊN MÁY để chuyển MP3/WAV."
            )
        return candidates, title

    def _extract_audio_urls(self, html: str):
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
            r'"audio_url"\s*:\s*"(https?://[^\"]+)',
            r'"audioUrl"\s*:\s*"(https?://[^\"]+)',
            r'"stream_audio_url"\s*:\s*"(https?://[^\"]+)',
            r'"streamAudioUrl"\s*:\s*"(https?://[^\"]+)',
            r'(https?://[^\"\'<>\s]+\.(?:m4a|mp3|wav|aac|flac|ogg|opus)(?:\?[^\"\'<>\s]*)?)',
        ]
        out = []
        for pattern in patterns:
            for match in re.finditer(pattern, decoded, re.I):
                value = match.group(1).replace("\\u0026", "&").replace("\\/", "/")
                if self._usable_url(value):
                    out.append(value)
        return self._dedupe(out)

    @staticmethod
    def _extract_title(html: str):
        for pattern in (
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            r'<title>(.*?)</title>',
        ):
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
        for pattern in (
            rf'/song/({UUID_PATTERN})',
            rf'"clip_id"\s*:\s*"({UUID_PATTERN})"',
            rf'"id"\s*:\s*"({UUID_PATTERN})"',
        ):
            m = re.search(pattern, html, re.I)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _extract_uuid(value: str):
        m = UUID_RE.search(value or "")
        return m.group(0) if m else None

    @staticmethod
    def _usable_url(value):
        if not isinstance(value, str) or not value.strip():
            return False
        value = value.strip().replace("\\/", "/")
        p = urlparse(value)
        if p.scheme not in {"http", "https"} or not p.netloc:
            return False
        low = value.lower()
        return not ("/api/forbidden" in low or low.endswith("/forbidden") or "silence.mp3" in low)

    @staticmethod
    def _dedupe(values):
        out, seen = [], set()
        for value in values:
            if value and value not in seen:
                seen.add(value)
                out.append(value)
        return out


class ConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("940x750")
        self.minsize(820, 640)
        self.configure(bg="#121417")
        self.resolver = Resolver()
        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.output_format = tk.StringVar(value="mp3")
        self.last_output = None
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
        style.configure("TRadiobutton", background="#121417", foreground="#e8eaed")

        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="SUNO AUDIO CONVERTER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text=f"v{APP_VERSION} • Link public nếu có audio hợp lệ • hoặc chọn file trên máy để chuyển đổi",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 14))

        ttk.Label(root, text="Link đầu vào (mỗi dòng một link)").pack(anchor="w")
        self.links = tk.Text(root, height=8, bg="#1b1f24", fg="#f1f3f4", insertbackground="white",
                             relief="flat", padx=12, pady=10, font=("Consolas", 10), wrap="word")
        self.links.pack(fill="x", pady=(6, 10))

        link_buttons = ttk.Frame(root)
        link_buttons.pack(fill="x", pady=(0, 12))
        self.link_btn = ttk.Button(link_buttons, text="CHUYỂN TỪ LINK CÔNG KHAI", command=self.start_links)
        self.link_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(link_buttons, text="MỞ LINK SUNO", command=self.open_suno_link).pack(side="left", padx=(6, 0))

        local_row = ttk.Frame(root)
        local_row.pack(fill="x", pady=(0, 12))
        self.local_btn = ttk.Button(local_row, text="CHỌN FILE M4A / MP3 / WAV TRÊN MÁY", command=self.choose_local_files)
        self.local_btn.pack(fill="x")

        opts = ttk.Frame(root)
        opts.pack(fill="x", pady=(0, 12))
        ttk.Label(opts, text="Đầu ra:").pack(side="left")
        ttk.Radiobutton(opts, text="MP3 320 kbps", variable=self.output_format, value="mp3").pack(side="left", padx=(12, 8))
        ttk.Radiobutton(opts, text="WAV 16-bit / 44.1 kHz", variable=self.output_format, value="wav").pack(side="left", padx=8)

        folder = ttk.Frame(root)
        folder.pack(fill="x", pady=(0, 12))
        ttk.Label(folder, text="Thư mục lưu:").pack(side="left")
        tk.Entry(folder, textvariable=self.output_dir, bg="#1b1f24", fg="#f1f3f4", insertbackground="white",
                 relief="flat", font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, padx=10, ipady=7)
        ttk.Button(folder, text="CHỌN THƯ MỤC", command=self.choose_folder).pack(side="right")

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))

        self.play_btn = ttk.Button(root, text="PHÁT FILE VỪA TẠO", command=self.play_last, state="disabled")
        self.play_btn.pack(fill="x", pady=(0, 10))

        ttk.Label(root, text="Nhật ký").pack(anchor="w")
        self.log_box = tk.Text(root, height=14, bg="#0d0f12", fg="#c7d0d9", relief="flat", padx=10, pady=8,
                               state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, pady=(6, 0))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def open_suno_link(self):
        first = next((x.strip() for x in self.links.get("1.0", "end").splitlines() if x.strip()), "")
        if not first:
            messagebox.showwarning(APP_NAME, "Hãy dán link Suno trước.")
            return
        webbrowser.open(first)

    def choose_local_files(self):
        files = filedialog.askopenfilenames(
            title="Chọn file âm thanh",
            filetypes=[
                ("Audio", "*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.opus *.mp4 *.webm"),
                ("All files", "*.*"),
            ],
        )
        if files:
            self._start_worker("local", list(files))

    def start_links(self):
        urls = [x.strip() for x in self.links.get("1.0", "end").splitlines() if x.strip()]
        if not urls:
            messagebox.showwarning(APP_NAME, "Hãy dán ít nhất một link.")
            return
        self._start_worker("links", urls)

    def _start_worker(self, mode, items):
        if self.is_running:
            return
        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được thư mục lưu:\n{exc}")
            return
        self.is_running = True
        self.link_btn.config(state="disabled")
        self.local_btn.config(state="disabled")
        self.progress.configure(value=0)
        fmt = self.output_format.get()
        threading.Thread(target=self._worker, args=(mode, items, out_dir, fmt), daemon=True).start()

    def _worker(self, mode, items, out_dir: Path, fmt: str):
        ok = 0
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
                        last_error = None
                        blocked = False
                        with tempfile.TemporaryDirectory(prefix="suno_converter_") as tmp:
                            source = Path(tmp) / "source_audio"
                            for n, audio_url in enumerate(candidates, 1):
                                try:
                                    if source.exists():
                                        source.unlink()
                                    if output.exists():
                                        output.unlink()
                                    self.log(f"  • Nguồn {n}/{len(candidates)}: {source_kind(audio_url)}")
                                    info = self._download(audio_url, source)
                                    self.log(f"    ↳ {info['size_mb']:.2f} MB • {info['content_type'] or 'không rõ'}")
                                    self._convert_and_validate(ffmpeg, source, output, fmt)
                                    converted = True
                                    break
                                except SunoAccessBlocked as exc:
                                    blocked = True
                                    last_error = exc
                                    self.log(f"    ↳ Suno chặn truy cập trực tiếp: {exc}")
                                except Exception as exc:
                                    last_error = exc
                                    self.log(f"    ↳ Nguồn không hợp lệ: {str(exc)[-300:]}")
                        if not converted:
                            if is_suno and blocked:
                                raise SunoAccessBlocked(
                                    "Suno đang chặn tải file trực tiếp cho link này. "
                                    "Hãy dùng chức năng Download của Suno, rồi chọn file đã tải trong ứng dụng để chuyển đổi."
                                )
                            raise RuntimeError(f"Không có nguồn audio hợp lệ. Lỗi cuối: {last_error}")

                    self.last_output = output
                    self.after(0, lambda: self.play_btn.config(state="normal"))
                    self.log(f"  ✓ Đã tạo file phát được: {output}")
                    ok += 1
                except SunoAccessBlocked as exc:
                    self.log(f"  ✗ SUNO ACCESS: {exc}")
                except Exception as exc:
                    self.log(f"  ✗ Lỗi: {exc}")
                self.set_progress((i / total) * 100)

            self.log(f"Hoàn tất: {ok}/{total} file thành công.")
            self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Hoàn tất: {ok}/{total} file thành công."))
        finally:
            self.is_running = False
            self.after(0, lambda: self.link_btn.config(state="normal"))
            self.after(0, lambda: self.local_btn.config(state="normal"))

    def _download(self, url: str, dest: Path):
        headers = {
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
        }
        with self.resolver.session.get(url, headers=headers, stream=True, timeout=(20, 120), allow_redirects=True) as r:
            if r.status_code in (401, 403):
                raise SunoAccessBlocked(f"HTTP {r.status_code} từ {urlparse(r.url or url).netloc}")
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                raise RuntimeError(f"Nguồn trả về {ctype}, không phải audio")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        if not dest.exists() or dest.stat().st_size < 16384:
            raise RuntimeError("Nguồn trả về file quá nhỏ/placeholder")
        return {"size_mb": dest.stat().st_size / (1024 * 1024), "content_type": ctype}

    @staticmethod
    def _convert_and_validate(ffmpeg: str, source: Path, output: Path, fmt: str):
        common = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn"]
        if fmt == "mp3":
            cmd = common + [
                "-codec:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2",
                "-id3v2_version", "3", "-write_id3v1", "1", str(output),
            ]
        else:
            cmd = common + ["-codec:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(output)]

        result = subprocess.run(cmd, capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "FFmpeg convert lỗi")[-900:])
        if not output.exists() or output.stat().st_size < 16384:
            raise RuntimeError("FFmpeg tạo file đầu ra quá nhỏ hoặc rỗng")

        # Giải mã lại toàn bộ file đầu ra trước khi báo thành công.
        check = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(output), "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check.returncode != 0:
            try:
                output.unlink()
            except OSError:
                pass
            raise RuntimeError((check.stderr or "File đầu ra không giải mã được")[-900:])

    @staticmethod
    def _unique_output(folder: Path, title: str, fmt: str):
        base = safe_name(title)
        candidate = folder / f"{base}.{fmt}"
        n = 2
        while candidate.exists():
            candidate = folder / f"{base} ({n}).{fmt}"
            n += 1
        return candidate

    def play_last(self):
        if not self.last_output or not Path(self.last_output).exists():
            messagebox.showwarning(APP_NAME, "Chưa có file đầu ra để phát.")
            return
        try:
            os.startfile(str(self.last_output))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Không mở được file:\n{exc}")

    def log(self, text):
        def append():
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, append)

    def set_progress(self, value):
        self.after(0, lambda: self.progress.configure(value=max(0, min(100, value))))


if __name__ == "__main__":
    ConverterApp().mainloop()
