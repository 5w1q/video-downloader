"""
阿里云百炼 DashScope Paraformer 文件转写（无平台字幕时的备用方案）。
模型推荐：paraformer-v2（中文+多语种，适合视频总结）。

依赖环境变量：
  DASHSCOPE_API_KEY  — 百炼 API Key（北京地域）
  PUBLIC_BASE_URL    — 站点公网 HTTPS 根地址（如 https://video.example.com），
                        供阿里云服务器下载音频；不可用 localhost。

可选：ALIYUN_ASR_MODEL（默认 paraformer-v2）、ALIYUN_ASR_MAX_SECONDS（默认 3600=60 分钟）、ALIYUN_ASR_ENABLED
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from http import HTTPStatus
from typing import Any, Optional

import httpx
import yt_dlp

from douyin import DouyinParser, is_douyin_url, normalize_media_url
from downloader import _ytdlp_base_opts

logger = logging.getLogger(__name__)


def _runtime_frontend_url() -> str:
    """
    运行时前端域名：
    - 显式设置 FRONTEND_URL / PUBLIC_BASE_URL 优先
    - 生产环境默认 https://video.sayhi-ab.asia
    - 其他环境默认 http://localhost:5173
    """
    explicit = (os.getenv("PUBLIC_BASE_URL") or os.getenv("FRONTEND_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    env = (os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    if env in ("prod", "production"):
        return "https://video.sayhi-ab.asia"
    return "http://localhost:5173"


def _asr_enabled() -> bool:
    if os.getenv("ALIYUN_ASR_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    if not (os.getenv("DASHSCOPE_API_KEY") or "").strip():
        return False
    base = _runtime_frontend_url()
    if not base:
        return False
    low = base.lower()
    if "localhost" in low or "127.0.0.1" in low or "0.0.0.0" in low:
        return False
    return True


def _public_pull_url(token: str) -> str:
    base = _runtime_frontend_url()
    return f"{base}/api/asr-audio-pull/{token}"


def _download_audio_clip(page_url: str) -> tuple[str, str] | None:
    """返回 (m4a_path, workdir)；失败返回 None。"""
    page_url = normalize_media_url(page_url)
    workdir = tempfile.mkdtemp(prefix="asr_aliyun_")
    max_sec = int(os.getenv("ALIYUN_ASR_MAX_SECONDS", "3600"))
    # 长片段转码可能较久，超时与 max_sec 挂钩并设上限，避免子进程无限挂起
    ff_timeout = max(1200, min(max_sec * 3, 10800))
    try:
        if is_douyin_url(page_url):
            parser = DouyinParser(download_dir=workdir)
            r = parser.download(page_url)
            src = r.get("filepath") or ""
            if not src or not os.path.isfile(src):
                return None
            out_clip = os.path.join(workdir, "clip.m4a")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    src,
                    "-t",
                    str(max_sec),
                    "-vn",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    out_clip,
                ],
                check=True,
                capture_output=True,
                timeout=ff_timeout,
            )
            try:
                os.unlink(src)
            except OSError:
                pass
            return (out_clip, workdir) if os.path.isfile(out_clip) else None

        outtmpl = os.path.join(workdir, "aud.%(ext)s")
        opts: dict[str, Any] = {
            **_ytdlp_base_opts(page_url),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "format": "ba/b/worstaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "128",
                }
            ],
            "socket_timeout": 180,
            "retries": 2,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([page_url])
        m4a_files = [
            os.path.join(workdir, n)
            for n in os.listdir(workdir)
            if n.endswith(".m4a") and os.path.isfile(os.path.join(workdir, n))
        ]
        if not m4a_files:
            return None
        audio_path = m4a_files[0]
        if max_sec > 0:
            clip = os.path.join(workdir, "trim.m4a")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    audio_path,
                    "-t",
                    str(max_sec),
                    "-vn",
                    "-c:a",
                    "copy",
                    clip,
                ],
                check=True,
                capture_output=True,
                timeout=ff_timeout,
            )
            try:
                os.unlink(audio_path)
            except OSError:
                pass
            audio_path = clip
        return audio_path, workdir
    except Exception as e:
        logger.warning("ASR 音频准备失败: %s", e)
        shutil.rmtree(workdir, ignore_errors=True)
        return None


def _parse_transcription_json(data: dict) -> tuple[list[dict], str]:
    segments: list[dict] = []
    texts: list[str] = []
    for tr in data.get("transcripts") or []:
        if not isinstance(tr, dict):
            continue
        for sent in tr.get("sentences") or []:
            if not isinstance(sent, dict):
                continue
            t = (sent.get("text") or "").strip()
            if not t:
                continue
            try:
                st = float(sent.get("begin_time", 0)) / 1000.0
                et = float(sent.get("end_time", 0)) / 1000.0
            except (TypeError, ValueError):
                st, et = 0.0, 0.0
            if et <= st:
                et = st + 0.3
            segments.append({"start": round(st, 2), "end": round(et, 2), "text": t})
            texts.append(t)
    return segments, " ".join(texts).strip()


def _results_from_output(fout: Any) -> list:
    if fout is None:
        return []
    if isinstance(fout, dict):
        return fout.get("results") or []
    return getattr(fout, "results", None) or []


def _subtask_url(r0: Any) -> tuple[str | None, str | None]:
    if isinstance(r0, dict):
        return r0.get("subtask_status"), r0.get("transcription_url")
    return getattr(r0, "subtask_status", None), getattr(r0, "transcription_url", None)


def _output_obj(resp: Any) -> Any:
    if resp is None:
        return None
    if isinstance(resp, dict):
        return resp.get("output")
    return getattr(resp, "output", None)


def _task_id_from_submit(resp: Any) -> str | None:
    out = _output_obj(resp)
    if out is None:
        return None
    if isinstance(out, dict):
        tid = out.get("task_id")
        return str(tid) if tid else None
    tid = getattr(out, "task_id", None)
    return str(tid) if tid else None


def try_paraformer_transcribe(page_url: str) -> Optional[dict]:
    if not _asr_enabled():
        return None

    from asr_temp_store import abandon_token, register_audio

    try:
        import dashscope
        from dashscope.audio.asr import Transcription
    except ImportError:
        logger.warning("未安装 dashscope，跳过阿里云 ASR")
        return None

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"].strip()
    model = (os.getenv("ALIYUN_ASR_MODEL") or "paraformer-v2").strip()

    workdir: str | None = None
    token: str | None = None
    bundle = _download_audio_clip(page_url)
    if not bundle:
        return None
    audio_path, workdir = bundle
    try:
        token = register_audio(audio_path, media_type="audio/mp4")
        public_url = _public_pull_url(token)
        task_response = Transcription.async_call(
            model=model,
            file_urls=[public_url],
            language_hints=["zh", "en"],
        )
        sc = getattr(task_response, "status_code", None) or (
            task_response.get("status_code") if isinstance(task_response, dict) else None
        )
        if sc != HTTPStatus.OK:
            logger.warning(
                "DashScope 提交失败: %s %s",
                sc,
                getattr(task_response, "message", "") or (
                    task_response.get("message") if isinstance(task_response, dict) else ""
                ),
            )
            return None
        tid = _task_id_from_submit(task_response)
        if not tid:
            logger.warning("DashScope 无 task_id")
            return None
        final = Transcription.wait(task=tid)
        fsc = getattr(final, "status_code", None) or (
            final.get("status_code") if isinstance(final, dict) else None
        )
        if fsc != HTTPStatus.OK:
            logger.warning("DashScope wait 失败: %s", fsc)
            return None
        results = _results_from_output(_output_obj(final))
        if not results:
            return None
        st, turl = _subtask_url(results[0])
        if st != "SUCCEEDED" or not turl:
            logger.warning("DashScope 子任务: %s %s", st, turl)
            return None
        tr = httpx.get(turl, timeout=120, follow_redirects=True)
        tr.raise_for_status()
        payload = tr.json()
        segments, full_text = _parse_transcription_json(payload)
        if not full_text:
            return None
        return {
            "has_subtitle": True,
            "language": "zh",
            "subtitle_type": "auto",
            "segments": segments,
            "full_text": full_text,
        }
    except Exception as e:
        logger.warning("阿里云 ASR 失败: %s", e)
        return None
    finally:
        if token:
            abandon_token(token)
        if workdir and os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
