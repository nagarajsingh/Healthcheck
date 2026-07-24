FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.30.14

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Corporate networks may block plain HTTP package repositories with status 470.
# Convert Debian package sources to HTTPS before apt or Playwright uses them.
RUN if [ -f /etc/apt/sources.list ]; then \
      sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list; \
    fi \
    && if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
    && rm -rf /var/lib/apt/lists/*

# Install kubectl directly instead of configuring another apt repository.
RUN curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client=true

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
