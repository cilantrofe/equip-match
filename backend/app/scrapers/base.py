"""Базовые классы и утилиты для скраперов.

`BaseScraper` — абстрактный корень иерархии: общая логика источников,
сохранения товаров и характеристик.

`BaseHttpScraper` — расширение для скраперов, которые обходят HTTP-страницы.
Реализует цикл обхода ссылок, ретраи, семафор параллелизма и персистенцию
в БД. Подклассы реализуют два метода: `collect_links` и `parse_page`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from abc import ABC, abstractmethod
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crud import create_source_if_missing, upsert_product
from app.db.models import ProductSpec
from app.db.session import async_session
from app.normalization.normalizer import normalize_for_spec
from app.normalization.spec_aliases import canonicalize_spec_name, weight_for

ALLOWED_CATEGORIES: frozenset[str] = frozenset({"Видеомонитор", "Вызывная панель"})

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_WHITESPACE_RE = re.compile(r"\s+")


def _clean(text: Optional[str]) -> str:
    """Нормализовать пробелы и убрать неразрывные символы."""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text.replace("\u00a0", " ").replace("\u202f", " ")).strip()


def _extract_table_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Извлечь пары (название, значение) из всех `<table>` на странице."""
    pairs: list[tuple[str, str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [_clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                pairs.append((cells[0], cells[1]))
    return pairs


def _extract_dl_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Извлечь пары (название, значение) из всех `<dl>` на странице."""
    pairs: list[tuple[str, str]] = []
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            k = _clean(dt.get_text(" ", strip=True))
            v = _clean(dd.get_text(" ", strip=True))
            if k and v:
                pairs.append((k, v))
    return pairs


class BaseScraper(ABC):
    """Абстрактный базовый скрапер: источник, сохранение товаров и характеристик."""

    source_name: str = ""
    source_url: str = ""
    source_brand: str = ""
    default_currency: str = "RUB"

    @property
    def _log(self) -> logging.Logger:
        return logging.getLogger(type(self).__name__)

    async def get_or_create_source(self, session: AsyncSession) -> int:
        """Получить или создать запись источника, вернуть его `id`."""
        src = await create_source_if_missing(session, self.source_name, self.source_url)
        return src.id

    async def save_product(self, session: AsyncSession, product_data: dict) -> object:
        """Сохранить или обновить товар через `upsert_product`."""
        return await upsert_product(session, product_data)

    async def save_specs(
        self,
        session: AsyncSession,
        product_id: int,
        pairs: list[tuple[str, str]],
    ) -> None:
        """Канонизировать, нормализовать, дедублировать и сохранить характеристики.

        Принимает кортежи `(raw_name, raw_value)`. Характеристики из
        `EXCLUDE_SPECS` и с пустыми значениями отбрасываются. Старые
        характеристики товара атомарно заменяются (delete-then-insert).
        """
        seen: dict[str, tuple[str, object]] = {}
        for raw_name, raw_value in pairs:
            if not raw_name:
                continue
            canonical = canonicalize_spec_name(raw_name)
            if not canonical:
                continue
            if canonical in seen:
                continue
            nv = normalize_for_spec(canonical, raw_value if raw_value is not None else "")
            if nv.kind == "empty":
                continue
            seen[canonical] = (raw_name, nv)

        await session.execute(
            delete(ProductSpec).where(ProductSpec.product_id == product_id)
        )
        for canonical, (raw_name, nv) in seen.items():
            session.add(
                ProductSpec(
                    product_id=product_id,
                    spec_name=raw_name,
                    spec_name_canonical=canonical,
                    spec_value_text=nv.value_text,
                    spec_value_num=nv.value_num,
                    spec_unit=nv.unit,
                    weight=weight_for(canonical),
                )
            )
        await session.commit()

    @staticmethod
    def _clean(text: Optional[str]) -> str:
        return _clean(text)

    def _is_allowed_category(self, category: Optional[str]) -> bool:
        """Проверить, входит ли категория в разрешённый список."""
        return category in ALLOWED_CATEGORIES

    @classmethod
    def run_standalone(cls, **kwargs) -> None:
        """Запустить скрапер в режиме командной строки."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        asyncio.run(cls().run(**kwargs))

    @abstractmethod
    async def run(self) -> None:
        """Запустить полный цикл скрапинга."""
        ...


class BaseHttpScraper(BaseScraper, ABC):
    """Базовый скрапер для обхода HTTP-страниц.

    Подклассы реализуют два метода:
    - `collect_links(session)` → множество URL товаров для обхода;
    - `parse_page(soup, html, url)` → `(product_data, spec_pairs)` или `None`.

    Цикл обхода, ретраи, семафор параллелизма и персистенция в БД
    реализованы здесь.
    """

    request_delay: float = 1.0
    concurrency: int = 3
    retries: int = 3
    default_headers: dict = {}

    async def fetch(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> tuple[Optional[int], Optional[str]]:
        """Выполнить GET-запрос с ретраями. Возвращает `(status, html)` или `(None, None)`."""
        delay = 0.5
        for attempt in range(1, self.retries + 1):
            try:
                async with session.get(url, timeout=30, allow_redirects=True) as resp:
                    return resp.status, await resp.text(encoding="utf-8", errors="replace")
            except Exception as exc:
                if attempt < self.retries:
                    await asyncio.sleep(delay * attempt + random.random())
                else:
                    self._log.warning("Fetch failed (%s) — %s", type(exc).__name__, url)
        return None, None

    def _is_error_page(self, html: str) -> bool:
        """Вернуть `True`, если HTML — страница ошибки шлюза (502/503)."""
        lower = html.lower()
        return (
            "502 bad gateway" in lower
            or "503 service temporarily unavailable" in lower
        )

    @abstractmethod
    async def collect_links(self, session: aiohttp.ClientSession) -> set[str]:
        """Обнаружить и вернуть все URL товаров для данного источника."""
        ...

    @abstractmethod
    def parse_page(
        self,
        soup: BeautifulSoup,
        html: str,
        url: str,
    ) -> Optional[tuple[dict, list[tuple[str, str]]]]:
        """Разобрать одну страницу товара.

        Возвращает `(product_data, spec_pairs)`, где `product_data` не должен
        содержать `source_id` (добавляется в цикле обхода). `None` — пропустить.
        """
        ...

    async def _process_link(
        self,
        session_http: aiohttp.ClientSession,
        url: str,
        source_id: int,
        sem: asyncio.Semaphore,
        counters: dict,
    ) -> None:
        """Обработать один URL: скачать, разобрать и сохранить товар."""
        async with sem:
            counters["processed"] += 1
            status, html = await self.fetch(session_http, url)

            if status is None:
                self._log.warning("No response: %s", url)
                counters["errors"] += 1
                return
            if status != 200:
                self._log.debug("HTTP %d — skipping: %s", status, url)
                counters["skipped"] += 1
                await asyncio.sleep(self.request_delay + random.random())
                return
            if not html or self._is_error_page(html):
                self._log.debug("Error page — skipping: %s", url)
                counters["skipped"] += 1
                await asyncio.sleep(self.request_delay + random.random())
                return

            soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
            result = self.parse_page(soup, html, url)
            if result is None:
                self._log.debug("Not a product page — skipping: %s", url)
                counters["skipped"] += 1
                await asyncio.sleep(self.request_delay + random.random())
                return

            product_data, pairs = result
            if not self._is_allowed_category(product_data.get("category")):
                self._log.debug(
                    "Category %r not allowed — skipping: %s",
                    product_data.get("category"),
                    url,
                )
                counters["skipped"] += 1
                await asyncio.sleep(self.request_delay + random.random())
                return

            product_data["source_id"] = source_id
            try:
                async with async_session() as db_sess:
                    product = await self.save_product(db_sess, product_data)
                    await self.save_specs(db_sess, product.id, pairs)
                self._log.debug("Saved [%d specs]: %s", len(pairs), url)
                counters["saved"] += 1
            except IntegrityError:
                self._log.debug("Duplicate product, skipping: %s", url)
                counters["skipped"] = counters.get("skipped", 0) + 1
            except Exception:
                self._log.exception("DB error for: %s", url)
                counters["errors"] += 1

            await asyncio.sleep(self.request_delay + random.random())

    async def run(self) -> None:
        """Запустить полный цикл скрапинга: сбор ссылок → обход → сохранение."""
        self._log.info("Starting — %s", self.source_url)
        async with aiohttp.ClientSession(
            headers=self.default_headers,
            cookie_jar=aiohttp.CookieJar(),
            connector=aiohttp.TCPConnector(limit_per_host=self.concurrency),
        ) as http:
            links = await self.collect_links(http)
            self._log.info("Found %d product links", len(links))

            if not links:
                self._log.warning("No links found — nothing to scrape")
                return

            async with async_session() as db_sess:
                source_id = await self.get_or_create_source(db_sess)

            self._log.info(
                "Processing %d links (concurrency=%d)", len(links), self.concurrency
            )
            sem = asyncio.Semaphore(self.concurrency)
            counters: dict[str, int] = {"processed": 0, "saved": 0, "skipped": 0, "errors": 0}

            await asyncio.gather(
                *[
                    self._process_link(http, link, source_id, sem, counters)
                    for link in sorted(links)
                ],
                return_exceptions=True,
            )

            self._log.info(
                "Done — processed=%d  saved=%d  skipped=%d  errors=%d",
                counters["processed"],
                counters["saved"],
                counters["skipped"],
                counters["errors"],
            )
