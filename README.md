# EquipMatch

Сервис для поиска аналогов оборудования в сфере smart building.

2 вида поиска аналога:
- По цене
- По техническим характеристикам

Структура проекта:
.
├── README.md
├── backend
│   ├── Dockerfile
│   ├── app
│   │   ├── api
│   │   │   └── router.py
│   │   ├── config.py
│   │   ├── db
│   │   │   ├── crud.py
│   │   │   ├── models.py
│   │   │   └── session.py
│   │   ├── main.py
│   │   ├── matching
│   │   │   └── matcher.py
│   │   ├── normalization
│   │   │   └── normalizer.py
│   │   ├── scrapers
│   │   │   ├── akuvox_rus_scraper.py
│   │   │   ├── basip_scraper.py
│   │   │   ├── camerussia_smart_house_scraper.py
│   │   │   ├── comelit_clients_api_scraper.py
│   │   │   ├── hikvisionpro_scraper.py
│   │   │   ├── run_scrapers.py
│   │   │   └── test_parse.py
│   │   └── services
│   │       └── lookup.py
│   ├── requirements.txt
│   └── static
│       └── index.html
├── backup with akuvox.sql
├── backup.sql
├── data-1774198026404.csv
├── data-1774201532566.csv
├── docker-compose.yml
├── image.png
├── index.html
├── infra
│   └── init_db.sql
├── products_specs.csv
├── products_specs.sql
├── products_specs.txt
└── query_history.sql