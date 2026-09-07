import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

import suno_engine_v5 as engine

APP_VERSION = "5.0.0"
app = FastAPI(title="Suno Audio Converter API", version=APP_VERSION)


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "engine": engine.ENGINE_VERSION,
        "ffmpeg": os.path.basename(engine.FFMPEG),
        "scope": "public-direct-audio-only",
    }


@app.get("/api/suno-info")
def suno_info(url: str = Query(..., description="Link bài hát Suno")):
    try:
        meta = engine.resolve_suno(url)
        return {
            "url": meta["url"],
            "id": meta["id"],
            "title": meta["title"],
            "artist": meta["artist"],
            "cover_url": meta["cover_url"],
            "duration": meta["duration"],
            "source_count": meta["source_count"],
        }
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/proxy-audio")
def proxy_audio(
    id: str = Query(..., description="UUID bài hát Suno"),
    format: str = Query("mp3", pattern="^(mp3|wav|m4a)$"),
    normalize: bool = Query(False),
    mono: bool = Query(False),
    start: float | None = Query(None, ge=0),
    end: float | None = Query(None, ge=0),
):
    try:
        result = engine.get_or_convert(
            id,
            fmt=format,
            normalize=normalize,
            mono=mono,
            start=start,
            end=end,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    path = Path(result["path"])
    title = result["meta"].get("title") or "audio"
    media = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}[format]
    headers = {"X-Suno-Cache": result.get("cache", "")}
    if result.get("source_host"):
        headers["X-Suno-Source-Host"] = result["source_host"]
    return FileResponse(
        path,
        filename=f"[Suno] {title}.{format}",
        media_type=media,
        headers=headers,
    )


@app.get("/api/cache-info")
def cache_info():
    return engine.cache_info()


@app.delete("/api/cache")
def clear_cache():
    return {"ok": True, "removed": engine.clear_cache()}
