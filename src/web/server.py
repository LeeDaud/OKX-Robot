"""
FastAPI 服务入口。
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.web.routes import config, trades, positions


@asynccontextmanager
async def lifespan(app: FastAPI):
    from dotenv import load_dotenv
    load_dotenv()
    yield


app = FastAPI(title="Auto Trader Manager", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(positions.router, prefix="/api")

# 生产环境：前端构建产物 — SPA fallback
import os
_frontend_dist = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_frontend_dist):
    from starlette.responses import FileResponse

    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except (HTTPException, Exception) as ex:
                if isinstance(ex, HTTPException) and ex.status_code == 404:
                    return FileResponse(os.path.join(self.directory, "index.html"))
                raise

    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")
