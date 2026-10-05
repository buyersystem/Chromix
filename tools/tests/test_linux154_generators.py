"""Linux 154 generator repairs use independent data fixtures and mocked probes."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from tools import linux_restored_generators as linux154
from tools import prepare_restored_build as prepare
from tools.tests.test_linux_typescript import FIXTURE, PORTABLE, install_typescript_fixture

PIN_KEYS = ("ChromiumVersion", "UngoogledCommit", "UngoogledLinuxCommit")
PINS153 = ("153.0.8010.36", "dd8fb9b5c837982faf41ba58cd30a5664e77c329",
           "a5ffa5e4a9fb722b97a5cf7966e29450a150c3dd")
PORTABLE154 = PORTABLE.replace(b"    RunTypeScript(sys.argv[1:])\n",
                               b"    sys.stdout.write(RunTypeScript(sys.argv[1:]))\n")


def tree_hash(root):
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = Path(directory) / name
            digest.update(json.dumps([path.relative_to(root).as_posix(),
                                      linux154.sha256(path.read_bytes())]).encode())
    return digest.hexdigest()


class Linux154GeneratorsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="linux154 generators ")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.src = self.work / "src"
        install_typescript_fixture(self.src)
        self.wrapper = self.src / prepare.TYPESCRIPT_WRAPPER
        self.wrapper.write_bytes(PORTABLE154)
        self.package = self.src / prepare.TYPESCRIPT_PACKAGE
        self.patch(prepare, "load_pins", return_value=dict(zip(PIN_KEYS, linux154.PINS)))
        self.patch(linux154, "TYPESCRIPT_TREE", tree_hash(self.package))
        self.write(linux154.LOCK, b"independent fixture lock\n")
        self.patch(linux154, "LOCK_SHA256", linux154.sha256(b"independent fixture lock\n"))
        repairs = {}
        for name, (_, _, old, new) in linux154.SOURCE_REPAIRS.items():
            data = b"fixture source, never executed\n" + old + b"\n"
            self.write(linux154.DEVTOOLS + "/" + name, data)
            repairs[name] = (linux154.sha256(data), linux154.sha256(data.replace(old, new)), old, new)
        self.patch(linux154, "SOURCE_REPAIRS", repairs)
        self.modules = {name: b"fixture module: " + name.encode() for name in linux154.MODULE_FILES}
        self.patch(linux154, "MODULE_FILES", {name: linux154.sha256(data) for name, data in self.modules.items()})
        self.binaries = {}
        for host, cpu in (("x64", 62), ("arm64", 183)):
            data = bytearray(64)
            data[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", data, 18, cpu)
            self.binaries[host] = bytes(data)
        self.patch(linux154, "BINARY_HASHES", {host: linux154.sha256(data) for host, data in self.binaries.items()})
        self.archives = {"module": self.archive("module", self.modules)}
        for host, binary in self.binaries.items():
            self.archives[host] = self.archive(host, {"bin/esbuild": binary,
                                                    "package.json": b"{}", "README.md": b"fixture"})
        self.patch(linux154, "ARCHIVES", {key: ("unused", linux154.sha256(path.read_bytes()))
                                         for key, path in self.archives.items()})
        self.patch(prepare, "binary_architectures", return_value={"x64", "arm64"})

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def write(self, relative, content):
        path = self.src / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def archive(self, name, files):
        path = self.work / (name + ".tgz")
        with tarfile.open(path, "w:gz") as stream:
            for relative, data in files.items():
                entry = tarfile.TarInfo("package/" + relative)
                entry.size = len(data)
                stream.addfile(entry, io.BytesIO(data))
        return path

    def install(self, host="x64"):
        return linux154.install_esbuild(self.src, host_arch=host, module_archive=self.archives["module"],
                                       native_archive=self.archives[host])

    def probe(self, command, **kwargs):
        output = "Version 6.0.2\n" if str(command[1]).endswith("/lib/tsc.js") else (
            "0.25.1\n" if command[1] == "--version" else "")
        return subprocess.CompletedProcess(command, 0, output)

    def repair(self, host="x64"):
        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe) as run, \
                mock.patch.object(prepare.os, "access", return_value=True):
            result = prepare.prepare_linux_typescript(self.src, host_arch=host, repair=True)
        return result, run

    def test_exact_wrapper_and_separate_153_identity(self):
        self.assertEqual(linux154.sha256(PORTABLE154), linux154.TYPESCRIPT_PORTABLE)
        self.assertNotEqual(linux154.TYPESCRIPT_PORTABLE, prepare.TYPESCRIPT_PORTABLE)
        self.assertEqual(linux154.sha256(FIXTURE["package"].encode()),
                         "3004f96b830f722041ea418dc29642d934fc64dcc207992e22a1dc37c7b270ae")
        self.wrapper.write_bytes(PORTABLE)
        with mock.patch.object(prepare.subprocess, "run") as run, \
                self.assertRaisesRegex(ValueError, "unknown restored"):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True)
        run.assert_not_called()
        with mock.patch.object(prepare, "load_pins", return_value=dict(zip(PIN_KEYS, PINS153))):
            for host in ("x64", "arm64", "x64"):
                result, run = self.repair(host)
                self.assertEqual(result["sha256"], prepare.TYPESCRIPT_REPAIRED[host])
                self.assertNotIn("esbuild", result)
                self.assertEqual(run.call_count, 1)
            self.wrapper.write_bytes(PORTABLE154)
            with self.assertRaisesRegex(ValueError, "unknown restored"):
                prepare.prepare_linux_typescript(self.src, host_arch="x64")

    def test_unknown_tuple_and_host_rejected_before_execution(self):
        for key in PIN_KEYS:
            pins = dict(zip(PIN_KEYS, linux154.PINS))
            pins[key] = "unknown"
            with mock.patch.object(prepare, "load_pins", return_value=pins), \
                    mock.patch.object(prepare.subprocess, "run") as run, \
                    self.assertRaisesRegex(ValueError, "pins/host"):
                prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True)
            run.assert_not_called()
        with self.assertRaisesRegex(ValueError, "pins/host"):
            prepare.prepare_linux_typescript(self.src, host_arch="ia32")

    def test_inspection_and_offline_install_never_execute(self):
        donor = self.write(linux154.DEVTOOLS + "/third_party/esbuild/esbuild", b"unverified donor binary")
        before = self.wrapper.stat().st_mtime_ns
        with mock.patch.object(prepare.subprocess, "run") as run:
            result = prepare.prepare_linux_typescript(self.src, host_arch="x64")
            self.assertTrue(result["esbuild"]["install_needed"])
            installed = self.install()
            self.assertFalse(installed["install_needed"])
            self.assertTrue(installed["repair_needed"])
        run.assert_not_called()
        self.assertEqual(self.wrapper.read_bytes(), PORTABLE154)
        self.assertEqual(self.wrapper.stat().st_mtime_ns, before)
        self.assertEqual(donor.read_bytes(), b"unverified donor binary")

    def test_native_host_switch_idempotence_and_module_probe(self):
        for host in ("x64", "arm64", "x64"):
            self.install(host)
            result, run = self.repair(host)
            self.assertEqual(result["sha256"], linux154.TYPESCRIPT_REPAIRED[host])
            self.assertEqual(result["esbuild"]["binary"]["host_arch"], host)
            self.assertFalse(result["repair_needed"])
            self.assertEqual(run.call_count, 3)
            node = self.src / f"third_party/node/linux/node-linux-{host}/bin/node"
            self.assertEqual(run.call_args_list[0].args[0][0], str(node))
            self.assertEqual(run.call_args_list[2].args[0][0], str(node))
            self.assertIn("require.resolve('esbuild')", run.call_args_list[2].args[0][2])
            self.assertIn("transformSync", run.call_args_list[2].args[0][2])
            paths = [self.wrapper, self.src / linux154.ESBUILD_BINARY,
                     *(self.src / name for name in result["esbuild"]["sources"])]
            mtimes = {path: path.stat().st_mtime_ns for path in paths}
            self.install(host)
            again, _ = self.repair(host)
            self.assertEqual(result, again)
            self.assertEqual(mtimes, {path: path.stat().st_mtime_ns for path in paths})
            self.assertIn(b"sys.stdout.write(RunTypeScript(sys.argv[1:]))", self.wrapper.read_bytes())
            self.assertNotIn(b"/usr/bin/tsc", self.wrapper.read_bytes())

    def test_missing_module_or_wrong_host_prevents_any_probe(self):
        for host in ("x64", "arm64"):
            if host == "arm64":
                self.install("x64")
            with mock.patch.object(prepare.subprocess, "run") as run, \
                    self.assertRaisesRegex(ValueError, "installed before finish"):
                prepare.prepare_linux_typescript(self.src, host_arch=host, repair=True)
            run.assert_not_called()
            self.assertEqual(self.wrapper.read_bytes(), PORTABLE154)

    def test_unknown_package_wrapper_lock_source_module_binary_fail_closed(self):
        self.install()
        paths = [self.wrapper, self.package / "lib/_tsc.js", self.package / "lib/lib.es5.d.ts",
                 self.src / linux154.LOCK, self.src / linux154.ESBUILD_BINARY,
                 self.src / linux154.ESBUILD_MODULE / "lib/main.js",
                 *(self.src / linux154.DEVTOOLS / name for name in linux154.SOURCE_REPAIRS)]
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"unrecognized bytes\n")
                with mock.patch.object(prepare.subprocess, "run") as run, self.assertRaises(ValueError):
                    prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True)
                run.assert_not_called()
                self.assertEqual(path.read_bytes(), original + b"unrecognized bytes\n")
                path.write_bytes(original)
        extra = self.package / "extra.js"
        extra.write_bytes(b"unexpected file")
        with self.assertRaisesRegex(ValueError, "bytes/inventory"):
            prepare.prepare_linux_typescript(self.src, host_arch="x64")

    def test_symlinks_hardlinks_and_external_module_are_never_followed(self):
        self.install()
        for relative in (linux154.ESBUILD_BINARY, linux154.ESBUILD_MODULE + "/lib/main.js",
                         linux154.LOCK, prepare.TYPESCRIPT_WRAPPER):
            path = self.src / relative
            data = path.read_bytes()
            target = self.work / "external"
            target.write_bytes(data)
            for hard in (False, True):
                path.unlink()
                os.link(target, path) if hard else path.symlink_to(target)
                with self.assertRaises(ValueError):
                    prepare.prepare_linux_typescript(self.src, host_arch="x64")
                self.assertEqual(target.read_bytes(), data)
                path.unlink()
                path.write_bytes(data)
                if relative == linux154.ESBUILD_BINARY:
                    path.chmod(0o755)
        module = self.src / linux154.ESBUILD_MODULE
        module.rename(self.work / "outside-module")
        module.symlink_to(self.work / "outside-module", target_is_directory=True)
        with mock.patch.object(prepare.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "linked"):
            self.install()
        run.assert_not_called()

    def test_atomic_wrapper_interruption_leaves_original_and_retries(self):
        self.install()
        original = self.wrapper.read_bytes()
        self.addCleanup(lambda: self.wrapper.write_bytes(original))
        calls = []
        def interrupt(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError("injected before publish interruption")
        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                self.assertRaisesRegex(OSError, "interruption"):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True,
                                             before_publish=interrupt)
        self.assertEqual(self.wrapper.read_bytes(), original)
        result, _ = self.repair()
        self.assertEqual(result["sha256"], linux154.TYPESCRIPT_REPAIRED["x64"])
        self.assertFalse(list(self.wrapper.parent.glob(".chromix-*")))

    def test_atomic_source_symlink_swap_is_rejected_without_outside_write(self):
        self.install()
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        outside = self.work / "outside"
        outside.write_bytes(b"outside original")
        original = target.read_bytes()
        def swap(path):
            target.unlink()
            target.symlink_to(outside)
        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                self.assertRaises((ValueError, OSError)):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True,
                                             before_publish=swap)
        self.assertEqual(outside.read_bytes(), b"outside original")
        self.assertTrue(target.is_symlink())
        target.unlink()
        target.write_bytes(original)

    def test_atomic_parent_directory_swap_and_hardlink_fail_closed(self):
        self.install()
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        original = target.read_bytes()
        outside_dir = self.work / "outside-dir"
        outside_dir.mkdir()
        def swap_parent(path):
            parent = target.parent
            displaced = self.work / "displaced-parent"
            parent.rename(displaced)
            parent.symlink_to(outside_dir, target_is_directory=True)
        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                self.assertRaises((ValueError, OSError)):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True,
                                             before_publish=swap_parent)
        self.assertEqual(list(outside_dir.iterdir()), [])
        parent = target.parent
        parent.unlink()
        (self.work / "displaced-parent").rename(parent)
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        hardlink = self.work / "hardlink"
        hardlink.write_bytes(original)
        target.unlink()
        os.link(hardlink, target)
        with self.assertRaises(ValueError):
            prepare.prepare_linux_typescript(self.src, host_arch="x64")
        target.unlink()
        target.write_bytes(original)

    def test_atomic_content_and_mtime_races_fail_before_publish(self):
        self.install()
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        original = target.read_bytes()

        def mutate_content(path):
            with target.open("ab") as stream:
                stream.write(b"race")

        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                self.assertRaises((ValueError, OSError)):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True,
                                             before_publish=mutate_content)
        self.assertNotEqual(target.read_bytes(), original)
        target.write_bytes(original)

        baseline = target.stat()

        def mutate_mtime(path):
            os.utime(target, ns=(baseline.st_atime_ns, baseline.st_mtime_ns + 1))

        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                self.assertRaises((ValueError, OSError)):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True,
                                             before_publish=mutate_mtime)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mtime_ns, baseline.st_mtime_ns + 1)
        os.utime(target, ns=(baseline.st_atime_ns, baseline.st_mtime_ns))

    def test_mtime_change_after_validation_fails_against_baseline(self):
        self.install()
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        original = target.read_bytes()
        baseline = target.stat()
        read_path = linux154._path_payload
        attempts = 0

        def mutate_after_validation(parent_fd, name):
            nonlocal attempts
            if name == target.name and attempts == 0:
                attempts += 1
                os.utime(target, ns=(baseline.st_atime_ns, baseline.st_mtime_ns + 1))
            return read_path(parent_fd, name)

        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                mock.patch.object(linux154, "_path_payload", side_effect=mutate_after_validation), \
                self.assertRaisesRegex(ValueError, "changed before publish"):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True)
        self.assertEqual(attempts, 1)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mtime_ns, baseline.st_mtime_ns + 1)
        self.assertFalse(list(target.parent.glob(".chromix-*")))

    def test_stable_arbitrary_cache_mtime_is_valid(self):
        self.install()
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        info = target.stat()
        os.utime(target, ns=(info.st_atime_ns, 1_000_000_000))
        result = prepare.prepare_linux_typescript(self.src, host_arch="x64")
        self.assertTrue(result["repair_needed"])
        self.assertEqual(target.stat().st_mtime_ns, 1_000_000_000)

    def test_mtime_change_during_exchange_rolls_back(self):
        self.install()
        target = self.src / linux154.DEVTOOLS / "scripts/build/esbuild.js"
        original = target.read_bytes()
        baseline = target.stat()
        rename = linux154._rename_at
        exchanges = 0

        def race_after_exchange(parent_fd, old, new, flags=0, *, destination_fd=None):
            nonlocal exchanges
            rename(parent_fd, old, new, flags, destination_fd=destination_fd)
            if flags == linux154._RENAME_EXCHANGE and exchanges == 0:
                exchanges += 1
                displaced = os.stat(old, dir_fd=parent_fd, follow_symlinks=False)
                os.utime(old, ns=(displaced.st_atime_ns, displaced.st_mtime_ns + 1),
                         dir_fd=parent_fd, follow_symlinks=False)

        with mock.patch.object(prepare.subprocess, "run", side_effect=self.probe), \
                mock.patch.object(prepare.os, "access", return_value=True), \
                mock.patch.object(linux154, "_rename_at", side_effect=race_after_exchange), \
                self.assertRaisesRegex(ValueError, "changed during publish"):
            prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True)
        self.assertEqual(exchanges, 1)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mtime_ns, baseline.st_mtime_ns + 1)
        self.assertFalse(list(target.parent.glob(".chromix-*")))

    def test_probe_failures_leave_original_sources(self):
        self.install()
        protected = {self.wrapper: self.wrapper.read_bytes(),
                     **{self.src / linux154.DEVTOOLS / name: (self.src / linux154.DEVTOOLS / name).read_bytes()
                        for name in linux154.SOURCE_REPAIRS}}
        for fail_index in (0, 1, 2):
            calls = 0
            def fail(command, **kwargs):
                nonlocal calls
                index = calls
                calls += 1
                return subprocess.CompletedProcess(command, 1, "fixture probe failure") if index == fail_index else self.probe(command)
            with mock.patch.object(prepare.subprocess, "run", side_effect=fail), \
                    mock.patch.object(prepare.os, "access", return_value=True), self.assertRaisesRegex(ValueError, "probe failed"):
                prepare.prepare_linux_typescript(self.src, host_arch="x64", repair=True)
            for path, data in protected.items():
                self.assertEqual(path.read_bytes(), data)

    def test_unverified_archives_rejected_before_any_install(self):
        for key in ("module", "x64"):
            archive = self.archives[key]
            original = archive.read_bytes()
            archive.write_bytes(original + b"tampered")
            with mock.patch.object(prepare.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "SHA256"):
                self.install()
            run.assert_not_called()
            self.assertFalse((self.src / linux154.ESBUILD_MODULE).exists())
            self.assertFalse((self.src / linux154.ESBUILD_BINARY).exists())
            archive.write_bytes(original)

    def test_archive_members_reject_links_traversal_duplicates_and_devices(self):
        for kind in ("link", "traversal", "duplicate", "device"):
            archive = self.work / "hostile.tgz"
            with tarfile.open(archive, "w:gz") as stream:
                entry = tarfile.TarInfo("package/../escape" if kind == "traversal" else "package/file")
                entry.size = 1
                if kind == "link":
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = "/tmp/outside"
                elif kind == "device":
                    entry.type = tarfile.CHRTYPE
                stream.addfile(entry, io.BytesIO(b"x"))
                if kind == "duplicate":
                    stream.addfile(entry, io.BytesIO(b"x"))
            with mock.patch.dict(linux154.ARCHIVES, module=("unused", linux154.sha256(archive.read_bytes()))), \
                    self.assertRaisesRegex(ValueError, "unsafe|duplicate"):
                linux154.archive_files(archive, "module")

    def test_restored_chain_records_repair_resume_and_failures_without_output_deletion(self):
        from tools.tests.test_prepare_restored_build import PrepareRestoredBuildTest
        fixture = PrepareRestoredBuildTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.fixture("linux", "arm64", host_arch="x64")
        shutil.rmtree(fixture.src / prepare.TYPESCRIPT_PACKAGE)
        shutil.copytree(self.src / linux154.DEVTOOLS, fixture.src / linux154.DEVTOOLS, dirs_exist_ok=True)
        self.src = fixture.src
        self.wrapper = self.src / prepare.TYPESCRIPT_WRAPPER
        self.package = self.src / prepare.TYPESCRIPT_PACKAGE
        self.install("x64")
        receipt = (fixture.src / ".chromix-upstream-restored.json").read_bytes()
        with fixture.native_context("linux", "x64"):
            native_probe = prepare.subprocess.run.side_effect
            def run(command, **kwargs):
                if command[0] == str(self.src / linux154.ESBUILD_BINARY) or command[1] == "-e":
                    return self.probe(command, **kwargs)
                return native_probe(command, **kwargs)
            with mock.patch.object(prepare.subprocess, "run", side_effect=run):
                inspected = prepare.prepare(fixture.work, "linux", "arm64", phase="inspect")
                self.assertTrue(inspected["generator_fingerprint"]["typescript"]["esbuild"]["repair_needed"])
                first = prepare.prepare(fixture.work, "linux", "arm64")
                self.assertTrue(first["ready_for_gn"])
                independent = fixture.object("keep.o")
                generated = fixture.write(fixture.out / "gen/generated.js", "retained")
                fixture.deps({"obj/keep.o": ["../../include/a.h"]})
                fixture.write(fixture.out / ".ninja_log", "# ninja log v5\n0\t1\t1\tgen/generated.js\tabc\n")
                resumed = prepare.prepare(fixture.work, "linux", "arm64")
                self.assertEqual(resumed["counters"]["generator_rechecks"], 0)
                self.assertTrue(generated.exists())
                self.wrapper.write_bytes(PORTABLE154)
                changed = prepare.prepare(fixture.work, "linux", "arm64")
                self.assertEqual(changed["counters"]["generator_rechecks"], 1)
                self.assertEqual(changed["counters"]["toolchain_invalidated_outputs"], 0)
                self.assertFalse(generated.exists())
                self.assertTrue(independent.exists())
                fixture.write(generated, "must survive validation failure")
                marker = (fixture.src / prepare.MARKER).read_bytes()
                compiler = self.package / "lib/_tsc.js"
                compiler.write_bytes(compiler.read_bytes() + b"unknown bytes")
                with self.assertRaisesRegex(ValueError, "bytes/inventory"):
                    prepare.prepare(fixture.work, "linux", "arm64")
                report = json.loads((fixture.work / "upstream-cache-preparation.json").read_text())
                self.assertFalse(report["ready_for_gn"])
                self.assertEqual(report["operation"], "inspect_linux_typescript")
                self.assertEqual((fixture.src / prepare.MARKER).read_bytes(), marker)
                self.assertTrue(generated.exists() and independent.exists())
        self.assertEqual((fixture.src / ".chromix-upstream-restored.json").read_bytes(), receipt)

    def test_generator_fingerprint_changes_after_repair_but_not_resume(self):
        self.install()
        before = prepare.prepare_linux_typescript(self.src, host_arch="x64")
        repaired, _ = self.repair()
        self.assertNotEqual(before, repaired)
        self.assertEqual(repaired, prepare.prepare_linux_typescript(self.src, host_arch="x64"))
        for entry in repaired["esbuild"]["sources"].values():
            self.assertEqual(entry["sha256"], entry["repaired_sha256"])


if __name__ == "__main__":
    unittest.main()
