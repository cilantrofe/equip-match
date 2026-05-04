"""Планировщик периодического запуска скраперов.

Расписание задаётся переменной окружения SCRAPE_CRON (cron-формат, по умолчанию
1-го числа каждого месяца в 02:00). Отключить можно через SCRAPE_ENABLED=false.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

_CRON = os.getenv("SCRAPE_CRON", "0 2 1 * *")
_ENABLED = os.getenv("SCRAPE_ENABLED", "true").lower() not in {"false", "0", "no"}

_scheduler: AsyncIOScheduler | None = None


async def _run_all_scrapers() -> None:
    """Запустить все скраперы последовательно. Ошибка одного не останавливает остальные."""
    from app.scrapers.akuvox_rus_scraper import AkuvoxRusScraper
    from app.scrapers.basip_scraper import BasIPScraper
    from app.scrapers.camerussia_smart_house_scraper import CamerussiaScraper
    from app.scrapers.comelit_clients_api_scraper import ComelitClientsScraper
    from app.scrapers.hikvisionpro_scraper import HikvisionProScraper

    scrapers = [
        AkuvoxRusScraper,
        BasIPScraper,
        CamerussiaScraper,
        ComelitClientsScraper,
        HikvisionProScraper,
    ]

    log.info("Scheduled scrape started (%d scrapers)", len(scrapers))
    for cls in scrapers:
        try:
            log.info("Running scraper: %s", cls.source_name)
            await cls().run()  # type: ignore[abstract]
            log.info("Scraper finished: %s", cls.source_name)
        except Exception:
            log.exception("Scraper failed: %s", cls.source_name)
    log.info("Scheduled scrape completed")


def start_scheduler() -> None:
    global _scheduler

    if not _ENABLED:
        log.info("Scrape scheduler disabled (SCRAPE_ENABLED=false)")
        return

    try:
        trigger = CronTrigger.from_crontab(_CRON)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid SCRAPE_CRON value {_CRON!r}: {exc}. "
            "Use standard 5-field cron syntax, e.g. '0 2 * * *'."
        ) from exc

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_all_scrapers,
        trigger=trigger,
        id="scrape_all",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info("Scrape scheduler started — cron: %r", _CRON)


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scrape scheduler stopped")
