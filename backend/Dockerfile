FROM node:22-slim AS dwg
# The DWG reader (GNU LibreDWG compiled to WebAssembly) runs as a separate program.
WORKDIR /dwg
COPY tools/dwg/package.json tools/dwg/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=dwg /usr/local/bin/node /usr/local/bin/node
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .
COPY --from=dwg /dwg/node_modules tools/dwg/node_modules
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "alembic upgrade head && python -m app.services.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
