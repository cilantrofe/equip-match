from app.db.session import async_session
from app.db.crud import get_product_by_sku, get_products_in_category
from app.matching.matcher import match_by_price, match_by_tech


async def lookup_product(sku: str):
    async with async_session() as session:
        target = await get_product_by_sku(session, sku)
        if not target:
            return None
        candidates = await get_products_in_category(session, target.category or "")
        for c in candidates[:5]:
            print("DEBUG:", c.id, c.category)
        candidates = [c for c in candidates if c.id != target.id]
        price_candidate, price_score = match_by_price(target, candidates)
        tech_candidate, tech_score = match_by_tech(target, candidates)

        def to_dict(p, score=0):
            if not p:
                return None
            return {
                "id": p.id,
                "sku": p.source_sku,
                "category": p.category,
                "brand": p.brand,
                "model": p.model,
                "price": float(p.price) if p.price else None,
                "score": score,
                "url": p.url,
            }

        return {
            "query": {
                "id": target.id,
                "sku": target.source_sku,
                "category": target.category,
                "brand": target.brand,
                "model": target.model,
                "price": float(target.price),
                "url": target.url,
            },
            "price_candidate": to_dict(price_candidate, price_score),
            "tech_candidate": to_dict(tech_candidate, tech_score),
        }
