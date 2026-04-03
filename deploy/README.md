# NarraNexus Cloud Deployment

Same codebase as local desktop — only configuration differs.

## Quick Start

1. Copy environment template:
   ```bash
   cp .env.cloud.example .env.cloud
   ```

2. Edit `.env.cloud` with your MySQL and Supabase credentials

3. Start services:
   ```bash
   docker compose -f docker-compose.cloud.yml up -d
   ```

4. Build and serve frontend:
   ```bash
   cd ../frontend && npm ci && npm run build
   ```

5. Access at `http://localhost`

## Without Docker

```bash
# Set up environment
cp .env.cloud.example .env.cloud
# Edit .env.cloud

# Start backend
./start-cloud.sh
```
