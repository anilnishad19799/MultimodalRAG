from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.container import build_services
from app.core.auth import current_user
from app.core.errors import RAGError
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.query.graph import LangGraphChatRouter

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, services=None, session_factory=None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(config.log_level)
        app.state.session_factory = session_factory or get_session_factory()
        app.state.services = services or build_services(config)
        app.state.settings = config
        app.state.chat_graph = LangGraphChatRouter(
            app.state.services, config.retrieval_limit, config.max_total_images
        )
        yield

    app = FastAPI(title="Multimodal RAG API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def require_account(request: Request, call_next):
        path = request.url.path
        public = path in {"/api/v1/health", "/api/v1/auth/signup", "/api/v1/auth/signin", "/"} or path.startswith("/static/")
        is_api = path.startswith("/api/v1/")
        if is_api and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin is None or origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})
        if is_api and not public:
            try:
                account = current_user(request)
                request.state.owner_id = account.id
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc, HTTPException):
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
                raise
        return await call_next(request)

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def web_ui() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.exception_handler(RAGError)
    async def handle_rag_error(_: Request, exc: RAGError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    return app


app = create_app()
