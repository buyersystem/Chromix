"""0224 contracts and compiled C++20 CPU checks; not a Chromium/GPU build."""
from pathlib import Path
import copy
import shutil
import subprocess
import tempfile
import unittest

from tools import check_patches, webgl_seeded_noise_diagnostic as diagnostic


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "patches/0224-webgl-seeded-readback-noise.patch"


def additions():
    return "\n".join(line[1:] for line in PATCH.read_text().splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


CPP_PREFIX = r'''
#include <algorithm>
#include <cassert>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <optional>
#include <span>
#include <vector>
// Only the span API is substituted; algorithms and integration are extracted
// verbatim from 0224. This does not claim Chromium-header compilation.
namespace base {
template<class T> using span = std::span<T>;
struct Policy {
  bool seeded_pixel_noise = false;
  uint64_t pixel_noise_seed = 0;
  bool canvas_pixel_noise = true;
};
struct UxrConfig {
  Policy policy;
  static UxrConfig& GetInstance() { static UxrConfig value; return value; }
  Policy GpuBackendPolicy() const { return policy; }
};
}
namespace gfx {
struct Size {
  int w = 32, h = 16;
  int width() const { return w; }
  int height() const { return h; }
};
}
'''

CPP_HARNESS = r'''
uint8_t UxrWebGLNoiseByte(uint8_t value, uint64_t seed, uint32_t x,
                          uint32_t y, uint32_t channel) {
  return base::UxrNoiseChannel(base::UxrPixelNoiseSeed(seed), x, y, channel, value);
}
constexpr int GL_BACK = 1, GL_RGBA = 2, GL_UNSIGNED_BYTE = 3;
struct Value {
  size_t n;
  size_t ValueOrDie() const { return n; }
};
struct Pixels {
  std::vector<uint8_t> bytes = std::vector<uint8_t>(4096, 0xa5);
  bool shared = false;
  bool IsShared() const { return shared; }
  base::span<uint8_t> ByteSpanMaybeShared() { return bytes; }
};
struct Pack { int alignment = 4, row_length = 0, skip_pixels = 0, skip_rows = 0; };
struct Drawing {
  gfx::Size size;
  gfx::Size Size() const { return size; }
};
struct Attributes { bool alpha = false; };
struct FakeGL {
  enum Result { kSuccess, kFailure, kPartial } result = kSuccess;
  uint8_t* caller = nullptr;
  bool native = false;
  int calls = 0;
  Pack pack;
  void ReadPixels(int, int, int width, int height, int, int, void* data) {
    ++calls;
    native = data == caller;
    if (native || result == kFailure) return;
    auto layout = UxrWebGLReadbackLayoutFor(width, height, pack.alignment,
        pack.row_length, pack.skip_pixels, pack.skip_rows, 4096);
    assert(layout);
    base::span<uint8_t> out(static_cast<uint8_t*>(data), layout->total);
    for (int row = 0; row < height; ++row) {
      if (result == kPartial && row == height - 1) break;
      auto bytes = out.subspan(layout->skip + row * layout->stride, layout->row_bytes);
      for (size_t i = 0; i < bytes.size(); ++i) bytes[i] = i % 4 == 3 ? 255 : 100;
    }
  }
};
struct Harness {
  int x = 0, y = 0, width = 3, height = 2;
  int format = GL_RGBA, type = GL_UNSIGNED_BYTE;
  int read_buffer_of_default_framebuffer_ = GL_BACK;
  bool lost = false;
  void* framebuffer = nullptr;
  Pixels storage;
  Pixels* pixels = &storage;
  Attributes attributes;
  Drawing drawing;
  FakeGL gl;
  Value offset_in_bytes{7}, buffer_size{4089};
  Drawing* GetDrawingBuffer() { return &drawing; }
  Attributes CreationAttributes() { return attributes; }
  Pack GetPackPixelStoreParams() { return gl.pack; }
  FakeGL* ContextGL() { return &gl; }
  bool isContextLost() { return lost; }
  void Run() {
    uint8_t* data = storage.bytes.data() + offset_in_bytes.n;
    gl.caller = data;
'''

CPP_TESTS = r'''
    ContextGL()->ReadPixels(x, y, width, height, format, type, data);
  }
};

int main() {
  // Exhaustive small PACK matrix, all alignments, skips, exact-size buffers.
  for (int alignment : {1, 2, 4, 8}) {
    for (int width = 1; width <= 9; ++width) {
      for (int height = 1; height <= 5; ++height) {
        for (int extra = 0; extra < 4; ++extra) {
          for (int skip = 0; skip <= extra; ++skip) {
            const int row_length = width + extra;
            const size_t stride = ((row_length * 4 + alignment - 1) / alignment) * alignment;
            const size_t first = stride * 2 + skip * 4;
            const size_t total = first + stride * (height - 1) + width * 4;
            auto layout = UxrWebGLReadbackLayoutFor(width, height, alignment,
                                                   row_length, skip, 2, total);
            assert(layout && layout->stride == stride && layout->skip == first);
            assert(layout->total == total && layout->row_bytes == size_t(width * 4));
            assert(!UxrWebGLReadbackLayoutFor(width, height, alignment,
                                             row_length, skip, 2, total - 1));
          }
        }
      }
    }
  }
  for (int bad : {-1, INT_MIN, INT_MAX}) {
    assert(!UxrWebGLReadbackLayoutFor(bad, 1, 4, 0, 0, 0, SIZE_MAX));
    assert(!UxrWebGLReadbackLayoutFor(1, bad, 4, 0, 0, 0, SIZE_MAX));
    assert(!UxrWebGLReadbackLayoutFor(1, 1, 4, bad, 0, 0, SIZE_MAX));
    assert(!UxrWebGLReadbackLayoutFor(1, 1, 4, 0, bad, 0, SIZE_MAX));
    assert(!UxrWebGLReadbackLayoutFor(1, 1, 4, 0, 0, bad, SIZE_MAX));
  }
  assert(!UxrWebGLReadbackLayoutFor(0, 1, 4, 0, 0, 0, 4));
  assert(!UxrWebGLReadbackLayoutFor(1, 0, 4, 0, 0, 0, 4));
  for (int bad : {0, 3, -1, INT_MAX})
    assert(!UxrWebGLReadbackLayoutFor(1, 1, bad, 0, 0, 0, 4));
  assert(!UxrWebGLReadbackLayoutFor(2, 1, 4, 1, 0, 0, SIZE_MAX));
  assert(!UxrWebGLReadbackLayoutFor(2, 1, 4, 0, 1, 0, SIZE_MAX));
  assert(UxrWebGLReadbackLayoutFor(1, 1, 4, 0, 0, 0, 4));
  assert(UxrWebGLReadbackLayoutFor(1024, 4096, 4, 0, 0, 0, SIZE_MAX));
  assert(!UxrWebGLReadbackLayoutFor(1024, 4097, 4, 0, 0, 0, SIZE_MAX));

  // Stable bounded RGB perturbations, saturation and independent seeds.
  size_t changes = 0, seed_changes = 0;
  for (uint32_t x = 0; x < 64; ++x) {
    for (uint32_t y = 0; y < 32; ++y) {
      for (uint32_t c = 0; c < 3; ++c) {
        for (uint8_t v : {0, 1, 100, 254, 255}) {
          int a = UxrWebGLNoiseByte(v, 42, x, y, c);
          assert(a >= 0 && a <= 255 && a >= v - 1 && a <= v + 1);
          assert(a == UxrWebGLNoiseByte(v, 42, x, y, c));
          changes += a != v;
          seed_changes += a != UxrWebGLNoiseByte(v, 43, x, y, c);
        }
      }
    }
  }
  assert(changes > 100 && seed_changes > 100);
  assert(base::UxrPixelNoiseSeed(42) == 42);

  // Actual integration block: off/native remains direct and writes nothing here.
  Harness native;
  native.Run();
  assert(native.gl.native && native.gl.calls == 1);
  base::UxrConfig::GetInstance().policy = {true, 42};
  base::UxrConfig::GetInstance().policy.canvas_pixel_noise = false;
  Harness disabled;
  disabled.Run();
  assert(disabled.gl.native && disabled.gl.calls == 1);
  base::UxrConfig::GetInstance().policy.canvas_pixel_noise = true;
  Harness success;
  success.gl.pack = {8, 7, 2, 1};
  success.Run();
  assert(!success.gl.native && success.gl.calls == 1);
  auto layout = *UxrWebGLReadbackLayoutFor(3, 2, 8, 7, 2, 1, 4089);
  for (size_t i = 0; i < success.storage.bytes.size(); ++i) {
    bool payload = false;
    for (int row = 0; row < 2; ++row) {
      const size_t start = 7 + layout.skip + row * layout.stride;
      payload |= i >= start && i < start + layout.row_bytes;
    }
    if (!payload) assert(success.storage.bytes[i] == 0xa5);
  }
  for (int row = 0; row < 2; ++row) {
    for (int col = 0; col < 3; ++col) {
      const size_t start = 7 + layout.skip + row * layout.stride + col * 4;
      for (uint32_t c = 0; c < 3; ++c)
        assert(success.storage.bytes[start + c] == UxrWebGLNoiseByte(100, 42, col, 15 - row, c));
      assert(success.storage.bytes[start + 3] == 255);
    }
  }
  const auto first = success.storage.bytes;
  success.Run();
  assert(success.storage.bytes == first);
  Harness crop;
  crop.x = 1; crop.y = 1; crop.width = 2; crop.height = 1;
  crop.Run();
  for (size_t i = 0; i < 8; ++i)
    assert(crop.storage.bytes[7 + i] == first[7 + layout.skip + layout.stride + 4 + i]);

  // Untouched and partial writes cannot turn sentinels into noisy pixels.
  for (auto result : {FakeGL::kFailure, FakeGL::kPartial}) {
    for (uint8_t sentinel : {0, 165, 255}) {
      Harness failure;
      std::ranges::fill(failure.storage.bytes, sentinel);
      failure.gl.result = result;
      failure.Run();
      assert(!failure.gl.native && failure.gl.calls == 1);
      assert(std::ranges::all_of(failure.storage.bytes,
                                [sentinel](uint8_t v) { return v == sentinel; }));
    }
  }
  Harness lost;
  lost.lost = true;
  lost.Run();
  assert(std::ranges::all_of(lost.storage.bytes, [](uint8_t v) { return v == 0xa5; }));

  // Every unsupported path remains the original single direct call.
  for (int test = 0; test < 15; ++test) {
    Harness fallback;
    switch (test) {
      case 0: fallback.attributes.alpha = true; break;
      case 1: fallback.storage.shared = true; break;
      case 2: fallback.framebuffer = &fallback; break;
      case 3: fallback.read_buffer_of_default_framebuffer_ = 0; break;
      case 4: fallback.format = 0; break;
      case 5: fallback.type = 0; break;
      case 6: fallback.x = -1; break;
      case 7: fallback.y = -1; break;
      case 8: fallback.width = 0; break;
      case 9: fallback.height = 0; break;
      case 10: fallback.x = INT_MAX; break;
      case 11: fallback.y = INT_MAX; break;
      case 12: fallback.width = INT_MAX; break;
      case 13: fallback.height = INT_MAX; break;
      case 14: fallback.gl.pack.skip_pixels = 1; break;
    }
    fallback.Run();
    assert(fallback.gl.native && fallback.gl.calls == 1);
  }
  std::cout << "PACK/offset/crop/Y/seed/alpha/failure/fallback checks passed\n";
}
'''


class WebGLSeededNoiseTest(unittest.TestCase):
    def test_patch_body_lint(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            shutil.copyfile(PATCH, directory / PATCH.name)
            report = check_patches.Report()
            check_patches.check_bodies(report, directory, False)
            self.assertEqual([], report.failures)

    def test_scope_and_error_contract(self):
        text = PATCH.read_text()
        added = additions()
        self.assertEqual(1, text.count("diff --git "))
        self.assertIn("policy.seeded_pixel_noise && policy.canvas_pixel_noise", added)
        self.assertIn("policy.pixel_noise_seed", added)
        for prohibited in ("GetError(", "SynthesizeGLError(", "UNSAFE_", "NOLINT",
                           "GetString(", "GetIntegerv(", "PixelStorei("):
            self.assertNotIn(prohibited, added)
        self.assertIn("if (!binder.Succeeded())", text)
        self.assertIn("pixels->ByteSpanMaybeShared().subspan(offset_in_bytes.ValueOrDie())", added)
        self.assertIn("GetPackPixelStoreParams()", added)
        self.assertIn("!CreationAttributes().alpha", added)
        self.assertIn("!pixels->IsShared()", added)
        self.assertIn("!isContextLost()", added)
        self.assertIn("PBO overloads never enter this helper", added)
        self.assertNotIn("webgl2_rendering_context_base.cc", text)

    def test_diagnostic_rejects_missing_evidence(self):
        self.assertTrue(diagnostic.analyze([]))
        modes = ('native', 'seed-a', 'seed-a-repeat', 'seed-b', 'off', 'noise-false')
        launches = []
        for mode in modes:
            value = 101 if mode in ('seed-a', 'seed-a-repeat') else 99 if mode == 'seed-b' else 100
            first = [value, value, value, 255] * 32
            rows = []
            for kind in ('webgl', 'webgl2'):
                crop = [value, value, value, 255] * 6
                packed = []
                if kind == 'webgl2':
                    for alignment in (1, 2, 4, 8):
                        stride = (28 + alignment - 1) // alignment * alignment
                        data = [165] * 200
                        for row in range(2):
                            start = 12 + stride + 8 + row * stride
                            data[start:start + 12] = crop[row * 12:(row + 1) * 12]
                        packed.append({'alignment': alignment, 'bytes': data, 'error': 0})
                rows.append({'kind': kind, 'first': first, 'again': first, 'pending': first,
                             'crop': crop, 'alpha': [0] * 16, 'sentinel': [165] * 64,
                             'short': [165] * 3, 'invalidError': 1281, 'shortError': 1282,
                             'pendingError': 1280, 'packed': packed})
            launches.append({'mode': mode, 'rows': rows})
        self.assertEqual([], diagnostic.analyze(launches))
        for field, value in (('sentinel', [164] * 64), ('pendingError', 0),
                             ('crop', [0] * 24), ('alpha', [255] * 16), ('packed', [])):
            broken = copy.deepcopy(launches)
            broken[1]['rows'][1][field] = value
            self.assertTrue(diagnostic.analyze(broken), field)

    def test_diagnostic_javascript_syntax(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable')
        result = subprocess.run([node, '--check'], input='const probe = ' + diagnostic.PROBE + ';',
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_compiled_cpu_algorithm_and_integration(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        if not compiler:
            self.skipTest("C++20 compiler unavailable")
        added = additions()
        helpers = added[added.index("// A bounded staging"):added.index("    const auto policy")]
        integration = added[added.index("    const auto policy"):]
        policy_patch = (ROOT / "patches/0221-pixel-noise-shared-helper.patch").read_text()
        header_patch = policy_patch.split("diff --git a/base/uxr_pixel_noise.h", 1)[1]
        header = "\n".join(line[1:] for line in header_patch.splitlines()
                           if line.startswith("+") and not line.startswith("+++"))
        source = CPP_PREFIX + header + helpers + CPP_HARNESS + integration + CPP_TESTS
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cpp = directory / "readback.cc"
            executable = directory / "readback"
            cpp.write_text(source)
            build = subprocess.run(
                [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(cpp),
                 "-o", str(executable)], capture_output=True, text=True, timeout=90)
            self.assertEqual(0, build.returncode, build.stdout + build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            self.assertIn("checks passed", run.stdout)


if __name__ == "__main__":
    unittest.main()
