"""Preview one Codex history; --apply backs up and removes non-gAAAA reasoning fields.

gAAAA is only a conservative format filter, not cryptographic validation.
Inherited byte/ordinal boundaries and all original byte offsets are preserved.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from uuid import UUID, uuid4


STORAGE_ID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl(?:\.zst)?$", re.I)
ENCRYPTED_FIELD = re.compile(rb'"encrypted_content"[ \t]*:[ \t]*"(?:[^"\\]|\\.)*"')


def unique_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def load_json(data, location):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique_members, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(f"Invalid JSON, UTF-8, or duplicate member at {location}") from None


def local_path(value, strict=False):
    text = str(value)
    if text.upper().startswith("\\\\?\\UNC\\") or (text.startswith("\\\\") and not text.startswith("\\\\?\\")):
        raise ValueError("UNC paths are not supported")
    path = Path(text.removeprefix("\\\\?\\")).resolve(strict=strict)
    return Path(str(path).removeprefix("\\\\?\\"))


def current_path(home, thread_id):
    with sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True) as database:
        rows = database.execute("SELECT rollout_path FROM threads WHERE id = ?", (thread_id,)).fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise ValueError("Expected exactly one thread with a rollout_path")
    return local_path(rows[0][0], strict=True)


def storage_id(path):
    match = STORAGE_ID.search(path.name)
    if not match:
        raise ValueError(f"Unknown rollout filename: {path}")
    return str(UUID(match[1]))


def remove_field(line, item, location):
    matches = list(ENCRYPTED_FIELD.finditer(line))
    if len(matches) != 1:
        raise ValueError(f"Expected one literal encrypted_content member at {location}")
    start, end = matches[0].span()
    right = end
    while right < len(line) and line[right:right + 1] in (b" ", b"\t"):
        right += 1
    if line[right:right + 1] == b",":
        end = right + 1
    else:
        left = len(line[:start].rstrip(b" \t"))
        if line[left - 1:left] != b",":
            raise ValueError(f"Missing adjacent member separator at {location}")
        start = left - 1
    replacement = b" " * (end - start)
    changed = line[:start] + replacement + line[end:]
    del item["payload"]["encrypted_content"]
    if len(changed) != len(line) or load_json(changed, location) != item:
        raise ValueError(f"Field removal changed other JSON values at {location}")
    return start, line[start:end], replacement


def inspect_history(home, thread_id):
    path = current_path(home, thread_id)
    current = path
    roots = [home / "sessions", home / "archived_sessions"]
    chain, seen = [], set()
    byte_limit = ordinal_limit = None
    while True:
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError(f"Rollout is outside Codex session directories: {path}")
        segment_id = storage_id(path)
        if segment_id in seen:
            raise ValueError(f"Cycle in history_base at {path}")
        seen.add(segment_id)
        raw = path.read_bytes()
        data = raw
        if path.suffix == ".zst":
            try:
                from compression import zstd
            except ImportError:
                raise ValueError("Reading .zst history requires Python 3.14 compression.zstd") from None
            data = zstd.decompress(raw)
        limit = len(data) if byte_limit is None else byte_limit
        if type(limit) is not int or not 0 < limit <= len(data) or data[limit - 1:limit] != b"\n":
            raise ValueError(f"Invalid byte boundary or incomplete final JSONL line at {path}")
        edits, offset, first_ordinal, previous, parent = [], 0, None, None, None
        for line_number, line in enumerate(data[:limit].splitlines(keepends=True), 1):
            location = f"{path}:{line_number}"
            if not line.endswith(b"\n"):
                raise ValueError(f"Incomplete JSONL line at {location}")
            item = load_json(line, location)
            if not isinstance(item, dict):
                raise ValueError(f"Invalid rollout record at {location}")
            payload, ordinal = item.get("payload"), item.get("ordinal")
            if type(ordinal) is not int or ordinal < 0:
                raise ValueError(f"Invalid rollout ordinal at {location}")
            if previous is not None and ordinal != previous + 1:
                raise ValueError(f"Non-contiguous ordinal at {location}")
            if line_number == 1:
                if item.get("type") != "session_meta" or not isinstance(payload, dict):
                    raise ValueError(f"Missing leading session_meta at {location}")
                first_ordinal, parent = ordinal, payload.get("history_base")
            elif item.get("type") == "session_meta":
                raise ValueError(f"Unexpected second session_meta at {location}")
            if item.get("type") == "response_item" and isinstance(payload, dict) and payload.get("type") == "reasoning" and "encrypted_content" in payload:
                encrypted = payload["encrypted_content"]
                if not isinstance(encrypted, str):
                    raise ValueError(f"Non-string encrypted_content at {location}")
                if encrypted and not encrypted.startswith("gAAAA"):
                    if path.suffix == ".zst":
                        raise ValueError(f"Refusing to rewrite compressed history: {location}")
                    start, before, after = remove_field(line, item, location)
                    edits.append((offset + start, before, after, line_number))
            previous = ordinal
            offset += len(line)
        if offset != limit or (ordinal_limit is not None and previous + 1 != ordinal_limit):
            raise ValueError(f"Byte and ordinal history boundaries disagree at {path}")
        chain.append({"path": path, "raw": raw, "limit": limit, "edits": edits})
        if parent is None:
            if first_ordinal != 0:
                raise ValueError(f"History without history_base must start at ordinal 0: {path}")
            break
        if not isinstance(parent, dict) or set(parent) != {"thread_id", "end_ordinal_exclusive", "end_byte_offset"}:
            raise ValueError(f"Unknown history_base schema at {path}")
        if type(parent["end_ordinal_exclusive"]) is not int or parent["end_ordinal_exclusive"] != first_ordinal:
            raise ValueError(f"history_base ordinal disagrees with segment start at {path}")
        if type(parent["end_byte_offset"]) is not int or parent["end_byte_offset"] <= 0:
            raise ValueError(f"Invalid history_base byte offset at {path}")
        parent_id = str(UUID(parent["thread_id"]))
        candidates = [local_path(candidate, strict=True) for root in roots if root.exists() for candidate in root.rglob("rollout-*.jsonl*") if STORAGE_ID.search(candidate.name) and storage_id(candidate) == parent_id]
        if len(candidates) != 1:
            raise ValueError(f"Expected one parent storage segment {parent_id}; found {len(candidates)}")
        path = candidates[0]
        byte_limit, ordinal_limit = parent["end_byte_offset"], parent["end_ordinal_exclusive"]
    return current, chain


def locked_file(path):
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Share reads only: existing writers prevent opening, and new writers/deleters are denied.
    handle = kernel.CreateFileW(str(path), 0xC0000000, 1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def write_new(path, data):
    with path.open("xb") as target:
        if target.write(data) != len(data):
            raise OSError(f"Short backup/manifest write: {path}")
        target.flush()
        os.fsync(target.fileno())
    if path.read_bytes() != data:
        raise OSError(f"Backup/manifest verification failed: {path}")


def apply_changes(home, thread_id, current, chain, backup_dir):
    if os.name != "nt":
        raise ValueError("--apply requires Windows file sharing locks")
    changed = [segment for segment in chain if segment["edits"]]
    with ExitStack() as stack:
        handles = {segment["path"]: stack.enter_context(locked_file(segment["path"])) for segment in sorted(changed, key=lambda entry: str(entry["path"]))}
        for segment in chain:
            target = handles.get(segment["path"])
            actual = target.read() if target is not None else segment["path"].read_bytes()
            if actual != segment["raw"]:
                raise OSError(f"History changed after preview: {segment['path']}")
        if current_path(home, thread_id) != current:
            raise OSError("Thread rollout_path changed after preview")
        backup_dir.mkdir(parents=True, exist_ok=False)
        manifest = {"thread_id": thread_id, "current_rollout": str(current), "filter": "nonempty reasoning encrypted_content without gAAAA prefix; no cryptographic validation", "files": []}
        for file_number, segment in enumerate(changed, 1):
            backup = backup_dir / f"{file_number:02d}-{segment['path'].name}.bak"
            write_new(backup, segment["raw"])
            expected = bytearray(segment["raw"])
            for offset, before, after, line_number in segment["edits"]:
                expected[offset:offset + len(before)] = after
            segment["expected"] = expected
            manifest["files"].append({"path": str(segment["path"]), "backup": str(backup), "size": len(segment["raw"]), "included_byte_limit": segment["limit"], "sha256_before": hashlib.sha256(segment["raw"]).hexdigest(), "sha256_after": hashlib.sha256(expected).hexdigest(), "field_count": len(segment["edits"]), "line_numbers": [edit[3] for edit in segment["edits"]]})
        write_new(backup_dir / "manifest.json", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        try:
            for segment in changed:
                target = handles[segment["path"]]
                for offset, _, after, line_number in segment["edits"]:
                    target.seek(offset)
                    if target.write(after) != len(after):
                        raise OSError(f"Short original write: {segment['path']}:{line_number}")
                target.flush()
                os.fsync(target.fileno())
                target.seek(0)
                if target.read() != segment["expected"]:
                    raise OSError(f"Full-file verification failed: {segment['path']}")
        except BaseException as error:
            rollback_errors = []
            for segment in reversed(changed):
                target = handles[segment["path"]]
                for offset, before, _, line_number in reversed(segment["edits"]):
                    try:
                        target.seek(offset)
                        if target.write(before) != len(before):
                            raise OSError(f"Short rollback write: {segment['path']}:{line_number}")
                    except BaseException as rollback_error:
                        rollback_errors.append(str(rollback_error))
                try:
                    target.flush()
                    os.fsync(target.fileno())
                    target.seek(0)
                    if target.read() != segment["raw"]:
                        raise OSError(f"Rollback verification failed: {segment['path']}")
                except BaseException as rollback_error:
                    rollback_errors.append(str(rollback_error))
            detail = f"{type(error).__name__}: {error}\nRollback errors: {rollback_errors}\n"
            try:
                write_new(backup_dir / "failure.txt", detail.encode("utf-8"))
            except OSError as record_error:
                print(f"Could not record failure: {record_error}", file=sys.stderr)
            raise RuntimeError(f"Cleanup failed; backups retained at {backup_dir}. {detail}") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thread_id", type=UUID)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--backup-dir", type=Path, help="New directory, created only when applying changes")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    home, thread_id = local_path(args.codex_home, strict=True), str(args.thread_id)
    # ponytail: hold one history chain in memory; stream if histories outgrow RAM.
    current, chain = inspect_history(home, thread_id)
    count = sum(len(segment["edits"]) for segment in chain)
    print(json.dumps({"mode": "apply" if args.apply else "preview", "thread_id": thread_id, "segments_checked": len(chain), "fields_to_remove": count, "files": [{"path": str(segment["path"]), "included_byte_limit": segment["limit"], "fields": len(segment["edits"])} for segment in chain]}, ensure_ascii=False, indent=2))
    if not args.apply or not count:
        return
    backup_dir = local_path(args.backup_dir) if args.backup_dir else home / "backups" / "encrypted-content" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8])
    apply_changes(home, thread_id, current, chain, backup_dir)
    print(f"Removed {count} fields; verified complete files. Backups: {backup_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
