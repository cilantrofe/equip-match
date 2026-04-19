# EquipMatch

Веб-серрвис для поиска аналогов оборудования в сфере smart building.

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
│   │   │   ├── run_scrapers.py
│   │   │   └── test_parse.py
│   │   └── services -- API методы
│   │       └── lookup.py
│   ├── requirements.txt
├── docker-compose.yml
├── infra
│   └── init_db.sql
```

В работе: автобновление БД раз в период(cron задачи), веб-верстка