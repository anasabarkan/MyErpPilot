# My ERP Pilot — FastAPI + PostgreSQL + Adminer

A containerized FastAPI backend with PostgreSQL and Adminer, orchestrated via Docker Compose.

## Quick Start

```bash
docker compose up --build
```

All three services will start automatically. The API waits for PostgreSQL to be healthy before connecting.

## Access Points

| Service       | URL                          | Description                      |
|---------------|------------------------------|----------------------------------|
| **API Docs**  | http://localhost:8000/docs   | Interactive Swagger UI           |
| **ReDoc**     | http://localhost:8000/redoc  | Alternative API documentation    |
| **Adminer**   | http://localhost:8080        | Database management interface    |
| **API Root**  | http://localhost:8000        | Health check endpoint            |

## Adminer — PostgreSQL Connection

When logging into Adminer, use these settings:

| Field      | Value        |
|------------|--------------|
| System     | PostgreSQL   |
| Server     | `db`         |
| Username   | `postgres`   |
| Password   | `postgres`   |
| Database   | `erp_pilot`  |

## API Endpoints

| Method   | Endpoint         | Description        |
|----------|------------------|--------------------|
| `GET`    | `/`              | Health check       |
| `POST`   | `/users/`        | Create a user      |
| `GET`    | `/users/`        | List all users     |
| `GET`    | `/users/{id}`    | Get user by ID     |
| `DELETE` | `/users/{id}`    | Delete user by ID  |

### Example — Create a User

```bash
curl -X POST http://localhost:8000/users/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "email": "alice@example.com"}'
```

## Project Structure

```
my-erp-pilot/
├── app/
│   ├── main.py            # FastAPI entry point
│   ├── config.py           # Settings from env vars
│   ├── database/
│   │   └── session.py      # SQLAlchemy engine & session
│   ├── models/
│   │   └── user.py         # User ORM model
│   ├── schemas/
│   │   └── user.py         # Pydantic schemas
│   ├── services/
│   │   └── user.py         # CRUD business logic
│   └── routers/
│       └── user.py         # API route handlers
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env
└── README.md
```

## Environment Variables

Defined in `.env` — shared by both `api` and `db` services:

| Variable            | Default      |
|---------------------|--------------|
| `POSTGRES_USER`     | `postgres`   |
| `POSTGRES_PASSWORD` | `postgres`   |
| `POSTGRES_DB`       | `erp_pilot`  |
| `POSTGRES_HOST`     | `db`         |
| `POSTGRES_PORT`     | `5432`       |

## Stopping the Project

```bash
docker compose down
```

To also remove the database volume (deletes all data):

```bash
docker compose down -v
```
