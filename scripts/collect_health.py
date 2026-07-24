#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from zoneinfo import ZoneInfo

import requests
import yaml
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


def run_command(command: List[str], timeout: int = 30) -> Dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"Command timed out after {timeout} seconds", "returncode": 124}
    except FileNotFoundError as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": 127}


def kubectl_base(context: Optional[str] = None) -> List[str]:
    command = ["kubectl"]
    if context:
        command += ["--context", context]
    return command


def kubectl_json(context: Optional[str], namespace: Optional[str], resource: str, timeout: int = 30) -> Dict[str, Any]:
    command = kubectl_base(context)
    if namespace:
        command += ["-n", namespace]
    command += ["get", resource, "-o", "json"]
    result = run_command(command, timeout=timeout)
    if not result["ok"]:
        raise RuntimeError(result["stderr"] or f"kubectl failed for {resource}")
    return json.loads(result["stdout"])


def deployment_summary(context: Optional[str], namespace: str) -> Dict[str, Any]:
    data = kubectl_json(context, namespace, "deployments")
    total = len(data.get("items", []))
    available = 0
    unavailable_names: List[str] = []
    for item in data.get("items", []):
        desired = item.get("spec", {}).get("replicas", 1) or 0
        available_replicas = item.get("status", {}).get("availableReplicas", 0) or 0
        if available_replicas >= desired:
            available += 1
        else:
            unavailable_names.append(item.get("metadata", {}).get("name", "unknown"))
    return {
        "component": "Deployments",
        "count": total,
        "status": f"{available} Available" + (f", {len(unavailable_names)} Unavailable" if unavailable_names else ""),
        "comments": ", ".join(unavailable_names[:10]),
        "health": "Green" if available == total else "Amber",
    }


def pod_summary(context: Optional[str], namespace: str) -> Dict[str, Any]:
    data = kubectl_json(context, namespace, "pods")
    items = data.get("items", [])
    phase_counts: Counter = Counter()
    not_ready: List[str] = []
    problem_reasons: Counter = Counter()
    for item in items:
        phase = item.get("status", {}).get("phase", "Unknown")
        phase_counts[phase] += 1
        statuses = item.get("status", {}).get("containerStatuses", []) or []
        all_ready = bool(statuses) and all(status.get("ready", False) for status in statuses)
        if phase == "Running" and not all_ready:
            not_ready.append(item.get("metadata", {}).get("name", "unknown"))
        for status in statuses:
            waiting = status.get("state", {}).get("waiting")
            terminated = status.get("state", {}).get("terminated")
            if waiting and waiting.get("reason"):
                problem_reasons[waiting["reason"]] += 1
            if terminated and terminated.get("reason") not in (None, "Completed"):
                problem_reasons[terminated["reason"]] += 1
    parts = [f"{phase_counts[p]} {p}" for p in ("Running", "Pending", "Failed", "Succeeded", "Unknown") if phase_counts[p]]
    if not_ready:
        parts.append(f"{len(not_ready)} Not Ready")
    healthy = phase_counts["Pending"] == 0 and phase_counts["Failed"] == 0 and phase_counts["Unknown"] == 0 and not not_ready
    comments: List[str] = []
    if problem_reasons:
        comments.append(", ".join(f"{key}: {value}" for key, value in problem_reasons.items()))
    if not_ready:
        comments.append("Not Ready: " + ", ".join(not_ready[:10]))
    return {
        "component": "Pods",
        "count": len(items),
        "status": ", ".join(parts) if parts else "No Pods",
        "comments": " | ".join(comments),
        "health": "Green" if healthy else "Amber",
    }


def find_running_pod_with_mount(context: Optional[str], namespace: str, mount_path: str, preferred_pod_prefix: str = "") -> Optional[Dict[str, str]]:
    pods = kubectl_json(context, namespace, "pods").get("items", [])
    if preferred_pod_prefix:
        pods.sort(key=lambda pod: 0 if pod.get("metadata", {}).get("name", "").startswith(preferred_pod_prefix) else 1)
    for pod in pods:
        if pod.get("status", {}).get("phase") != "Running":
            continue
        pod_name = pod.get("metadata", {}).get("name", "unknown")
        for container in pod.get("spec", {}).get("containers", []) or []:
            for volume_mount in container.get("volumeMounts", []) or []:
                mounted_path = volume_mount.get("mountPath", "")
                if mounted_path == mount_path or mount_path.startswith(mounted_path.rstrip("/") + "/"):
                    return {"pod": pod_name, "container": container.get("name", ""), "mount_path": mount_path}
    return None


