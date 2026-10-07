# Job Application Agent

Finds job postings across India, ranks them against your resume and drafts
applications. You review and submit every application yourself.

## Step 1 setup

1. Install uv: https://docs.astral.sh/uv/
2. Install dependencies: `uv sync`
3. Create your settings file: `cp .env.example .env`
4. Edit `config/search.yaml` with your roles, cities and deal-breakers
5. Create the database: `uv run alembic upgrade head`
6. Check everything works:
   - `uv run pytest`
   - `uv run job-agent config`
   - `uv run job-agent stats`

## Layout

- `src/job_agent/lifecycle.py`: job statuses and who may move a job between them
- `src/job_agent/schemas.py`: the job schema every source must return
- `src/job_agent/models.py`: database tables (`jobs`, `job_events`)
- `src/job_agent/db.py`: `upsert_job()` and `change_status()`
- `src/job_agent/config.py`: settings from `.env` and `config/search.yaml`
- `migrations/`: Alembic database migrations

## Rules

- Always change a job's status with `change_status()`. It enforces the lifecycle
  and writes the change to `job_events`.
- After changing a model, run
  `uv run alembic revision --autogenerate -m "what changed"` then
  `uv run alembic upgrade head`.

## Daily commands

1. `uv run job-agent fetch` gets new jobs from Adzuna
2. `uv run job-agent screen` applies the rule filters (scams, seniority, experience, location)
3. `uv run job-agent match` scores shortlisted jobs against `private/master_resume.json`
4. `uv run job-agent list --top 30` shows your best matches

Your master resume lives in `private/`, which Git ignores. It never goes to GitHub.
