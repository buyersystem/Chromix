"""Explicit pixel policy and actual inline C++/Canvas excerpts, not a Chromium build.

Dependency shims model Skia ownership; no GPU or browser acceptance is implied.
All reconstructed inputs come from checked-in predecessor patches.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest

import test_fingerprint_canvas as canvas
from test_fingerprint_config import added_source, config_binary
from test_fingerprint_features import block
from test_fingerprint_ua_locale import COMMAND_LINE_STUB

ROOT = Path(__file__).resolve().parents[2]
CXX = os.environ.get("CXX") or shutil.which("c++")
CONFIG_H = "base/uxr_config.h"
CONFIG_CC = "base/uxr_config.cc"
HELPER = "base/uxr_pixel_noise.h"
CHROME = "chrome/app/chrome_main.cc"
CANVAS = "third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc"
EXPORT = "third_party/blink/renderer/platform/graphics/image_data_buffer.cc"


def patch(number):
    return next((ROOT / "patches").glob(f"{number:04d}-*.patch"))


def hunks(number, target):
    text = patch(number).read_text(encoding="utf-8")
    section = next(part for part in text.split("diff --git ")
                   if f"+++ b/{target}\n" in part)
    result = []
    for body in re.split(r"^@@[^\n]*\n", section, flags=re.M)[1:]:
        lines = body.splitlines(keepends=True)
        before = "".join(line[1:] for line in lines if line.startswith((" ", "-")))
        after = "".join(line[1:] for line in lines if line.startswith((" ", "+")))
        result.append((before, after))
    return result


def apply_excerpt(text, number, target, indices=None):
    changes = hunks(number, target)
    if indices is not None:
        changes = [changes[i] for i in indices]
    for before, after in changes:
        assert text.count(before) == 1, (number, target, before)
        text = text.replace(before, after, 1)
    return text


@pytest.fixture(scope="module")
def sources():
    result = {}
    for target, initial, followups in (
        (CONFIG_H, 3, (158, 172, 218)),
        (CONFIG_CC, 2, (159, 173, 219)),
    ):
        text = added_source(patch(initial).name)
        for number in followups:
            text = apply_excerpt(text, number, target)
        result[target] = text
    result[HELPER] = hunks(221, HELPER)[0][1]
    # Only normalization excerpts are compiled, not ChromeMain's dependencies.
    chrome = added_source(patch(36).name)
    chrome = apply_excerpt(chrome, 176, CHROME, (1, 2))
    result[CHROME] = apply_excerpt(chrome, 220, CHROME)
    return result


def compile_cpp(directory, text, extra=()):
    if not CXX:
        pytest.skip("C++20 compiler required")
    source = directory / "pixel_test.cc"
    binary = directory / "pixel_test"
    source.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [CXX, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(directory),
         str(source), *map(str, extra), "-o", str(binary)],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


def run(binary, *args):
    result = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="module")
def policy_binary(config_binary, tmp_path_factory, sources):
    directory = tmp_path_factory.mktemp("explicit-pixel-policy") / "src"
    shutil.copytree(config_binary.parent, directory)
    for target in (CONFIG_H, CONFIG_CC, HELPER):
        (directory / target).write_text(sources[target], encoding="utf-8")
    return compile_cpp(directory, r'''
#include "base/uxr_config.h"
#include <cassert>
#include <iostream>
int main(int argc, char** argv) {
  base::flat_map<std::string, std::string> cfg;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i]; auto pos = arg.find('=');
    cfg[arg.substr(0, pos)] = arg.substr(pos + 1);
  }
  auto& config = base::UxrConfig::GetInstance();
  if (!config.SetAll(cfg)) {
    assert(!config.IsInitialized() && config.Snapshot().empty());
    auto policy = config.GpuBackendPolicy();
    assert(!policy.seeded_pixel_noise && policy.pixel_noise_seed == 0);
    std::cout << "rejected " << config.ValidationError();
    assert(config.SetAll({}));
    return 0;
  }
  assert(config.SetAll(cfg));
  const auto p = config.GpuBackendPolicy();
  std::cout << p.native << ' ' << p.seeded_pixel_noise << ' ' << p.pixel_noise_seed
            << ' ' << p.canvas_pixel_noise << ' ' << p.canvas_text_noise
            << ' ' << p.canvas_bridge << ' ' << p.capability_overrides;
}
''', (directory / CONFIG_CC,))


@pytest.mark.parametrize("backend", (None, "native", "compatibility"))
@pytest.mark.parametrize("synthetic", (False, True))
@pytest.mark.parametrize("mode", (None, "native", "seeded"))
@pytest.mark.parametrize("disabled", (False, True))
def test_policy_matrix(policy_binary, backend, synthetic, mode, disabled):
    args = ["uxr-canvas-seed=4294967297"]
    if backend is not None:
        args.append("uxr-gpu-backend=" + backend)
    if synthetic:
        args.append("uxr-synthetic-device-tests=true")
    if mode is not None:
        args.append("uxr-pixel-noise=" + mode)
    if disabled:
        args.append("uxr-disable-fingerprint-noise=false")
    native = backend == "native" if backend is not None else not synthetic
    seeded = mode == "seeded" and not disabled
    allowed = not native and synthetic
    expected = [native, seeded, 4294967297 if seeded and not disabled else 0,
                (allowed or seeded) and not disabled, not native and not disabled,
                allowed, not native]
    assert run(policy_binary, *args).split() == [str(int(v)) for v in expected]


@pytest.mark.parametrize("mode", ("", "Seeded", "NATIVE", "seeded ", " native", "true", "off", "seeded,native"))
@pytest.mark.parametrize("disabled", (False, True))
def test_modes_are_strict_even_when_disabled(policy_binary, mode, disabled):
    args = ["uxr-pixel-noise=" + mode, "uxr-canvas-seed=42"]
    if disabled:
        args.append("uxr-disable-fingerprint-noise=")
    assert run(policy_binary, *args).startswith("rejected invalid GPU backend or pixel noise policy")


@pytest.mark.parametrize("seed", (None, "", "0", "000", "+1", "-1", " 1", "1 ", "1.0", "1e2", "0x10", "abc", "18446744073709551616"))
def test_seed_validation_is_atomic_and_disable_wins(policy_binary, seed):
    args = ["uxr-pixel-noise=seeded"]
    if seed is not None:
        args.append("uxr-canvas-seed=" + seed)
    assert "nonzero decimal uint64 uxr-canvas-seed" in run(policy_binary, *args)
    assert run(policy_binary, *args, "uxr-disable-fingerprint-noise=") == "1 0 0 0 0 0 0"
    args[0] = "uxr-pixel-noise=native"
    assert run(policy_binary, *args) == "1 0 0 0 0 0 0"


@pytest.mark.parametrize("seed", ("1", "00042", "4294967296", "9223372036854775808", "18446744073709551615"))
def test_all_nonzero_uint64_seeds(policy_binary, seed):
    assert run(policy_binary, "uxr-pixel-noise=seeded", "uxr-canvas-seed=" + seed) == f"1 1 {int(seed)} 1 0 0 0"


def test_actual_header_compiles_and_matches_legacy_algorithm(tmp_path, sources):
    header = tmp_path / HELPER
    header.parent.mkdir()
    header.write_text(sources[HELPER], encoding="utf-8")
    binary = compile_cpp(tmp_path, r'''
#include "base/uxr_pixel_noise.h"
#include "base/uxr_pixel_noise.h"
#include <algorithm>
#include <cassert>
#include <climits>
#include <utility>
int main() {
  const std::pair<uint64_t, uint32_t> vectors[] = {
    {1, 1}, {12345, 12345}, {UINT32_MAX, UINT32_MAX},
    {uint64_t{1} << 32, 0x469913f8u}, {uint64_t{1} << 63, 0x6448276au},
    {UINT64_MAX, 0x9c0ff28bu}};
  for (auto [seed64, expected] : vectors) {
    assert(base::UxrPixelNoiseSeed(seed64) == expected);
    assert(base::UxrPixelNoiseSeed(seed64) == base::UxrPixelNoiseSeed(seed64));
    for (uint32_t x : {0u, 3u, UINT32_MAX})
      for (uint32_t y : {0u, 7u, UINT32_MAX})
        for (uint32_t ch = 0; ch < 3; ++ch)
          for (int v = 0; v < 256; ++v) {
            uint32_t z = expected ^ (x * 374761393u) ^ (y * 668265263u) ^
                         (ch * 0x9e3779b9u) ^ (uint32_t(v) * 2654435761u);
            z = (z ^ (z >> 16)) * 0x85ebca6bu;
            z = (z ^ (z >> 13)) * 0xc2b2ae35u;
            z ^= z >> 16;
            assert(base::UxrNoiseChannel(expected, x, y, ch, uint8_t(v)) ==
                   std::clamp(v + ((z & 1u) ? 1 : -1), 0, 255));
          }
  }
  for (uint64_t other : {uint64_t{43}, (uint64_t{1} << 32) | 42}) {
    int different = 0;
    for (uint32_t x = 0; x < 256; ++x)
      different += base::UxrNoiseChannel(base::UxrPixelNoiseSeed(42), x, 7, 1, 128) !=
                   base::UxrNoiseChannel(base::UxrPixelNoiseSeed(other), x, 7, 1, 128);
    assert(different > 0);
  }
}
''')
    run(binary)


@pytest.fixture(scope="module")
def canvas_sources(tmp_path_factory):
    original = canvas.patched_sources.__wrapped__(tmp_path_factory)
    readback = apply_excerpt(original["0020"], 160, CANVAS, (0, 1))
    # Compile the real noise block; unrelated bridge/opaque methods are not stubbed here.
    readback = apply_excerpt(readback, 222, CANVAS, (1, 2))
    export = apply_excerpt(original["0031"], 161, EXPORT)
    export = apply_excerpt(export, 223, EXPORT)
    return {"0020": readback, "0031": export}


@pytest.fixture(scope="module", params=(False, True), ids=("legacy", "native-seeded"))
def canvas_binary(request, tmp_path_factory, sources, canvas_sources):
    declaration = block(sources[CONFIG_H], "struct BASE_EXPORT UxrGpuBackendPolicy {").replace("BASE_EXPORT ", "") + ";"
    parser = block(sources[CONFIG_CC], "bool ParseGpuBackend(")
    support = canvas.CPP_SUPPORT.replace("namespace base {", "#include <map>\n" + sources[HELPER] + "\nnamespace base {\n"
        "template<class K, class V> using flat_map = std::map<K, V>;\n" + declaration +
        "\nbool StringToUint64(const std::string&, uint64_t*);\n" + parser, 1)
    support = support.replace("  bool synthetic = true;", """  bool synthetic = true;
  bool native = false;
  bool explicit_noise = false;
  UxrGpuBackendPolicy GpuBackendPolicy() const {
    flat_map<std::string, std::string> cfg{{"uxr-canvas-seed", seed}};
    if (synthetic) cfg["uxr-synthetic-device-tests"] = "true";
    if (native) cfg["uxr-gpu-backend"] = "native";
    if (explicit_noise) cfg["uxr-pixel-noise"] = "seeded";
    if (disabled) cfg["uxr-disable-fingerprint-noise"] = "";
    UxrGpuBackendPolicy policy;
    assert(ParseGpuBackend(cfg, &policy));
    return policy;
  }""")
    tests = canvas.CPP_TESTS.replace('  const std::string test = argv[1];', r'''  const std::string test = argv[1];
  if (test == "pixel-gates") {
    auto& config = base::UxrConfig::GetInstance();
    for (bool native : {false, true}) for (bool synthetic : {false, true})
      for (bool seeded : {false, true}) for (bool disabled : {false, true}) {
        config.native = native; config.synthetic = synthetic;
        config.explicit_noise = seeded; config.disabled = disabled;
        config.seed = disabled ? "invalid-but-disabled" : "12345";
        const bool enabled = ((!native && synthetic) || seeded) && !disabled;
        for (auto type : {kRGBA_8888_SkColorType, kBGRA_8888_SkColorType}) {
          Fixture original(type), readback = original;
          const auto before = original.bytes;
          ImageData read{readback.pm()}; ReadNoise(&read, 0, 0);
          auto encoded = ImageDataBuffer::Create(original.pm()); assert(encoded);
          assert(original.bytes == before);
          assert(Bytes(encoded->pixmap_) == Bytes(readback.pm()));
          assert((encoded->pixmap_.addr() != original.bytes.data()) == enabled);
          assert((readback.bytes != before) == enabled);
          assert(encoded->pixmap_.info().alphaType() == original.info.at);
          if (enabled) CheckPixels(original, encoded->pixmap_);
        }
      }
    return 0;
  }''')
    if request.param:
        tests = tests.replace('  const std::string test = argv[1];', '''  const std::string test = argv[1];
  auto& policy_config = base::UxrConfig::GetInstance();
  policy_config.synthetic = false;
  policy_config.native = true;
  policy_config.explicit_noise = true;
  assert(policy_config.GpuBackendPolicy().canvas_pixel_noise);
  assert(!policy_config.GpuBackendPolicy().canvas_bridge);
  assert(!policy_config.GpuBackendPolicy().capability_overrides);''')
    with pytest.MonkeyPatch.context() as change:
        change.setattr(canvas, "CPP_SUPPORT", support)
        change.setattr(canvas, "CPP_TESTS", tests)
        return canvas.runtime_binary.__wrapped__(tmp_path_factory, canvas_sources)


@pytest.mark.parametrize("case", ("pixel-gates", "transparent-oob", "coordinates", "channels", "padding", "constructors", "lifetime", "failures", "repeat", "uint64-seeds", "uint64-high-bits", "legacy-seeds"))
def test_real_canvas_noise_private_copy_alpha_crop_and_seed(canvas_binary, case):
    result = subprocess.run([str(canvas_binary), case], capture_output=True, text=True,
                            timeout=15, env=canvas.sanitizer_env())
    assert result.returncode == 0, result.stdout + result.stderr


def test_normalization_off_noise_false_and_direct_seed_requirement(tmp_path, sources):
    text = sources[CHROME]
    aliases = block(text, "    struct Alias {") + ";\n"
    aliases += block(text, "    static constexpr Alias kAliases[] = {") + ";\n"
    aliases += block(text, "    for (const auto& a : kAliases)")
    stub = COMMAND_LINE_STUB.replace("  void AppendSwitchASCII(", """  std::string GetSwitchValueNative(const std::string& key) const { return GetSwitchValueASCII(key); }
  void AppendSwitchNative(const std::string& key, const std::string& value) { values[key] = value; }
  void AppendSwitchASCII(""")
    normalize = block(text, "    const auto set_switch =") + ";\n" + aliases
    normalize += block(text, '    if (command_line->HasSwitch("fingerprint")) {\n      std::string seed =')
    normalize += block(text, '    if (command_line->HasSwitch("fingerprint-noise")')
    normalize += block(text, '    if (command_line->HasSwitch("fingerprint") &&\n        command_line->GetSwitchValueASCII("fingerprint") == "off")')
    binary = compile_cpp(tmp_path, stub + r'''
#include <cstdint>
namespace base {
uint64_t RandUint64() { return 4294967297ULL; }
std::string NumberToString(uint64_t n) { return std::to_string(n); }
}
void Normalize(base::CommandLine* command_line) {
''' + normalize + r'''
}
int main() {
  base::CommandLine cmd;
  cmd.values = {{"fingerprint", ""}, {"fingerprint-pixel-noise", "seeded"}};
  Normalize(&cmd);
  assert(cmd.GetSwitchValueASCII("uxr-pixel-noise") == "seeded");
  assert(cmd.GetSwitchValueASCII("uxr-canvas-seed") == "4294967297");
  assert(!cmd.HasSwitch("uxr-synthetic-device-tests") && !cmd.HasSwitch("uxr-canvas-bridge"));
  cmd.values = {{"fingerprint-pixel-noise", "seeded"}};
  Normalize(&cmd);
  assert(!cmd.HasSwitch("uxr-canvas-seed"));
  cmd.values = {{"fingerprint", "42"}, {"fingerprint-pixel-noise", "seeded"},
                {"fingerprint-noise", "false"}};
  Normalize(&cmd);
  assert(cmd.HasSwitch("uxr-disable-fingerprint-noise"));
  assert(cmd.GetSwitchValueASCII("uxr-canvas-seed") == "42");
  cmd.values = {{"fingerprint", "off"}, {"fingerprint-pixel-noise", "seeded"}};
  Normalize(&cmd);
  assert(cmd.HasSwitch("uxr-disable-fingerprint-noise"));
  assert(!cmd.HasSwitch("uxr-canvas-seed") && !cmd.HasSwitch("uxr-pixel-noise"));
  assert(!cmd.HasSwitch("fingerprint-pixel-noise"));
  cmd.values = {{"fingerprint-pixel-noise", "seeded"}, {"uxr-pixel-noise", "native"}};
  Normalize(&cmd);
  assert(cmd.GetSwitchValueASCII("uxr-pixel-noise") == "native");
}
''')
    run(binary)


def test_patch_contracts(sources, canvas_sources):
    assert '"uxr_pixel_noise.h"' in patch(217).read_text()
    policy = block(sources[CONFIG_CC], "bool ParseGpuBackend(")
    assert "(allow_synthetic || policy->seeded_pixel_noise) && !noise_disabled" in policy
    assert "policy->canvas_bridge = allow_synthetic;" in policy
    assert "policy->capability_overrides = !policy->native;" in policy
    assert 'cfg.find("uxr-pixel-noise")' in policy
    assert 'CHECK(snapshot.SetAll(uxr_cfg))' in added_source(patch(5).name)
    assert 'std::move(uxr_cfg), base::UxrConfig::kSchemaVersion' in added_source(patch(5).name)
    readback, export = canvas_sources["0020"], canvas_sources["0031"]
    assert "canvas_pixel_noise && !persona_noise" in readback
    assert "ph_config.GpuBackendPolicy().canvas_pixel_noise" in readback
    assert "config.GpuBackendPolicy().canvas_pixel_noise" in export
    assert "if (ph_pixel[3] == 0)" in readback and "if (pixel[3] == 0)" in export
    assert "static_cast<uint32_t>(sx) + static_cast<uint32_t>(ph_x)" in readback
    assert "static_cast<uint32_t>(sy) + static_cast<uint32_t>(ph_y)" in readback
    assert "gfx::MakeSkDataFromSpanWithCopy(gfx::SkPixmapToSpan(pm))" in export
    assert "retained_image = std::move(image);" in export
    for text in (readback, export):
        assert "base::UxrPixelNoiseSeed(" in text and "base::UxrNoiseChannel(" in text
    added = "\n".join(line[1:] for line in (patch(222).read_text() + patch(223).read_text()).splitlines()
                      if line.startswith("+") and not line.startswith("+++"))
    assert "GpuBackendPolicy().native" not in added
    assert "0x85ebca6b" not in added
