from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import songs, voice_profile, studio, performances
from app.services.jobs import recover_interrupted_jobs
from app.services.personal_model import recover_training
from app.services.samples import recover_samples
from app.services.singing_guides import recover as recover_guides


@asynccontextmanager
async def lifespan(app):
    recover_interrupted_jobs()
    recover_training()
    recover_samples()
    recover_guides()
    performances.service.recover()
    yield


app = FastAPI(title="Your Voice, Your Song", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[f"http://{host}:{port}"
                   for host in ("localhost", "127.0.0.1") for port in (3000, 3001, 3002)],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(voice_profile.router, prefix="/api/voice-profile")
app.include_router(songs.router, prefix="/api/songs")
app.include_router(studio.router, prefix="/api/studio")
app.include_router(performances.router, prefix="/api/performances")


@app.get("/health")
def health():
    return {"status": "ok", "engine": "RVC personal training", "version": "0.3.0"}
