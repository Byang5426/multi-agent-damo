"""多Agent系统 FastAPI 应用入口。"""

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from multi_agent.api.routes import router
from multi_agent.config import settings
from multi_agent.defaults.prompts import DEFAULT_PROMPTS
from multi_agent.prompt_loader import init_prompt_loader
from multi_agent.store.pg_store import PgStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 全局持久化存储实例（单例）
store = PgStore(
    dsn=settings.pg_dsn,
    min_connections=settings.pg_min_connections,
    max_connections=settings.pg_max_connections,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化数据库，关闭时释放连接。"""
    await store.initialize()
    logger.info("Database initialized (PostgreSQL)")

    # 初始化 Prompt 加载器，并种子填充默认 Prompt
    init_prompt_loader(store)
    seeded = await store.seed_prompts(DEFAULT_PROMPTS)
    logger.info("Seeded %d default prompts", seeded)

    yield
    await store.close()
    logger.info("Database connection closed")


app = FastAPI(
    title="多Agent系统",
    description="企业级多Agent工作流编排系统，支持即时任务、项目型任务的自动拆解与协作执行。",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """请求ID中间件：为每个请求注入唯一的 request_id，用于链路追踪。"""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(router, prefix="/api/v1")
app.state.store = store

# 项目根目录（用于静态文件）
ROOT_DIR = Path(__file__).parent.parent.parent


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/admin")
async def serve_admin():
    """管理控制台页面。"""
    return FileResponse(ROOT_DIR / "admin.html", media_type="text/html")


@app.get("/")
async def serve_index():
    """C端用户页面。"""
    return FileResponse(ROOT_DIR / "index.html", media_type="text/html")


if __name__ == "__main__":
    uvicorn.run(
        "multi_agent.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
