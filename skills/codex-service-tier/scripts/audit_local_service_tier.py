#!/usr/bin/env python3
"""Audit locally persisted Codex service-tier evidence for specific threads."""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


TIER_FIELDS = {"service_tier", "serviceTier"}
TIER_VALUES = {"auto", "default", "flex", "fast", "priority", "ultrafast"}
THREAD_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def fail(message):
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def find_fields(value, location="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key in TIER_FIELDS and (child is None or isinstance(child, str) and child in TIER_VALUES):
                yield child_location, child, value
            yield from find_fields(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from find_fields(child, f"{location}[{index}]")


def classify_field(location, parent):
    normalized = location.lower()
    parent_location = normalized.rsplit(".", 1)[0]
    if parent_location.endswith(".thread_settings"):
        return "configuration"
    if parent_location.endswith(".turn_start_options"):
        return "request"
    if isinstance(parent, dict) and parent.get("object") == "response":
        return "response"
    return "unclassified"


def evidence(source, location, value, **details):
    item = {"source": source, "field_path": location, "value": value}
    item.update(details)
    return item


def latest_by_position(items, position_name):
    if not items:
        return None
    return max(items, key=lambda item: item[position_name])


def timestamp_key(item):
    timestamp = item.get("timestamp")
    if not timestamp:
        return 0
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def latest_by_timestamp(items):
    if not items:
        return None
    return max(items, key=timestamp_key)


def latest_evidence(*items):
    return latest_by_timestamp([item for item in items if item is not None])


def effective_config_evidence(codex_home):
    config_path = codex_home / "config.toml"
    auth_path = codex_home / "auth.json"
    try:
        raw_config = config_path.read_text(encoding="utf-8")
        config = tomllib.loads(raw_config)
    except (OSError, tomllib.TOMLDecodeError) as error:
        fail(f"Cannot read Codex config {config_path}: {error}")

    profile_name = config.get("profile")
    field_path = "$.service_tier"
    service_tier = config.get("service_tier")
    section_name = ""
    if profile_name:
        profile = config.get("profiles", {}).get(profile_name, {})
        if "service_tier" in profile:
            service_tier = profile.get("service_tier")
            field_path = f"$.profiles.{profile_name}.service_tier"
            section_name = f"profiles.{profile_name}"

    line_number = None
    fast_mode_line = None
    active_section = ""
    for index, line in enumerate(raw_config.splitlines(), 1):
        section_match = re.match(r"\s*\[([^\]]+)\]\s*(?:#.*)?$", line)
        if section_match:
            active_section = section_match.group(1)
            continue
        if active_section == section_name and re.match(r"\s*service_tier\s*=", line):
            line_number = index
        if active_section == "features" and re.match(r"\s*fast_mode\s*=", line):
            fast_mode_line = index

    auth_mode = None
    if auth_path.is_file():
        try:
            auth_mode = json.loads(auth_path.read_text(encoding="utf-8")).get("auth_mode")
        except (OSError, json.JSONDecodeError) as error:
            fail(f"Cannot read Codex auth mode {auth_path}: {error}")

    provider_name = config.get("model_provider", "openai")
    provider = config.get("model_providers", {}).get(provider_name, {})

    return {
        "path": str(config_path),
        "auth_path": str(auth_path),
        "auth_mode": auth_mode,
        "profile": profile_name,
        "model": config.get("model"),
        "model_provider": provider_name,
        "provider_requires_openai_auth": provider.get("requires_openai_auth"),
        "service_tier": evidence("config", field_path, service_tier, line=line_number),
        "fast_mode_feature": evidence("config", "$.features.fast_mode", config.get("features", {}).get("fast_mode"), line=fast_mode_line),
    }


def desktop_state_evidence(codex_home):
    state_path = codex_home / ".codex-global-state.json"
    if not state_path.is_file():
        return {"path": str(state_path), "evidence": None}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot read Codex Desktop state {state_path}: {error}")
    value = state.get("electron-persisted-atom-state", {}).get("default-service-tier")
    return {
        "path": str(state_path),
        "evidence": evidence("desktop_state", "$.electron-persisted-atom-state.default-service-tier", value),
    }


def rollout_evidence(codex_home, thread_id):
    files = []
    for directory_name in ("sessions", "archived_sessions"):
        root = codex_home / directory_name
        if root.is_dir():
            files.extend(root.rglob(f"*{thread_id}.jsonl"))

    found = {"configuration": [], "request": [], "response": [], "unclassified": []}
    candidate_count = 0
    for path in sorted(set(files)):
        try:
            stream = path.open(encoding="utf-8")
        except OSError as error:
            fail(f"Cannot read rollout {path}: {error}")
        with stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for location, value, parent in find_fields(record):
                    candidate_count += 1
                    category = classify_field(location, parent)
                    found[category].append(evidence(
                        "rollout",
                        location,
                        value,
                        path=str(path),
                        line=line_number,
                        timestamp=record.get("timestamp"),
                        record_type=record.get("type"),
                    ))

    return {
        "paths": [str(path) for path in sorted(set(files))],
        "candidate_count": candidate_count,
        "latest_configuration": latest_by_timestamp(found["configuration"]),
        "latest_request": latest_by_timestamp(found["request"]),
        "latest_response": latest_by_timestamp(found["response"]),
        "latest_unclassified": latest_by_timestamp(found["unclassified"]),
    }


def normalize_handler_tier(raw_value):
    if raw_value in ("None", "Some(None)"):
        return None
    match = re.fullmatch(r'Some\(Some\("([^"]+)"\)\)', raw_value)
    if match and match.group(1) in TIER_VALUES:
        return match.group(1)
    return None


def sqlite_evidence(codex_home, thread_id):
    database_path = codex_home / "logs_2.sqlite"
    if not database_path.is_file():
        return {"path": str(database_path), "candidate_count": 0, "latest_request": None, "latest_response": None, "latest_unclassified": None}

    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT id, ts, target, file, line, feedback_log_body FROM logs "
            "WHERE thread_id = ? AND (feedback_log_body LIKE '%service_tier%' OR feedback_log_body LIKE '%serviceTier%') "
            "ORDER BY id",
            (thread_id,),
        ).fetchall()
    except sqlite3.Error as error:
        fail(f"Cannot query Codex log database {database_path}: {error}")
    finally:
        if "connection" in locals():
            connection.close()

    found = {"request": [], "response": [], "unclassified": []}
    for row_id, timestamp, target, file_name, source_line, body in rows:
        timestamp_utc = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None
        parsed = None
        try:
            parsed = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            pass

        if parsed is not None:
            for location, value, parent in find_fields(parsed):
                category = classify_field(location, parent)
                if category == "configuration":
                    category = "unclassified"
                found[category].append(evidence(
                    "sqlite",
                    location,
                    value,
                    row_id=row_id,
                    timestamp=timestamp_utc,
                    target=target,
                    source_file=file_name,
                    source_line=source_line,
                ))

        if target == "codex_core::session::handlers" and "TurnStartOptions" in (body or ""):
            match = re.search(r"service_tier:\s*(Some\(Some\(\"[^\"]+\"\)\)|Some\(None\)|None)", body)
            if match:
                found["request"].append(evidence(
                    "sqlite",
                    "$text.TurnStartOptions.service_tier",
                    normalize_handler_tier(match.group(1)),
                    row_id=row_id,
                    timestamp=timestamp_utc,
                    target=target,
                    source_file=file_name,
                    source_line=source_line,
                ))

    return {
        "path": str(database_path),
        "candidate_count": len(rows),
        "latest_request": latest_by_position(found["request"], "row_id"),
        "latest_response": latest_by_position(found["response"], "row_id"),
        "latest_unclassified": latest_by_position(found["unclassified"], "row_id"),
    }


def desktop_evidence(thread_id):
    local_app_data = os.environ.get("LOCALAPPDATA")
    logs_root = Path(local_app_data) / "Codex" / "Logs" if local_app_data else None
    rg_path = shutil.which("rg")
    if logs_root is None or not logs_root.is_dir():
        return {"root": str(logs_root) if logs_root else None, "candidate_files": [], "candidate_count": 0, "latest_associated_response": None, "latest_associated_unclassified": None, "latest_unassociated": None}
    if rg_path is None:
        fail("rg is required to audit Codex Desktop logs.")

    file_search = subprocess.run(
        [rg_path, "--files-with-matches", "--glob", "*.log", "--fixed-strings", thread_id, str(logs_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if file_search.returncode not in (0, 1):
        fail(f"Cannot locate Codex Desktop logs for thread {thread_id}: rg exit code {file_search.returncode}")
    candidate_files = [line for line in file_search.stdout.splitlines() if line]
    if not candidate_files:
        return {"root": str(logs_root), "candidate_files": [], "candidate_count": 0, "latest_associated_response": None, "latest_associated_unclassified": None, "latest_unassociated": None}

    field_search = subprocess.run(
        [rg_path, "--json", "-n", "serviceTier|service_tier", *candidate_files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if field_search.returncode not in (0, 1):
        fail(f"Cannot inspect Codex Desktop tier fields for thread {thread_id}: rg exit code {field_search.returncode}")

    associated_unclassified = []
    unassociated = []
    pattern = re.compile(r'(serviceTier|service_tier)\\?["\']?\s*(?:=|:)\s*\\?["\']?(null|None|auto|default|flex|fast|priority|ultrafast)', re.IGNORECASE)
    for raw_result in field_search.stdout.splitlines():
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            continue
        if result.get("type") != "match":
            continue
        data = result.get("data", {})
        line_text = data.get("lines", {}).get("text", "")
        line_number = data.get("line_number")
        path_text = data.get("path", {}).get("text")
        timestamp = line_text.split(maxsplit=1)[0] if line_text else None
        for match in pattern.finditer(line_text):
            raw_value = match.group(2)
            value = None if raw_value.lower() in ("null", "none") else raw_value.lower()
            item = evidence(
                "desktop",
                f"$text.{match.group(1)}",
                value,
                path=path_text,
                line=line_number,
                timestamp=timestamp,
            )
            if thread_id in line_text:
                associated_unclassified.append(item)
            else:
                unassociated.append(item)

    return {
        "root": str(logs_root),
        "candidate_files": candidate_files,
        "candidate_count": len(associated_unclassified) + len(unassociated),
        "latest_associated_response": None,
        "latest_associated_unclassified": latest_by_timestamp(associated_unclassified),
        "latest_unassociated": latest_by_timestamp(unassociated),
    }


parser = argparse.ArgumentParser(description="Audit local Codex service-tier evidence.")
parser.add_argument("--thread-id", action="append", required=True, help="Codex main-thread or side-conversation ID; repeat for more than one.")
args = parser.parse_args()
invalid_thread_ids = [thread_id for thread_id in args.thread_id if THREAD_ID_PATTERN.fullmatch(thread_id) is None]
if invalid_thread_ids:
    fail("Every --thread-id must be a Codex UUID.")

codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
config_result = effective_config_evidence(codex_home)
desktop_state_result = desktop_state_evidence(codex_home)
thread_results = []
for thread_id in dict.fromkeys(args.thread_id):
    rollout_result = rollout_evidence(codex_home, thread_id)
    sqlite_result = sqlite_evidence(codex_home, thread_id)
    desktop_result = desktop_evidence(thread_id)
    actual_response = latest_evidence(rollout_result["latest_response"], sqlite_result["latest_response"], desktop_result["latest_associated_response"])
    request_override = latest_evidence(rollout_result["latest_request"], sqlite_result["latest_request"])
    thread_results.append({
        "thread_id": thread_id,
        "rollout": rollout_result,
        "sqlite": sqlite_result,
        "desktop": desktop_result,
        "latest_task_configuration": rollout_result["latest_configuration"],
        "latest_request_override": request_override,
        "actual_response_service_tier": actual_response,
    })

print(json.dumps({"config": config_result, "desktop_state": desktop_state_result, "threads": thread_results}, ensure_ascii=False, indent=2))
