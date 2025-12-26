# backend/app/main.py
from fastapi import FastAPI
from .api import router as api_router

app = FastAPI(title="PDF Swift Extractor API", version="0.1")
app.include_router(api_router, prefix="")