def filesystem_summary(context: Optional[str], namespace: str, threshold: int, mount_path: str, warning_threshold: int = 90, preferred_pod_prefix: str = "") -> Dict[str, Any]:
    selected = find_running_pod_with_mount(context, namespace, mount_path, preferred_pod_prefix)
    component = f"File System ({mount_path})"
    if not selected:
        return {"component": component, "count": "N/A", "status": "Mount Not Found", "comments": f"No running pod was found with {mount_path} mounted", "health": "Amber"}
    command = kubectl_base(context) + ["-n", namespace, "exec", selected["pod"], "-c", selected["container"], "--", "df", "-P", mount_path]
    result = run_command(command, timeout=30)
    if not result["ok"]:
        return {"component": component, "count": "N/A", "status": "Unable to Check", "comments": result["stderr"], "health": "Amber"}
    lines = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
    fields = lines[-1].split() if len(lines) >= 2 else []
    if len(fields) < 6:
        return {"component": component, "count": "N/A", "status": "Unable to Parse", "comments": result["stdout"], "health": "Amber"}
    usage_text = fields[4]
    try:
        usage_percent = int(usage_text.rstrip("%"))
    except ValueError:
        return {"component": component, "count": usage_text, "status": "Unable to Parse", "comments": result["stdout"], "health": "Amber"}
    if usage_percent <= threshold:
        health, status = "Green", "Under Threshold"
    elif usage_percent <= warning_threshold:
        health, status = "Amber", "Warning"
    else:
        health, status = "Red", "Critical"
    return {
        "component": component,
        "count": usage_text,
        "status": status,
        "comments": f"Mount: {fields[5]}; Filesystem: {fields[0]}; Total blocks: {fields[1]}; Used blocks: {fields[2]}; Available blocks: {fields[3]}; Pod: {selected['pod']}; Container: {selected['container']}",
        "health": health,
    }


def get_stream_config(env_cfg: Dict[str, Any]) -> Dict[str, Any]:
    return env_cfg.get("streams") or env_cfg.get("streams_check") or {}


