"""Execute final GPU identity patches with dependency stubs, not real adapters."""
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest

import test_fingerprint_gpu as gpu

ROOT = gpu.ROOT
patched_sources = gpu.patched_sources
patched_0030 = gpu.patched_0030
persona_sources = gpu.persona_sources


def patch_path(number):
    return next((ROOT / 'patches').glob(number + '-*.patch'))


def patch_target(number):
    return re.search(r'^\+\+\+ b/(.*)$', patch_path(number).read_text(), re.M)[1]


def apply(directory, number, reverse=False):
    if not gpu.PATCH_BIN:
        pytest.skip('GNU patch is required')
    result = subprocess.run([gpu.PATCH_BIN, '-p1', '--fuzz=0', '--batch', '--get=0',
        '--no-backup-if-mismatch', '--reject-file=-', '--reverse' if reverse else '--forward',
        '-i', str(patch_path(number))], cwd=directory, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'offset' not in result.stdout and 'fuzz' not in result.stdout


@pytest.mark.parametrize('number,predecessor', [('0147','0110'),('0148','0030')])
def test_final_identity_patch_strict_roundtrip(tmp_path, patched_sources, patched_0030, number, predecessor):
    before = patched_0030 if predecessor == '0030' else patched_sources[predecessor]
    target = tmp_path / patch_target(number); target.parent.mkdir(parents=True)
    target.write_text(before)
    apply(tmp_path, number)
    after = target.read_text()
    assert after != before
    if number == '0147':
        for name in ('Renderer','Vendor'):
            block = gpu.excerpt(after, f'    case WebGLDebugRendererInfo::kUnmasked{name}Webgl:',
                                '      SynthesizeGLError(')
            assert 'drawing_buffer_->ContextInfo()' in block
            assert 'ContextGL()->GetString(GL_RENDERER)' in block
            assert 'CurrentPersona' not in block
            assert 'GetFarbleSeed' not in block
            assert 'presented ? String(*presented)' in block
            assert 'presented.empty()' not in block
            assert block.index('ExtensionEnabled(') < block.index('ContextGL()')
        assert 'if (isContextLost())\n    return ScriptValue::CreateNull' in after
    apply(tmp_path, number, reverse=True)
    assert target.read_text() == before


@pytest.fixture(scope='module', params=gpu.SIMULATED_PLATFORMS)
def final_adapter_binary(request, tmp_path_factory, patched_sources, patched_0030, persona_sources):
    directory = tmp_path_factory.mktemp('final-adapter-source')
    target = directory / patch_target('0148'); target.parent.mkdir(parents=True)
    target.write_text(patched_0030); apply(directory, '0148')
    return gpu.identity_binary.__wrapped__(SimpleNamespace(param=request.param), tmp_path_factory,
                                          patched_sources, target.read_text(), persona_sources)


@pytest.mark.parametrize('config', [{}, {'uxr-fingerprint-seed':'42'},
    {'uxr-fingerprint-seed':'42','uxr-webgl-vendor':'Other','uxr-webgl-renderer':'Other GPU'},
    {'uxr-webgpu-vendor':'Other','uxr-webgpu-architecture':'other-arch'},
    {'uxr-webgl-real':'true','uxr-fingerprint-seed':'42'}])
def test_public_webgpu_always_keeps_complete_native_adapter(final_adapter_binary, config):
    result = gpu.run_identity(final_adapter_binary, config, synthetic=False)
    gpu.assert_native_adapter(result)
    gpu.assert_native_adapter(result, 'software')


def test_synthetic_mode_is_explicit_and_still_preserves_software(final_adapter_binary):
    result = gpu.run_identity(final_adapter_binary, {'uxr-fingerprint-seed':'42'}, synthetic=True)
    gpu.assert_platform_template(result, final_adapter_binary)
    gpu.assert_native_adapter(result, 'software')


@pytest.fixture(scope='module')
def context_binary(tmp_path_factory):
    if not gpu.CXX:
        pytest.skip('C++20 compiler is required')
    directory = tmp_path_factory.mktemp('webgl-context-identity')
    for number in ('0101','0102'):
        target = directory / patch_target(number); target.parent.mkdir(parents=True,exist_ok=True)
        apply(directory, number)
    files = {
        'third_party/blink/public/platform/platform.h': r'''
#pragma once
namespace gl { enum class ANGLEImplementation { kNone=0, kD3D11=1, kOpenGL=2,
kOpenGLES=3, kNull=4, kVulkan=5, kSwiftShader=6, kMetal=7, kDefault=8, kD3D11Warp=9 }; }
namespace blink { struct Platform { struct WebGLContextInfo {
  unsigned vendor_id=0, device_id=0;
  bool using_gpu_compositing=false;
  gl::ANGLEImplementation angle_implementation=gl::ANGLEImplementation::kNone;
}; }; }
''',
        'base/strings/string_util.h': r'''
#pragma once
#include <string>
#include <string_view>
namespace base { inline std::string ToLowerASCII(std::string_view text) {
  std::string out(text); for (auto& c:out) if (c>='A' && c<='Z') c=char(c-'A'+'a'); return out;
} }
''',
        'components/ungoogled/persona_profile.h': r'''
#pragma once
#include <string>
namespace ungoogled {
struct Persona { bool webgl_real=false, webgl_native_capabilities=true, webgl_identity_explicit=false;
  std::string webgl_vendor="explicit-vendor", webgl_renderer="explicit-renderer"; };
inline Persona persona;
inline const Persona& CurrentPersona() { return persona; }
}
''',
        'context.cc': r'''
#include <cassert>
#include <cstdlib>
#include <iostream>
#include "components/ungoogled/persona_profile.h"
#include "third_party/blink/renderer/modules/webgl/gpu_fingerprint.h"
int main(int argc, char** argv) {
  assert(argc==8 || argc==10);
  ungoogled::persona.webgl_native_capabilities=std::atoi(argv[1]);
  ungoogled::persona.webgl_identity_explicit=std::atoi(argv[2]);
  ungoogled::persona.webgl_real=std::atoi(argv[3]);
  if (argc==10) {
    ungoogled::persona.webgl_vendor=argv[8];
    ungoogled::persona.webgl_renderer=argv[9];
  }
  blink::Platform::WebGLContextInfo info;
  info.angle_implementation=static_cast<gl::ANGLEImplementation>(std::atoi(argv[4]));
  info.vendor_id=std::strtoul(argv[5],nullptr,0); info.device_id=std::strtoul(argv[6],nullptr,0);
  info.using_gpu_compositing=true;  // A hardware compositor does not certify WebGL.
  const auto before=ungoogled::CurrentPersona().webgl_renderer;
  const auto renderer=blink::GetGLRendererStringForFingerprint(info,argv[7]);
  const auto vendor=blink::GetGLVendorStringForFingerprint(info,argv[7]);
  std::cout << vendor.value_or("<native>") << '\n' << renderer.value_or("<native>") << '\n';
  // Context loss/restoration and another context must not poison a launch-global cache.
  auto software=info; software.angle_implementation=gl::ANGLEImplementation::kSwiftShader;
  assert(!blink::GetGLRendererStringForFingerprint(software,"SwiftShader").has_value());
  assert(!blink::GetGLVendorStringForFingerprint(software,"SwiftShader").has_value());
  assert(blink::GetGLRendererStringForFingerprint(info,argv[7])==renderer);
  assert(ungoogled::CurrentPersona().webgl_renderer==before);
}
'''}
    for name, source in files.items():
        path = directory / name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(source)
    binary = directory / 'context'
    command = [gpu.CXX, '-std=c++20', '-Wall', '-Wextra', '-Werror', '-I', str(directory),
               str(directory/'context.cc'), str(directory/patch_target('0101')), '-o', str(binary)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


@pytest.mark.parametrize('native,explicit,real,angle,vendor,device,renderer,override', [
    (1,0,0,1,0x10de,0x1234,'ANGLE NVIDIA',False),
    (1,1,0,1,0x10de,0x1234,'ANGLE NVIDIA',True),
    (0,0,0,1,0x10de,0x1234,'ANGLE NVIDIA',True),
    (0,1,1,1,0x10de,0x1234,'ANGLE NVIDIA',False),
    (1,1,0,2,0x1002,0x1234,'Mesa AMD Radeon',True),
    (1,1,0,7,0x106b,0x1234,'Apple GPU Metal',True),
    (1,1,0,1,0x1414,0x008c,'Unknown',False),
    (1,1,0,1,0,0,'',False),
] + [(n,1,0,a,0,0,'hardware-like name',False) for n in (0,1) for a in (4,6,9)] +
    [(n,1,0,2,0,0,s,False) for n in (0,1) for s in ('SwiftShader','LLVMPIPE','softpipe',
        'lavapipe','Mesa Software Rasterizer','Software Renderer','Microsoft Basic Render Driver')])
def test_identity_follows_actual_context_backend(context_binary, native, explicit, real, angle, vendor, device, renderer, override):
    result = subprocess.run([str(context_binary),*[str(x) for x in (native,explicit,real,angle,vendor,device)],renderer],
                            text=True,capture_output=True,timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == (['explicit-vendor','explicit-renderer'] if override else ['<native>','<native>'])


@pytest.mark.parametrize('vendor,renderer', [('Unknown Vendor',''), ('','Unknown Renderer')])
def test_unknown_one_sided_identity_never_borrows_native_counterpart(context_binary, vendor, renderer):
    result = subprocess.run([str(context_binary), '1', '1', '0', '1', '4318', '4660',
                             'ANGLE native hardware', vendor, renderer],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [vendor, renderer]
