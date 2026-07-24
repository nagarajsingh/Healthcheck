FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ARG KUBECTL_VERSION=v1.30.14

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Install kubectl directly instead of using APT. This avoids failures when
# corporate proxies block Ubuntu archive repositories over plain HTTP.
RUN curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client=true

COPY requirements.txt /app/requirements.txt

# The Playwright base image already includes Chromium and all required Linux
# libraries, so do not run `playwright install --with-deps` here.
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -c "from playwright.sync_api import sync_playwright; print('Playwright import successful')"

COPY scripts/ /app/scripts/
COPY templates/ /app/templates/
COPY config/ /app/config/

RUN useradd --uid 10001 --create-home healthcheck \
    && mkdir -p /app/output \
    && chown -R healthcheck:healthcheck /app

USER 10001

ENTRYPOINT ["python", "/app/scripts/collect_health.py"]
CMD ["--config", "/app/config/environments.yaml", "--template-dir", "/app/templates", "--output-dir", "/app/output"]
