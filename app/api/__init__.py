from fastapi import FastAPI
from .beatmaps import router as beatmaps_router

def init() -> FastAPI:
    app = FastAPI()

    app.include_router(beatmaps_router)

    return app

app = init()