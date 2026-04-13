import os
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import unquote

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

from downloader import VideoDownloader
from douyin import DouyinParser, is_douyin_url, normalize_media_url
from database import init_db


downloader = VideoDownloader()
douyin_parser = DouyinParser(download_dir=downloader.DOWNLOAD_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    if os.getenv("PERSIST_DOWNLOADS", "").lower() in ("1", "true", "yes"):
        return
    download_dir = downloader.DOWNLOAD_DIR
    if os.path.exists(download_dir):
        for f in os.listdir(download_dir):
            try:
                os.remove(os.path.join(download_dir, f))
            except OSError:
                pass


app = FastAPI(
    title="万能视频下载器 API",
    description="基于 yt-dlp 的万能视频下载服务，支持 1800+ 平台",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ParseRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    format_id: str = "bestvideo+bestaudio/best"
    # True：文件留在服务端 downloads，返回 JSON（批量脚本 / n8n）；False：返回文件流给浏览器
    return_json: bool = False
    # True：在响应发送完毕后删除服务端临时文件（浏览器另存为场景，减少磁盘占用）
    delete_after_send: bool = False


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "万能视频下载器服务运行中"}


@app.post("/api/parse")
async def parse_video(req: ParseRequest):
    """解析视频信息（抖音走专用模块，其他走 yt-dlp）"""
    try:
        loop = asyncio.get_event_loop()
        url = normalize_media_url(req.url)
        if is_douyin_url(url):
            result = await loop.run_in_executor(None, douyin_parser.parse, url)
        else:
            result = await loop.run_in_executor(None, downloader.parse_video, url)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "success": False,
            "error": f"解析失败: {str(e)}"
        })


@app.post("/api/download")
async def download_video(req: DownloadRequest):
    """服务端下载视频后提供文件下载（抖音走专用模块）"""
    try:
        loop = asyncio.get_event_loop()
        url = normalize_media_url(req.url)
        if is_douyin_url(url):
            result = await loop.run_in_executor(None, douyin_parser.download, url)
        else:
            result = await loop.run_in_executor(
                None, downloader.download_video, url, req.format_id
            )
        filepath = result["filepath"]
        if not os.path.exists(filepath):
            raise HTTPException(status_code=500, detail="下载的文件不存在")

        if req.return_json:
            return {
                "success": True,
                "data": {
                    "filename": result["filename"],
                    "title": result.get("title", ""),
                },
            }

        bg = None
        if req.delete_after_send:
            def _unlink_safe(p: str) -> None:
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass

            bg = BackgroundTask(_unlink_safe, filepath)

        return FileResponse(
            path=filepath,
            filename=result["filename"],
            media_type="application/octet-stream",
            background=bg,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "success": False,
            "error": f"下载失败: {str(e)}"
        })


@app.post("/api/direct-url")
async def get_direct_url(req: DownloadRequest):
    """获取视频直链"""
    try:
        loop = asyncio.get_event_loop()
        url = normalize_media_url(req.url)
        result = await loop.run_in_executor(
            None, downloader.get_direct_url, url, req.format_id
        )
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "success": False,
            "error": f"获取直链失败: {str(e)}"
        })


@app.get("/api/proxy/thumbnail")
async def proxy_thumbnail(url: str = Query(..., description="缩略图URL")):
    """代理获取视频缩略图，绕过防盗链"""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": url,
            })
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        raise HTTPException(status_code=502, detail="缩略图加载失败")


# 挂载功能模块路由
from api_summarize import router as summarize_router
from api_auth import router as auth_router
from api_payment import router as payment_router
from api_bulk_download import router as bulk_download_router

app.include_router(summarize_router)
app.include_router(auth_router)
app.include_router(payment_router)
app.include_router(bulk_download_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
