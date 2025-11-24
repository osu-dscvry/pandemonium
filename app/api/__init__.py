from fastapi import FastAPI
from .beatmaps import router as beatmaps_router
from .oauth import router as oauth_router
from .discovery import router as discovery_router

def init() -> FastAPI:
    app = FastAPI()

    app.include_router(beatmaps_router)
    app.include_router(oauth_router)
    app.include_router(discovery_router)

    return app

app = init()