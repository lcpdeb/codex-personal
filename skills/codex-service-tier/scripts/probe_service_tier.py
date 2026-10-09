#!/usr/bin/env python3
"""Send one minimal Responses API request and print its service-tier evidence."""

import argparse
import json
import os
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def fail(message):
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


parser = argparse.ArgumentParser(description="Probe the actual service tier of one new Codex provider request.")
parser.add_argument("--model", default="gpt-5.6-luna")
parser.add_argument("--reasoning-effort", default="none")
parser.add_argument("--service-tier", choices=("auto", "default", "flex", "fast", "priority", "ultrafast"))
args = parser.parse_args()

codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
config_path = codex_home / "config.toml"
try:
    with config_path.open("rb") as stream:
        config = tomllib.load(stream)
except (OSError, tomllib.TOMLDecodeError) as error:
    fail(f"Cannot read Codex config {config_path}: {error}")

provider_name = config.get("model_provider", "openai")
provider = config.get("model_providers", {}).get(provider_name)
if provider is None:
    if provider_name != "openai":
        fail(f"Model provider is not configured: {provider_name}")
    provider = {"base_url": "https://api.openai.com/v1", "requires_openai_auth": True}

base_url = provider.get("base_url")
if not isinstance(base_url, str) or not base_url:
    fail(f"Model provider has no base_url: {provider_name}")
parsed_base_url = urllib.parse.urlparse(base_url)
if parsed_base_url.scheme not in ("http", "https") or not parsed_base_url.netloc:
    fail(f"Model provider base_url is invalid: {provider_name}")

service_tier = args.service_tier or config.get("service_tier")
if not service_tier:
    fail("No service tier was supplied or configured.")

headers = {"Content-Type": "application/json"}
configured_headers = provider.get("http_headers") or {}
if not isinstance(configured_headers, dict):
    fail(f"Model provider http_headers must be a table: {provider_name}")
headers.update({str(name): str(value) for name, value in configured_headers.items()})

bearer_token = provider.get("experimental_bearer_token")
if bearer_token:
    headers["Authorization"] = f"Bearer {bearer_token}"
elif provider.get("requires_openai_auth", provider_name == "openai"):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        auth_path = codex_home / "auth.json"
        try:
            api_key = json.loads(auth_path.read_text(encoding="utf-8")).get("OPENAI_API_KEY")
        except (OSError, json.JSONDecodeError) as error:
            fail(f"Cannot read Codex auth {auth_path}: {error}")
    if not api_key:
        fail("No OPENAI_API_KEY is available.")
    headers["Authorization"] = f"Bearer {api_key}"

payload = json.dumps({
    "model": args.model,
    "input": "Reply exactly: OK",
    "reasoning": {"effort": args.reasoning_effort},
    "service_tier": service_tier,
    "store": False,
    "max_output_tokens": 16,
}).encode("utf-8")
endpoint = base_url.rstrip("/") + "/responses"
request_started_utc = datetime.now(timezone.utc).isoformat()
request = urllib.request.Request(endpoint, data=payload, method="POST", headers=headers)

try:
    with urllib.request.build_opener(NoRedirectHandler).open(request, timeout=120) as result:
        response = json.load(result)
except urllib.error.HTTPError as error:
    fail(f"Probe request failed for provider {provider_name}: HTTP {error.code}")
except urllib.error.URLError as error:
    fail(f"Probe request failed for provider {provider_name}: {error.reason}")
except json.JSONDecodeError as error:
    fail(f"Probe response is not valid JSON: {error}")

if not isinstance(response, dict):
    fail("Probe response is not a JSON object.")

usage = response.get("usage") or {}
reasoning = response.get("reasoning") or {}
evidence = {
    "request_started_utc": request_started_utc,
    "provider": provider_name,
    "response_id": response.get("id"),
    "model": response.get("model"),
    "requested_service_tier": service_tier,
    "actual_service_tier": response.get("service_tier"),
    "requested_reasoning_effort": args.reasoning_effort,
    "actual_reasoning_effort": reasoning.get("effort"),
    "status": response.get("status"),
    "input_tokens": usage.get("input_tokens"),
    "output_tokens": usage.get("output_tokens"),
    "total_tokens": usage.get("total_tokens"),
}
print(json.dumps(evidence, ensure_ascii=False, indent=2))
