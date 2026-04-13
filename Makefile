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
        dev-db-proxy dev-backend dev-frontend dev-mcp dev-poller \
        clean

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
	@echo "    make dev-db-proxy       SQLite proxy (start first, port 8100)"
	@echo "    make dev-backend        FastAPI server (port 8000)"
	@echo "    make dev-frontend       Vite dev server"
	@echo "    make dev-mcp            MCP servers"
	@echo "    make dev-poller         Module poller service"
	@echo ""
	@echo "  Database:"
	@echo "    Schema auto-migrates on startup (schema_registry.py)"
	@echo ""
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
	DASHBOARD_BIND_HOST=127.0.0.1 uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-mcp:
	uv run python -m xyz_agent_context.module.module_runner mcp

dev-poller:
	uv run python -m xyz_agent_context.services.module_poller

# ── Database ────────────────────────────────────────────────────────────────
# Schema is auto-migrated on startup via schema_registry.auto_migrate().
# No manual sync needed. To add tables/columns, edit schema_registry.py.

# ── MindFlow (doc tooling moved to MindFlow plugin) ────────────────────────
# Doc check/scaffold/audit commands are now in the MindFlow plugin CLI.
# Install MindFlow plugin for doc management: bash related_project/MindFlow/install.sh

# ── Cleanup ─────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
