# =============================================================================
# NarraNexus — Developer Command Surface
# =============================================================================
# Usage: make <target>
#
# Prerequisites:
#   - Python (uv): pip install uv
#   - Node.js: npm install (in frontend/)
#   - Database: MySQL running with .env configured
# =============================================================================

.PHONY: help lint lint-backend lint-frontend typecheck typecheck-backend typecheck-frontend \
        test test-backend build build-frontend \
        dev-backend dev-frontend dev-mcp dev-poller \
        db-sync db-sync-dry clean

# Default target
help:
	@echo ""
	@echo "  NarraNexus Developer Commands"
	@echo "  ─────────────────────────────"
	@echo ""
	@echo "  Lint & Type Check:"
	@echo "    make lint               Run all linters"
	@echo "    make lint-backend       Ruff check on Python code"
	@echo "    make lint-frontend      ESLint on frontend"
	@echo "    make typecheck          Run all type checkers"
	@echo "    make typecheck-backend  Pyright on Python code"
	@echo "    make typecheck-frontend tsc --noEmit on frontend"
	@echo ""
	@echo "  Test:"
	@echo "    make test               Run all tests"
	@echo "    make test-backend       Run pytest"
	@echo ""
	@echo "  Build:"
	@echo "    make build-frontend     Build frontend for production"
	@echo ""
	@echo "  Dev Servers (run in separate terminals):"
	@echo "    make dev-backend        FastAPI server (port 8000)"
	@echo "    make dev-frontend       Vite dev server"
	@echo "    make dev-mcp            MCP servers"
	@echo "    make dev-poller         Module poller service"
	@echo ""
	@echo "  Database:"
	@echo "    make db-sync-dry        Preview table schema changes"
	@echo "    make db-sync            Apply table schema changes"
	@echo ""
	@echo "  Cleanup:"
	@echo "    make clean              Remove generated artifacts"
	@echo ""

# ── Lint ────────────────────────────────────────────────────────────────────

lint: lint-backend lint-frontend

lint-backend:
	uv run ruff check src/ backend/

lint-frontend:
	cd frontend && npx eslint src/

# ── Type Check ──────────────────────────────────────────────────────────────

typecheck: typecheck-backend typecheck-frontend

typecheck-backend:
	uv run pyright src/ backend/

typecheck-frontend:
	cd frontend && npx tsc --noEmit

# ── Test ────────────────────────────────────────────────────────────────────

test: test-backend

test-backend:
	uv run pytest tests/ -v

# ── Build ───────────────────────────────────────────────────────────────────

build-frontend:
	cd frontend && npm run build

# ── Dev Servers ─────────────────────────────────────────────────────────────

dev-db-proxy:
	uv run python -m xyz_agent_context.utils.sqlite_proxy_server

dev-backend:
	uv run uvicorn backend.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-mcp:
	uv run python -m xyz_agent_context.module.module_runner mcp

dev-poller:
	uv run python -m xyz_agent_context.services.module_poller

# ── Database ────────────────────────────────────────────────────────────────

db-sync-dry:
	cd src/xyz_agent_context/utils/database_table_management && uv run python sync_all_tables.py --dry-run

db-sync:
	cd src/xyz_agent_context/utils/database_table_management && uv run python sync_all_tables.py

# ── Cleanup ─────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
