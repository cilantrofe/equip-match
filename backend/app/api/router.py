from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.services.lookup import lookup_product

router = APIRouter()

class MatchItem(BaseModel):
    id: int
    sku: Optional[str]
    brand: Optional[str]
    model: Optional[str]
    price: Optional[float]
    score: Optional[float]

class LookupResponse(BaseModel):
    query: Dict[str, Any]
    price_candidate: Optional[MatchItem]
    tech_candidate: Optional[MatchItem]

@router.get("/lookup", response_model=LookupResponse)
async def lookup(sku: str):
    result = await lookup_product(sku)
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return result
