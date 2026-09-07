import hashlib
import html as html_lib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urlparse

import imageio_ffmpeg
import requests

APP_NAME = "Suno Audio Converter"
APP_VERSION = "3.1.0"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)


class Cancelled(RuntimeError):
    pass


class SourceBlocked(RuntimeError):
    pass


def safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:140] or "audio"


def extract_uuid(text: str):
    m = UUID_RE.search(text or "")
    return m.group(0) if m else None


def meta_content(html: str, key: str, attr: str = "property"):
    patterns = [
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, html or "", re.I | re.S)
        if m:
            return html_lib.unescape(m.group(1)).strip()
    return None


def parse_time(value: str):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if ":" not in raw:
            return max(0.0, float(raw))
        parts = [float(x) for x in raw.split(":")]
        if len(parts) == 2:
            return max(0.0, parts[0] * 60 + parts[1])
        if len(parts) == 3:
            return max(0.0, parts[0] * 3600 + parts[1] * 60 + parts[2])
    except ValueError:
        pass
    raise ValueError(f"Thời gian không hợp lệ: {value}")


def format_duration(seconds):
    try:
        seconds = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(max(0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


@dataclass
class TrackMeta:
    page_url: str
    uuid: str = ""
    title: str = "audio"
    artist: str = ""
    duration: float | None = None
    cover_url: str = ""
    audio_urls: list[str] = field(default_factory=list)


class PublicSunoResolver:
    """Chỉ đọc metadata và URL audio trực tiếp xuất hiện công khai trên trang Suno.

    Không gọi endpoint quyền/private và không giải mã DRM/luồng được bảo vệ.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://suno.com/",
        })

    def resolve(self, raw_url: str) -> TrackMeta:
        url = raw_url.strip().strip("'\"")
        if not url:
            raise ValueError("Link trống")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url

        p = urlparse(url)
        host = p.netloc.lower().split(":")[0]
        if host not in {"suno.com", "www.suno.com"}:
            raise ValueError("Chỉ nhận link bài hát suno.com")

        try:
            r = self.session.get(
                url,
                timeout=(15, 30),
                allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            r.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Không mở được trang Suno: {exc}") from exc

        final_url = r.url
        text = r.text
        clip_id = extract_uuid(url) or extract_uuid(final_url) or self._extract_uuid_from_html(text) or ""

        title = (
            meta_content(text, "og:title")
            or meta_content(text, "twitter:title", "name")
            or self._extract_title_tag(text)
            or clip_id
            or "audio"
        )
        title = re.sub(r"\s*[|·-]\s*Suno.*$", "", title, flags=re.I).strip()

        cover = meta_content(text, "og:image") or meta_content(text, "twitter:image", "name") or ""
        artist = (
            meta_content(text, "music:musician")
            or meta_content(text, "author", "name")
            or self._extract_json_string(text, ("display_name", "displayName", "artist", "username"))
            or ""
        )
        duration = self._extract_duration(text)
        audio_urls = self._extract_public_audio_urls(text)

        canonical = f"https://suno.com/song/{clip_id}" if clip_id else ""
        if canonical and canonical != final_url:
            try:
                c = self.session.get(
                    canonical,
                    timeout=(10, 20),
                    allow_redirects=True,
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                if c.ok:
                    audio_urls.extend(self._extract_public_audio_urls(c.text))
                    title2 = meta_content(c.text, "og:title")
                    if title2 and (not title or title == clip_id):
                        title = title2
                    cover = cover or meta_content(c.text, "og:image") or ""
                    duration = duration if duration is not None else self._extract_duration(c.text)
            except requests.RequestException:
                pass

        return TrackMeta(
            page_url=final_url,
            uuid=clip_id,
            title=safe_name(title),
            artist=safe_name(artist) if artist else "",
            duration=duration,
            cover_url=cover,
            audio_urls=self._dedupe(audio_urls),
        )

    @staticmethod
    def _extract_title_tag(text: str):
        m = re.search(r"<title>(.*?)</title>", text or "", re.I | re.S)
        return html_lib.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else None

    @staticmethod
    def _extract_uuid_from_html(text: str):
        for pattern in (
            rf'/song/({UUID_PATTERN})',
            rf'"clip_id"\s*:\s*"({UUID_PATTERN})"',
            rf'"id"\s*:\s*"({UUID_PATTERN})"',
        ):
            m = re.search(pattern, text or "", re.I)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _extract_json_string(text: str, keys):
        for key in keys:
            m = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text or "", re.I)
            if m:
                value = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
                if value:
                    return value
        return None

    @staticmethod
    def _extract_duration(text: str):
        for pattern in (
            r'"duration"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            r'"durationSeconds"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        ):
            m = re.search(pattern, text or "", re.I)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
        return None

    def _extract_public_audio_urls(self, text: str):
        if not text:
            return []
        decoded = (
            text.replace("\\u0026", "&")
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
            r'(https?://[^\"\'<>\s]+\.(?:m4a|mp3|wav|aac|flac|ogg|opus|mp4|webm)(?:\?[^\"\'<>\s]*)?)',
        ]
        out = []
        for pattern in patterns:
            for m in re.finditer(pattern, decoded, re.I):
                value = m.group(1).replace("\\u0026", "&").replace("\\/", "/")
                if self._usable_public_url(value):
                    out.append(value)

        def rank(u):
            path = urlparse(u).path.lower()
            if path.endswith(".m4a"):
                return 0
            if path.endswith(".mp4"):
                return 1
            if path.endswith(".mp3"):
                return 2
            return 3

        return sorted(self._dedupe(out), key=rank)

    @staticmethod
    def _usable_public_url(value):
        if not isinstance(value, str) or not value.strip():
            return False
        p = urlparse(value.strip())
        if p.scheme not in {"http", "https"} or not p.netloc:
            return False
        low = value.lower()
        blocked_markers = ("/api/forbidden", "silence.mp3", "/mango/rights", "license", "drm")
        return not any(marker in low for marker in blocked_markers)

    @staticmethod
    def _dedupe(values):
        out, seen = [], set()
        for v in values:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out


class SunoAudioConverter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x790")
        self.minsize(980, 690)
        self.configure(bg="#121417")

        self.resolver = PublicSunoResolver()
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.output_format = tk.StringVar(value="mp3")
        self.normalize = tk.BooleanVar(value=False)
        self.mono = tk.BooleanVar(value=False)
        self.save_cover = tk.BooleanVar(value=False)
        self.trim_start = tk.StringVar(value="")
        self.trim_end = tk.StringVar(value="")
        self.running = False
        self.cancel_requested = False
        self.queue = []
        self.success_outputs = []
        self.cache_dir = Path.home() / ".suno_audio_converter" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
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
        style.configure("Hint.TLabel", font=("Segoe UI", 9), foreground="#aab2bd")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=8)
        style.configure("TRadiobutton", background="#121417", foreground="#e8eaed")
        style.configure("TCheckbutton", background="#121417", foreground="#e8eaed")
        style.configure(
            "Dark.Treeview",
            rowheight=28,
            font=("Segoe UI", 9),
            background="#171a1f",
            fieldbackground="#171a1f",
            foreground="#e8eaed",
            bordercolor="#343a40",
            lightcolor="#343a40",
            darkcolor="#343a40",
        )
        style.map("Dark.Treeview", background=[("selected", "#343a40")], foreground=[("selected", "#ffffff")])
        style.configure(
            "Dark.Treeview.Heading",
            font=("Segoe UI Semibold", 9),
            background="#252a30",
            foreground="#ffffff",
            relief="flat",
        )

        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="SUNO AUDIO CONVERTER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text=(
                f"v{APP_VERSION} • Link Suno → kiểm tra nguồn audio công khai → MP3/WAV/M4A • "
                "thử hết nguồn, chẩn đoán fragment/placeholder/HTTP"
            ),
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 12))

        ttk.Label(root, text="Link bài hát Suno (mỗi dòng một link)").pack(anchor="w")
        self.links = tk.Text(
            root, height=5, bg="#1b1f24", fg="#f1f3f4", insertbackground="white",
            relief="flat", padx=10, pady=8, font=("Consolas", 10), wrap="word"
        )
        self.links.pack(fill="x", pady=(5, 8))

        link_actions = ttk.Frame(root)
        link_actions.pack(fill="x", pady=(0, 10))
        self.add_btn = ttk.Button(link_actions, text="THÊM VÀO HÀNG ĐỢI", command=self.add_links)
        self.add_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(link_actions, text="XÓA DÒNG CHỌN", command=self.remove_selected).pack(side="left", padx=5)
        ttk.Button(link_actions, text="XÓA HÀNG ĐỢI", command=self.clear_queue).pack(side="left", padx=5)
        ttk.Button(link_actions, text="XÓA CACHE", command=self.clear_cache).pack(side="left", padx=(5, 0))

        options = ttk.Frame(root)
        options.pack(fill="x", pady=(0, 10))
        ttk.Label(options, text="Định dạng:").pack(side="left")
        ttk.Radiobutton(options, text="MP3 320 kbps", variable=self.output_format, value="mp3").pack(side="left", padx=(10, 6))
        ttk.Radiobutton(options, text="WAV 16-bit / 44.1 kHz", variable=self.output_format, value="wav").pack(side="left", padx=6)
        ttk.Radiobutton(options, text="M4A AAC 256 kbps", variable=self.output_format, value="m4a").pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Chuẩn hóa âm lượng", variable=self.normalize).pack(side="left", padx=(18, 6))
        ttk.Checkbutton(options, text="Mono", variable=self.mono).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Lưu cover", variable=self.save_cover).pack(side="left", padx=6)

        trim = ttk.Frame(root)
        trim.pack(fill="x", pady=(0, 10))
        ttk.Label(trim, text="Cắt từ:").pack(side="left")
        tk.Entry(trim, textvariable=self.trim_start, width=12).pack(side="left", padx=(6, 14))
        ttk.Label(trim, text="đến:").pack(side="left")
        tk.Entry(trim, textvariable=self.trim_end, width=12).pack(side="left", padx=(6, 14))
        ttk.Label(trim, text="(giây hoặc MM:SS / HH:MM:SS)", style="Hint.TLabel").pack(side="left")

        folder = ttk.Frame(root)
        folder.pack(fill="x", pady=(0, 10))
        ttk.Label(folder, text="Thư mục lưu:").pack(side="left")
        tk.Entry(
            folder, textvariable=self.output_dir, bg="#1b1f24", fg="#f1f3f4",
            insertbackground="white", relief="flat", font=("Segoe UI", 10)
        ).pack(side="left", fill="x", expand=True, padx=10, ipady=6)
        ttk.Button(folder, text="CHỌN THƯ MỤC", command=self.choose_folder).pack(side="right")

        self.tree = ttk.Treeview(
            root,
            columns=("status", "title", "duration", "output"),
            show="headings",
            height=9,
            style="Dark.Treeview",
        )
        self.tree.heading("status", text="Trạng thái")
        self.tree.heading("title", text="Bài hát")
        self.tree.heading("duration", text="Thời lượng")
        self.tree.heading("output", text="File đầu ra / chẩn đoán")
        self.tree.column("status", width=150, anchor="center")
        self.tree.column("title", width=300)
        self.tree.column("duration", width=90, anchor="center")
        self.tree.column("output", width=520)
        self.tree.pack(fill="both", expand=True, pady=(0, 10))

        run_actions = ttk.Frame(root)
        run_actions.pack(fill="x", pady=(0, 8))
        self.start_btn = ttk.Button(run_actions, text="BẮT ĐẦU XỬ LÝ HÀNG ĐỢI", command=self.start_queue)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.cancel_btn = ttk.Button(run_actions, text="DỪNG", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=5)
        ttk.Button(run_actions, text="ĐÓNG GÓI TẤT CẢ .ZIP", command=self.make_zip).pack(side="left", padx=5)
        ttk.Button(run_actions, text="MỞ THƯ MỤC", command=self.open_output_folder).pack(side="left", padx=(5, 0))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))

        ttk.Label(root, text="Nhật ký").pack(anchor="w")
        self.log_box = tk.Text(
            root, height=9, bg="#0d0f12", fg="#c7d0d9", relief="flat",
            padx=10, pady=8, state="disabled", font=("Consolas", 9)
        )
        self.log_box.pack(fill="both", expand=False, pady=(5, 0))

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def add_links(self):
        urls = [x.strip() for x in self.links.get("1.0", "end").splitlines() if x.strip()]
        if not urls:
            messagebox.showwarning(APP_NAME, "Hãy dán ít nhất một link Suno.")
            return
        existing = {item["url"] for item in self.queue}
        added = 0
        for url in urls:
            if url in existing:
                continue
            item_id = self.tree.insert("", "end", values=("Chờ", url, "", ""))
            self.queue.append({"url": url, "tree_id": item_id, "output": None, "meta": None})
            existing.add(url)
            added += 1
        self.log(f"Đã thêm {added} link vào hàng đợi.")

    def remove_selected(self):
        if self.running:
            return
        selected = set(self.tree.selection())
        if not selected:
            return
        self.queue = [item for item in self.queue if item["tree_id"] not in selected]
        for node in selected:
            self.tree.delete(node)

    def clear_queue(self):
        if self.running:
            return
        self.queue.clear()
        for node in self.tree.get_children():
            self.tree.delete(node)

    def clear_cache(self):
        if self.running:
            return
        shutil.rmtree(self.cache_dir, ignore_errors=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log("Đã xóa cache.")

    def start_queue(self):
        if self.running:
            return
        if not self.queue:
            messagebox.showwarning(APP_NAME, "Hàng đợi đang trống.")
            return
        try:
            start = parse_time(self.trim_start.get())
            end = parse_time(self.trim_end.get())
            if start is not None and end is not None and end <= start:
                raise ValueError("Mốc 'đến' phải lớn hơn mốc 'từ'.")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được thư mục lưu:\n{exc}")
            return

        self.running = True
        self.cancel_requested = False
        self.start_btn.config(state="disabled")
        self.add_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress.configure(value=0)
        self.success_outputs.clear()
        opts = {
            "fmt": self.output_format.get(),
            "normalize": bool(self.normalize.get()),
            "mono": bool(self.mono.get()),
            "save_cover": bool(self.save_cover.get()),
            "start": start,
            "end": end,
        }
        threading.Thread(target=self._worker, args=(out_dir, opts), daemon=True).start()

    def cancel(self):
        self.cancel_requested = True
        self.log("Đã yêu cầu dừng...")

    def _worker(self, out_dir: Path, opts: dict):
        total = len(self.queue)
        ok = 0
        failed = 0
        try:
            for idx, item in enumerate(self.queue, 1):
                if self.cancel_requested:
                    raise Cancelled("Đã dừng theo yêu cầu.")
                self.set_progress(((idx - 1) / total) * 100)
                tree_id = item["tree_id"]
                self.set_row(tree_id, status="Đang phân tích", output="")
                self.log(f"[{idx}/{total}] {item['url']}")
                try:
                    meta = self.resolver.resolve(item["url"])
                    item["meta"] = meta
                    self.set_row(tree_id, title=meta.title, duration=format_duration(meta.duration))
                    self.log(
                        f"  ✓ Metadata: {meta.title}"
                        + (f" • {meta.artist}" if meta.artist else "")
                        + (f" • {format_duration(meta.duration)}" if meta.duration is not None else "")
                    )
                    self.log(f"  ↳ Tìm thấy {len(meta.audio_urls)} URL audio công khai ứng viên")

                    cache_key = self._cache_key(item["url"], opts)
                    cache_out = self.cache_dir / f"{cache_key}.{opts['fmt']}"
                    output = self._unique_output(out_dir, f"[Suno] {meta.title}", opts["fmt"])
                    reason = ""
                    if cache_out.exists() and cache_out.stat().st_size > 16384 and self._probe_audio(cache_out)[0]:
                        shutil.copy2(cache_out, output)
                        self.log("  ↳ Cache hit: dùng file đã mã hóa trước đó.")
                        converted = True
                    else:
                        converted, reason = self._process_sources(meta, output, opts, tree_id, idx, total)
                        if converted:
                            try:
                                shutil.copy2(output, cache_out)
                            except OSError:
                                pass

                    if not converted:
                        failed += 1
                        self.set_row(tree_id, status="Không xử lý được", output=reason)
                        self.log(f"  ✗ Kết luận: {reason}")
                        continue

                    if opts["save_cover"] and meta.cover_url:
                        self._save_cover(meta, out_dir)
                    item["output"] = output
                    self.success_outputs.append(output)
                    self.set_row(tree_id, status="Hoàn tất", output=str(output))
                    self.log(f"  ✓ Đã lưu: {output}")
                    ok += 1
                except Exception as exc:
                    failed += 1
                    self.set_row(tree_id, status="Lỗi", output=str(exc)[:160])
                    self.log(f"  ✗ Lỗi: {exc}")
                self.set_progress((idx / total) * 100)

            self.log(f"Hoàn tất: {ok}/{total} thành công • {failed} lỗi.")
            msg = f"Hoàn tất: {ok}/{total} bài thành công."
            if failed:
                msg += f"\n{failed} bài chưa xử lý được. Xem cột chẩn đoán và Nhật ký."
            self.after(0, lambda: messagebox.showinfo(APP_NAME, msg))
        except Cancelled as exc:
            self.log(str(exc))
        finally:
            self.running = False
            self.after(0, lambda: self.start_btn.config(state="normal"))
            self.after(0, lambda: self.add_btn.config(state="normal"))
            self.after(0, lambda: self.cancel_btn.config(state="disabled"))

    def _process_sources(self, meta: TrackMeta, output: Path, opts: dict, tree_id: str, track_index: int, total_tracks: int):
        if not meta.audio_urls:
            return False, "Trang không công khai URL audio trực tiếp."

        errors = []
        blocked = 0
        self.set_row(tree_id, status="Đang kiểm tra nguồn")
        with tempfile.TemporaryDirectory(prefix="suno_audio_converter_") as tmp:
            for n, audio_url in enumerate(meta.audio_urls, 1):
                if self.cancel_requested:
                    raise Cancelled("Đã dừng theo yêu cầu.")
                source = Path(tmp) / f"source_{n}.bin"
                host = urlparse(audio_url).netloc
                try:
                    self.log(f"  • Nguồn {n}/{len(meta.audio_urls)}: {host}")
                    info = self._download_public_audio(
                        audio_url,
                        source,
                        lambda p: self.set_progress((((track_index - 1) + (p / 100.0)) / total_tracks) * 100),
                    )
                    self.log(
                        f"    ↳ HTTP {info['status']} • {info['size_mb']:.2f} MB • "
                        f"{info['content_type'] or 'unknown'}"
                    )
                    if info.get("content_range"):
                        self.log(f"    ↳ Content-Range: {info['content_range']}")

                    media_note = self._inspect_media_structure(source, info["content_type"])
                    ok, probe_error = self._probe_audio(source)
                    if not ok:
                        detail = media_note or self._short_ffmpeg_error(probe_error) or "FFmpeg không đọc được nguồn"
                        raise RuntimeError(detail)

                    self.set_row(tree_id, status="Đang mã hóa")
                    self._transcode(source, output, opts)
                    out_ok, out_err = self._probe_audio(output)
                    if not out_ok:
                        try:
                            output.unlink()
                        except OSError:
                            pass
                        raise RuntimeError("File đầu ra không giải mã được: " + self._short_ffmpeg_error(out_err))
                    return True, ""
                except SourceBlocked as exc:
                    blocked += 1
                    text = f"{host}: {exc}"
                    errors.append(text)
                    self.log(f"    ↳ Bị từ chối, thử nguồn tiếp theo: {exc}")
                except Exception as exc:
                    text = f"{host}: {str(exc)[-260:]}"
                    errors.append(text)
                    self.log(f"    ↳ Không dùng được, thử nguồn tiếp theo: {str(exc)[-420:]}")
                finally:
                    try:
                        if output.exists() and not self._probe_audio(output)[0]:
                            output.unlink()
                    except OSError:
                        pass

        if blocked and blocked == len(meta.audio_urls):
            return False, "Tất cả URL audio công khai đều từ chối truy cập (401/403)."
        if errors:
            return False, errors[-1]
        return False, "Không có nguồn audio công khai hợp lệ."

    def _download_public_audio(self, url: str, dest: Path, progress_cb=None):
        headers = {
            "User-Agent": UA,
            "Referer": "https://suno.com/",
            "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
        }
        try:
            with self.resolver.session.get(
                url,
                headers=headers,
                stream=True,
                timeout=(15, 120),
                allow_redirects=True,
            ) as r:
                if r.status_code in (401, 403):
                    raise SourceBlocked(f"HTTP {r.status_code}")
                r.raise_for_status()
                ctype = (r.headers.get("Content-Type") or "").lower()
                if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                    raise RuntimeError(f"Server trả về {ctype}, không phải audio")
                total = 0
                try:
                    total = int(r.headers.get("Content-Length") or 0)
                except ValueError:
                    total = 0
                written = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=512 * 1024):
                        if self.cancel_requested:
                            raise Cancelled("Đã dừng theo yêu cầu.")
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
                            if progress_cb and total > 0:
                                progress_cb(min(100.0, written * 100.0 / total))
                if progress_cb:
                    progress_cb(100.0)
                status = r.status_code
                final_url = r.url
                content_range = r.headers.get("Content-Range") or ""
        except SourceBlocked:
            raise
        except requests.RequestException as exc:
            raise RuntimeError(f"Tải nguồn thất bại: {exc}") from exc

        if not dest.exists() or dest.stat().st_size < 16384:
            size = dest.stat().st_size if dest.exists() else 0
            raise RuntimeError(f"Nguồn chỉ có {size} byte: file quá nhỏ/placeholder")
        return {
            "size_mb": dest.stat().st_size / (1024 * 1024),
            "content_type": ctype,
            "status": status,
            "final_url": final_url,
            "content_range": content_range,
        }

    @staticmethod
    def _inspect_media_structure(path: Path, content_type: str):
        try:
            with open(path, "rb") as f:
                head = f.read(2 * 1024 * 1024)
        except OSError:
            return "Không đọc được file tạm"
        if not head:
            return "File nguồn rỗng"

        low_ct = (content_type or "").lower()
        has_ftyp = b"ftyp" in head[:128]
        has_moov = b"moov" in head
        has_moof = b"moof" in head
        has_mdat = b"mdat" in head

        if "mp4" in low_ct or has_ftyp or has_moof or has_mdat:
            if has_moof and not has_moov:
                return "Đây là MP4/fMP4 fragment (có moof nhưng thiếu moov/init), không phải file audio độc lập"
            if has_mdat and not has_moov and not has_ftyp:
                return "Dữ liệu MP4 thiếu header/init segment; không phải file M4A/MP4 độc lập"
            if not has_ftyp and not has_moov:
                return "Server ghi audio/mp4 nhưng dữ liệu không có header MP4/M4A hợp lệ"

        if "mpeg" in low_ct or path.suffix.lower() == ".mp3":
            if not (head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)):
                return "Server ghi MP3 nhưng file không có header MP3 hợp lệ"
        return ""

    def _probe_audio(self, path: Path):
        result = subprocess.run(
            [self.ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0, (result.stderr or result.stdout or "")

    @staticmethod
    def _short_ffmpeg_error(text: str):
        lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
        return lines[-1][-320:] if lines else ""

    def _transcode(self, source: Path, output: Path, opts: dict):
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if opts["start"] is not None:
            cmd += ["-ss", f"{opts['start']:.3f}"]
        cmd += ["-i", str(source), "-map", "0:a:0", "-vn"]
        if opts["end"] is not None:
            duration = opts["end"] - (opts["start"] or 0)
            cmd += ["-t", f"{duration:.3f}"]
        filters = []
        if opts["normalize"]:
            filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
        if filters:
            cmd += ["-af", ",".join(filters)]
        cmd += ["-ar", "44100", "-ac", "1" if opts["mono"] else "2"]

        fmt = opts["fmt"]
        if fmt == "mp3":
            cmd += [
                "-codec:a", "libmp3lame", "-b:a", "320k",
                "-id3v2_version", "3", "-write_id3v1", "1", str(output)
            ]
        elif fmt == "wav":
            cmd += ["-codec:a", "pcm_s16le", str(output)]
        else:
            cmd += ["-codec:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(output)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(self._short_ffmpeg_error(result.stderr or result.stdout) or "FFmpeg lỗi")
        if not output.exists() or output.stat().st_size < 16384:
            raise RuntimeError("FFmpeg tạo file đầu ra quá nhỏ hoặc rỗng")

    def _save_cover(self, meta: TrackMeta, out_dir: Path):
        try:
            ext = Path(urlparse(meta.cover_url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                ext = ".jpg"
            dest = self._unique_output(out_dir, f"[Suno] {meta.title} cover", ext.lstrip("."))
            r = self.resolver.session.get(meta.cover_url, timeout=(10, 30), stream=True)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(512 * 1024):
                    if chunk:
                        f.write(chunk)
            self.log(f"  ↳ Đã lưu cover: {dest.name}")
        except Exception as exc:
            self.log(f"  ↳ Không lưu được cover: {exc}")

    def make_zip(self):
        outputs = [Path(p) for p in self.success_outputs if Path(p).exists()]
        if not outputs:
            messagebox.showwarning(APP_NAME, "Chưa có file hoàn tất để đóng ZIP.")
            return
        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        zip_path = self._unique_output(out_dir, "Suno Audio Converter - All", "zip")
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for p in outputs:
                    zf.write(p, arcname=p.name)
            self.log(f"✓ Đã tạo ZIP: {zip_path}")
            messagebox.showinfo(APP_NAME, f"Đã tạo:\n{zip_path}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được ZIP:\n{exc}")

    def open_output_folder(self):
        folder = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Không mở được thư mục:\n{exc}")

    def set_row(self, tree_id, status=None, title=None, duration=None, output=None):
        def apply():
            values = list(self.tree.item(tree_id, "values"))
            while len(values) < 4:
                values.append("")
            if status is not None:
                values[0] = status
            if title is not None:
                values[1] = title
            if duration is not None:
                values[2] = duration
            if output is not None:
                values[3] = output
            self.tree.item(tree_id, values=values)
        self.after(0, apply)

    def set_progress(self, value):
        self.after(0, lambda: self.progress.configure(value=max(0, min(100, value))))

    def log(self, text):
        def append():
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        self.after(0, append)

    @staticmethod
    def _cache_key(url: str, opts: dict):
        payload = "|".join([
            url,
            str(opts.get("fmt")),
            str(opts.get("normalize")),
            str(opts.get("mono")),
            str(opts.get("start")),
            str(opts.get("end")),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _unique_output(folder: Path, title: str, ext: str):
        base = safe_name(title)
        candidate = folder / f"{base}.{ext}"
        n = 2
        while candidate.exists():
            candidate = folder / f"{base} ({n}).{ext}"
            n += 1
        return candidate


if __name__ == "__main__":
    SunoAudioConverter().mainloop()
