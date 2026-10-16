
## PIXI

run pixi develop
 ```bash
 pixi run -e dev insar
 ```

## Start Database PostgresSQL + PostGIS

Start the database:

```bash
docker compose up -d

# To access the DB by terminal
docker exec -it insar_postgres psql -U insar -d insar
```

```SQL

CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Version();
```

## Start Alembic

Initialize Alembic (once)

```bash
pixi run alembic init alembic
```

Generate the initial migration

```bash
pixi run alembic revision --autogenerate -m "initial insar schema"
```

Check the migration status

```bash
# Migration revision currently applied to PostgreSQL
pixi run alembic current

# Latest migration revision available in your porject
pixi run alembic heads

# Migration history
pixi run alembic history
```

Apply it:

```bash
pixi run alembic upgrade head
```

Rollback:
```bash
pixi run alembic downgrade -1
```