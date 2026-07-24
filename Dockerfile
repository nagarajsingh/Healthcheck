FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key \
       | gpg --dearmor -o /usr/share/keyrings/kubernetes-apt-keyring.gpg \
    && echo 'deb [signed-by=/usr/share/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' \
       > /etc/apt/sources.list.d/kubernetes.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends kubectl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium

COPY scripts/ /app/scripts/
COPY templates/ /app/templates/
COPY config/ /app/config/

RUN useradd --uid 10001 --create-home healthcheck \
    && mkdir -p /app/output \
    && chown -R healthcheck:healthcheck /app /ms-playwright

USER 10001

ENTRYPOINT ["python", "/app/scripts/collect_health.py"]
CMD ["--config", "/app/config/environments.yaml", "--template-dir", "/app/templates", "--output-dir", "/app/output"]
