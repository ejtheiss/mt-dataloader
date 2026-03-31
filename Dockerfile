FROM python:3.12-slim

ARG APP_VERSION=0.5.0
LABEL org.opencontainers.image.version="${APP_VERSION}"
LABEL org.opencontainers.image.title="MT Dataloader"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Do not run pyngrok/ngrok download here: `installer.install_ngrok` takes a path string,
# not PyngrokConfig, and bin.ngrok.com often returns 403 during `docker build` (WAF/bot filters).
# The agent is installed on first tunnel start from /listen (pyngrok downloads then).

COPY . .

RUN mkdir -p runs logs

EXPOSE 8000

ENV DATALOADER_RUNS_DIR=runs \
    DATALOADER_LOG_LEVEL=INFO

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
