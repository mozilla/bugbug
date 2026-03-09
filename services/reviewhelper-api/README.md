# Review Helper Backend

FastAPI backend for Mozilla's Review Helper supporting both Phabricator and GitHub platforms.

## Setup

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Copy environment variables:

   ```bash
   cp .env.example .env
   ```

3. Run database migrations:

   ```bash
   uv run alembic upgrade head
   ```

4. Start the server:
   ```bash
   uv run uvicorn app.main:app --reload
   ```

## API Documentation

Once the server is running, visit `/docs` for interactive API documentation.

## Cloud SQL Setup

This application supports Google Cloud SQL for PostgreSQL with two connection methods:

### Local Development with Cloud SQL Auth Proxy

1. Install and run the Cloud SQL Auth Proxy:

   Follow instructions from the [Cloud SQL Auth Proxy documentation](https://docs.cloud.google.com/sql/docs/postgres/connect-auth-proxy#install).

2. Configure your `.env`:

   ```bash
   DATABASE_URL=postgresql+asyncpg://user:password@127.0.0.1:5432/reviewhelper
   ```

### Cloud Run Deployment

Cloud Run connects to Cloud SQL via Unix socket automatically. Set these environment variables:

```bash
CLOUD_SQL_INSTANCE=project-id:us-central1:instance-name
DB_USER=reviewhelper
DB_PASS=your-db-password
DB_NAME=reviewhelper
```

Ensure your Cloud Run service has the Cloud SQL connection configured in the deployment settings.
