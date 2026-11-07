#!/usr/bin/env python3
"""R-063/T34: release archive secret scanner.

Usage:
    py -3 tools/ci/check_release_secrets.py <archive...> --report <path> [--allowlist <json>]

Exit codes: 0 = no findings, 1 = findings, 2 = error.

Archives are read in memory (zipfile/tarfile); nothing is extracted to disk.
Only locations (archive path + line), rule names and counts are emitted.
Matched secret bodies are never printed, written to the report, or included
in exception messages.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tarfile
import zipfile

# ---------------------------------------------------------------------------
# Detection rules (high-confidence only)
# ---------------------------------------------------------------------------

# AWS Access Key ID: AKIA + 16 uppercase alnum
RULE_AWS_ACCESS_KEY = "aws_access_key_id"
# AWS Secret Key: 40 base64-ish chars, only in assignment / JSON value context
RULE_AWS_SECRET_KEY = "aws_secret_access_key"
RULE_GITHUB_PAT = "github_pat"
RULE_SLACK_TOKEN = "slack_token"
RULE_GOOGLE_API_KEY = "google_api_key"
RULE_SK_TOKEN = "sk_token"
RULE_JWT = "jwt"
RULE_SECRET_FILENAME = "secret_filename"

# Secret token patterns. Each is applied per line.
TOKEN_RULES = [
    (RULE_AWS_ACCESS_KEY, re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        RULE_AWS_SECRET_KEY,
        re.compile(
            r"(?:aws_secret_access_key|secret_access_key|secret_key)"
            r"\s*[=:]\s*[\"']?([A-Za-z0-9/+]{40})[\"']?",
            re.IGNORECASE,
        ),
    ),
    (RULE_GITHUB_PAT, re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    (RULE_GITHUB_PAT, re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    (RULE_SLACK_TOKEN, re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    (RULE_GOOGLE_API_KEY, re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    (RULE_SK_TOKEN, re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    (
        RULE_JWT,
        re.compile(
            r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
        ),
    ),
]

# Secret filename patterns (matched against the basename, case-insensitive).
SECRET_FILENAME_PATTERNS = [
    re.compile(r"^\.env(\..*)?$", re.IGNORECASE),
    re.compile(r"^.*\.pem$", re.IGNORECASE),
    re.compile(r"^.*\.key$", re.IGNORECASE),
    re.compile(r"^id_rsa.*$", re.IGNORECASE),
    re.compile(r"^.*\.p12$", re.IGNORECASE),
    re.compile(r"^.*\.pfx$", re.IGNORECASE),
    re.compile(r"^credentials(\..*)?$", re.IGNORECASE),
    re.compile(r"^secrets?(\..*)?$", re.IGNORECASE),
]

LIMITATIONS = [
    "Regex-based detection only; absence of findings does not prove absence of secrets.",
    "Unknown or obfuscated secret formats are not detected.",
    "Real user environment credentials are never collected or read.",
]


class ScanError(Exception):
    """Raised for archive-level errors (path traversal, unreadable archive)."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def is_unsafe_member_name(name: str) -> bool:
    """Return True if the archive member name is a path-traversal risk."""
    if not name:
        return True
    # Normalize separators for inspection.
    norm = name.replace("\\", "/")
    if norm.startswith("/"):
        return True
    if _DRIVE_RE.match(name) or _DRIVE_RE.match(norm):
        return True
    parts = norm.split("/")
    if ".." in parts:
        return True
    return False


# ---------------------------------------------------------------------------
# Archive reading (in memory)
# ---------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _iter_zip(path: str):
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if is_unsafe_member_name(name):
                raise ScanError(
                    "unsafe archive member name (path traversal): %r" % name
                )
            data = zf.read(info)
            yield name, data


def _iter_tar(path: str):
    with tarfile.open(path, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name
            if is_unsafe_member_name(name):
                raise ScanError(
                    "unsafe archive member name (path traversal): %r" % name
                )
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            data = fobj.read()
            yield name, data


def iter_archive(path: str):
    """Yield (member_name, bytes) for each file in the archive."""
    if zipfile.is_zipfile(path):
        yield from _iter_zip(path)
        return
    try:
        yield from _iter_tar(path)
        return
    except tarfile.TarError:
        pass
    raise ScanError("unsupported or unreadable archive: %r" % path)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _basename(member_name: str) -> str:
    return member_name.replace("\\", "/").rsplit("/", 1)[-1]


def _is_secret_filename(member_name: str) -> bool:
    base = _basename(member_name)
    return any(p.match(base) for p in SECRET_FILENAME_PATTERNS)


def scan_member(member_name: str, data: bytes):
    """Return list of findings (dicts) for a single archive member.

    Each finding: {path, rule, line}. Secret bodies are never included.
    """
    findings = []
    if _is_secret_filename(member_name):
        findings.append({"path": member_name, "rule": RULE_SECRET_FILENAME, "line": 0})

    text = _decode(data)
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in TOKEN_RULES:
            if pattern.search(line):
                findings.append({"path": member_name, "rule": rule, "line": lineno})
    return findings


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path):
    if not path:
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("entries", [])
    result = []
    for entry in entries:
        result.append(
            {
                "path": entry.get("path"),
                "rule": entry.get("rule"),
                "reason": entry.get("reason", ""),
            }
        )
    return result


def apply_allowlist(findings, allowlist):
    """Split findings into (kept, applied).

    An allowlist entry applies only when BOTH path and rule match exactly.
    """
    kept = []
    applied = []
    for finding in findings:
        matched = None
        for entry in allowlist:
            if entry["path"] == finding["path"] and entry["rule"] == finding["rule"]:
                matched = entry
                break
        if matched is not None:
            applied.append(
                {
                    "path": finding["path"],
                    "rule": finding["rule"],
                    "reason": matched["reason"],
                }
            )
        else:
            kept.append(finding)
    return kept, applied


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_report(archives, files_scanned, findings, allowlist_applied):
    return {
        "archives": archives,
        "files_scanned": files_scanned,
        "findings": findings,
        "allowlist_applied": allowlist_applied,
        "limitations": LIMITATIONS,
    }


def run(archives, report_path, allowlist_path=None):
    allowlist = load_allowlist(allowlist_path)
    all_findings = []
    files_scanned = 0

    for archive in archives:
        if not os.path.isfile(archive):
            raise ScanError("archive not found: %r" % archive)
        for member_name, data in iter_archive(archive):
            files_scanned += 1
            for finding in scan_member(member_name, data):
                finding = dict(finding)
                finding["archive"] = archive
                all_findings.append(finding)

    kept, applied = apply_allowlist(all_findings, allowlist)
    report = build_report(archives, files_scanned, kept, applied)

    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan release archives for secrets (R-063/T34)."
    )
    parser.add_argument("archives", nargs="+", help="archive files to scan")
    parser.add_argument("--report", required=True, help="path to write JSON report")
    parser.add_argument("--allowlist", default=None, help="allowlist JSON path")
    args = parser.parse_args(argv)

    try:
        report = run(args.archives, args.report, args.allowlist)
    except ScanError as exc:
        # Never include secret bodies; only structural error info.
        sys.stderr.write("error: %s\n" % exc)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2

    count = len(report["findings"])
    sys.stdout.write(
        "scanned %d file(s) in %d archive(s); %d finding(s)\n"
        % (report["files_scanned"], len(report["archives"]), count)
    )
    for finding in report["findings"]:
        sys.stdout.write(
            "%s:%s: %s (line %s)\n"
            % (finding["archive"], finding["path"], finding["rule"], finding["line"])
        )
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main())
