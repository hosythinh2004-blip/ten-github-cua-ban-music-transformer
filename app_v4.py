import os
import re
import socket
import threading
import time
import tkinter as tk
import webbrowser
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urlparse

import requests
import uvicorn

import api_server

APP_NAME = "Suno Audio Converter"
APP_VERSION = "4.0.0"


def safe_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:140] or "audio"


def format_duration(seconds):
    try:
        seconds = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(max(0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def response_filename(headers: dict, fallback: str) -> str:
    cd = headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=utf-8''([^;]+)", cd, re.I)
    if m:
        return safe_name(unquote(m.group(1)).strip('"'))
    m = re.search(r'filename="([^"]+)"', cd, re.I)
    if m:
        return safe_name(m.group(1))
    return safe_name(fallback)


class SunoAudioConverter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x820")
        self.minsize(980, 700)
        self.configure(bg="#111417")

        self.port = find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.api_session = requests.Session()
        self.server = None
        self.server_ready = False

        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.output_format = tk.StringVar(value="mp3")
        self.normalize = tk.BooleanVar(value=False)
        self.mono = tk.BooleanVar(value=False)
        self.save_cover = tk.BooleanVar(value=False)
        self.trim_start = tk.StringVar(value="")
        self.trim_end = tk.StringVar(value="")
        self.api_status = tk.StringVar(value="API nội bộ: đang khởi động...")

        self.queue = []
        self.running = False
        self.stop_requested = False
        self.success_outputs = []
        self.last_output = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        threading.Thread(target=self._start_embedded_api, daemon=True).start()

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background="#111417")
        style.configure("TLabel", background="#111417", foreground="#e8eaed", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), foreground="#ffffff")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), foreground="#9aa0a6")
        style.configure("Api.TLabel", font=("Segoe UI", 9), foreground="#8ab4f8")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=8)
        style.configure("TRadiobutton", background="#111417", foreground="#e8eaed")
        style.configure("TCheckbutton", background="#111417", foreground="#e8eaed")
        style.configure(
            "Dark.Treeview",
            background="#181c20",
            fieldbackground="#181c20",
            foreground="#e8eaed",
            rowheight=28,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background="#252a30",
            foreground="#ffffff",
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map("Dark.Treeview", background=[("selected", "#30475e")], foreground=[("selected", "#ffffff")])

        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        ttk.Label(top, text="SUNO AUDIO CONVERTER", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text=f"v{APP_VERSION}", style="Hint.TLabel").pack(side="left", padx=(10, 0), pady=(9, 0))
        ttk.Button(top, text="API DOCS", command=self.open_api_docs).pack(side="right")

        ttk.Label(
            root,
            text="Link Suno → API nội bộ localhost → nguồn audio trực tiếp công khai → FFmpeg → MP3 / WAV / M4A",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 2))
        ttk.Label(root, textvariable=self.api_status, style="Api.TLabel").pack(anchor="w", pady=(0, 12))

        ttk.Label(root, text="Link bài hát Suno (mỗi dòng một link)").pack(anchor="w")
        self.links = tk.Text(
            root,
            height=5,
            bg="#1b1f24",
            fg="#f1f3f4",
            insertbackground="white",
            relief="flat",
            padx=10,
            pady=8,
            font=("Consolas", 10),
            wrap="word",
        )
        self.links.pack(fill="x", pady=(5, 8))

        link_actions = ttk.Frame(root)
        link_actions.pack(fill="x", pady=(0, 10))
        self.add_btn = ttk.Button(link_actions, text="PHÂN TÍCH + THÊM VÀO HÀNG ĐỢI", command=self.add_links, state="disabled")
        self.add_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(link_actions, text="XÓA DÒNG CHỌN", command=self.remove_selected).pack(side="left", padx=5)
        ttk.Button(link_actions, text="XÓA HÀNG ĐỢI", command=self.clear_queue).pack(side="left", padx=5)
        ttk.Button(link_actions, text="XÓA CACHE", command=self.clear_cache).pack(side="left", padx=(5, 0))

        options = ttk.Frame(root)
        options.pack(fill="x", pady=(0, 8))
        ttk.Label(options, text="Đầu ra:").pack(side="left")
        ttk.Radiobutton(options, text="MP3 320 kbps", variable=self.output_format, value="mp3").pack(side="left", padx=(10, 6))
        ttk.Radiobutton(options, text="WAV 16-bit / 44.1 kHz", variable=self.output_format, value="wav").pack(side="left", padx=6)
        ttk.Radiobutton(options, text="M4A AAC 256 kbps", variable=self.output_format, value="m4a").pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Chuẩn hóa âm lượng", variable=self.normalize).pack(side="left", padx=(18, 6))
        ttk.Checkbutton(options, text="Mono", variable=self.mono).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Lưu cover", variable=self.save_cover).pack(side="left", padx=6)

        trim = ttk.Frame(root)
        trim.pack(fill="x", pady=(0, 8))
        ttk.Label(trim, text="Cắt từ:").pack(side="left")
        tk.Entry(trim, textvariable=self.trim_start, width=11).pack(side="left", padx=(6, 14))
        ttk.Label(trim, text="đến:").pack(side="left")
        tk.Entry(trim, textvariable=self.trim_end, width=11).pack(side="left", padx=(6, 14))
        ttk.Label(trim, text="giây hoặc MM:SS / HH:MM:SS", style="Hint.TLabel").pack(side="left")

        folder = ttk.Frame(root)
        folder.pack(fill="x", pady=(0, 10))
        ttk.Label(folder, text="Thư mục lưu:").pack(side="left")
        tk.Entry(
            folder,
            textvariable=self.output_dir,
            bg="#1b1f24",
            fg="#f1f3f4",
            insertbackground="white",
            relief="flat",
            font=("Segoe UI", 10),
        ).pack(side="left", fill="x", expand=True, padx=10, ipady=6)
        ttk.Button(folder, text="CHỌN THƯ MỤC", command=self.choose_folder).pack(side="right")

        tree_wrap = ttk.Frame(root)
        tree_wrap.pack(fill="both", expand=True, pady=(0, 10))
        cols = ("status", "title", "artist", "duration", "sources", "output")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=10, style="Dark.Treeview")
        headings = {
            "status": "Trạng thái",
            "title": "Bài hát",
            "artist": "Tác giả",
            "duration": "Thời lượng",
            "sources": "Nguồn",
            "output": "File đầu ra / Chẩn đoán",
        }
        for key, text in headings.items():
            self.tree.heading(key, text=text)
        self.tree.column("status", width=135, anchor="center")
        self.tree.column("title", width=240)
        self.tree.column("artist", width=150)
        self.tree.column("duration", width=85, anchor="center")
        self.tree.column("sources", width=65, anchor="center")
        self.tree.column("output", width=440)
        yscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        run_actions = ttk.Frame(root)
        run_actions.pack(fill="x", pady=(0, 8))
        self.start_btn = ttk.Button(run_actions, text="BẮT ĐẦU XỬ LÝ", command=self.start_queue, state="disabled")
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.stop_btn = ttk.Button(run_actions, text="DỪNG", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=5)
        self.play_btn = ttk.Button(run_actions, text="PHÁT FILE", command=self.play_selected, state="disabled")
        self.play_btn.pack(side="left", padx=5)
        ttk.Button(run_actions, text="ĐÓNG GÓI .ZIP", command=self.make_zip).pack(side="left", padx=5)
        ttk.Button(run_actions, text="MỞ THƯ MỤC", command=self.open_output_folder).pack(side="left", padx=(5, 0))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))

        ttk.Label(root, text="Nhật ký").pack(anchor="w")
        self.log_box = tk.Text(
            root,
            height=8,
            bg="#0d0f12",
            fg="#c7d0d9",
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_box.pack(fill="x", pady=(5, 0))

    def _start_embedded_api(self):
        try:
            config = uvicorn.Config(
                api_server.app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
            )
            self.server = uvicorn.Server(config)
            threading.Thread(target=self.server.run, daemon=True).start()

            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    r = self.api_session.get(f"{self.base_url}/health", timeout=1)
                    if r.ok and r.json().get("ok"):
                        self.server_ready = True
                        version = r.json().get("version", "")
                        self.after(0, lambda: self.api_status.set(f"API nội bộ: {self.base_url} • backend {version} • sẵn sàng"))
                        self.after(0, lambda: self.add_btn.config(state="normal"))
                        self.after(0, lambda: self.start_btn.config(state="normal"))
                        self.log("✓ API nội bộ đã sẵn sàng.")
                        return
                except Exception:
                    time.sleep(0.25)
            raise RuntimeError("API nội bộ không khởi động trong 15 giây")
        except Exception as exc:
            self.after(0, lambda: self.api_status.set("API nội bộ: lỗi khởi động"))
            self.log(f"✗ Không khởi động được API nội bộ: {exc}")

    def open_api_docs(self):
        if self.server_ready:
            webbrowser.open(f"{self.base_url}/docs")
        else:
            messagebox.showwarning(APP_NAME, "API nội bộ chưa sẵn sàng.")

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def add_links(self):
        if not self.server_ready:
            messagebox.showwarning(APP_NAME, "API nội bộ chưa sẵn sàng.")
            return
        urls = [x.strip() for x in self.links.get("1.0", "end").splitlines() if x.strip()]
        if not urls:
            messagebox.showwarning(APP_NAME, "Hãy dán ít nhất một link Suno.")
            return

        existing = {item["url"] for item in self.queue}
        new_items = []
        for url in urls:
            if url in existing:
                continue
            host = urlparse(url if "://" in url else "https://" + url).netloc.lower().split(":")[0]
            if host not in {"suno.com", "www.suno.com"}:
                self.log(f"Bỏ qua link không phải suno.com: {url}")
                continue
            tree_id = self.tree.insert("", "end", values=("Đang phân tích", url, "", "", "", ""))
            item = {"url": url, "tree_id": tree_id, "meta": None, "output": None}
            self.queue.append(item)
            new_items.append(item)
            existing.add(url)

        if not new_items:
            return
        self.add_btn.config(state="disabled")
        threading.Thread(target=self._resolve_items, args=(new_items,), daemon=True).start()

    def _resolve_items(self, items):
        try:
            for item in items:
                tree_id = item["tree_id"]
                try:
                    r = self.api_session.get(
                        f"{self.base_url}/api/suno-info",
                        params={"url": item["url"]},
                        timeout=(10, 60),
                    )
                    if not r.ok:
                        raise RuntimeError(self._api_error(r))
                    meta = r.json()
                    item["meta"] = meta
                    status = "Sẵn sàng" if meta.get("source_count", 0) else "Không có nguồn public"
                    self.set_row(
                        tree_id,
                        status=status,
                        title=meta.get("title") or item["url"],
                        artist=meta.get("artist") or "",
                        duration=format_duration(meta.get("duration")),
                        sources=str(meta.get("source_count", 0)),
                        output="",
                    )
                    self.log(
                        f"✓ {meta.get('title') or 'Bài hát'} • UUID: {meta.get('id') or 'không có'} • "
                        f"nguồn public: {meta.get('source_count', 0)}"
                    )
                except Exception as exc:
                    self.set_row(tree_id, status="Lỗi phân tích", output=str(exc)[-350:])
                    self.log(f"✗ Phân tích link lỗi: {exc}")
        finally:
            self.after(0, lambda: self.add_btn.config(state="normal" if self.server_ready and not self.running else "disabled"))

    def remove_selected(self):
        if self.running:
            return
        selected = set(self.tree.selection())
        if not selected:
            return
        self.queue = [item for item in self.queue if item["tree_id"] not in selected]
        for tree_id in selected:
            self.tree.delete(tree_id)

    def clear_queue(self):
        if self.running:
            return
        self.queue.clear()
        self.success_outputs.clear()
        self.last_output = None
        self.play_btn.config(state="disabled")
        for node in self.tree.get_children():
            self.tree.delete(node)
        self.progress.configure(value=0)

    def clear_cache(self):
        if not self.server_ready or self.running:
            return
        try:
            r = self.api_session.delete(f"{self.base_url}/api/cache", timeout=20)
            r.raise_for_status()
            count = r.json().get("removed", 0)
            self.log(f"✓ Đã xóa cache: {count} mục.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Không xóa được cache:\n{exc}")

    def start_queue(self):
        if self.running:
            return
        if not self.server_ready:
            messagebox.showwarning(APP_NAME, "API nội bộ chưa sẵn sàng.")
            return
        candidates = [item for item in self.queue if item.get("meta") and item["meta"].get("id")]
        if not candidates:
            messagebox.showwarning(APP_NAME, "Chưa có bài hợp lệ trong hàng đợi.")
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

        opts = {
            "format": self.output_format.get(),
            "normalize": bool(self.normalize.get()),
            "mono": bool(self.mono.get()),
            "start": start,
            "end": end,
            "save_cover": bool(self.save_cover.get()),
        }

        self.running = True
        self.stop_requested = False
        self.success_outputs.clear()
        self.start_btn.config(state="disabled")
        self.add_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.configure(value=0)
        threading.Thread(target=self._process_queue, args=(candidates, out_dir, opts), daemon=True).start()

    def stop(self):
        self.stop_requested = True
        self.log("Đã yêu cầu dừng. Tác vụ máy chủ hiện tại có thể cần hoàn tất trước khi dừng.")

    def _process_queue(self, items, out_dir: Path, opts: dict):
        ok = 0
        total = len(items)
        try:
            for idx, item in enumerate(items, 1):
                if self.stop_requested:
                    break
                meta = item["meta"]
                tree_id = item["tree_id"]
                self.set_row(tree_id, status="Máy chủ đang xử lý", output="")
                self.log(f"[{idx}/{total}] {meta.get('title')} • {opts['format'].upper()}")

                params = {
                    "id": meta["id"],
                    "format": opts["format"],
                    "normalize": str(opts["normalize"]).lower(),
                    "mono": str(opts["mono"]).lower(),
                }
                if opts["start"] is not None:
                    params["start"] = opts["start"]
                if opts["end"] is not None:
                    params["end"] = opts["end"]

                try:
                    with self.api_session.get(
                        f"{self.base_url}/api/proxy-audio",
                        params=params,
                        stream=True,
                        timeout=(20, 1200),
                    ) as r:
                        if not r.ok:
                            raise RuntimeError(self._api_error(r))

                        fallback = f"[Suno] {meta.get('title')}.{opts['format']}"
                        filename = response_filename(r.headers, fallback)
                        output = self._unique_output(out_dir, filename)
                        total_bytes = int(r.headers.get("Content-Length") or 0)
                        received = 0
                        self.set_row(tree_id, status="Đang nhận file")
                        with open(output, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1024 * 1024):
                                if self.stop_requested:
                                    raise RuntimeError("Đã dừng theo yêu cầu")
                                if not chunk:
                                    continue
                                f.write(chunk)
                                received += len(chunk)
                                if total_bytes > 0:
                                    part = min(1.0, received / total_bytes)
                                    self.set_progress(((idx - 1 + part) / total) * 100)

                    if not output.exists() or output.stat().st_size < 16384:
                        raise RuntimeError("File nhận từ API quá nhỏ hoặc rỗng")

                    if opts["save_cover"] and meta.get("cover_url"):
                        self._save_cover(meta, out_dir)

                    item["output"] = output
                    self.last_output = output
                    self.success_outputs.append(output)
                    cache_state = r.headers.get("X-Suno-Cache", "") if 'r' in locals() else ""
                    source_host = r.headers.get("X-Suno-Source-Host", "") if 'r' in locals() else ""
                    note = str(output)
                    if cache_state:
                        note += f" • cache {cache_state}"
                    if source_host:
                        note += f" • {source_host}"
                    self.set_row(tree_id, status="Hoàn tất", output=note)
                    self.log(f"  ✓ Đã lưu: {output}")
                    ok += 1
                    self.after(0, lambda: self.play_btn.config(state="normal"))
                except Exception as exc:
                    msg = re.sub(r"\s+", " ", str(exc)).strip()
                    self.set_row(tree_id, status="Không xử lý được", output=msg[-500:])
                    self.log(f"  ✗ {msg}")

                self.set_progress((idx / total) * 100)

            self.log(f"Hoàn tất: {ok}/{total} bài thành công.")
            if not self.stop_requested:
                self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Hoàn tất: {ok}/{total} bài thành công."))
        finally:
            self.running = False
            self.after(0, lambda: self.start_btn.config(state="normal" if self.server_ready else "disabled"))
            self.after(0, lambda: self.add_btn.config(state="normal" if self.server_ready else "disabled"))
            self.after(0, lambda: self.stop_btn.config(state="disabled"))

    def _save_cover(self, meta: dict, out_dir: Path):
        try:
            cover_url = meta.get("cover_url") or ""
            ext = Path(urlparse(cover_url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                ext = ".jpg"
            dest = self._unique_output(out_dir, f"[Suno] {meta.get('title')} cover{ext}")
            with requests.get(cover_url, stream=True, timeout=(10, 60)) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(512 * 1024):
                        if chunk:
                            f.write(chunk)
            self.log(f"  ↳ Cover: {dest.name}")
        except Exception as exc:
            self.log(f"  ↳ Không lưu được cover: {exc}")

    def make_zip(self):
        outputs = [Path(p) for p in self.success_outputs if Path(p).exists()]
        if not outputs:
            messagebox.showwarning(APP_NAME, "Chưa có file hoàn tất để đóng ZIP.")
            return
        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        zip_path = self._unique_output(out_dir, "Suno Audio Converter - All.zip")
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for p in outputs:
                    zf.write(p, arcname=p.name)
            self.log(f"✓ Đã tạo ZIP: {zip_path}")
            messagebox.showinfo(APP_NAME, f"Đã tạo:\n{zip_path}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được ZIP:\n{exc}")

    def play_selected(self):
        path = None
        selected = self.tree.selection()
        if selected:
            selected_id = selected[0]
            for item in self.queue:
                if item["tree_id"] == selected_id and item.get("output"):
                    path = Path(item["output"])
                    break
        if path is None and self.last_output:
            path = Path(self.last_output)
        if not path or not path.exists():
            messagebox.showwarning(APP_NAME, "Chưa có file đầu ra để phát.")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Không mở được file:\n{exc}")

    def open_output_folder(self):
        folder = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Không mở được thư mục:\n{exc}")

    def _api_error(self, response: requests.Response) -> str:
        try:
            payload = response.json()
            detail = payload.get("detail", payload)
            if isinstance(detail, dict):
                message = detail.get("message") or str(detail)
                attempts = detail.get("attempts") or []
                parts = [message]
                for attempt in attempts:
                    host = attempt.get("host", "nguồn")
                    diagnosis = attempt.get("diagnosis") or attempt.get("error") or attempt.get("status")
                    size = attempt.get("size_mb")
                    size_text = f" {size}MB" if size is not None else ""
                    parts.append(f"{host}{size_text}: {diagnosis}")
                return " | ".join(parts)
            return str(detail)
        except Exception:
            return f"HTTP {response.status_code}: {response.text[-500:]}"

    def set_row(self, tree_id, status=None, title=None, artist=None, duration=None, sources=None, output=None):
        def apply():
            if not self.tree.exists(tree_id):
                return
            values = list(self.tree.item(tree_id, "values"))
            while len(values) < 6:
                values.append("")
            updates = [status, title, artist, duration, sources, output]
            for i, value in enumerate(updates):
                if value is not None:
                    values[i] = value
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
    def _unique_output(folder: Path, filename: str) -> Path:
        name = safe_name(filename)
        p = Path(name)
        stem = p.stem or "audio"
        suffix = p.suffix
        candidate = folder / f"{stem}{suffix}"
        n = 2
        while candidate.exists():
            candidate = folder / f"{stem} ({n}){suffix}"
            n += 1
        return candidate

    def on_close(self):
        self.stop_requested = True
        try:
            if self.server is not None:
                self.server.should_exit = True
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    SunoAudioConverter().mainloop()
