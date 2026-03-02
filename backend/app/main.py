from fastapi import FastAPI
from app.api.router import router as api_router

app = FastAPI(title="Product Matcher")
app.include_router(api_router, prefix="/api")
