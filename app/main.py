from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.core.database import close_driver
from app.services.scheduler import start_scheduler, stop_scheduler
from app.routes import chat, modules, recommendations, jobs, articles, module_articles, topics, auth, student, engagement, admin, parent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LearNexus backend starting up")
    start_scheduler()
    yield
    stop_scheduler()
    close_driver()
    logger.info("LearNexus backend shut down")


app = FastAPI(title="LearNexus API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "An internal server error occurred"},
    )


app.include_router(chat.router, tags=["chat"])
app.include_router(modules.router, tags=["modules"])
app.include_router(topics.router, tags=["topics"])
app.include_router(articles.router, tags=["articles"])
app.include_router(module_articles.router, tags=["module-articles"])
app.include_router(auth.router)
app.include_router(student.router)
app.include_router(engagement.router)
app.include_router(recommendations.router, prefix="/api", tags=["recommendations"])
app.include_router(jobs.router, prefix="/api", tags=["jobs"])
app.include_router(admin.router)
app.include_router(parent.router)
