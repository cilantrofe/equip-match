import argparse
import asyncio
import json
import logging
import os
from glob import glob
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from app.db.session import async_session
from app.scrapers.base import ALLOWED_CATEGORIES, BaseScraper

BASE = "https://camerussia.com"
CATALOG_PATH = "/catalog/smart-house/"


def _load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_json_files(dir_path: str) -> list[str]:
    return sorted(glob(os.path.join(dir_path, "*.json")))


def _category_from_filename(filename: str) -> str:
    base = os.path.basename(filename).lower()
    if "abonent_ip" in base:
        return "Видеомонитор"
    return "Вызывная панель"


def extract_products_from_api_response(parsed_json: Any, filename: str = "") -> List[Dict[str, Any]]:
    if not parsed_json:
        return []

    candidates = None
    if isinstance(parsed_json, dict):
        for key in ("products", "items", "data", "result", "list"):
            val = parsed_json.get(key)
            if isinstance(val, list):
                candidates = val
                break
            if isinstance(val, dict):
                for sub in ("items", "products", "list"):
                    if isinstance(val.get(sub), list):
                        candidates = val[sub]
                        break
                if candidates:
                    break
        if candidates is None:
            for v in parsed_json.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    sample = v[0]
                    if any(k in sample for k in ("product_id", "id", "name", "price", "url")):
                        candidates = v
                        break
    elif isinstance(parsed_json, list):
        candidates = parsed_json

    if not candidates:
        return []

    out: List[Dict[str, Any]] = []
    for it in candidates:
        if not isinstance(it, dict):
            continue

        name = it.get("name") or it.get("full_name") or it.get("title") or None
        price = it.get("price")
        try:
            price = float(price) if price is not None else None
        except Exception:
            price = None

        image: Optional[str] = it.get("main_image_link")
        if not image:
            imgs = it.get("images")
            if isinstance(imgs, list) and imgs:
                image = imgs[0]
        if isinstance(image, str) and image.startswith("//"):
            image = "https:" + image

        code = it.get("code") or it.get("sku") or None

        params: Dict[str, Any] = {}
        for p in it.get("parameters") or it.get("params") or []:
            if not isinstance(p, dict):
                continue
            pname = p.get("name") or p.get("param_name")
            if not pname:
                continue
            params[pname] = p.get("value_float") if p.get("value_float") is not None else p.get("value")

        slug = it.get("url") or it.get("product_url") or it.get("slug")
        full_url = None
        if slug:
            if isinstance(slug, str) and (slug.startswith("http://") or slug.startswith("https://")):
                full_url = slug
            else:
                full_url = urljoin(BASE, f"/product/{slug}")

        brand = (
            it.get("brand") or it.get("manufacturer") or it.get("PROPERTY_BRAND_VALUE")
            or it.get("vendor") or "Camerussia"
        )

        category = None
        for cat_key in ("section", "sections", "category", "categories", "SECTION_NAME", "iblock_section"):
            val = it.get(cat_key)
            if isinstance(val, str) and val:
                category = val
                break
            if isinstance(val, list) and val:
                first = val[0]
                category = first.get("name") or first.get("NAME") or str(first) if isinstance(first, dict) else str(first)
                break
            if isinstance(val, dict) and val:
                category = val.get("name") or val.get("NAME") or val.get("title")
                break
        if not category:
            category = _category_from_filename(filename)


        out.append({
            "url": full_url,
            "name": name,
            "price": price,
            "image": image,
            "params": params,
            "code": code,
            "brand": brand,
            "category": category,
            "raw_api_obj": it,
        })

    return out


class CamerussiaScraper(BaseScraper):
    source_name = "Camerussia Smart House (imported json)"
    source_url = BASE + CATALOG_PATH
    data_dir: str = "./camerussia_jsons"

    async def _save_products(self, products: List[Dict[str, Any]]) -> int:
        async with async_session() as session:
            source_id = await self.get_or_create_source(session)
            saved = 0
            for prod in products:
                product_data = {
                    "source_id": source_id,
                    "source_sku": prod.get("code") or prod.get("name") or prod.get("url"),
                    "brand": prod.get("brand") or "Camerussia",
                    "model": prod.get("name"),
                    "category": prod.get("category"),
                    "price": prod.get("price"),
                    "currency": "RUB" if prod.get("price") is not None else None,
                    "url": prod.get("url"),
                }
                if product_data.get("category") not in ALLOWED_CATEGORIES:
                    self._log.debug("Category %r not allowed — skipping: %s", product_data.get("category"), prod.get("name"))
                    continue
                try:
                    product = await self.save_product(session, product_data)
                except Exception:
                    self._log.exception("Failed to save product: %s", prod.get("name"))
                    continue

                # Build spec pairs from params only — image/price/code go into Product fields
                pairs: list[tuple[str, str]] = []
                for k, v in (prod.get("params") or {}).items():
                    if v is None:
                        continue
                    if isinstance(v, dict):
                        v = v.get("value_float") if v.get("value_float") is not None else v.get("value")
                    if v is None:
                        continue
                    pairs.append((str(k), str(v)))

                await self.save_specs(session, product.id, pairs)
                saved += 1

        return saved

    async def run(self) -> None:
        self._log.info("Starting — data_dir: %s", self.data_dir)
        # Both glob and file reads are sync I/O — offload to thread pool
        files = await asyncio.to_thread(_list_json_files, self.data_dir)
        if not files:
            self._log.warning("No JSON files found in %s", self.data_dir)
            return

        self._log.info("Found %d JSON files", len(files))
        total_saved = 0
        for fp in files:
            try:
                data = await asyncio.to_thread(_load_json_file, fp)
            except Exception:
                self._log.exception("Failed to read: %s", fp)
                continue

            products = extract_products_from_api_response(data, filename=fp)
            self._log.info("%s — %d products found", os.path.basename(fp), len(products))
            if products:
                saved = await self._save_products(products)
                total_saved += saved
                self._log.info("%s — saved %d / %d", os.path.basename(fp), saved, len(products))

        self._log.info("Done — total saved: %d", total_saved)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Import camerussia JSON files and save products to DB"
    )
    parser.add_argument("--dir", "-d", default="./camerussia_jsons",
                        help="Directory with JSON files")
    args = parser.parse_args()

    dir_path = os.path.abspath(args.dir)
    if not os.path.isdir(dir_path):
        logging.error("Directory not found: %s", dir_path)
        return

    scraper = CamerussiaScraper()
    scraper.data_dir = dir_path
    asyncio.run(scraper.run())


if __name__ == "__main__":
    main()
