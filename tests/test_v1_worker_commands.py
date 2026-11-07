"""Tests for v1 worker docker command grammar (network, mount, staging)."""
from __future__ import annotations

import os
import tempfile
import unittest

from ai_yori_murasaki import policy as policy_mod
from ai_yori_murasaki.adapters import base


class TestDockerNetworkGrammar(unittest.TestCase):
    def test_network_create_ok(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=lab",
            "aym-labnet",
        ]
        base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_create_equals_form_ok(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label=ai-yori-murasaki=lab",
            "aym-labnet",
        ]
        base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_create_requires_internal(self):
        argv = [
            "docker", "network", "create",
            "--label", "ai-yori-murasaki=lab",
            "aym-labnet",
        ]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_create_requires_label(self):
        argv = ["docker", "network", "create", "--internal", "aym-labnet"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_create_wrong_label(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=prod",
            "aym-labnet",
        ]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_create_unknown_flag(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=lab",
            "--driver", "bridge",
            "aym-labnet",
        ]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_create_name_not_aym(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=lab",
            "othernet",
        ]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"othernet"})

    def test_network_create_name_not_allowed(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=lab",
            "aym-labnet",
        ]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-other"})

    def test_network_create_requires_allowed_names(self):
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=lab",
            "aym-labnet",
        ]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv)

    def test_network_inspect_ok(self):
        argv = ["docker", "network", "inspect", "aym-labnet"]
        base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_inspect_not_allowed(self):
        argv = ["docker", "network", "inspect", "aym-labnet"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-other"})

    def test_network_inspect_requires_allowed_names(self):
        argv = ["docker", "network", "inspect", "aym-labnet"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv)

    def test_network_rm_ok(self):
        argv = ["docker", "network", "rm", "aym-labnet"]
        base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_rm_not_allowed(self):
        argv = ["docker", "network", "rm", "aym-labnet"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-other"})

    def test_network_rm_requires_allowed_names(self):
        argv = ["docker", "network", "rm", "aym-labnet"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv)

    def test_network_global_prune_rejected(self):
        argv = ["docker", "network", "prune", "-f"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-labnet"})

    def test_network_connect_rejected(self):
        argv = ["docker", "network", "connect", "aym-labnet", "aym-c1"]
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(argv, allowed_names={"aym-labnet"})


class TestDockerMountGrammar(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        self.staged = os.path.join(self.root, "staged")
        os.makedirs(self.staged, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _mount(self, src, extra=""):
        return f"type=bind,source={src},target=/app,readonly=true{extra}"

    def test_mount_ok(self):
        base._validate_docker_mount(self._mount(self.staged),
                                    staging_root=self.root)

    def test_mount_unknown_key_rejected(self):
        val = self._mount(self.staged) + ",consistency=cached"
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(val, staging_root=self.root)

    def test_mount_duplicate_key_rejected(self):
        val = self._mount(self.staged) + ",source=" + self.staged
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(val, staging_root=self.root)

    def test_mount_missing_key_rejected(self):
        val = f"type=bind,source={self.staged},target=/app"
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(val, staging_root=self.root)

    def test_mount_traversal_rejected(self):
        bad = os.path.join(self.root, "..", "escape")
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(self._mount(bad), staging_root=self.root)

    def test_mount_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(policy_mod.PolicyDenied):
                base._validate_docker_mount(self._mount(other),
                                            staging_root=self.root)

    def test_mount_relative_source_rejected(self):
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(self._mount("staged"),
                                        staging_root=self.root)

    def test_mount_requires_staging_root(self):
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(self._mount(self.staged))

    def test_mount_symlink_ancestor_rejected(self):
        link = os.path.join(self.root, "link")
        try:
            os.symlink(self.staged, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not available")
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_mount(self._mount(link), staging_root=self.root)

    def test_mount_native_windows_absolute(self):
        if os.name != "nt":
            self.skipTest("native Windows path test")
        from pathlib import Path
        win_root = str(Path(self.root))
        win_staged = str(Path(self.staged))
        self.assertTrue(Path(win_staged).is_absolute())
        base._validate_docker_mount(self._mount(win_staged),
                                    staging_root=win_root)

    def test_is_absolute_path_native_windows(self):
        if os.name != "nt":
            self.skipTest("native Windows path test")
        from pathlib import Path
        p = str(Path(self.staged))
        self.assertTrue(base._is_absolute_path(p))
        self.assertFalse(base._is_absolute_path("relative\\path"))


class TestDockerRunGrammar(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        self.staged = os.path.join(self.root, "staged")
        os.makedirs(self.staged, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _argv(self, mount=None):
        if mount is None:
            mount = f"type=bind,source={self.staged},target=/app,readonly=true"
        return [
            "docker", "run", "--rm",
            "--name", "aym-c1",
            "--label", "ai-yori-murasaki=lab",
            "--network", "aym-labnet",
            "--user", "65534:65534",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "64",
            "--memory", "128m",
            "--cpus", "1",
            "--mount", mount,
            "img:latest", "python", "/app/run.py",
        ]

    def test_run_ok(self):
        base._validate_docker_argv(
            self._argv(), staging_root=self.root,
            allowed_images={"img:latest"}, allowed_names={"aym-c1"})

    def test_run_unknown_mount_flag_rejected(self):
        mount = (f"type=bind,source={self.staged},target=/app,"
                 "readonly=true,consistency=cached")
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(
                self._argv(mount), staging_root=self.root,
                allowed_images={"img:latest"}, allowed_names={"aym-c1"})

    def test_run_duplicate_mount_key_rejected(self):
        mount = (f"type=bind,source={self.staged},target=/app,"
                 f"readonly=true,source={self.staged}")
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(
                self._argv(mount), staging_root=self.root,
                allowed_images={"img:latest"}, allowed_names={"aym-c1"})

    def test_run_image_not_allowed(self):
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(
                self._argv(), staging_root=self.root,
                allowed_images={"other:latest"}, allowed_names={"aym-c1"})

    def test_run_requires_allowed_images(self):
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(
                self._argv(), staging_root=self.root,
                allowed_names={"aym-c1"})

    def test_run_network_not_allowed(self):
        argv = self._argv()
        idx = argv.index("--network")
        argv[idx + 1] = "host"
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(
                argv, staging_root=self.root,
                allowed_images={"img:latest"}, allowed_names={"aym-c1"})

    def test_run_forbidden_flag_rejected(self):
        argv = self._argv()
        argv.insert(2, "--privileged")
        with self.assertRaises(policy_mod.PolicyDenied):
            base._validate_docker_argv(
                argv, staging_root=self.root,
                allowed_images={"img:latest"}, allowed_names={"aym-c1"})


class TestBuildDockerArgvFromPlan(unittest.TestCase):
    def test_build_ok(self):
        plan = {
            "id": "c1",
            "image": "img:latest",
            "network": "aym-labnet",
            "staged_source": "/tmp/staged",
            "script": "/app/run.py",
            "args": ["--x"],
        }
        argv = base._build_docker_argv_from_plan(plan)
        self.assertEqual(argv[0], "docker")
        self.assertEqual(argv[1], "run")
        self.assertIn("aym-c1", argv)
        self.assertIn("aym-labnet", argv)

    def test_build_rejects_relative_source(self):
        plan = {
            "id": "c1",
            "image": "img:latest",
            "network": "aym-labnet",
            "staged_source": "staged",
            "script": "/app/run.py",
            "args": [],
        }
        with self.assertRaises(policy_mod.PolicyDenied):
            base._build_docker_argv_from_plan(plan)

    def test_build_rejects_traversal_source(self):
        plan = {
            "id": "c1",
            "image": "img:latest",
            "network": "aym-labnet",
            "staged_source": "/tmp/../etc",
            "script": "/app/run.py",
            "args": [],
        }
        with self.assertRaises(policy_mod.PolicyDenied):
            base._build_docker_argv_from_plan(plan)

    def test_build_rejects_bad_network(self):
        plan = {
            "id": "c1",
            "image": "img:latest",
            "network": "host",
            "staged_source": "/tmp/staged",
            "script": "/app/run.py",
            "args": [],
        }
        with self.assertRaises(policy_mod.PolicyDenied):
            base._build_docker_argv_from_plan(plan)


if __name__ == "__main__":
    unittest.main()
