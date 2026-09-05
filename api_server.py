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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

APP_NAME = "Suno Audio Converter API"
APP_VERSION = "2.0.0"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)
CACHE_DIR = Path.home() / ".suno_audio_converter" / "api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

app = FastAPI(title=APP_NAME, version=APP_VERSION)

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://suno.com/",
})

_lock_guard = threading.Lock()
_cache_locks: dict[str, threading.Lock] = {}


class SourceError(RuntimeError):
    pass


def _safe_name(value: str) -> str:
    value = unquote(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:140] or "audio"


def _extract_uuid(value: str) -> str | None:
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
    blocked_markers = (
        "/api/forbidden",
        "silence.mp3",
        "/mango/rights",
        "/rights",
        "license",
        "drm",
    )
    return not any(marker in low for marker in blocked_markers)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
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
        r'"url"\s*:\s*"(https?://[^\"]+\.(?:m4a|mp3|wav|aac|flac|ogg|opus|mp4|webm)(?:\?[^\"]*)?)',
        r'(https?://[^\"\'<>\s]+\.(?:m4a|mp3|wav|aac|flac|ogg|opus|mp4|webm)(?:\?[^\"\'<>\s]*)?)',
    ]
    out: list[str] = []
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


def resolve_suno(url: str) -> dict:
    raw = (url or "").strip().strip("'\"")
    if not raw:
        raise HTTPException(status_code=400, detail="URL trống")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    host = urlparse(raw).netloc.lower().split(":")[0]
    if host not in {"suno.com", "www.suno.com"}:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ link suno.com")

    try:
        r = session.get(
            raw,
            timeout=(15, 30),
            allow_redirects=True,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Không mở được trang Suno: {exc}") from exc

    text = r.text
    clip_id = _extract_uuid(raw) or _extract_uuid(r.url) or _extract_uuid_from_html(text) or ""
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
                c = session.get(
                    canonical,
                    timeout=(10, 20),
                    allow_redirects=True,
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                if c.ok:
                    audio_urls.extend(_extract_public_audio_urls(c.text))
                    title = title or _meta_content(c.text, "og:title") or title
                    cover = cover or _meta_content(c.text, "og:image") or cover
                    duration = duration if duration is not None else _extract_duration(c.text)
            except requests.RequestException:
                pass

    audio_urls = _dedupe(audio_urls)
    return {
        "url": r.url,
        "id": clip_id,
        "title": _safe_name(title),
        "artist": _safe_name(artist) if artist else "",
        "cover_url": cover,
        "duration": duration,
        "source_count": len(audio_urls),
        "audio_urls": audio_urls,
    }


def _run_ffmpeg(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _validate_audio(path: Path, full_decode: bool = True) -> tuple[bool, str]:
    args = ["-v", "error", "-i", str(path), "-map", "0:a:0"]
    if not full_decode:
        args += ["-t", "8"]
    args += ["-f", "null", "-"]
    try:
        result = _run_ffmpeg(args, timeout=600 if full_decode else 60)
    except subprocess.TimeoutExpired:
        return False, "FFmpeg timeout khi kiểm tra audio"
    if result.returncode == 0:
        return True, "OK"
    return False, (result.stderr or result.stdout or "FFmpeg không đọc được audio")[-900:]


def _diagnose_source(path: Path, content_type: str = "") -> str:
    if not path.exists():
        return "không có file"
    size = path.stat().st_size
    if size < 16384:
        return f"placeholder/file quá nhỏ ({size} bytes)"
    try:
        head = path.read_bytes()[:512 * 1024]
    except OSError:
        head = b""
    low_head = head[:4096].lower()
    if b"<html" in low_head or b"<!doctype" in low_head:
        return "HTML, không phải audio"
    if low_head.lstrip().startswith((b"{", b"[")):
        return "JSON/text, không phải audio"

    has_ftyp = b"ftyp" in head[:128]
    has_moov = b"moov" in head
    has_moof = b"moof" in head
    if has_moof and not has_moov:
        return "MP4/fMP4 fragment thiếu init/moov, không phải file audio độc lập"
    if "mp4" in (content_type or "").lower() and not has_ftyp:
        return "server ghi audio/mp4 nhưng dữ liệu không có header MP4/M4A hợp lệ"

    ok, err = _validate_audio(path, full_decode=False)
    if ok:
        return "audio hợp lệ"
    clean = re.sub(r"\s+", " ", err).strip()
    return clean[-350:] or "FFmpeg không đọc được nguồn"


def _download_public_audio(url: str, dest: Path) -> dict:
    headers = {
        "User-Agent": UA,
        "Referer": "https://suno.com/",
        "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    try:
        with session.get(url, headers=headers, stream=True, timeout=(15, 180), allow_redirects=True) as r:
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


def _transcode(
    source: Path,
    output: Path,
    fmt: str,
    normalize: bool = False,
    mono: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> None:
    if fmt not in {"mp3", "wav", "m4a"}:
        raise HTTPException(status_code=400, detail="format chỉ nhận mp3, wav hoặc m4a")
    if start is not None and start < 0:
        raise HTTPException(status_code=400, detail="start phải >= 0")
    if end is not None and end < 0:
        raise HTTPException(status_code=400, detail="end phải >= 0")
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=400, detail="end phải lớn hơn start")

    args = ["-y", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(source), "-map", "0:a:0", "-vn"]
    if end is not None:
        duration = end - (start or 0.0)
        args += ["-t", f"{duration:.3f}"]
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
        args += [
            "-codec:a", "aac", "-b:a", "256k",
            "-movflags", "+faststart", str(output),
        ]

    try:
        result = _run_ffmpeg(args, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg timeout khi mã hóa") from exc
    if result.returncode != 0 or not output.exists() or output.stat().st_size < 16384:
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg lỗi")[-1200:])


def _cache_key(
    clip_id: str,
    fmt: str,
    normalize: bool,
    mono: bool,
    start: float | None,
    end: float | None,
) -> str:
    payload = f"{clip_id}|{fmt}|{int(normalize)}|{int(mono)}|{start}|{end}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_cache_lock(key: str) -> threading.Lock:
    with _lock_guard:
        lock = _cache_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _cache_locks[key] = lock
        return lock


def _media_type(fmt: str) -> str:
    return {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}[fmt]


@app.get("/api/suno-info")
def suno_info(url: str = Query(..., description="Link bài hát Suno")):
    meta = resolve_suno(url)
    return {
        "url": meta["url"],
        "id": meta["id"],
        "title": meta["title"],
        "artist": meta["artist"],
        "cover_url": meta["cover_url"],
        "duration": meta["duration"],
        "source_count": meta["source_count"],
    }


@app.get("/api/proxy-audio")
def proxy_audio(
    id: str = Query(..., description="UUID bài hát Suno"),
    format: str = Query("mp3", pattern="^(mp3|wav|m4a)$"),
    normalize: bool = Query(False),
    mono: bool = Query(False),
    start: float | None = Query(None, ge=0),
    end: float | None = Query(None, ge=0),
):
    if not re.fullmatch(UUID_PATTERN, id, re.I):
        raise HTTPException(status_code=400, detail="ID bài hát không hợp lệ")
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=400, detail="end phải lớn hơn start")

    meta = resolve_suno(f"https://suno.com/song/{id}")
    if not meta["audio_urls"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Trang không cung cấp file audio công khai hoàn chỉnh.",
                "attempts": [],
            },
        )

    key = _cache_key(id, format, normalize, mono, start, end)
    output = CACHE_DIR / f"{key}.{format}"
    lock = _get_cache_lock(key)

    with lock:
        valid, _ = _validate_audio(output, full_decode=False) if output.exists() and output.stat().st_size > 16384 else (False, "")
        if valid:
            return FileResponse(
                output,
                filename=f"[Suno] {meta['title']}.{format}",
                media_type=_media_type(format),
                headers={"X-Suno-Cache": "HIT"},
            )

        attempts: list[dict] = []
        with tempfile.TemporaryDirectory(prefix="suno_api_") as tmp:
            for idx, audio_url in enumerate(meta["audio_urls"], 1):
                source = Path(tmp) / f"source_{idx}"
                host = urlparse(audio_url).netloc
                attempt = {"index": idx, "host": host, "status": "failed"}
                try:
                    info = _download_public_audio(audio_url, source)
                    attempt.update({
                        "size_mb": round(info["size_mb"], 3),
                        "content_type": info["content_type"],
                    })
                    diagnosis = _diagnose_source(source, info["content_type"])
                    attempt["diagnosis"] = diagnosis
                    valid_source, source_err = _validate_audio(source, full_decode=False)
                    if not valid_source:
                        raise SourceError(diagnosis or source_err)

                    _transcode(
                        source,
                        output,
                        format,
                        normalize=normalize,
                        mono=mono,
                        start=start,
                        end=end,
                    )
                    valid_output, output_err = _validate_audio(output, full_decode=True)
                    if not valid_output:
                        try:
                            output.unlink()
                        except OSError:
                            pass
                        raise SourceError(f"File đầu ra không hợp lệ: {output_err}")

                    attempt["status"] = "ok"
                    attempts.append(attempt)
                    return FileResponse(
                        output,
                        filename=f"[Suno] {meta['title']}.{format}",
                        media_type=_media_type(format),
                        headers={
                            "X-Suno-Cache": "MISS",
                            "X-Suno-Source-Host": info["host"],
                        },
                    )
                except Exception as exc:
                    attempt["error"] = re.sub(r"\s+", " ", str(exc)).strip()[-500:]
                    attempts.append(attempt)
                    try:
                        if output.exists():
                            output.unlink()
                    except OSError:
                        pass

    raise HTTPException(
        status_code=409,
        detail={
            "message": "Không có nguồn audio công khai hoàn chỉnh có thể chuyển đổi.",
            "attempts": attempts,
        },
    )


@app.get("/api/cache-info")
def cache_info():
    files = [p for p in CACHE_DIR.glob("*") if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in files)
    return {
        "files": len(files),
        "size_mb": round(total_bytes / (1024 * 1024), 2),
        "path": str(CACHE_DIR),
    }


@app.delete("/api/cache")
def clear_cache():
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
    return {"ok": True, "removed": removed}


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "ffmpeg": os.path.basename(FFMPEG),
        "scope": "public-direct-audio-only",
    }
