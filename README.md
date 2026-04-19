# EquipMatch

Веб-серрвис поиска похожих товаров по SKU в сфере smart building.

Два независимых сценария: по характеристикам (`lookup_tech`) и по цене
(`lookup_price`). Оба достают target из БД, поднимают кандидатов той
же категории (с отсечением самого target в SQL) и прогоняют через
соответствующий матчер.

### Что было проделано:
* `backend/app/scrapers` - классы парсеров с использованием различных способов парсинга(через API, html, json и тд)
* `backend/app/db`, `infra` - создание БД из различных неструктированных данных сайта
* `backend/alembic` - миграции данных
* `backend/normalization` - ETL, к-ый собирает эти данные во что-то адекватное и что можно сравнивать
* `backend/app/matching` - матчинг товаров: по цене и по характеристикам.
    `match_by_tech` собирает характеристики обеих сторон в каноническом
    виде, считает сходство по каждой характеристике target и возвращает
    взвешенный скор кандидата вместе с разбивкой, чтобы было видно, какая
    характеристика какой вклад дала.

    `match_by_price` сравнивает относительную разницу цен и возвращает
    top-N ближайших кандидатов.
* `backend/app/api` - API
* `backend/app/services` - сам сервис
* В разработке: Автобновление БД раз в период(cron задачи)
* В разработке: верстка веб-странички

Структура проекта:

```bash
.
├── README.md
├── backend
│   ├── Dockerfile
│   ├── alembic  --- Миграции
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions
│   │       ├── 0001_add_weight_canonical_and_indexes.py
│   ├── alembic.ini
│   ├── app
│   │   ├── api
│   │   │   └── router.py
│   │   ├── config.py
│   │   ├── db --- организация БД
│   │   │   ├── crud.py
│   │   │   ├── models.py
│   │   │   └── session.py
│   │   ├── main.py
│   │   ├── matching --- алгоритмы матчинга
│   │   │   └── matcher.py
│   │   ├── normalization --- ETL
│   │   │   ├── normalizer.py
│   │   │   └── spec_aliases.py
│   │   ├── scrapers --- Парсеры
│   │   │   ├── akuvox_rus_scraper.py
│   │   │   ├── base.py
│   │   │   ├── basip_scraper.py
│   │   │   ├── camerussia_smart_house_scraper.py
│   │   │   ├── comelit_clients_api_scraper.py
│   │   │   ├── hikvisionpro_scraper.py
│   │   └── services -- API методы
│   │       └── lookup.py
│   ├── requirements.txt
├── docker-compose.yml
├── infra
│   └── init_db.sql
```