import os
import json
import argparse
import asyncio
from glob import glob
from typing import List, Dict, Any
from urllib.parse import urljoin

from app.db.session import async_session
from app.db.crud import create_source_if_missing, upsert_product, add_spec
from app.normalization.normalizer import parse_number_and_unit, normalize_spec_name

BASE = "https://camerussia.com"
CATALOG_PATH = "/catalog/smart-house/"

# -------- helpers to extract products from arbitrary JSON --------
def extract_products_from_api_response(parsed_json: Any) -> List[Dict[str, Any]]:
    """
    Попытка извлечь список product-объектов из JSON-ответа.
    Возвращает список нормализованных словарей:
      { url, name, price, image, params(dict), code, product_id, raw_api_obj }
    """
    if not parsed_json:
        return []

    candidates = None
    if isinstance(parsed_json, dict):
        for key in ("products", "items", "data", "result", "list"):
            if key in parsed_json:
                val = parsed_json[key]
                if isinstance(val, list):
                    candidates = val
                    break
                if isinstance(val, dict):
                    # если внутри есть items/products
                    for sub in ("items", "products", "list"):
                        if sub in val and isinstance(val[sub], list):
                            candidates = val[sub]
                            break
                    if candidates:
                        break
        # fallback: найти первую top-level value, которая выглядит как список dict-ов
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

    out = []
    for it in candidates:
        if not isinstance(it, dict):
            continue

        name = it.get("name") or it.get("full_name") or it.get("title") or None
        price = it.get("price")
        try:
            if price is not None:
                price = float(price)
        except Exception:
            price = None

        image = it.get("main_image_link")
        if not image:
            imgs = it.get("images")
            if isinstance(imgs, list) and imgs:
                image = imgs[0]
        if isinstance(image, str) and image.startswith("//"):
            image = "https:" + image

        code = it.get("code") or it.get("sku") or None
        product_id = it.get("product_id") or it.get("id") or None

        params = {}
        for p in it.get("parameters") or it.get("params") or []:
            if not isinstance(p, dict):
                continue
            pname = p.get("name") or p.get("param_name")
            if not pname:
                continue
            if "value_float" in p and p.get("value_float") is not None:
                params[pname] = p.get("value_float")
            else:
                params[pname] = p.get("value")

        slug = it.get("url") or it.get("product_url") or it.get("slug")
        full_url = None
        if slug:
            if isinstance(slug, str) and (slug.startswith("http://") or slug.startswith("https://")):
                full_url = slug
            else:
                # на camerussia slug формат – без ведущего /product/, поэтому формируем
                # e.g. https://camerussia.com/product/<slug>
                try:
                    full_url = urljoin(BASE, f"/product/{slug}")
                except Exception:
                    full_url = urljoin(BASE, str(slug))

        prod = {
            "url": full_url,
            "name": name,
            "price": price,
            "image": image,
            "params": params,
            "code": code,
            "product_id": product_id,
            "raw_api_obj": it
        }
        out.append(prod)

    return out

