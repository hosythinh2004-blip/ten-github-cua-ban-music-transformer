import hashlib
import html as html_lib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

APP_NAME = "Suno Audio Converter API"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)
CACHE_DIR = Path.home() / ".suno_audio_converter" / "api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

app = FastAPI(title=APP_NAME, version="1.0.0")

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://suno.com/",
})


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
            value = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
            if value:
                return value
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
    p = urlparse(value.strip())
    if p.scheme not in {"http", "https"} or not p.netloc:
        return False
    low = value.lower()
    blocked_markers = (
        "/api/forbidden",
        "silence.mp3",
        "/mango/rights",
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
            except requests.RequestException:
                pass

    return {
        "url": r.url,
        "id": clip_id,
        "title": title,
        "artist": artist,
        "cover_url": cover,
        "duration": duration,
        "audio_urls": _dedupe(audio_urls),
    }


def _is_valid_audio(path: Path) -> bool:
    result = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def _download_public_audio(url: str, dest: Path) -> None:
    headers = {
        "User-Agent": UA,
        "Referer": "https://suno.com/",
        "Accept": "audio/*,video/mp4,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    try:
        with session.get(url, headers=headers, stream=True, timeout=(15, 120), allow_redirects=True) as r:
            if r.status_code in (401, 403):
                raise RuntimeError(f"Nguồn từ chối truy cập (HTTP {r.status_code})")
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                raise RuntimeError(f"Nguồn trả về {ctype}, không phải audio")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException as exc:
        raise RuntimeError(f"Tải nguồn thất bại: {exc}") from exc

    if not dest.exists() or dest.stat().st_size < 16384:
        raise RuntimeError("Nguồn quá nhỏ/placeholder")


def _transcode(source: Path, output: Path, fmt: str) -> None:
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-map", "0:a:0", "-vn"]
    if fmt == "mp3":
        cmd += ["-codec:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2", str(output)]
    elif fmt == "wav":
        cmd += ["-codec:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(output)]
    elif fmt == "m4a":
        cmd += ["-codec:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(output)]
    else:
        raise HTTPException(status_code=400, detail="format chỉ nhận mp3, wav hoặc m4a")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0 or not output.exists() or output.stat().st_size < 16384:
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg lỗi")[-1200:])


@app.get("/api/suno-info")
def suno_info(url: str = Query(..., description="Link bài hát Suno")):
    return resolve_suno(url)


@app.get("/api/proxy-audio")
def proxy_audio(
    id: str = Query(..., description="UUID bài hát Suno"),
    format: str = Query("mp3", pattern="^(mp3|wav|m4a)$"),
):
    if not re.fullmatch(UUID_PATTERN, id, re.I):
        raise HTTPException(status_code=400, detail="ID bài hát không hợp lệ")

    meta = resolve_suno(f"https://suno.com/song/{id}")
    if not meta["audio_urls"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "Trang không cung cấp file audio công khai hoàn chỉnh. "
                "API này không dùng guest token, rights endpoint hoặc giải mã DRM."
            ),
        )

    cache_key = hashlib.sha256(f"{id}|{format}".encode("utf-8")).hexdigest()
    output = CACHE_DIR / f"{cache_key}.{format}"
    if output.exists() and output.stat().st_size > 16384 and _is_valid_audio(output):
        return FileResponse(output, filename=f"{meta['title']}.{format}")

    last_error = None
    with tempfile.TemporaryDirectory(prefix="suno_api_") as tmp:
        for idx, audio_url in enumerate(meta["audio_urls"], 1):
            source = Path(tmp) / f"source_{idx}"
            try:
                _download_public_audio(audio_url, source)
                if not _is_valid_audio(source):
                    raise RuntimeError("Nguồn tải về không phải file audio hoàn chỉnh FFmpeg đọc được")
                _transcode(source, output, format)
                if not _is_valid_audio(output):
                    try:
                        output.unlink()
                    except OSError:
                        pass
                    raise RuntimeError("File đầu ra không hợp lệ")
                return FileResponse(output, filename=f"{meta['title']}.{format}")
            except Exception as exc:
                last_error = exc
                try:
                    if output.exists():
                        output.unlink()
                except OSError:
                    pass

    raise HTTPException(
        status_code=409,
        detail=f"Không có nguồn audio công khai hoàn chỉnh có thể chuyển đổi. Lỗi cuối: {last_error}",
    )


@app.get("/health")
def health():
    return {"ok": True, "ffmpeg": os.path.basename(FFMPEG)}
