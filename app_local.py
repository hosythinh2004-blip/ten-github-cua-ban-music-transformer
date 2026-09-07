import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import imageio_ffmpeg

APP_NAME = "Audio Encoder"
APP_VERSION = "2.0.0"
SUPPORTED = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".webm"}


def safe_name(value: str) -> str:
    bad = '<>:"/\\|?*'
    for ch in bad:
        value = value.replace(ch, "_")
    value = " ".join(value.split()).strip(" ._")
    return value[:140] or "audio"


class AudioEncoder(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x680")
        self.minsize(780, 580)
        self.configure(bg="#121417")

        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.output_format = tk.StringVar(value="mp3")
        self.files = []
        self.last_output = None
        self.running = False

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

        ttk.Label(root, text="AUDIO ENCODER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text=f"v{APP_VERSION} • Offline 100% • Không Suno API • Không Internet • Chỉ mã hóa file âm thanh trên máy",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 14))

        self.choose_btn = ttk.Button(root, text="CHỌN FILE ÂM THANH", command=self.choose_files)
        self.choose_btn.pack(fill="x", pady=(0, 10))

        self.file_box = tk.Listbox(
            root,
            height=10,
            bg="#1b1f24",
            fg="#f1f3f4",
            selectbackground="#343a40",
            relief="flat",
            font=("Consolas", 10),
        )
        self.file_box.pack(fill="both", expand=False, pady=(0, 10))

        file_actions = ttk.Frame(root)
        file_actions.pack(fill="x", pady=(0, 12))
        ttk.Button(file_actions, text="XÓA FILE ĐÃ CHỌN", command=self.remove_selected).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(file_actions, text="XÓA TẤT CẢ", command=self.clear_files).pack(side="left", fill="x", expand=True, padx=(5, 0))

        opts = ttk.Frame(root)
        opts.pack(fill="x", pady=(0, 12))
        ttk.Label(opts, text="Đầu ra:").pack(side="left")
        ttk.Radiobutton(opts, text="MP3 320 kbps", variable=self.output_format, value="mp3").pack(side="left", padx=(12, 8))
        ttk.Radiobutton(opts, text="WAV 16-bit / 44.1 kHz", variable=self.output_format, value="wav").pack(side="left", padx=8)

        folder = ttk.Frame(root)
        folder.pack(fill="x", pady=(0, 12))
        ttk.Label(folder, text="Thư mục lưu:").pack(side="left")
        tk.Entry(
            folder,
            textvariable=self.output_dir,
            bg="#1b1f24",
            fg="#f1f3f4",
            insertbackground="white",
            relief="flat",
            font=("Segoe UI", 10),
        ).pack(side="left", fill="x", expand=True, padx=10, ipady=7)
        ttk.Button(folder, text="CHỌN THƯ MỤC", command=self.choose_folder).pack(side="right")

        self.start_btn = ttk.Button(root, text="BẮT ĐẦU MÃ HÓA", command=self.start)
        self.start_btn.pack(fill="x", pady=(0, 10))

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 10))

        action_row = ttk.Frame(root)
        action_row.pack(fill="x", pady=(0, 10))
        self.play_btn = ttk.Button(action_row, text="PHÁT FILE VỪA TẠO", command=self.play_last, state="disabled")
        self.play_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(action_row, text="MỞ THƯ MỤC LƯU", command=self.open_output_folder).pack(side="left", fill="x", expand=True, padx=(5, 0))

        ttk.Label(root, text="Nhật ký").pack(anchor="w")
        self.log_box = tk.Text(
            root,
            height=11,
            bg="#0d0f12",
            fg="#c7d0d9",
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_box.pack(fill="both", expand=True, pady=(6, 0))

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Chọn file âm thanh",
            filetypes=[
                ("Audio", "*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.opus *.mp4 *.webm"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        for raw in paths:
            p = Path(raw)
            if p.suffix.lower() not in SUPPORTED:
                continue
            if p not in self.files:
                self.files.append(p)
                self.file_box.insert("end", str(p))

    def remove_selected(self):
        selected = list(self.file_box.curselection())
        for idx in reversed(selected):
            self.file_box.delete(idx)
            del self.files[idx]

    def clear_files(self):
        self.files.clear()
        self.file_box.delete(0, "end")

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if folder:
            self.output_dir.set(folder)

    def start(self):
        if self.running:
            return
        if not self.files:
            messagebox.showwarning(APP_NAME, "Hãy chọn ít nhất một file âm thanh.")
            return

        out_dir = Path(self.output_dir.get().strip() or Path.home() / "Downloads")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Không tạo được thư mục lưu:\n{exc}")
            return

        self.running = True
        self.start_btn.config(state="disabled")
        self.choose_btn.config(state="disabled")
        self.progress.configure(value=0)
        fmt = self.output_format.get()
        threading.Thread(target=self._worker, args=(list(self.files), out_dir, fmt), daemon=True).start()

    def _worker(self, files, out_dir: Path, fmt: str):
        ok = 0
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.log(f"FFmpeg: {ffmpeg}")
        total = len(files)

        try:
            for i, source in enumerate(files, 1):
                self.set_progress(((i - 1) / total) * 100)
                try:
                    self.log(f"[{i}/{total}] {source}")
                    if not source.exists():
                        raise RuntimeError("File không tồn tại")
                    if source.stat().st_size < 4096:
                        raise RuntimeError("File nguồn quá nhỏ hoặc rỗng")

                    output = self._unique_output(out_dir, source.stem, fmt)
                    self._convert(ffmpeg, source, output, fmt)
                    self._validate(ffmpeg, output)

                    self.last_output = output
                    self.after(0, lambda: self.play_btn.config(state="normal"))
                    self.log(f"  ✓ Đã tạo: {output}")
                    ok += 1
                except Exception as exc:
                    self.log(f"  ✗ Lỗi: {exc}")
                self.set_progress((i / total) * 100)

            self.log(f"Hoàn tất: {ok}/{total} file thành công.")
            self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Hoàn tất: {ok}/{total} file thành công."))
        finally:
            self.running = False
            self.after(0, lambda: self.start_btn.config(state="normal"))
            self.after(0, lambda: self.choose_btn.config(state="normal"))

    @staticmethod
    def _convert(ffmpeg: str, source: Path, output: Path, fmt: str):
        common = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
        ]

        if fmt == "mp3":
            cmd = common + [
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-id3v2_version",
                "3",
                "-write_id3v1",
                "1",
                str(output),
            ]
        else:
            cmd = common + [
                "-codec:a",
                "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(output),
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "FFmpeg convert lỗi")[-1000:])
        if not output.exists() or output.stat().st_size < 16384:
            raise RuntimeError("File đầu ra quá nhỏ hoặc rỗng")

    @staticmethod
    def _validate(ffmpeg: str, output: Path):
        check = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(output),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check.returncode != 0:
            try:
                output.unlink()
            except OSError:
                pass
            raise RuntimeError((check.stderr or "File đầu ra không giải mã được")[-1000:])

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

    def open_output_folder(self):
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
    AudioEncoder().mainloop()
