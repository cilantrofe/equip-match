import asyncio
import logging
import random
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
from app.normalization.normalizer import normalize_value
from app.normalization.spec_aliases import canonicalize_spec_name, weight_for

ALLOWED_CATEGORIES: frozenset[str] = frozenset({"Видеомонитор", "Вызывная панель"})


class BaseScraper(ABC):
    source_name: str = ""
    source_url: str = ""

    @property
    def _log(self) -> logging.Logger:
        return logging.getLogger(type(self).__name__)

    async def get_or_create_source(self, session: AsyncSession) -> int:
        src = await create_source_if_missing(session, self.source_name, self.source_url)
        return src.id

    async def save_product(self, session: AsyncSession, product_data: dict):
        return await upsert_product(session, product_data)

    async def save_specs(
        self,
        session: AsyncSession,
        product_id: int,
        pairs: list[tuple[str, str]],
    ) -> None:
        """Canonicalize, normalize, deduplicate and persist spec pairs.

        Accepts (raw_name, raw_value) string tuples. Specs excluded by
        EXCLUDE_SPECS or with empty values are silently dropped. Old specs
        for the product are replaced atomically (delete-then-insert).
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
            nv = normalize_value(raw_value if raw_value is not None else "")
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
        if not text:
            return ""
        return text.replace("\u00a0", " ").replace("\u202f", " ").strip()

    @abstractmethod
    async def run(self) -> None: ...


class BaseHttpScraper(BaseScraper, ABC):
    """Base for scrapers that crawl HTTP pages.

    Subclasses implement two methods:
    - collect_links(session) → set of product URLs to visit
    - parse_page(soup, html, url) → (product_data, spec_pairs) or None to skip

    The crawl loop, retry logic, semaphore, and DB persistence are handled here.
    """

    request_delay: float = 1.0
    concurrency: int = 3
    retries: int = 3
    default_headers: dict = {}

    async def fetch(
        self, session: aiohttp.ClientSession, url: str
    ) -> tuple[Optional[int], Optional[str]]:
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
        lower = html.lower()
        return "502 bad gateway" in lower or "503 service temporarily unavailable" in lower

    @abstractmethod
    async def collect_links(self, session: aiohttp.ClientSession) -> set[str]:
        """Discover and return all product URLs for this source."""
        ...

    @abstractmethod
    def parse_page(
        self,
        soup: BeautifulSoup,
        html: str,
        url: str,
    ) -> Optional[tuple[dict, list[tuple[str, str]]]]:
        """Parse one product page.

        Returns (product_data, spec_pairs) where product_data must NOT include
        source_id (injected by the crawl loop). Returns None to skip the page.
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
            if product_data.get("category") not in ALLOWED_CATEGORIES:
                self._log.debug("Category %r not allowed — skipping: %s", product_data.get("category"), url)
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
                pass
            except Exception:
                self._log.exception("DB error for: %s", url)
                counters["errors"] += 1

            await asyncio.sleep(self.request_delay + random.random())

    async def run(self) -> None:
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

            self._log.info("Processing %d links (concurrency=%d)", len(links), self.concurrency)
            sem = asyncio.Semaphore(self.concurrency)
            counters = {"processed": 0, "saved": 0, "skipped": 0, "errors": 0}

            await asyncio.gather(
                *[self._process_link(http, link, source_id, sem, counters)
                  for link in sorted(links)],
                return_exceptions=True,
            )

            self._log.info(
                "Done — processed=%d  saved=%d  skipped=%d  errors=%d",
                counters["processed"], counters["saved"],
                counters["skipped"], counters["errors"],
            )
