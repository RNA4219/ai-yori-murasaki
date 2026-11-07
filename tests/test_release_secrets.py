"""Tests for tools/ci/check_release_secrets.py (R-063/T34).

All secrets used here are synthetic dummies that only mimic the real format.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "tools", "ci", "check_release_secrets.py")

# Synthetic dummy secrets (clearly fake, format-only).
DUMMY_AWS_ACCESS = "AKIA" + "X" * 16
DUMMY_GITHUB_PAT = "ghp_" + "a" * 33
DUMMY_SLACK = "xoxb-" + "1" * 12
DUMMY_GOOGLE = "AIza" + "B" * 35
DUMMY_SK = "sk-" + "c" * 24
DUMMY_JWT = "eyJ" + "d" * 10 + "." + "e" * 10 + "." + "f" * 10
DUMMY_AWS_SECRET = "aws_secret_access_key = " + "A" * 40

ALL_DUMMIES = [
    DUMMY_AWS_ACCESS,
    DUMMY_GITHUB_PAT,
    DUMMY_SLACK,
    DUMMY_GOOGLE,
    DUMMY_SK,
    DUMMY_JWT,
    "A" * 40,
]


def _run(args):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
    )


def _make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def _make_targz(path, members):
    with tarfile.open(path, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _assert_no_secret_leak(text):
    for dummy in ALL_DUMMIES:
        assert dummy not in text, "secret body leaked into output"


def test_zip_detects_dummy_secrets(tmp_path):
    archive = tmp_path / "dist.zip"
    _make_zip(
        archive,
        {
            "pkg/module.py": "x = 1\nkey = '%s'\n" % DUMMY_AWS_ACCESS,
            "pkg/other.py": "token = '%s'\n" % DUMMY_GITHUB_PAT,
        },
    )
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    rules = {f["rule"] for f in data["findings"]}
    assert "aws_access_key_id" in rules
    assert "github_pat" in rules
    assert data["files_scanned"] == 2
    _assert_no_secret_leak(result.stdout)
    _assert_no_secret_leak(result.stderr)
    _assert_no_secret_leak(report.read_text(encoding="utf-8"))


def test_targz_detects_dummy_secrets(tmp_path):
    archive = tmp_path / "dist.tar.gz"
    _make_targz(
        archive,
        {
            "pkg/a.py": "slack = '%s'\n" % DUMMY_SLACK,
            "pkg/b.py": "g = '%s'\n" % DUMMY_GOOGLE,
            "pkg/c.py": "s = '%s'\n" % DUMMY_SK,
            "pkg/d.py": "j = '%s'\n" % DUMMY_JWT,
            "pkg/e.py": DUMMY_AWS_SECRET + "\n",
        },
    )
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    rules = {f["rule"] for f in data["findings"]}
    for expected in (
        "slack_token",
        "google_api_key",
        "sk_token",
        "jwt",
        "aws_secret_access_key",
    ):
        assert expected in rules
    _assert_no_secret_leak(result.stdout)
    _assert_no_secret_leak(report.read_text(encoding="utf-8"))


def test_secret_filename_detected(tmp_path):
    archive = tmp_path / "dist.zip"
    _make_zip(
        archive,
        {
            "pkg/.env": "FOO=bar\n",
            "pkg/server.pem": "not a real key\n",
            "pkg/id_rsa": "dummy\n",
        },
    )
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    paths = {f["path"] for f in data["findings"]}
    assert "pkg/.env" in paths
    assert "pkg/server.pem" in paths
    assert "pkg/id_rsa" in paths


def test_clean_archive_exit_zero(tmp_path):
    archive = tmp_path / "clean.zip"
    _make_zip(
        archive,
        {
            "pkg/__init__.py": "",
            "pkg/module.py": "def add(a, b):\n    return a + b\n",
            "README.md": "# hello\n",
        },
    )
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["findings"] == []
    assert data["files_scanned"] == 3


def test_path_traversal_zip_exit_two(tmp_path):
    archive = tmp_path / "evil.zip"
    _make_zip(archive, {"../evil.py": "x = 1\n"})
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 2
    _assert_no_secret_leak(result.stdout)
    _assert_no_secret_leak(result.stderr)


def test_path_traversal_absolute_exit_two(tmp_path):
    archive = tmp_path / "evil2.zip"
    _make_zip(archive, {"/etc/passwd": "x = 1\n"})
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 2


def test_path_traversal_drive_letter_exit_two(tmp_path):
    archive = tmp_path / "evil3.zip"
    _make_zip(archive, {"C:\\windows\\x.py": "x = 1\n"})
    report = tmp_path / "report.json"
    result = _run([str(archive), "--report", str(report)])
    assert result.returncode == 2


def test_allowlist_records_reason(tmp_path):
    archive = tmp_path / "dist.zip"
    _make_zip(
        archive,
        {
            "tests/fixtures/dummy.py": "k = '%s'\n" % DUMMY_AWS_ACCESS,
            "pkg/real.py": "k = '%s'\n" % DUMMY_GITHUB_PAT,
        },
    )
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": "tests/fixtures/dummy.py",
                        "rule": "aws_access_key_id",
                        "reason": "synthetic test fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    result = _run(
        [str(archive), "--report", str(report), "--allowlist", str(allowlist)]
    )
    assert result.returncode == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    applied = data["allowlist_applied"]
    assert len(applied) == 1
    assert applied[0]["path"] == "tests/fixtures/dummy.py"
    assert applied[0]["rule"] == "aws_access_key_id"
    assert applied[0]["reason"] == "synthetic test fixture"
    # The non-allowlisted finding remains.
    remaining = {(f["path"], f["rule"]) for f in data["findings"]}
    assert ("pkg/real.py", "github_pat") in remaining
    assert ("tests/fixtures/dummy.py", "aws_access_key_id") not in remaining
    _assert_no_secret_leak(report.read_text(encoding="utf-8"))


def test_allowlist_requires_both_path_and_rule(tmp_path):
    archive = tmp_path / "dist.zip"
    _make_zip(archive, {"pkg/a.py": "k = '%s'\n" % DUMMY_AWS_ACCESS})
    allowlist = tmp_path / "allow.json"
    allowlist.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "path": "pkg/a.py",
                        "rule": "github_pat",
                        "reason": "wrong rule",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    result = _run(
        [str(archive), "--report", str(report), "--allowlist", str(allowlist)]
    )
    assert result.returncode == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["allowlist_applied"] == []
    assert len(data["findings"]) == 1


def test_missing_archive_exit_two(tmp_path):
    report = tmp_path / "report.json"
    result = _run([str(tmp_path / "nope.zip"), "--report", str(report)])
    assert result.returncode == 2
