.PHONY: help build up down logs restart clean migrate-check test-health \
        shell-postgres shell-redis ps model-reinit build-frontend

# Default target
help:
	@echo "Enterprise RAG Pipeline — Makefile"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  build          Build all application Docker images"
	@echo "  up             Start the full stack (infra + services)"
	@echo "  up-infra       Start infrastructure services only"
	@echo "  down           Stop and remove containers"
	@echo "  down-volumes   Stop containers and remove named volumes"
	@echo "  logs           Follow logs for all services"
	@echo "  logs-app       Follow logs for application services only"
	@echo "  ps             Show running containers"
	@echo "  build-frontend Build the frontend Docker image only"
	@echo "  logs-frontend  Follow logs for the frontend service"
	@echo "  restart        Restart all application services"
	@echo "  clean          Remove containers, volumes, and built images"
	@echo "  model-reinit   Force re-download and re-quantize ONNX models"
	@echo "  migrate-check  Verify the PostgreSQL schema is applied"
	@echo "  test-health    Check health endpoints for API services"
	@echo "  shell-postgres Open psql shell in the postgres container"
	@echo "  shell-redis    Open redis-cli shell in the redis container"
	@echo "  lint           Run ruff linter across all services"
	@echo "  format         Run ruff formatter across all services"

# ─── Environment ────────────────────────────────────────────────────────────────

.env:
	@if [ ! -f .env ]; then \
		echo "Copying .env.example → .env"; \
		cp .env.example .env; \
	fi

# ─── Build ──────────────────────────────────────────────────────────────────────

build: .env
	docker compose build --parallel

build-no-cache: .env
	docker compose build --no-cache --parallel

build-frontend: .env
	docker compose build frontend

# ─── Lifecycle ──────────────────────────────────────────────────────────────────

up: .env
	docker compose up -d
	@echo ""
	@echo "Stack is starting. Run 'make logs' to follow progress."
	@echo "  Frontend:      http://localhost:3001"
	@echo "  Ingest API:    http://localhost:18000/docs"
	@echo "  Retrieval API: http://localhost:18001/docs"
	@echo "  OCR API:       http://localhost:8002/docs"
	@echo "  RabbitMQ UI:   http://localhost:15672  (guest/guest or env creds)"
	@echo "  MinIO Console: http://localhost:19001"
	@echo "  Prometheus:    http://localhost:9090"
	@echo "  Grafana:       http://localhost:3000"
	@echo "  Jaeger UI:     http://localhost:16686"

up-infra: .env
	docker compose up -d postgres redis rabbitmq minio minio-init jaeger prometheus grafana

down:
	docker compose down

down-volumes:
	docker compose down -v

# ─── Logs ───────────────────────────────────────────────────────────────────────

logs:
	docker compose logs -f

logs-app:
	docker compose logs -f ingest-api ingestion-worker ocr-service embedding-service retrieval-api frontend

logs-frontend:
	docker compose logs -f frontend

logs-infra:
	docker compose logs -f postgres redis rabbitmq minio

# ─── Status ─────────────────────────────────────────────────────────────────────

ps:
	docker compose ps

# ─── Restart ────────────────────────────────────────────────────────────────────

restart:
	docker compose restart ingest-api ingestion-worker ocr-service embedding-service retrieval-api frontend

restart-all:
	docker compose restart

# ─── Models ─────────────────────────────────────────────────────────────────────

model-reinit:
	docker compose run --rm -e FORCE_REINIT=true model-init

# ─── Database ───────────────────────────────────────────────────────────────────

migrate-check:
	docker compose exec postgres psql -U raguser -d ragdb -c "\dt"

shell-postgres:
	docker compose exec postgres psql -U raguser -d ragdb

# ─── Redis ──────────────────────────────────────────────────────────────────────

shell-redis:
	docker compose exec redis redis-cli

# ─── Health checks ──────────────────────────────────────────────────────────────

test-health:
	@echo "Checking service health endpoints..."
	@curl -sf http://localhost:18000/api/v1/health | python3 -m json.tool || echo "ingest-api: UNHEALTHY"
	@curl -sf http://localhost:18001/api/v1/health | python3 -m json.tool || echo "retrieval-api: UNHEALTHY"

# ─── Clean ──────────────────────────────────────────────────────────────────────

clean: down-volumes
	docker compose rm -f
	docker rmi -f $$(docker images --filter=reference="document-simple-rag*" -q) 2>/dev/null || true

# ─── Linting / Formatting ────────────────────────────────────────────────────────

lint:
	@for svc in shared services/ingest-api services/ingestion-worker services/ocr-service services/embedding-service services/retrieval-api services/model-init; do \
		echo "Linting $$svc ..."; \
		(cd $$svc && python -m ruff check . 2>/dev/null || true); \
	done

format:
	@for svc in shared services/ingest-api services/ingestion-worker services/ocr-service services/embedding-service services/retrieval-api services/model-init; do \
		echo "Formatting $$svc ..."; \
		(cd $$svc && python -m ruff format . 2>/dev/null || true); \
	done
