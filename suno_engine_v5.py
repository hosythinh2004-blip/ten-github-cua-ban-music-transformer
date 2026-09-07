import hashlib
import html as html_lib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import imageio_ffmpeg
import requests

ENGINE_VERSION = "5.0.0"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
CACHE_DIR = Path.home() / ".suno_audio_converter" / "v5_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_session = requests.Session()
_session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://suno.com/",
})
_lock_guard = threading.Lock()
_cache_locks: dict[str, threading.Lock] = {}


class EngineError(RuntimeError):
    pass


class SourceError(EngineError):
    pass


def safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:140] or "audio"


def extract_uuid(value: str) -> str | None:
    m = UUID_RE.search(value or "")
    return m.group(0) if m else None


def _meta_content(text: str, key: str, attr: str = "property") -> str | None:
    patterns = [
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I | re.S)
        if m:
            return html_lib.unescape(m.group(1)).strip()
    return None


def _extract_uuid_from_html(text: str) -> str | None:
    for pattern in (
        rf'/song/({UUID_PATTERN})',
        rf'"clip_id"\s*:\s*"({UUID_PATTERN})"',
        rf'"id"\s*:\s*"({UUID_PATTERN})"',
    ):
        m = re.search(pattern, text or "", re.I)
        if m:
            return m.group(1)
    return None


def _extract_title_tag(text: str) -> str | None:
    m = re.search(r"<title>(.*?)</title>", text or "", re.I | re.S)
    return html_lib.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else None


def _extract_json_string(text: str, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        m = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text or "", re.I)
        if m:
            value = (
                m.group(1)
                .replace("\\/", "/")
                .replace("\\u0026", "&")
                .replace("\\u002F", "/")
            )
            if value:
                return html_lib.unescape(value)
    return None


def _extract_duration(text: str) -> float | None:
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


