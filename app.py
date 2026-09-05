import json
import os
import re
import shutil
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
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".webm"}


def safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:120] or "audio"


def guess_name_from_url(url: str) -> str:
    path = Path(urlparse(url).path)
    stem = path.stem or "audio"
    return safe_name(stem)


class Resolver:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Origin": "https://suno.com",
            "Accept": "*/*",
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
            return url, guess_name_from_url(url)

        if host in {"suno.com", "www.suno.com"}:
            return self._resolve_suno(url)

        # Generic public page fallback: only accepts clearly exposed audio URLs in HTML.
        return self._resolve_audio_from_page(url)

    def _resolve_suno(self, url: str):
        final_url = url
        if "/s/" in urlparse(url).path:
            try:
                r = self.session.get(url, allow_redirects=True, timeout=20, stream=True)
                final_url = r.url
                r.close()
            except requests.RequestException:
                final_url = url

        clip_id = self._extract_uuid(final_url) or self._extract_uuid(url)
        if not clip_id:
            # Some short links render the UUID in page HTML rather than the redirect URL.
            try:
                html = self.session.get(url, timeout=20).text
                clip_id = self._extract_uuid(html)
                if not clip_id:
                    return self._extract_from_html(html, final_url)
            except requests.RequestException as exc:
                raise RuntimeError(f"Không đọc được link Suno: {exc}") from exc

        # Public clip metadata endpoint. If unavailable, fall back to public CDN convention.
        meta_urls = [
            f"https://studio-api-prod.suno.com/api/clip/{clip_id}",
            f"https://studio-api.suno.ai/api/clip/{clip_id}",
        ]
        for api_url in meta_urls:
            try:
                r = self.session.get(api_url, timeout=20)
                if r.ok:
                    data = r.json()
                    audio_url = (
                        data.get("audio_url")
                        or data.get("audioUrl")
                        or data.get("stream_audio_url")
                        or data.get("streamAudioUrl")
                    )
                    if audio_url:
                        title = safe_name(data.get("title") or clip_id)
                        return audio_url, title
            except (requests.RequestException, json.JSONDecodeError, ValueError):
                pass

        cdn_url = f"https://cdn1.suno.ai/{clip_id}.mp3"
        try:
            r = self.session.get(cdn_url, timeout=20, stream=True)
            if r.ok:
                r.close()
                return cdn_url, clip_id
            r.close()
        except requests.RequestException:
            pass

        # Last fallback: inspect the public page for an exposed audio URL.
        try:
            html = self.session.get(final_url, timeout=20).text
            return self._extract_from_html(html, final_url, clip_id)
        except requests.RequestException as exc:
            raise RuntimeError("Không tìm thấy audio công khai từ link Suno này.") from exc

    def _resolve_audio_from_page(self, url: str):
        try:
            r = self.session.get(url, timeout=20)
            r.raise_for_status()
            return self._extract_from_html(r.text, r.url)
        except requests.RequestException as exc:
            raise RuntimeError(f"Không đọc được trang: {exc}") from exc

    def _extract_from_html(self, html: str, base_url: str, fallback_name: str = "audio"):
        decoded = html.replace("\\u0026", "&").replace("\\/", "/")
        patterns = [
            r'"audio_url"\s*:\s*"(https?://[^\"]+)',
            r'"audioUrl"\s*:\s*"(https?://[^\"]+)',
            r'"stream_audio_url"\s*:\s*"(https?://[^\"]+)',
            r'"streamAudioUrl"\s*:\s*"(https?://[^\"]+)',
            r'(https?://[^\"\'<>\s]+\.(?:m4a|mp3|wav|aac|flac|ogg|opus)(?:\?[^\"\'<>\s]*)?)',
        ]
        for pattern in patterns:
            m = re.search(pattern, decoded, re.I)
            if m:
                audio_url = m.group(1).replace("&amp;", "&")
                return audio_url, safe_name(fallback_name or guess_name_from_url(base_url))
        raise RuntimeError("Trang không công khai URL audio mà tool có thể đọc.")

    @staticmethod
    def _extract_uuid(text: str):
        m = UUID_RE.search(text or "")
        return m.group(0) if m else None


class ConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("880x660")
        self.minsize(760, 560)
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
            text="Dán link Suno/public audio → tải nguồn audio được phép truy cập → chuyển MP3 hoặc WAV",
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
            height=12,
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

        self.is_running = True
        self.start_btn.config(state="disabled")
        self.progress.configure(value=0)
        threading.Thread(target=self._worker, args=(urls, out_dir), daemon=True).start()

    def _worker(self, urls, out_dir: Path):
        ok_count = 0
        try:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            self.log(f"FFmpeg: {ffmpeg}")
            total = len(urls)
            for index, url in enumerate(urls, 1):
                base_progress = ((index - 1) / total) * 100
                self.set_progress(base_progress)
                self.log(f"[{index}/{total}] Đang xử lý: {url}")
                try:
                    audio_url, title = self.resolver.resolve(url)
                    self.log(f"  ✓ Tìm thấy nguồn audio")
                    with tempfile.TemporaryDirectory(prefix="suno_converter_") as tmp:
                        source = Path(tmp) / "source_audio"
                        self._download(audio_url, source)
                        output = self._unique_output(out_dir, title, self.output_format.get())
                        self._convert(ffmpeg, source, output, self.output_format.get())
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
        headers = {"User-Agent": UA, "Referer": "https://suno.com/", "Accept": "*/*"}
        with requests.get(url, headers=headers, stream=True, timeout=(20, 120), allow_redirects=True) as r:
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type:
                raise RuntimeError("URL nguồn trả về HTML thay vì file audio.")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        if not dest.exists() or dest.stat().st_size < 1024:
            raise RuntimeError("File audio tải về rỗng hoặc không hợp lệ.")

    @staticmethod
    def _convert(ffmpeg: str, source: Path, output: Path, fmt: str):
        if fmt == "mp3":
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-vn", "-codec:a", "libmp3lame", "-b:a", "320k",
                str(output),
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-vn", "-codec:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
                str(output),
            ]
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "FFmpeg lỗi không xác định").strip()
            raise RuntimeError(err[-1200:])

    @staticmethod
    def _unique_output(folder: Path, title: str, fmt: str):
        base = safe_name(title)
        candidate = folder / f"{base}.{fmt}"
        i = 2
        while candidate.exists():
            candidate = folder / f"{base} ({i}).{fmt}"
            i += 1
        return candidate


if __name__ == "__main__":
    app = ConverterApp()
    app.mainloop()
