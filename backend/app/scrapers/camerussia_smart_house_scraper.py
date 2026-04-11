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


def extract_products_from_api_response(parsed_json: Any) -> List[Dict[str, Any]]:
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

                    for sub in ("items", "products", "list"):
                        if sub in val and isinstance(val[sub], list):
                            candidates = val[sub]
                            break
                    if candidates:
                        break
        if candidates is None:
            for v in parsed_json.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    sample = v[0]
                    if any(
                        k in sample
                        for k in ("product_id", "id", "name", "price", "url")
                    ):
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
            if isinstance(slug, str) and (
                slug.startswith("http://") or slug.startswith("https://")
            ):
                full_url = slug
            else:
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
            "raw_api_obj": it,
        }
        out.append(prod)

    return out


async def save_products_to_db(
    products: List[Dict[str, Any]],
    source_label: str = "Camerussia Smart House (imported json)",
):
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
                "raw_html": (
                    json.dumps(prod.get("raw_api_obj"), ensure_ascii=False)[:1000000]
                    if prod.get("raw_api_obj")
                    else ""
                ),
            }
            try:
                p = await upsert_product(db_sess, product_data)
            except Exception as e:
                continue

            if prod.get("code"):
                try:
                    await add_spec(
                        db_sess,
                        p.id,
                        normalize_spec_name("source_sku"),
                        prod.get("code"),
                        None,
                        None,
                    )
                except Exception:
                    pass

            if prod.get("image"):
                try:
                    await add_spec(
                        db_sess,
                        p.id,
                        normalize_spec_name("image"),
                        prod.get("image"),
                        None,
                        None,
                    )
                except Exception:
                    pass

            if prod.get("price") is not None:
                try:
                    await add_spec(
                        db_sess,
                        p.id,
                        normalize_spec_name("price"),
                        None,
                        float(prod.get("price")),
                        "RUB",
                    )
                except Exception:
                    pass

            for k, v in (prod.get("params") or {}).items():
                try:
                    sname = normalize_spec_name(k)

                    if isinstance(v, (int, float)):
                        await add_spec(db_sess, p.id, sname, None, float(v), None)
                        continue

                    if isinstance(v, dict):

                        if v.get("value_float") is not None:
                            try:
                                await add_spec(
                                    db_sess,
                                    p.id,
                                    sname,
                                    None,
                                    float(v.get("value_float")),
                                    None,
                                )
                            except Exception:

                                await add_spec(
                                    db_sess,
                                    p.id,
                                    sname,
                                    v.get("value") or None,
                                    None,
                                    None,
                                )
                            continue

                        v = v.get("value", None)

                    if v is None:
                        await add_spec(db_sess, p.id, sname, None, None, None)
                        continue

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


def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception as e:
            raise


async def process_directory(dir_path: str, source_label: str):
    files = sorted(glob(os.path.join(dir_path, "*.json")))
    if not files:
        return

    total_files = 0
    total_products = 0
    total_saved = 0

    for fp in files:
        total_files += 1
        try:
            data = load_json_file(fp)
        except Exception as e:
            continue

        products = []
        if isinstance(data, list):
            products = extract_products_from_api_response(data)
        elif isinstance(data, dict):
            extracted = extract_products_from_api_response(data)
            if extracted:
                products = extracted
            else:
                products = extract_products_from_api_response(data)
        else:
            continue

        total_products += len(products)

        if products:
            saved = await save_products_to_db(products, source_label=source_label)
            total_saved += saved


def main():
    parser = argparse.ArgumentParser(
        description="Import camerussia JSON files and save products to DB"
    )
    parser.add_argument(
        "--dir", "-d", default="./camerussia_jsons", help="Directory with JSON files"
    )
    parser.add_argument(
        "--source-label",
        "-s",
        default="Camerussia Smart House (imported json)",
        help="Source label for DB records",
    )
    args = parser.parse_args()

    dir_path = os.path.abspath(args.dir)
    if not os.path.isdir(dir_path):
        return

    asyncio.run(process_directory(dir_path, args.source_label))


if __name__ == "__main__":
    main()
