"""
阿里云百炼语音转写（无平台字幕时的备用方案）。

公网部署：Paraformer 文件转写（需 PUBLIC_BASE_URL，供阿里云拉取音频）。
本机 / localhost：改用 qwen3-asr-flash + 本地 file:// 上传（无需公网域名）。

依赖环境变量：
  DASHSCOPE_API_KEY  — 百炼 API Key（北京地域；可回退 SUMMARIZE_LLM_API_KEY）
  PUBLIC_BASE_URL    — 公网 HTTPS 根地址（仅 Paraformer 公网拉取模式需要）

可选：ALIYUN_ASR_MODEL、ALIYUN_ASR_LOCAL_MODEL、ALIYUN_ASR_MAX_SECONDS、ALIYUN_ASR_ENABLED
"""

from __future__ import annotations

import logging
import os
import re
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


def _dashscope_api_key() -> str:
    return (
        os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("SUMMARIZE_LLM_API_KEY")
        or ""
    ).strip()


def _public_base_usable() -> bool:
    base = _runtime_frontend_url()
    if not base:
        return False
    low = base.lower()
    return not ("localhost" in low or "127.0.0.1" in low or "0.0.0.0" in low)


def _asr_enabled() -> bool:
    if os.getenv("ALIYUN_ASR_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    if not _dashscope_api_key():
        return False
    # 本机可用本地 file://；公网用 Paraformer 拉取 URL
    return True


def _public_pull_url(token: str) -> str:
    base = _runtime_frontend_url()
    return f"{base}/api/asr-audio-pull/{token}"


def _asr_max_seconds(*, local_mode: bool) -> int:
    try:
        configured = int(os.getenv("ALIYUN_ASR_MAX_SECONDS", "3600"))
    except ValueError:
        configured = 3600
    # qwen3-asr-flash 官方上限约 5 分钟
    if local_mode:
        try:
            local_cap = int(os.getenv("ALIYUN_ASR_LOCAL_MAX_SECONDS", "300"))
        except ValueError:
            local_cap = 300
        return max(30, min(configured, local_cap, 300))
    return max(30, configured)


def _ffmpeg_to_wav_16k(src: str, workdir: str, *, max_sec: int) -> Optional[str]:
    """转成 16kHz 单声道 PCM WAV（qwen3-asr-flash 对 m4a/aac 常报 format illegal）。"""
    if not src or not os.path.isfile(src):
        return None
    out = os.path.join(workdir, "asr_16k.wav")
    ff_timeout = max(120, min(max_sec * 3, 1800))
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                src,
                "-t",
                str(max_sec),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                out,
            ],
            check=True,
            capture_output=True,
            timeout=ff_timeout,
        )
    except Exception as e:
        logger.warning("ASR 转 WAV 失败: %s", e)
        return None
    if not os.path.isfile(out) or os.path.getsize(out) < 1000:
        logger.warning("ASR WAV 无效或过小: %s", out)
        return None
    return out


def _download_audio_clip(page_url: str, *, max_sec: int) -> tuple[str, str] | None:
    """下载并抽出音频，返回 (audio_path, workdir)；失败返回 None。"""
    page_url = normalize_media_url(page_url)
    workdir = tempfile.mkdtemp(prefix="asr_aliyun_")
    ff_timeout = max(1200, min(max_sec * 3, 10800))
    try:
        src: str | None = None
        if is_douyin_url(page_url):
            from pathlib import Path

            parser = DouyinParser(download_dir=workdir)
            last_err: Exception | None = None
            src = None
            # 优先 parse → CDN 直链下载（与 /api/parse 同路径，比二次解析短链更稳）
            try:
                info = parser.parse(page_url)
                direct = ""
                for f in info.get("formats") or []:
                    u = (f.get("_direct_url") or "").strip()
                    if u.startswith("http"):
                        direct = u
                        break
                if direct:
                    src_path = Path(workdir) / "src.mp4"
                    parser._download_file(direct, src_path)
                    if src_path.is_file() and src_path.stat().st_size > 1000:
                        src = str(src_path)
            except Exception as e:
                last_err = e
                logger.info("抖音 ASR 经 parse+CDN 失败: %s", e)

            if not src:
                for mode in ("video", "audio"):
                    try:
                        r = parser.download(page_url, mode=mode)
                        cand = r.get("filepath") or ""
                        if cand and os.path.isfile(cand) and os.path.getsize(cand) > 1000:
                            src = cand
                            break
                    except Exception as e:
                        last_err = e
                        logger.info("抖音 ASR 下载 mode=%s 失败: %s", mode, e)

            if not src:
                raise RuntimeError(last_err or "抖音音频下载失败")

            wav = _ffmpeg_to_wav_16k(src, workdir, max_sec=max_sec)
            try:
                if src and os.path.isfile(src):
                    os.unlink(src)
            except OSError:
                pass
            if not wav:
                return None
            return (wav, workdir)

        outtmpl = os.path.join(workdir, "aud.%(ext)s")
        opts = {
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
            "download_ranges": yt_dlp.utils.download_range_func(None, [(0, max_sec)]),
            "force_keyframes_at_cuts": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([page_url])
        m4a = None
        for name in os.listdir(workdir):
            if name.endswith(".m4a"):
                m4a = os.path.join(workdir, name)
                break
        if not m4a or not os.path.isfile(m4a):
            # 可能留下原始音视频，再转 wav
            for name in os.listdir(workdir):
                p = os.path.join(workdir, name)
                if os.path.isfile(p) and name.startswith("aud.") and os.path.getsize(p) > 1000:
                    wav = _ffmpeg_to_wav_16k(p, workdir, max_sec=max_sec)
                    return (wav, workdir) if wav else None
            return None
        wav = _ffmpeg_to_wav_16k(m4a, workdir, max_sec=max_sec)
        return (wav, workdir) if wav else (m4a, workdir)
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


def _file_uri(path: str) -> str:
    abs_path = os.path.abspath(path).replace("\\", "/")
    if not abs_path.startswith("/"):
        abs_path = "/" + abs_path
    return f"file://{abs_path}"


def _segments_from_plain_text(text: str) -> tuple[list[dict], str]:
    full = (text or "").strip()
    if not full:
        return [], ""
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;])\s*", full) if p.strip()]
    if not parts:
        parts = [full]
    segments: list[dict] = []
    t = 0.0
    for p in parts:
        # 粗估语速，避免前端把「单条 0:00」误判成简介占位
        dur = max(1.5, min(12.0, len(p) * 0.18))
        segments.append({"start": round(t, 2), "end": round(t + dur, 2), "text": p})
        t += dur
    return segments, full