# -------- DB save helper (re-uses your app.crud helpers) --------
async def save_products_to_db(products: List[Dict[str, Any]], source_label: str = "Camerussia Smart House (imported json)"):
    async with async_session() as db_sess:
        src = await create_source_if_missing(db_sess, source_label, BASE + CATALOG_PATH)
        source_id = src.id

        saved = 0
        for prod in products:
            product_data = {
                "source_id": source_id,
                "source_sku": prod.get("code") or prod.get("name") or prod.get("url"),
                "brand": None,
                "model": prod.get("name"),
                "category": None,
                "price": prod.get("price"),
                "currency": "RUB" if prod.get("price") is not None else None,
                "url": prod.get("url"),
                "raw_html": (json.dumps(prod.get("raw_api_obj"), ensure_ascii=False)[:1000000] if prod.get("raw_api_obj") else "") ,
            }
            try:
                p = await upsert_product(db_sess, product_data)
            except Exception as e:
                print("DB upsert error for", prod.get("url"), e)
                continue

            # code / sku
            if prod.get("code"):
                try:
                    await add_spec(db_sess, p.id, normalize_spec_name("source_sku"), prod.get("code"), None, None)
                except Exception:
                    pass

            # image
            if prod.get("image"):
                try:
                    await add_spec(db_sess, p.id, normalize_spec_name("image"), prod.get("image"), None, None)
                except Exception:
                    pass

            # price as numeric spec
            if prod.get("price") is not None:
                try:
                    await add_spec(db_sess, p.id, normalize_spec_name("price"), None, float(prod.get("price")), "RUB")
                except Exception:
                    pass

            for k, v in (prod.get("params") or {}).items():
                try:
                    sname = normalize_spec_name(k)

                    # 1) если значение уже числовое — сохраняем как numeric spec
                    if isinstance(v, (int, float)):
                        await add_spec(db_sess, p.id, sname, None, float(v), None)
                        continue

                    # 2) если значение — dict (например raw API object with value/value_float)
                    if isinstance(v, dict):
                        # предпочтение value_float если есть
                        if v.get("value_float") is not None:
                            try:
                                await add_spec(db_sess, p.id, sname, None, float(v.get("value_float")), None)
                            except Exception:
                                # fallback to raw value
                                await add_spec(db_sess, p.id, sname, v.get("value") or None, None, None)
                            continue
                        # возьмём текстовое поле value если есть
                        v = v.get("value", None)

                    # 3) None -> сохраняем пустой spec (или пропускаем)
                    if v is None:
                        await add_spec(db_sess, p.id, sname, None, None, None)
                        continue

                    # 4) Приводим к строке и пытаемся распарсить число + единицу
                    if not isinstance(v, str):
                        v = str(v)

                    num, unit = parse_number_and_unit(v)

                    if num is not None:
                        await add_spec(db_sess, p.id, sname, None, num, unit)
                    else:
                        text_val = v.strip() or None
                        await add_spec(db_sess, p.id, sname, text_val, None, None)

                except Exception as e:
                    print("add_spec param error", p.id, k, e)

            saved += 1
        return saved

# -------- file processing --------
def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception as e:
            # если файл очень большой и содержит несколько JSON-объектов — можно доработать
            raise

async def process_directory(dir_path: str, source_label: str):
    files = sorted(glob(os.path.join(dir_path, "*.json")))
    if not files:
        print("No JSON files found in", dir_path)
        return

    total_files = 0
    total_products = 0
    total_saved = 0

    for fp in files:
        total_files += 1
        print(f"\n[FILE] Processing {fp} ...")
        try:
            data = load_json_file(fp)
        except Exception as e:
            print("[ERROR] Failed to load JSON:", e)
            continue

        # получаем список объектов
        products = []
        # если файл уже хранит массив продуктов в корне
        if isinstance(data, list):
            products = extract_products_from_api_response(data)
        elif isinstance(data, dict):
            # сначала пробуем извлечь products ключ из HTML-like ответа
            extracted = extract_products_from_api_response(data)
            if extracted:
                products = extracted
            else:
                # пробуем искать глубже: некоторые файлы могут содержать 'response' -> 'data' -> 'products'
                # найти любой список dict'ов, похожих на продукты
                # reuse extract_products_from_api_response's heuristics by passing the dict directly
                products = extract_products_from_api_response(data)
        else:
            print("[WARN] JSON root is neither list nor dict - skipping")
            continue

        print(f"[INFO] Found {len(products)} parsed product objects in file {os.path.basename(fp)}")
        total_products += len(products)

        # save to db in batches (to avoid too big transactions)
        if products:
            saved = await save_products_to_db(products, source_label=source_label)
            print(f"[DB] saved {saved} products from file {os.path.basename(fp)}")
            total_saved += saved

    print("\n=== SUMMARY ===")
    print("files processed:", total_files)
    print("products parsed:", total_products)
    print("products saved:", total_saved)


# -------- CLI entrypoint --------
def main():
    parser = argparse.ArgumentParser(description="Import camerussia JSON files and save products to DB")
    parser.add_argument("--dir", "-d", default="./camerussia_jsons", help="Directory with JSON files")
    parser.add_argument("--source-label", "-s", default="Camerussia Smart House (imported json)", help="Source label for DB records")
    args = parser.parse_args()

    dir_path = os.path.abspath(args.dir)
    if not os.path.isdir(dir_path):
        print("Directory not found:", dir_path)
        return

    asyncio.run(process_directory(dir_path, args.source_label))


if __name__ == "__main__":
    main()