def _usable_public_url(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    p = urlparse(value)
    if p.scheme not in {"http", "https"} or not p.netloc:
        return False
    low = value.lower()
    blocked = (
        "/api/forbidden",
        "silence.mp3",
        "/mango/rights",
        "license",
        "drm",
    )
    return not any(marker in low for marker in blocked)


def _dedupe(values: list[str]) -> list[str]:
    out, seen = [], set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _extract_public_audio_urls(text: str) -> list[str]:
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
            if _usable_public_url(value):
                out.append(value)

    def rank(url: str) -> int:
        path = urlparse(url).path.lower()
        if path.endswith(".m4a"):
            return 0
        if path.endswith(".mp4"):
            return 1
        if path.endswith(".mp3"):
            return 2
        return 3

    return sorted(_dedupe(out), key=rank)


def resolve_suno(raw_url: str) -> dict:
    raw = (raw_url or "").strip().strip("'\"")
    if not raw:
        raise EngineError("Link trống")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    host = urlparse(raw).netloc.lower().split(":")[0]
    if host not in {"suno.com", "www.suno.com"}:
        raise EngineError("Chỉ hỗ trợ link suno.com")

    try:
        r = _session.get(
            raw,
            timeout=(15, 30),
            allow_redirects=True,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise EngineError(f"Không mở được trang Suno: {exc}") from exc

    text = r.text
    clip_id = extract_uuid(raw) or extract_uuid(r.url) or _extract_uuid_from_html(text) or ""
    title = (
        _meta_content(text, "og:title")
        or _meta_content(text, "twitter:title", "name")
        or _extract_title_tag(text)
        or clip_id
        or "audio"
    )
    title = re.sub(r"\s*[|·-]\s*Suno.*$", "", title, flags=re.I).strip()
    artist = (
        _meta_content(text, "music:musician")
        or _meta_content(text, "author", "name")
        or _extract_json_string(text, ("display_name", "displayName", "artist", "username"))
        or ""
    )
    cover = _meta_content(text, "og:image") or _meta_content(text, "twitter:image", "name") or ""
    duration = _extract_duration(text)
    audio_urls = _extract_public_audio_urls(text)

    if clip_id:
        canonical = f"https://suno.com/song/{clip_id}"
        if canonical != r.url:
            try:
                c = _session.get(
                    canonical,
                    timeout=(10, 20),
                    allow_redirects=True,
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                if c.ok:
                    audio_urls.extend(_extract_public_audio_urls(c.text))
                    if not cover:
                        cover = _meta_content(c.text, "og:image") or cover
                    if duration is None:
                        duration = _extract_duration(c.text)
            except requests.RequestException:
                pass

    audio_urls = _dedupe(audio_urls)
    return {
        "url": r.url,
        "id": clip_id,
        "title": safe_name(title),
        "artist": safe_name(artist) if artist else "",
        "cover_url": cover,
        "duration": duration,
        "audio_urls": audio_urls,
        "source_count": len(audio_urls),
    }


def _run_ffmpeg(args: list[str], timeout: int = 900):
    return subprocess.run(
        [FFMPEG] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def validate_audio(path: Path, full_decode: bool = False) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size < 4096:
        return False, "file quá nhỏ hoặc rỗng"
    args = ["-v", "error", "-i", str(path), "-map", "0:a:0"]
    if full_decode:
        args += ["-f", "null", "-"]
    else:
        args += ["-t", "2", "-f", "null", "-"]
    try:
        result = _run_ffmpeg(args, timeout=120 if not full_decode else 900)
    except subprocess.TimeoutExpired:
        return False, "FFmpeg timeout khi kiểm tra audio"
    return result.returncode == 0, (result.stderr or result.stdout or "")[-800:]


def diagnose_source(path: Path, content_type: str = "") -> str:
    if not path.exists():
        return "không có file tạm"
    size = path.stat().st_size
    if size < 16384:
        return f"placeholder/quá nhỏ ({size} bytes)"
    try:
        head = path.read_bytes()[:1024 * 256]
    except OSError:
        return "không đọc được file tạm"
    low = head.lower()
    stripped = head.lstrip()
    if stripped.startswith((b"<html", b"<!doctype", b"{")):
        return "response HTML/JSON, không phải audio"
    has_ftyp = b"ftyp" in head[:64]
    has_moov = b"moov" in head
    has_moof = b"moof" in head
    if has_moof and not has_moov:
        return "MP4/fMP4 fragment thiếu init/moov, không phải file audio độc lập"
    if "mp4" in (content_type or "").lower() and not has_ftyp:
        return "server ghi audio/mp4 nhưng dữ liệu không có header MP4/M4A hợp lệ"
    ok, err = validate_audio(path, full_decode=False)
    if ok:
        return "audio hợp lệ"
    clean = re.sub(r"\s+", " ", err).strip()
    return clean[-350:] or "FFmpeg không đọc được nguồn"


def download_public_audio(url: str, dest: Path) -> dict:
    headers = {
        "User-Agent": UA,
        "Referer": "https://suno.com/",
        "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    try:
        with _session.get(url, headers=headers, stream=True, timeout=(15, 180), allow_redirects=True) as r:
            host = urlparse(r.url or url).netloc
            if r.status_code in (401, 403):
                raise SourceError(f"HTTP {r.status_code} từ {host}")
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                raise SourceError(f"{ctype or 'text response'} từ {host}, không phải audio")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    except SourceError:
        raise
    except requests.RequestException as exc:
        raise SourceError(f"Tải nguồn thất bại: {exc}") from exc

    if not dest.exists():
        raise SourceError("Nguồn không tạo được file tạm")
    return {
        "size": dest.stat().st_size,
        "size_mb": dest.stat().st_size / (1024 * 1024),
        "content_type": ctype,
        "host": host,
    }


def transcode(
    source: Path,
    output: Path,
    fmt: str,
    normalize: bool = False,
    mono: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> None:
    if fmt not in {"mp3", "wav", "m4a"}:
        raise EngineError("Định dạng chỉ nhận mp3, wav hoặc m4a")
    if start is not None and end is not None and end <= start:
        raise EngineError("Mốc kết thúc phải lớn hơn mốc bắt đầu")

    args = ["-y", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(source), "-map", "0:a:0", "-vn"]
    if end is not None:
        args += ["-t", f"{end - (start or 0.0):.3f}"]
    if normalize:
        args += ["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"]
    args += ["-ar", "44100", "-ac", "1" if mono else "2"]

    if fmt == "mp3":
        args += [
            "-codec:a", "libmp3lame", "-b:a", "320k",
            "-id3v2_version", "3", "-write_id3v1", "1", str(output),
        ]
    elif fmt == "wav":
        args += ["-codec:a", "pcm_s16le", str(output)]
    else:
        args += ["-codec:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(output)]

    try:
        result = _run_ffmpeg(args, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise EngineError("FFmpeg timeout khi mã hóa") from exc
    if result.returncode != 0 or not output.exists() or output.stat().st_size < 16384:
        raise EngineError((result.stderr or result.stdout or "FFmpeg lỗi")[-1200:])


def _cache_key(clip_id: str, fmt: str, normalize: bool, mono: bool, start, end) -> str:
    payload = f"{clip_id}|{fmt}|{int(normalize)}|{int(mono)}|{start}|{end}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_cache_lock(key: str) -> threading.Lock:
    with _lock_guard:
        if key not in _cache_locks:
            _cache_locks[key] = threading.Lock()
        return _cache_locks[key]


def get_or_convert(
    clip_id: str,
    fmt: str = "mp3",
    normalize: bool = False,
    mono: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> dict:
    if not re.fullmatch(UUID_PATTERN, clip_id or "", re.I):
        raise EngineError("ID bài hát không hợp lệ")

    meta = resolve_suno(f"https://suno.com/song/{clip_id}")
    if not meta["audio_urls"]:
        raise EngineError("Trang không cung cấp file audio trực tiếp công khai")

    key = _cache_key(clip_id, fmt, normalize, mono, start, end)
    output = CACHE_DIR / f"{key}.{fmt}"
    lock = _get_cache_lock(key)

    with lock:
        if output.exists() and output.stat().st_size > 16384:
            valid, _ = validate_audio(output, full_decode=False)
            if valid:
                return {"path": output, "meta": meta, "cache": "HIT", "attempts": []}

        attempts = []
        with tempfile.TemporaryDirectory(prefix="suno_v5_") as tmp:
            for idx, audio_url in enumerate(meta["audio_urls"], 1):
                source = Path(tmp) / f"source_{idx}"
                attempt = {"index": idx, "host": urlparse(audio_url).netloc, "status": "failed"}
                try:
                    info = download_public_audio(audio_url, source)
                    attempt.update({
                        "size_mb": round(info["size_mb"], 3),
                        "content_type": info["content_type"],
                    })
                    diagnosis = diagnose_source(source, info["content_type"])
                    attempt["diagnosis"] = diagnosis
                    valid, err = validate_audio(source, full_decode=False)
                    if not valid:
                        raise SourceError(diagnosis or err)

                    transcode(source, output, fmt, normalize, mono, start, end)
                    valid_out, out_err = validate_audio(output, full_decode=True)
                    if not valid_out:
                        try:
                            output.unlink()
                        except OSError:
                            pass
                        raise SourceError(f"File đầu ra không hợp lệ: {out_err}")

                    attempt["status"] = "ok"
                    attempts.append(attempt)
                    return {
                        "path": output,
                        "meta": meta,
                        "cache": "MISS",
                        "source_host": info["host"],
                        "attempts": attempts,
                    }
                except Exception as exc:
                    attempt["error"] = re.sub(r"\s+", " ", str(exc)).strip()[-500:]
                    attempts.append(attempt)
                    try:
                        if output.exists():
                            output.unlink()
                    except OSError:
                        pass

        parts = ["Không có nguồn audio công khai hoàn chỉnh có thể chuyển đổi."]
        for a in attempts:
            size = f" {a.get('size_mb')}MB" if a.get("size_mb") is not None else ""
            why = a.get("diagnosis") or a.get("error") or "không rõ"
            parts.append(f"{a.get('host','nguồn')}{size}: {why}")
        raise EngineError(" | ".join(parts))


def cache_info() -> dict:
    files = [p for p in CACHE_DIR.glob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return {"files": len(files), "size_mb": round(total / (1024 * 1024), 2), "path": str(CACHE_DIR)}


def clear_cache() -> int:
    removed = 0
    for p in list(CACHE_DIR.glob("*")):
        try:
            if p.is_file() or p.is_symlink():
                p.unlink()
            else:
                shutil.rmtree(p, ignore_errors=True)
            removed += 1
        except OSError:
            pass
    return removed


def copy_result(result: dict, out_dir: Path, fmt: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    title = result["meta"].get("title") or "audio"
    base = safe_name(f"[Suno] {title}")
    candidate = out_dir / f"{base}.{fmt}"
    n = 2
    while candidate.exists():
        candidate = out_dir / f"{base} ({n}).{fmt}"
        n += 1
    shutil.copy2(result["path"], candidate)
    return candidate


def save_cover(meta: dict, out_dir: Path) -> Path | None:
    url = meta.get("cover_url") or ""
    if not url:
        return None
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    base = safe_name(f"[Suno] {meta.get('title') or 'audio'} cover")
    dest = out_dir / f"{base}{ext}"
    n = 2
    while dest.exists():
        dest = out_dir / f"{base} ({n}){ext}"
        n += 1
    try:
        with _session.get(url, stream=True, timeout=(10, 60)) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(512 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest
    except Exception:
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass
        return None
