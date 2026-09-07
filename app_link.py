import html as html_lib
import json
import os
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

APP_NAME = "Suno Link Audio Encoder"
APP_VERSION = "2.1.0"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
SUPPORTED = {".m4a", ".mp4", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".webm"}


def safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:140] or "audio"


def source_kind(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext, label in ((".m4a", "M4A"), (".mp4", "M4A/MP4"), (".mp3", "MP3"), (".aac", "AAC")):
        if path.endswith(ext):
            return label
    return "audio"


class PublicLinkResolver:
    """Đọc duy nhất nội dung công khai của trang/link. Không đăng nhập, không đoán CDN, không gọi private API."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    def resolve(self, raw_url: str):
        url = raw_url.strip().strip("'\"")
        if not url:
            raise RuntimeError("Link trống")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url

        p = urlparse(url)
        if p.scheme not in {"http", "https"}:
            raise RuntimeError("Link không hợp lệ")

        # Nếu người dùng dán trực tiếp URL audio công khai.
        if Path(p.path).suffix.lower() in SUPPORTED:
            return [url], safe_name(Path(p.path).stem or "audio")

        try:
            r = self.session.get(
                url,
                timeout=(15, 30),
                allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            r.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Không đọc được link: {exc}") from exc

        ctype = (r.headers.get("Content-Type") or "").lower()
        if "audio/" in ctype or "video/mp4" in ctype:
            return [r.url], safe_name(Path(urlparse(r.url).path).stem or "audio")

        page = r.text
        title = self._extract_title(page) or safe_name(Path(urlparse(r.url).path).stem or "audio")
        candidates = self._extract_audio_urls(page)

        if not candidates:
            raise RuntimeError(
                "Trang Suno này không công khai một file M4A/MP3 độc lập trong HTML. "
                "Tool chỉ có thể mã hóa khi link cung cấp trực tiếp file audio công khai; "
                "nó không ghép/rip luồng playback phân mảnh."
            )

        # Ưu tiên M4A/MP4, rồi mới MP3/AAC.
        candidates = sorted(candidates, key=self._priority)
        return candidates, title

    @staticmethod
    def _priority(url: str):
        path = urlparse(url).path.lower()
        if path.endswith(".m4a"):
            return 0
        if path.endswith(".mp4"):
            return 1
        if path.endswith(".aac"):
            return 2
        if path.endswith(".mp3"):
            return 3
        return 9

    @staticmethod
    def _extract_title(page: str):
        decoded = html_lib.unescape(page or "")
        for pat in (
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            r'<title>(.*?)</title>',
        ):
            m = re.search(pat, decoded, re.I | re.S)
            if m:
                value = re.sub(r"\s+", " ", m.group(1)).strip()
                value = re.sub(r"\s*[|·-]\s*Suno.*$", "", value, flags=re.I)
                if value:
                    return safe_name(value)
        return None

    def _extract_audio_urls(self, page: str):
        if not page:
            return []
        decoded = html_lib.unescape(page)
        decoded = (
            decoded.replace("\\u0026", "&")
            .replace("\\u002F", "/")
            .replace("\\/", "/")
        )

        patterns = [
            r'<meta[^>]+property=["\']og:audio(?::url)?["\'][^>]+content=["\'](https?://[^"\']+)',
            r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:audio(?::url)?["\']',
            r'<audio[^>]+src=["\'](https?://[^"\']+)',
            r'<source[^>]+src=["\'](https?://[^"\']+)',
            r'"(?:audio_url|audioUrl|stream_audio_url|streamAudioUrl|download_url|downloadUrl|url)"\s*:\s*"(https?://[^\"]+\.(?:m4a|mp4|mp3|aac|wav|flac|ogg|opus)(?:\?[^\"]*)?)"',
            r'(https?://[^\"\'<>\s]+\.(?:m4a|mp4|mp3|aac|wav|flac|ogg|opus)(?:\?[^\"\'<>\s]*)?)',
        ]

        found = []
        for pat in patterns:
            for m in re.finditer(pat, decoded, re.I):
                value = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
                if self._usable(value):
                    found.append(value)

        out, seen = [], set()
        for value in found:
            if value not in seen:
                seen.add(value)
                out.append(value)
        return out

    @staticmethod
    def _usable(url: str):
        if not isinstance(url, str) or not url.strip():
            return False
        p = urlparse(url.strip())
        if p.scheme not in {"http", "https"} or not p.netloc:
            return False
        low = url.lower()
        if any(x in low for x in ("/api/forbidden", "silence.mp3", ".m3u8", ".mpd")):
            return False
        return True


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("940x760")
        self.minsize(820, 640)
        self.configure(bg="#121417")

        self.resolver = PublicLinkResolver()
        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.output_format = tk.StringVar(value="mp3")
        self.running = False
        self.last_output = None
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
        ttk.Label(root, text="SUNO LINK → MP3 / WAV", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text=f"v{APP_VERSION} • Dán link Suno → lấy file audio công khai (ưu tiên M4A) → mã hóa MP3/WAV",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 14))

        ttk.Label(root, text="Link đầu vào (mỗi dòng một link)").pack(anchor="w")
        self.links = tk.Text(
            root, height=8, bg="#1b1f24", fg="#f1f3f4", insertbackground="white",
            relief="flat", padx=12, pady=10, font=("Consolas", 10), wrap="word"
        )
        self.links.pack(fill="x", pady=(6, 12))

        opts = ttk.Frame(root)
        opts.pack(fill="x", pady=(0, 12))
        ttk.Label(opts, text="Đầu ra:").pack(side="left")
        ttk.Radiobutton(opts, text="MP3 320 kbps", variable=self.output_format, value="mp3").pack(side="left", padx=(12, 8))
        ttk.Radiobutton(opts, text="WAV 16-bit / 44.1 kHz", variable=self.output_format, value="wav").pack(side="left", padx=8)

        folder = ttk.Frame(root)
        folder.pack(fill="x", pady=(0, 12))
        ttk.Label(folder, text="Thư mục lưu:").pack(side="left")
        tk.Entry(folder, textvariable=self.output_dir, bg="#1b1f24", fg="#f1f3f4",
                 insertbackground="white", relief="flat", font=("Segoe UI", 10)).pack(
                     side="left", fill="x", expand=True, padx=10, ipady=7
                 )
        ttk.Button(folder, text="CHỌN THƯ MỤC", command=self.choose_folder).pack(side="right")

        self.start_btn = ttk.Button(root, text="LẤY M4A VÀ MÃ HÓA", command=self.start)
        self.start_btn.pack(fill="x", pady=(0, 10))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(root)
        row.pack(fill="x", pady=(0, 10))
        self.play_btn = ttk.Button(row, text="PHÁT FILE VỪA TẠO", command=self.play_last, state="disabled")
        self.play_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(row, text="MỞ THƯ MỤC LƯU", command=self.open_folder).pack(side="left", fill="x", expand=True, padx=(5, 0))

        ttk.Label(root, text="Nhật ký").pack(anchor="w")
        self.log_box = tk.Text(root, height=15, bg="#0d0f12", fg="#c7d0d9", relief="flat",
                               padx=10, pady=8, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, pady=(6, 0))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def start(self):
        if self.running:
            return
        urls = [x.strip() for x in self.links.get("1.0", "end").splitlines() if x.strip()]
        if not urls:
            messagebox.showwarning(APP_NAME, "Hãy dán ít nhất một link Suno.")
            return
        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được thư mục lưu:\n{exc}")
            return

        self.running = True
        self.start_btn.config(state="disabled")
        self.progress.configure(value=0)
        threading.Thread(
            target=self._worker,
            args=(urls, out_dir, self.output_format.get()),
            daemon=True,
        ).start()

    def _worker(self, urls, out_dir: Path, fmt: str):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.log(f"FFmpeg: {ffmpeg}")
        ok = 0
        total = len(urls)

        try:
            for i, url in enumerate(urls, 1):
                self.set_progress(((i - 1) / total) * 100)
                self.log(f"[{i}/{total}] {url}")
                try:
                    candidates, title = self.resolver.resolve(url)
                    self.log(f"  ✓ Tìm thấy {len(candidates)} URL audio công khai; ưu tiên M4A/MP4")
                    output = self._unique_output(out_dir, title, fmt)
                    success = False
                    last_error = None

                    with tempfile.TemporaryDirectory(prefix="suno_link_encoder_") as tmp:
                        temp_source = Path(tmp) / "source_audio"
                        for n, audio_url in enumerate(candidates, 1):
                            try:
                                self.log(f"  • Nguồn {n}/{len(candidates)}: {source_kind(audio_url)} • {urlparse(audio_url).netloc}")
                                if temp_source.exists():
                                    temp_source.unlink()
                                if output.exists():
                                    output.unlink()

                                size_mb, ctype = self._download(audio_url, temp_source)
                                self.log(f"    ↳ Tải {size_mb:.2f} MB • {ctype or 'content-type không rõ'}")
                                self._validate_source(ffmpeg, temp_source)
                                self.log("    ✓ FFmpeg xác nhận nguồn audio hợp lệ")
                                self._convert(ffmpeg, temp_source, output, fmt)
                                self._validate_output(ffmpeg, output)
                                success = True
                                break
                            except Exception as exc:
                                last_error = exc
                                self.log(f"    ✗ Bỏ nguồn này: {str(exc)[-450:]}")

                    if not success:
                        raise RuntimeError(f"Không có file audio công khai hợp lệ để mã hóa. Lỗi cuối: {last_error}")

                    self.last_output = output
                    self.after(0, lambda: self.play_btn.config(state="normal"))
                    self.log(f"  ✓ ĐÃ LƯU: {output}")
                    ok += 1
                except Exception as exc:
                    self.log(f"  ✗ Lỗi: {exc}")
                self.set_progress((i / total) * 100)

            self.log(f"Hoàn tất: {ok}/{total} file thành công.")
            self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Hoàn tất: {ok}/{total} file thành công."))
        finally:
            self.running = False
            self.after(0, lambda: self.start_btn.config(state="normal"))

    def _download(self, url: str, dest: Path):
        headers = {
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
        }
        try:
            with self.resolver.session.get(
                url, headers=headers, stream=True, timeout=(20, 120), allow_redirects=True
            ) as r:
                if r.status_code in (401, 403):
                    raise RuntimeError(f"HTTP {r.status_code}: nguồn không cho tải trực tiếp")
                r.raise_for_status()
                ctype = (r.headers.get("Content-Type") or "").lower()
                if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                    raise RuntimeError(f"Server trả {ctype}, không phải file audio")
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except requests.RequestException as exc:
            raise RuntimeError(f"Tải nguồn lỗi: {exc}") from exc

        if not dest.exists() or dest.stat().st_size < 16384:
            raise RuntimeError("Nguồn quá nhỏ/placeholder")
        return dest.stat().st_size / (1024 * 1024), ctype

    @staticmethod
    def _validate_source(ffmpeg: str, source: Path):
        check = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(source), "-map", "0:a:0", "-t", "3", "-f", "null", "-"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check.returncode != 0:
            raise RuntimeError((check.stderr or "FFmpeg không đọc được nguồn")[-700:])

    @staticmethod
    def _convert(ffmpeg: str, source: Path, output: Path, fmt: str):
        common = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-map", "0:a:0", "-vn"]
        if fmt == "mp3":
            cmd = common + [
                "-codec:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2",
                "-id3v2_version", "3", "-write_id3v1", "1", str(output)
            ]
        else:
            cmd = common + ["-codec:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(output)]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "FFmpeg mã hóa lỗi")[-900:])
        if not output.exists() or output.stat().st_size < 16384:
            raise RuntimeError("File đầu ra quá nhỏ/rỗng")

    @staticmethod
    def _validate_output(ffmpeg: str, output: Path):
        check = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(output), "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True, text=True,
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

    def open_folder(self):
        folder = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Không mở được thư mục:\n{exc}")

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
    App().mainloop()
