## Brief overview
Project-specific conventions for the ShopGym web gym project.

## Temporary files and directories
- Never use system temporary directories like `/tmp/` for project artifacts
- Always use `_tmp/` inside the project root (`_tmp/`) for any temporary files, test databases, screenshots, logs, etc.
- Example: Docker DB mounts → `_tmp/gym_1/shop.db`, Playwright screenshots → `_tmp/pw_test_screenshots/`
- `_tmp/` should be added to `.gitignore`

## Docker lifecycle
- Pre-create SQLite DB and JSONL log files with `touch` before `docker run` (required for file bind-mounts)
- Use named containers (`shopgym_1`, `shopgym_pw`, etc.) so they can be reliably stopped and removed
- Always clean up containers after tests: `docker stop <name> && docker rm <name>`
- Seed via `POST /api/reset` after the health check returns `{"status":"ok"}`

## Project structure
- Shop Flask app lives in `shop/` — do not modify it for gym/test concerns
- Playwright tests live in `pw_test/` with their own `.venv/`
- Gym environment code goes in `gym_env/`
- Scripts that drive Docker or orchestrate parallel instances go in `scripts/`

## Python environment
- Each subdirectory with its own dependencies (`pw_test/`, `gym_env/`, etc.) gets its own `.venv/`
- Always invoke Python via `.venv/bin/python` and pip via `.venv/bin/pip` — never the system Python
- Use `playwright install chromium` (not `install` alone) to avoid downloading unused browsers