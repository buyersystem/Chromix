import json
import unittest
from unittest import mock

from tools import restore_upstream_cache as restore
from tools.tests import test_restore_upstream_cache as fixtures


class Linux154RestoreLinksTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RestoreUpstreamCacheTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.identity = {
            "chromium_version": "154.0.8037.57",
            "ungoogled_commit": "800d0bb5078472e4442c1fd73373172754a60939",
            "head_sha": "56b2567742c0423e8e70fd5da286f0762605d26c",
            "platform": "linux", "arch": "x64",
            "repository": "ungoogled-software/ungoogled-chromium-portablelinux",
            "repository_id": 177191557, "head_branch": "154.0.8037.57-1", "event": "push",
            "workflow_path": ".github/workflows/build.yml", "run_id": 36144376832,
            "artifact_id": 10896197724, "artifact_name": "build-cache-x86_64",
            "artifact_size_in_bytes": 4736781615,
            "artifact_digest": "sha256:88b9b494993940caafc5f9c3f0abf7af97a8f6a8705f6aec0f4ab820967ffdab",
        }

    def record_link(self, name=restore.LINUX154_ESBUILD_LINK):
        fixture = self.fixture
        relative = fixture.donor.relative_to(fixture.cache / "tree").as_posix()
        fixture.result.update(skipped_external_symlinks=1,
                              external_symlink_paths=[relative + "/" + name])
        fixture.write(fixture.cache / "result.json", json.dumps(fixture.result))

    def record_esbuild_chain(self):
        fixture = self.fixture
        relative = fixture.donor.relative_to(fixture.cache / "tree").as_posix()
        paths = [relative + "/" + name for name in
                 (restore.LINUX154_ESBUILD_LINK, restore.LINUX154_ESBUILD_ALIAS)]
        fixture.result.update(skipped_external_symlinks=len(paths), external_symlink_paths=paths)
        fixture.write(fixture.cache / "result.json", json.dumps(fixture.result))

    def test_exact_linux154_link_is_allowed_only_with_verified_identity(self):
        name = restore.LINUX154_ESBUILD_LINK
        self.assertTrue(restore.is_known_external_link(name, "linux", self.identity))
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform):
                self.assertFalse(restore.is_known_external_link(name, platform, self.identity))
        for key in self.identity:
            with self.subTest(key=key):
                identity = dict(self.identity, **{key: "unverified"})
                self.assertFalse(restore.is_known_external_link(name, "linux", identity))
        self.assertFalse(restore.is_known_external_link(name, "linux"))
        for key in self.identity:
            with self.subTest(missing=key):
                identity = dict(self.identity)
                del identity[key]
                self.assertFalse(restore.is_known_external_link(name, "linux", identity))
        for key in ("repository_id", "run_id", "artifact_id", "artifact_size_in_bytes"):
            with self.subTest(wrong_type=key):
                identity = dict(self.identity, **{key: float(self.identity[key])})
                self.assertFalse(restore.is_known_external_link(name, "linux", identity))
        self.assertFalse(restore.is_known_external_link(name, "linux", dict(self.identity, extra="unverified")))
        for suffix in ("/bin/esbuild", "-other", "/../other"):
            with self.subTest(suffix=suffix):
                self.assertFalse(restore.is_known_external_link(name + suffix, "linux", self.identity))
        for suffix in ("/bin/esbuild", "-other", "/../other"):
            with self.subTest(alias_suffix=suffix):
                self.assertFalse(restore.is_known_external_link(restore.LINUX154_ESBUILD_ALIAS + suffix,
                                                                "linux", self.identity))

    def test_both_architectures_record_omission_without_recreating_external_link(self):
        fixture = self.fixture
        for arch in ("x64", "arm64"):
            with self.subTest(arch=arch):
                fixture.make_cache("linux", arch)
                self.record_link()
                identity = dict(self.identity)
                if arch == "arm64":
                    identity.update(arch="arm64", artifact_id=10902309838,
                                    artifact_name="build-cache-arm64", artifact_size_in_bytes=5671963322,
                                    artifact_digest="sha256:7c27123cc1b93b0fa6a66ced3ad053d35eaa48e8ddf94d2ac433ce21f3c924ee")
                omitted = restore.missing_host_links(fixture.cache, fixture.donor,
                                                     fixture.result, "linux", identity)
                self.assertEqual(omitted, [restore.LINUX154_ESBUILD_LINK])
                self.assertFalse((fixture.donor / restore.LINUX154_ESBUILD_LINK).exists())

    def test_omission_rejects_unknown_or_existing_paths(self):
        fixture = self.fixture
        for suffix in ("-other", "/bin/esbuild"):
            with self.subTest(suffix=suffix):
                self.record_link(restore.LINUX154_ESBUILD_LINK + suffix)
                with self.assertRaisesRegex(restore.Miss, "unknown external symlink"):
                    restore.missing_host_links(fixture.cache, fixture.donor,
                                               fixture.result, "linux", self.identity)
        self.record_link()
        target = fixture.donor / restore.LINUX154_ESBUILD_LINK
        target.mkdir(parents=True)
        with self.assertRaisesRegex(restore.Miss, "unexpectedly exists"):
            restore.missing_host_links(fixture.cache, fixture.donor,
                                       fixture.result, "linux", self.identity)
        target.rmdir()
        target.symlink_to("/missing-unverified-esbuild")
        with self.assertRaises(restore.Miss):
            restore.missing_host_links(fixture.cache, fixture.donor,
                                       fixture.result, "linux", self.identity)

    def test_restored_receipt_rechecks_exact_identity_and_path(self):
        fixture = self.fixture
        self.record_link()
        identity, pin, manifest = restore.identities(fixture.repo, "linux", "x64")
        identity.update(self.identity)
        version = "\n".join(f"{key}={value}" for key, value in zip(
            ("MAJOR", "MINOR", "BUILD", "PATCH"), self.identity["chromium_version"].split("."))) + "\n"
        fixture.write(fixture.donor / "chrome/VERSION", version)
        with mock.patch.object(restore, "identities", return_value=(identity, pin, manifest)):
            entry = fixture.invoke()
            self.assertEqual(entry["status"], "hit", entry)
            self.assertEqual(entry["receipt"]["external_symlink_paths"], [restore.LINUX154_ESBUILD_LINK])
            self.assertFalse((fixture.work / "src" / restore.LINUX154_ESBUILD_LINK).exists())
            receipt = restore.verify_restored(fixture.work, "linux", "x64", fixture.repo)
            marker = fixture.work / "src" / restore.MARKER
            receipt["external_symlink_paths"] = [restore.LINUX154_ESBUILD_LINK + "/unexpected"]
            fixture.write(marker, json.dumps(receipt))
            with self.assertRaisesRegex(restore.Miss, "unknown omitted host links"):
                restore.verify_restored(fixture.work, "linux", "x64", fixture.repo)

    def test_legacy_object_import_does_not_create_module_placeholder(self):
        fixture = self.fixture
        name = fixture.donor.relative_to(fixture.cache).as_posix() + "/" + restore.LINUX154_ESBUILD_LINK
        result = dict(skipped_external_symlinks=1, external_symlink_paths=[name])
        with self.assertRaisesRegex(restore.Miss, "unknown external symlink"):
            restore.importer.preserve_external_tool_lookups(fixture.cache, fixture.donor, result)
        self.assertFalse((fixture.donor / restore.LINUX154_ESBUILD_LINK).exists())


if __name__ == "__main__":
    unittest.main()
