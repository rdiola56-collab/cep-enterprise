"""CEP: ENTERPRISE API application (modular monolith entry point)."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from .api.routes import router

app = FastAPI(title="CEP: ENTERPRISE", version="0.1.0", description="Universal Estimating Engine API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CEP_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
