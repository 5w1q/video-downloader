"""公网可访问的短时音频 URL（供阿里云 Paraformer 拉取）。"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from asr_temp_store import cleanup_after_response, pop_audio

router = APIRouter(prefix="/api", tags=["ASR"])


@router.get("/asr-audio-pull/{token}")
async def asr_audio_pull(token: str, background_tasks: BackgroundTasks):
    row = pop_audio(token)
    if not row:
        raise HTTPException(status_code=404, detail="无效或已过期")
    path, media_type = row
    background_tasks.add_task(cleanup_after_response, path)
    return FileResponse(path, media_type=media_type, filename="audio.m4a")
