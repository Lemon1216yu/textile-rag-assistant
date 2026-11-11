FROM node:22-alpine AS frontend
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /source/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=$NPM_REGISTRY --no-audit --no-fund
COPY 使用说明.html /source/
COPY docs/离线RAG运行说明.md docs/模型配置设置.md /source/docs/
COPY 手动验收/ /source/手动验收/
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /opt/zhizhi
ARG LOCAL_MODELS=0
COPY requirements.txt requirements-enterprise.txt requirements-local-models.txt ./
RUN pip install --no-cache-dir -r requirements-enterprise.txt && if [ "$LOCAL_MODELS" = "1" ]; then pip install --no-cache-dir -r requirements-local-models.txt; fi && useradd --uid 10001 --create-home app
COPY enterprise/ ./enterprise/
COPY examples/public_sources.json ./examples/public_sources.json
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY --from=frontend /source/frontend/dist ./frontend/dist/
RUN mkdir -p data/enterprise-files tmp && chown -R app:app data tmp
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=8)"
CMD ["python", "-m", "uvicorn", "enterprise.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
