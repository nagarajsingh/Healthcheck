# Kubernetes Health Check

Daily Kubernetes health reporting for deployments, pods, mounted filesystems, Spring Cloud Data Flow streams, endpoints, and the TitanMS Legal Entity dropdown.

## TitanMS legal-entity check

The check opens the login page without entering a username or password, reads the Legal Entity dropdown, and verifies the configured entities.

Expected successful report row:

```json
{
  "component": "TitanMS Legal Entities",
  "count": "5/5",
  "status": "All Entities Available",
  "comments": "Entities: AE, BH, EG, KW, QA",
  "health": "Green"
}
```

Configuration is under `titanms_legal_entities` in `config/environments.yaml` and `manifests/k8s-dev.yaml`.

The default selectors assume a native HTML `<select>`:

```yaml
selectors:
  dropdown: "select"
  options: "select option"
```

For a custom React or Angular dropdown, inspect the element and update the selectors, for example:

```yaml
selectors:
  dropdown: '[role="combobox"]'
  options: '[role="option"]'
```

## Project structure

```text
├── Dockerfile
├── README.md
├── azure-pipelines.yaml
├── config
│   └── environments.yaml
├── manifests
│   ├── k8s-dev.yaml
│   └── k8s-prod.yaml
├── requirements.txt
├── scripts
│   ├── collect_health.py
│   └── send_email.py
└── templates
    └── health-report.html.j2
```

## Local validation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m py_compile scripts/collect_health.py scripts/send_email.py
```

Run the report using your current kubeconfig:

```bash
python scripts/collect_health.py \
  --config config/environments.yaml \
  --template-dir templates \
  --output-dir output
```

Generated files:

```text
output/health-report.html
output/health-report.json
output/email-subject.txt
```

## Build

```bash
docker build \
  -t mashrequae.azurecr.io/kubernetes-health-check:dev .
```

The image includes `kubectl`, Playwright, and Chromium. The browser runs headlessly with `--no-sandbox` and `--disable-dev-shm-usage` for Kubernetes compatibility.

## Deploy to development

```bash
kubectl apply -f manifests/k8s-dev.yaml
```

Run an immediate test job:

```bash
JOB_NAME="health-check-titanms-$(date +%s)"

kubectl create job \
  --from=cronjob/kubernetes-health-check \
  "$JOB_NAME" \
  -n health-check-dev

kubectl logs \
  -n health-check-dev \
  -f job/"$JOB_NAME"
```

## Health behaviour

- **Green:** every configured entity is present and no unexpected option is returned.
- **Amber:** every expected entity is present, but additional unexpected entities are also present.
- **Red:** expected entities are missing, the page cannot be opened, the dropdown times out, or browser execution fails.

## Network requirements

The health-check pod must resolve and connect to:

```text
https://titanms-dev.mashreqdev.com/login
https://scdf-dev.mashreqdev.com/streams/definitions
```

Both hosts are included in `NO_PROXY` and `no_proxy` in the development manifest.

## Production manifest

`manifests/k8s-prod.yaml` intentionally contains an empty environment list. Add only approved production namespaces and URLs before deploying it.
