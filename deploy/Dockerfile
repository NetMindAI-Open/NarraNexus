FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY backend/ backend/

# Install dependencies
RUN uv sync --no-dev

# Expose port
EXPOSE 8000

# Start server
CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
