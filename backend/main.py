import os
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from downloader import VideoDownloader
from douyin import DouyinParser, is_douyin_url, normalize_media_url
from database import init_db, get_db_backend


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

def _allowed_origins() -> list[str]:
    raw = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
    if raw:
        return [v.strip() for v in raw.split(",") if v.strip()]
    return [
        "https://video.sayhi-ab.asia",
        "https://sayhi-ab.asia",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
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


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "message": "万能视频下载器服务运行中",
        "infra": {
            "db_backend": get_db_backend(),
            "domain": os.getenv("APP_PUBLIC_DOMAIN", "video.sayhi-ab.asia"),
        },
    }


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

        return FileResponse(
            path=filepath,
            filename=result["filename"],
            media_type="application/octet-stream",
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
        target = unquote(url) if "%" in url else url
        host = (urlparse(target).netloc or "").lower()
        # 抖音图床校验 Referer；用图片 URL 自身作 Referer 常被拒
        if "douyinpic.com" in host or "douyin.com" in host or "douyin.cn" in host or "iesdouyin.com" in host:
            referer = "https://www.douyin.com/"
        else:
            referer = target
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(target, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
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
from api_asr_pull import router as asr_pull_router

app.include_router(summarize_router)
app.include_router(auth_router)
app.include_router(payment_router)
app.include_router(bulk_download_router)
app.include_router(asr_pull_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