def _extract_text_from_mm_response(resp: Any) -> str:
    out = _output_obj(resp)
    if out is None:
        return ""
    choices = None
    if isinstance(out, dict):
        choices = out.get("choices")
    else:
        choices = getattr(out, "choices", None)
    if not choices:
        if isinstance(out, dict):
            return str(out.get("text") or "").strip()
        return str(getattr(out, "text", "") or "").strip()
    c0 = choices[0]
    msg = c0.get("message") if isinstance(c0, dict) else getattr(c0, "message", None)
    if msg is None:
        return ""
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                chunks.append(item.strip())
            elif isinstance(item, dict):
                t = (item.get("text") or item.get("transcript") or "").strip()
                if t:
                    chunks.append(t)
        return "\n".join(chunks).strip()
    return str(content or "").strip()


def _transcribe_local_file(audio_path: str) -> Optional[dict]:
    """本机模式：qwen3-asr-flash + file://，由 SDK 上传，无需公网 PUBLIC_BASE_URL。"""
    try:
        import dashscope
    except ImportError:
        logger.warning("未安装 dashscope，跳过本地 ASR")
        return None

    api_key = _dashscope_api_key()
    model = (os.getenv("ALIYUN_ASR_LOCAL_MODEL") or "qwen3-asr-flash").strip()
    uri = _file_uri(audio_path)
    messages = [{"role": "user", "content": [{"audio": uri}]}]
    try:
        resp = dashscope.MultiModalConversation.call(
            api_key=api_key,
            model=model,
            messages=messages,
            result_format="message",
            asr_options={"enable_itn": False, "language": "zh"},
        )
    except Exception as e:
        logger.warning("本地 ASR 调用失败: %s", e)
        return None

    sc = getattr(resp, "status_code", None) or (
        resp.get("status_code") if isinstance(resp, dict) else None
    )
    if sc not in (None, HTTPStatus.OK, 200):
        msg = getattr(resp, "message", "") or (
            resp.get("message") if isinstance(resp, dict) else ""
        )
        logger.warning("本地 ASR 提交失败: %s %s", sc, msg)
        return None

    text = _extract_text_from_mm_response(resp)
    segments, full_text = _segments_from_plain_text(text)
    if not full_text:
        logger.warning("本地 ASR 未返回文本")
        return None
    return {
        "has_subtitle": True,
        "language": "zh",
        "subtitle_type": "auto",
        "subtitle_source": "aliyun_asr",
        "segments": segments,
        "full_text": full_text,
    }


def _transcribe_via_public_url(audio_path: str) -> Optional[dict]:
    """公网模式：Paraformer 异步转写，阿里云拉取 /api/asr-audio-pull/{token}。"""
    from asr_temp_store import abandon_token, register_audio

    try:
        import dashscope
        from dashscope.audio.asr import Transcription
    except ImportError:
        logger.warning("未安装 dashscope，跳过阿里云 ASR")
        return None

    dashscope.api_key = _dashscope_api_key()
    model = (os.getenv("ALIYUN_ASR_MODEL") or "paraformer-v2").strip()
    token: str | None = None
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
                getattr(task_response, "message", "")
                or (
                    task_response.get("message")
                    if isinstance(task_response, dict)
                    else ""
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
            "subtitle_source": "aliyun_asr",
            "segments": segments,
            "full_text": full_text,
        }
    finally:
        if token:
            abandon_token(token)


def try_paraformer_transcribe(page_url: str) -> Optional[dict]:
    if not _asr_enabled():
        return None

    local_mode = not _public_base_usable()
    max_sec = _asr_max_seconds(local_mode=local_mode)
    workdir: str | None = None
    bundle = _download_audio_clip(page_url, max_sec=max_sec)
    if not bundle:
        return None
    audio_path, workdir = bundle
    try:
        if local_mode:
            # 下载阶段已尽量转成 wav；若仍是其它格式再转一次
            wav = audio_path
            if not str(audio_path).lower().endswith(".wav"):
                wav = _ffmpeg_to_wav_16k(audio_path, workdir, max_sec=max_sec)
            if not wav:
                logger.warning("本地 ASR：WAV 转码失败，跳过转写")
                return None
            logger.info(
                "ASR 使用本机 file:// 模式（qwen3-asr-flash），wav=%s bytes=%s",
                wav,
                os.path.getsize(wav),
            )
            return _transcribe_local_file(wav)
        # 公网 Paraformer：wav 也更稳
        send_path = audio_path
        if not str(audio_path).lower().endswith((".wav", ".mp3", ".m4a")):
            send_path = _ffmpeg_to_wav_16k(audio_path, workdir, max_sec=max_sec) or audio_path
        return _transcribe_via_public_url(send_path)
    except Exception as e:
        logger.warning("阿里云 ASR 失败: %s", e)
        return None
    finally:
        if workdir and os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