def build_scdf_request_url(base_url: str, page_size: int) -> str:
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("page", "0")
    query.setdefault("size", str(page_size))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def extract_scdf_definitions(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("content", "items", "definitions"):
        if isinstance(payload.get(key), list):
            return [item for item in payload[key] if isinstance(item, dict)]
    embedded = payload.get("_embedded")
    if isinstance(embedded, dict):
        for value in embedded.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def stream_summary(cfg: Dict[str, Any], timeout: int) -> Optional[Dict[str, Any]]:
    if not cfg.get("enabled", False):
        return None
    expected = [str(item).strip().upper() for item in cfg.get("expected_descriptions", []) if str(item).strip()]
    url = build_scdf_request_url(str(cfg.get("api_url") or cfg.get("url") or ""), int(cfg.get("page_size", 200)))
    try:
        if cfg.get("transport", "requests").lower() == "curl":
            command = ["curl", "-sS", "--max-time", str(timeout)]
            if not cfg.get("verify_ssl", True):
                command.append("-k")
            command.append(url)
            result = run_command(command, timeout=timeout + 5)
            if not result["ok"]:
                raise RuntimeError(result["stderr"])
            payload = json.loads(result["stdout"])
        else:
            response = requests.get(url, timeout=timeout, verify=cfg.get("verify_ssl", True))
            response.raise_for_status()
            payload = response.json()
        definitions = extract_scdf_definitions(payload)
        matched: Dict[str, List[Dict[str, Any]]] = {item: [] for item in expected}
        for definition in definitions:
            description = str(definition.get("description") or definition.get("name") or "").strip().upper()
            for entity in expected:
                if description == entity or description.startswith(entity):
                    matched[entity].append(definition)
                    break
        deployed, comments = [], []
        for entity in expected:
            values = matched[entity]
            statuses = [str(v.get("status") or v.get("deploymentStatus") or "unknown").strip().lower() for v in values]
            is_deployed = any(status in {"deployed", "deploying", "complete", "running"} for status in statuses)
            if is_deployed:
                deployed.append(entity)
            comments.append(f"{entity}={'deployed' if is_deployed else 'not deployed'}")
        missing = [entity for entity in expected if entity not in deployed]
        return {
            "component": "Streams",
            "count": f"{len(deployed)}/{len(expected)}",
            "status": "All Deployed" if not missing else "Not Deployed: " + ", ".join(missing),
            "comments": "; ".join(comments),
            "health": "Green" if not missing else "Red",
        }
    except Exception as exc:
        return {"component": "Streams", "count": f"0/{len(expected)}", "status": "Unable to Check", "comments": str(exc), "health": "Red"}


def normalize_entity(value: Any) -> str:
    return " ".join(str(value).strip().upper().split())


def titanms_legal_entities_summary(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not cfg.get("enabled", False):
        return None
    expected = {normalize_entity(item) for item in cfg.get("expected_entities", []) if normalize_entity(item)}
    url = str(cfg.get("url", "")).strip()
    selectors = cfg.get("selectors", {}) or {}
    dropdown_selector = str(selectors.get("dropdown", "select")).strip()
    options_selector = str(selectors.get("options", "select option")).strip()
    timeout_seconds = int(cfg.get("timeout_seconds", 30))
    timeout_ms = timeout_seconds * 1000
    if not url or not expected:
        return {"component": "TitanMS Legal Entities", "count": "N/A", "status": "Configuration Error", "comments": "URL or expected entities are missing", "health": "Red"}
    ignored = {normalize_entity(item) for item in cfg.get("ignored_options", ["Select", "Select Legal Entity", "Legal Entity", "Choose Legal Entity", "-- Select --"])}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                context = browser.new_context(ignore_https_errors=not cfg.get("verify_ssl", True))
                try:
                    page = context.new_page()
                    page.set_default_timeout(timeout_ms)
                    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if response is not None and response.status >= 400:
                        return {"component": "TitanMS Legal Entities", "count": f"0/{len(expected)}", "status": "Page Unavailable", "comments": f"HTTP {response.status} returned by {url}", "health": "Red"}
                    dropdown = page.locator(dropdown_selector).first
                    dropdown.wait_for(state="visible", timeout=timeout_ms)
                    tag_name = dropdown.evaluate("(element) => element.tagName.toLowerCase()")
                    if tag_name == "select":
                        option_texts = page.locator(options_selector).all_inner_texts()
                    else:
                        dropdown.click()
                        options = page.locator(options_selector)
                        options.first.wait_for(state="visible", timeout=timeout_ms)
                        option_texts = options.all_inner_texts()
                    actual = {normalize_entity(text) for text in option_texts if normalize_entity(text)} - ignored
                    missing = sorted(expected - actual)
                    unexpected = sorted(actual - expected)
                    found_count = len(expected) - len(missing)
                    if missing:
                        comments = "Available: " + (", ".join(sorted(actual)) if actual else "None") + " | Missing: " + ", ".join(missing)
                        if unexpected:
                            comments += " | Unexpected: " + ", ".join(unexpected)
                        return {"component": "TitanMS Legal Entities", "count": f"{found_count}/{len(expected)}", "status": "Missing Entities: " + ", ".join(missing), "comments": comments, "health": "Red"}
                    if unexpected:
                        return {"component": "TitanMS Legal Entities", "count": f"{found_count}/{len(expected)}", "status": "All Expected Entities Available", "comments": "Entities: " + ", ".join(sorted(expected)) + " | Unexpected: " + ", ".join(unexpected), "health": "Amber"}
                    return {"component": "TitanMS Legal Entities", "count": f"{len(expected)}/{len(expected)}", "status": "All Entities Available", "comments": "Entities: " + ", ".join(sorted(expected)), "health": "Green"}
                finally:
                    context.close()
            finally:
                browser.close()
    except PlaywrightTimeoutError as exc:
        return {"component": "TitanMS Legal Entities", "count": f"0/{len(expected)}", "status": "Page or Dropdown Timeout", "comments": f"Timed out after {timeout_seconds} seconds: {exc}", "health": "Red"}
    except Exception as exc:
        return {"component": "TitanMS Legal Entities", "count": f"0/{len(expected)}", "status": "Unable to Check", "comments": str(exc), "health": "Red"}


def endpoint_summaries(cfg: Dict[str, Any], timeout: int) -> List[Dict[str, Any]]:
    if not cfg.get("enabled", False):
        return []
    rows: List[Dict[str, Any]] = []
    for item in cfg.get("items", []):
        accepted = set(item.get("acceptable_status_codes", [200, 301, 302, 401, 403]))
        try:
            response = requests.get(item["url"], timeout=timeout, verify=item.get("verify_ssl", False), allow_redirects=False)
            accessible = response.status_code in accepted
            rows.append({"component": item["name"], "count": "Green" if accessible else "Red", "status": "Accessible" if accessible else f"HTTP {response.status_code}", "comments": item.get("comments", ""), "health": "Green" if accessible else "Red"})
        except Exception as exc:
            rows.append({"component": item["name"], "count": "Red", "status": "Not Accessible", "comments": str(exc), "health": "Red"})
    return rows


def collect_environment(env_cfg: Dict[str, Any], report_cfg: Dict[str, Any]) -> Dict[str, Any]:
    name, namespace, context = env_cfg["name"], env_cfg["namespace"], env_cfg.get("kube_context")
    connectivity = run_command(kubectl_base(context) + ["cluster-info"], timeout=20)
    if not connectivity["ok"]:
        return {"name": name, "rows": [{"component": "Cluster Connectivity", "count": "Red", "status": "Unavailable", "comments": connectivity["stderr"], "health": "Red"}]}
    namespace_check = run_command(kubectl_base(context) + ["get", "namespace", namespace], timeout=20)
    if not namespace_check["ok"]:
        return {"name": name, "rows": [{"component": "Namespace", "count": "Red", "status": "Not Found or Not Accessible", "comments": namespace_check["stderr"], "health": "Red"}]}
    rows: List[Dict[str, Any]] = []
    filesystem_cfg = env_cfg.get("filesystem", {})
    if filesystem_cfg.get("enabled", True):
        threshold = int(filesystem_cfg.get("threshold_percent", report_cfg.get("filesystem_threshold_percent", 75)))
        warning_threshold = int(filesystem_cfg.get("warning_threshold_percent", report_cfg.get("filesystem_warning_threshold_percent", 90)))
        mount_paths = filesystem_cfg.get("mount_paths") or [filesystem_cfg.get("mount_path", "/h2hnetapp")]
        for mount_path in mount_paths:
            try:
                rows.append(filesystem_summary(context, namespace, threshold, mount_path, warning_threshold, filesystem_cfg.get("preferred_pod_prefix", "")))
            except Exception as exc:
                rows.append({"component": f"File System ({mount_path})", "count": "N/A", "status": "Unable to Check", "comments": str(exc), "health": "Amber"})
    for component, function in (("Deployments", deployment_summary), ("Pods", pod_summary)):
        try:
            rows.append(function(context, namespace))
        except Exception as exc:
            rows.append({"component": component, "count": "N/A", "status": "Unable to Check", "comments": str(exc), "health": "Red"})
    timeout = int(report_cfg.get("request_timeout_seconds", 15))
    stream_row = stream_summary(get_stream_config(env_cfg), timeout)
    if stream_row:
        rows.append(stream_row)
    titanms_row = titanms_legal_entities_summary(env_cfg.get("titanms_legal_entities", {}))
    if titanms_row:
        rows.append(titanms_row)
    rows.extend(endpoint_summaries(env_cfg.get("endpoints", {}), timeout))
    return {"name": name, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--template-dir", default="templates")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with Path(args.config).open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    report_cfg = cfg.get("report", {})
    now = datetime.now(ZoneInfo(report_cfg.get("timezone", "Asia/Kolkata")))
    environments = [collect_environment(env, report_cfg) for env in cfg.get("environments", [])]
    template = Environment(loader=FileSystemLoader(args.template_dir), autoescape=True).get_template("health-report.html.j2")
    generated_at = now.strftime("%d-%B-%Y %I:%M %p %Z")
    title = report_cfg.get("title", "Kubernetes Environment Status")
    html_report = template.render(title=title, generated_at=generated_at, environments=environments)
    health_values = [row["health"] for environment in environments for row in environment["rows"]]
    overall_health = "CRITICAL" if "Red" in health_values else "WARNING" if "Amber" in health_values else "GREEN"
    subject = f"[{overall_health}] {title} - {now.strftime('%d-%B-%Y')}"
    (output_dir / "health-report.html").write_text(html_report, encoding="utf-8")
    (output_dir / "health-report.json").write_text(json.dumps({"title": title, "generated_at": generated_at, "environments": environments}, indent=2), encoding="utf-8")
    (output_dir / "email-subject.txt").write_text(subject, encoding="utf-8")
    print(f"Generated report in {output_dir}")
    print(f"Subject: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
