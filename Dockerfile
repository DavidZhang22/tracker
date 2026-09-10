FROM node:24-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.8.22
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/README.MD ./
RUN uv sync --frozen --no-dev
COPY backend/app/main.py ./app/main.py
COPY backend/app/tracker ./app/tracker
COPY --from=frontend /src/frontend/build /app/frontend/build
RUN useradd --create-home tracker && mkdir -p /data && chown tracker:tracker /data && chmod 700 /data
ENV TRACKER_DB=/data/tracker.sqlite3
USER tracker
EXPOSE 8000
CMD ["/app/backend/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
