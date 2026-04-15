# My ERP Pilot Backend

FastAPI backend with PostgreSQL, RBAC, and Odoo data-source management.

## Quick start

```bash
docker compose up --build
```

Services:

1. API docs: `http://localhost:8000/docs`
2. ReDoc: `http://localhost:8000/redoc`
3. Adminer: `http://localhost:8080`

## Optional local Odoo stack

The compose file includes an optional Odoo profile for local development:

```bash
docker compose --profile odoo up --build
```

Additional services:

1. Odoo: `http://localhost:8069`
2. Odoo PostgreSQL: internal service `odoo-db`

Use these defaults for the Odoo profile unless overridden in `.env`:

1. `ODOO_DB_NAME=odoo`
2. `ODOO_DB_USER=odoo`
3. `ODOO_DB_PASSWORD=odoo`

## Odoo credential encryption

Set a valid Fernet key for credential encryption at rest:

1. `ODOO_CREDENTIALS_ENCRYPTION_KEY=<your_fernet_key>`

Generate one with Python:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Without this key, Odoo credential save/test endpoints will fail.

## API namespaces

1. `GET /api/v1/` health and auth/user/role/audit APIs
2. ` /api/v1/data-sources/*` Odoo data-source settings APIs

## Odoo settings APIs

1. `POST /api/v1/data-sources/test-connection`
2. `POST /api/v1/data-sources/`
3. `GET /api/v1/data-sources/`
4. `GET /api/v1/data-sources/active`
5. `PATCH /api/v1/data-sources/{id}`
6. `DELETE /api/v1/data-sources/{id}`
7. `POST /api/v1/data-sources/{id}/test`
8. `GET /api/v1/data-sources/{id}/models`
9. `POST /api/v1/data-sources/{id}/activate`

All settings endpoints are protected by `manage_odoo_sources`.
