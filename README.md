# EquipMatch

Веб-сервис поиска похожих товаров по SKU в сфере smart building.

Два независимых сценария: по характеристикам (`lookup_tech`) и по цене
(`lookup_price`). Оба достают target из БД, поднимают кандидатов той
же категории (с отсечением самого target в SQL) и прогоняют через
соответствующий матчер.

## Быстрый старт (Docker Compose)

### Требования

- [Docker](https://docs.docker.com/get-docker/) >= 20.10
- [Docker Compose](https://docs.docker.com/compose/install/) >= 2.0

### 1. Клонировать репозиторий

```bash
git clone https://github.com/cilantrofe/equip-match.git
cd equipment-recommendation-system
```

### 2. Создать файл `.env`

В корне проекта (рядом с `docker-compose.yml`) создайте файл `.env`:

```dotenv
# PostgreSQL — учётные данные базы данных
POSTGRES_USER=equipmatch
POSTGRES_PASSWORD=secret
POSTGRES_DB=equipmatch

# Строка подключения для SQLAlchemy (asyncpg)
# Хост — db (имя сервиса в docker-compose)
DATABASE_URL=postgresql+asyncpg://equipmatch:secret@db:5432/equipmatch

# Опционально: Планировщик скраперов
# SCRAPE_ENABLED=true          — включить/выключить (true по умолчанию)
# SCRAPE_CRON=0 2 * * *        — расписание в cron-формате (по умолчанию: 1-го числа каждого месяца в 02:00)

# Разрешённые CORS-источники (через запятую)
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000

# Опционально: лимит запросов (по умолчанию 60 запросов за 60 секунд)
# RATE_LIMIT_CALLS=60
# RATE_LIMIT_PERIOD=60

# Опционально: уровень логирования (DEBUG, INFO, WARNING, ERROR)
# LOG_LEVEL=INFO
```

> Значения `POSTGRES_USER`, `POSTGRES_PASSWORD` и `POSTGRES_DB` должны совпадать
> с теми, что указаны в `DATABASE_URL`.

### 3. Запустить сервисы

```bash
docker compose up --build -d
```

Это поднимет два контейнера:
- `db` — PostgreSQL 15 на порту `5432`
- `backend` — FastAPI на порту `8000`

### 4. Применить миграции

После первого запуска нужно применить миграции Alembic:

```bash
docker compose exec backend alembic upgrade head
```

### 5. Проверить работу

```bash
curl http://localhost:8000/api/health
# {"status":"ok"}
```

Документация API (Swagger UI) после запуска будет доступна по адресу: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Запуск тестов

```bash
cd backend
pytest                         # все тесты
pytest -v                      # с подробным выводом
pytest --cov=app tests/        # с покрытием
```

---

## Переменные окружения

| Переменная          | Обязательная | Описание                                                         | Пример                                           |
|---------------------|:------------:|------------------------------------------------------------------|--------------------------------------------------|
| `DATABASE_URL`      | да           | Строка подключения SQLAlchemy (asyncpg)                          | `postgresql+asyncpg://user:pass@host:5432/db`    |
| `POSTGRES_USER`     | да*          | Пользователь PostgreSQL (только для Docker Compose)              | `equipmatch`                                     |
| `POSTGRES_PASSWORD` | да*          | Пароль PostgreSQL (только для Docker Compose)                    | `secret`                                         |
| `POSTGRES_DB`       | да*          | Имя базы данных (только для Docker Compose)                      | `equipmatch`                                     |
| `SCRAPE_ENABLED`    | нет          | Включить/выключить автообновление БД                    | `true`                                     |
| `SCRAPE_CRON`       | нет          | Расписание обновления в cron-формате (по умолчанию: 1-го числа каждого месяца в 02:00)                      | `0 2 1 * *`                                     |
| `ALLOWED_ORIGINS`   | да           | Разрешённые CORS-источники через запятую                         | `http://localhost:5173,http://localhost:3000`     |
| `RATE_LIMIT_CALLS`  | нет          | Кол-во запросов за период (по умолчанию `60`)                    | `60`                                             |
| `RATE_LIMIT_PERIOD` | нет          | Период в секундах для rate limit (по умолчанию `60`)             | `60`                                             |
| `LOG_LEVEL`         | нет          | Уровень логирования (по умолчанию `INFO`)                        | `DEBUG`                                          |

\* — должны совпадать со значениями в `DATABASE_URL`

---

## Структура проекта

```
.
├── README.md
├── docker-compose.yml
├── infra
│   └── init_db.sql              — базовая SQL-схема
└── backend
    ├── Dockerfile
    ├── alembic.ini
    ├── alembic                  — миграции
    │   └── versions
    │       ├── 0001_add_weight_canonical_and_indexes.py
    │       └── 0002_unique_source_sku_nullable_weight.py
    ├── requirements.txt
    ├── requirements-dev.txt
    └── app
        ├── main.py              — точка входа FastAPI
        ├── config.py            — конфигурация из env
        ├── scheduler.py         — cron-задачи
        ├── api
        │   └── router.py        — HTTP-роуты
        ├── db                   — модели и сессия SQLAlchemy
        │   ├── crud.py
        │   ├── models.py
        │   └── session.py
        ├── matching             — алгоритмы матчинга
        │   └── matcher.py
        ├── normalization        — ETL / нормализация характеристик
        │   ├── normalizer.py
        │   └── spec_aliases.py
        ├── scrapers             — парсеры источников
        │   ├── base.py
        │   ├── akuvox_rus_scraper.py
        │   ├── basip_scraper.py
        │   ├── camerussia_smart_house_scraper.py
        │   ├── comelit_clients_api_scraper.py
        │   └── hikvisionpro_scraper.py
        └── services
            └── lookup.py        — бизнес-логика поиска
```

---

## API

| Метод  | Путь                  | Описание                                      |
|--------|-----------------------|-----------------------------------------------|
| GET    | `/api/health`         | Проверка работоспособности сервиса и БД       |
| GET    | `/api/brands`         | Список всех брендов в базе                    |
| GET    | `/api/status`         | Время последнего обновления данных            |
| GET    | `/api/lookup/price`   | Top-N товаров, ближайших по цене              |
| POST   | `/api/lookup/tech`    | Top-N товаров, похожих по характеристикам     |


### Пример: поиск по цене

```bash
curl "http://localhost:8000/api/lookup/price?sku=<SKU>&limit=5"
```

### Пример: поиск по характеристикам

```bash
curl -X POST http://localhost:8000/api/lookup/tech \
  -H "Content-Type: application/json" \
  -d '{"sku": "<SKU>", "limit": 5}'
```

С переопределением весов характеристик:

```bash
curl -X POST http://localhost:8000/api/lookup/tech \
  -H "Content-Type: application/json" \
  -d '{"sku": "<SKU>", "limit": 5, "weights": {"display_resolution": 2.0}}'
```

---
