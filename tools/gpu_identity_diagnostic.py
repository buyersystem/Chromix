#!/usr/bin/env python3
"""Compare WebGL identity opt-in on a caller-supplied browser, using localhost only.

This is a diagnostic, not device certification or proof of a matching patch build.
No SDK defaults, downloads, synthetic capabilities or software-guard bypasses.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading


PROBE = r'''async () => {
  const bounded = async (work, ms, label) => {
    let timer;
    try { return await Promise.race([work, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(label + ' timed out')), ms);
    })]); } finally { clearTimeout(timer); }
  };
  const plain = value => ArrayBuffer.isView(value) ? Array.from(value) : value;
  const glSnapshot = gl => {
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const names = ['MAX_TEXTURE_SIZE', 'MAX_CUBE_MAP_TEXTURE_SIZE', 'MAX_RENDERBUFFER_SIZE',
      'MAX_VIEWPORT_DIMS', 'MAX_VERTEX_ATTRIBS', 'MAX_VERTEX_UNIFORM_VECTORS',
      'MAX_VARYING_VECTORS', 'MAX_COMBINED_TEXTURE_IMAGE_UNITS', 'MAX_VERTEX_TEXTURE_IMAGE_UNITS',
      'MAX_TEXTURE_IMAGE_UNITS', 'MAX_FRAGMENT_UNIFORM_VECTORS', 'ALIASED_LINE_WIDTH_RANGE',
      'ALIASED_POINT_SIZE_RANGE', 'RED_BITS', 'GREEN_BITS', 'BLUE_BITS', 'ALPHA_BITS',
      'DEPTH_BITS', 'STENCIL_BITS', 'SUBPIXEL_BITS', 'SAMPLE_BUFFERS', 'SAMPLES'];
    if (typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext) names.push('MAX_3D_TEXTURE_SIZE',
      'MAX_ARRAY_TEXTURE_LAYERS', 'MAX_COLOR_ATTACHMENTS', 'MAX_DRAW_BUFFERS', 'MAX_SAMPLES',
      'MAX_ELEMENT_INDEX', 'MAX_ELEMENTS_INDICES', 'MAX_ELEMENTS_VERTICES', 'MAX_TEXTURE_LOD_BIAS',
      'MAX_FRAGMENT_INPUT_COMPONENTS', 'MAX_FRAGMENT_UNIFORM_COMPONENTS',
      'MAX_VERTEX_OUTPUT_COMPONENTS', 'MAX_VERTEX_UNIFORM_COMPONENTS', 'MAX_VARYING_COMPONENTS',
      'MAX_UNIFORM_BUFFER_BINDINGS', 'MAX_UNIFORM_BLOCK_SIZE', 'UNIFORM_BUFFER_OFFSET_ALIGNMENT',
      'MAX_COMBINED_UNIFORM_BLOCKS', 'MAX_VERTEX_UNIFORM_BLOCKS', 'MAX_FRAGMENT_UNIFORM_BLOCKS',
      'MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS', 'MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS',
      'MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS', 'MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS',
      'MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS', 'MAX_SERVER_WAIT_TIMEOUT');
    const limits = Object.fromEntries(names.map(name => [name, plain(gl.getParameter(gl[name]))]));
    const precision = {};
    for (const shader of ['VERTEX_SHADER', 'FRAGMENT_SHADER'])
      for (const level of ['LOW_FLOAT', 'MEDIUM_FLOAT', 'HIGH_FLOAT', 'LOW_INT', 'MEDIUM_INT', 'HIGH_INT']) {
        const p = gl.getShaderPrecisionFormat(gl[shader], gl[level]);
        precision[shader + '/' + level] = p ? [p.rangeMin, p.rangeMax, p.precision] : null;
      }
    return {identity: {vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION), shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      debugExtension: !!debug, unmaskedVendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null},
      limits, extensions: (gl.getSupportedExtensions() || []).sort(), precision,
      attributes: gl.getContextAttributes(), error: gl.getError()};
  };
  const webgl = async api => {
    const canvas = document.createElement('canvas'); canvas.width = 16; canvas.height = 16;
    const gl = canvas.getContext(api);
    if (!gl) return {status: 'unavailable', reason: 'context unavailable'};
    const result = {status: 'observed', ready: glSnapshot(gl)};
    await new Promise(resolve => setTimeout(resolve, 0));
    result.again = glSnapshot(gl);
    const lose = gl.getExtension('WEBGL_lose_context');
    if (!lose) { result.lifecycle = {status: 'unavailable', reason: 'WEBGL_lose_context unavailable'}; return result; }
    try {
      const lost = new Promise(resolve => canvas.addEventListener('webglcontextlost', event => {
        event.preventDefault(); resolve();
      }, {once: true}));
      lose.loseContext(); await bounded(lost, 8000, api + ' loss');
      result.lost = {isContextLost: gl.isContextLost(), vendor: gl.getParameter(gl.VENDOR),
        renderer: gl.getParameter(gl.RENDERER), unmaskedVendor: gl.getParameter(0x9245),
        unmaskedRenderer: gl.getParameter(0x9246)};
      const restored = new Promise(resolve => canvas.addEventListener('webglcontextrestored', resolve, {once: true}));
      lose.restoreContext(); await bounded(restored, 8000, api + ' restore');
      result.restored = glSnapshot(gl);
      result.lifecycle = {status: 'observed', isContextLost: gl.isContextLost()};
    } catch (error) { result.lifecycle = {status: 'failed', error: String(error)}; }
    return result;
  };
  const webgpu = async () => {
    if (!navigator.gpu) return {status: 'unavailable', reason: 'navigator.gpu unavailable'};
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return {status: 'unavailable', reason: 'requestAdapter returned null'};
    const info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
    if (!info) return {status: 'unavailable', reason: 'adapter info unavailable'};
    const names = object => {
      const keys = new Set();
      for (let p = object; p && p !== Object.prototype; p = Object.getPrototypeOf(p))
        for (const key of Object.getOwnPropertyNames(p)) if (key !== 'constructor') keys.add(key);
      return [...keys].sort();
    };
    const values = object => Object.fromEntries(names(object).flatMap(key => {
      const value = object[key];
      return ['number', 'string', 'boolean'].includes(typeof value) ? [[key, value]] : [];
    }));
    return {status: 'observed', identity: values(info), limits: values(adapter.limits),
      features: [...adapter.features].sort(), isFallbackAdapter: adapter.isFallbackAdapter ?? null};
  };
  const run = async () => {
    const result = {secureContext: isSecureContext, webgl: {}};
    for (const api of ['webgl', 'webgl2']) {
      try { result.webgl[api] = await webgl(api); }
      catch (error) { result.webgl[api] = {status: 'failed', error: String(error)}; }
    }
    try { result.webgpu = await bounded(webgpu(), 8000, 'WebGPU'); }
    catch (error) { result.webgpu = {status: 'failed', error: String(error)}; }
    return result;
  };
  return await bounded(run(), 45000, 'GPU identity probe');
}'''

SOFTWARE_MARKERS = ('swiftshader', 'llvmpipe', 'softpipe', 'lavapipe',
                    'software rasterizer', 'software renderer', 'basic render driver')
MODES = ('default', 'default-identity', 'native-identity', 'compatibility', 'compatibility-identity')


def launch_cases(vendor, renderer):
    for name, value in (('vendor', vendor), ('renderer', renderer)):
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f'{name} must be a nonempty string without control characters')
    identity = ['--fingerprint-gpu-vendor=' + vendor, '--fingerprint-gpu-renderer=' + renderer]
    base = ['--disable-background-networking', '--no-first-run']
    return [(mode, base + backend + names) for mode, backend, names in (
        ('default', [], []), ('default-identity', [], identity),
        ('native-identity', ['--fingerprint-gpu-backend=native'], identity),
        ('compatibility', ['--fingerprint-gpu-backend=compatibility'], []),
        ('compatibility-identity', ['--fingerprint-gpu-backend=compatibility'], identity))]


def compare_launches(launches, vendor, renderer):
    checks = []

    def check(name, status, **evidence):
        checks.append({'check': name, 'status': status, **evidence})

    def equal(name, before, after):
        check(name, 'passed' if before == after else 'failed', before=before, after=after)

    by_mode = {}
    for launch in launches:
        mode = launch.get('mode')
        if mode not in MODES or mode in by_mode:
            check('launch-matrix', 'failed', reason='unknown or duplicate mode')
            continue
        by_mode[mode] = launch
        if launch.get('status') != 'observed':
            check(mode + '/launch', 'failed', reason=launch.get('error', 'launch not observed'))
    for mode in MODES:
        if mode not in by_mode:
            check(mode + '/launch', 'failed', reason='missing launch')

    def row(mode, api):
        probe = by_mode.get(mode, {}).get('probe', {})
        return probe.get('webgl', {}).get(api, {}) if api != 'webgpu' else probe.get('webgpu', {})

    for mode in MODES:
        probe = by_mode.get(mode, {}).get('probe', {})
        if by_mode.get(mode, {}).get('status') == 'observed':
            equal(mode + '/secure-context', True, probe.get('secureContext'))
        for api in ('webgl', 'webgl2'):
            item = row(mode, api)
            label = mode + '/' + api
            if item.get('status') != 'observed':
                check(label, 'incomplete' if item.get('status') == 'unavailable' else 'failed', evidence=item)
                continue
            ready = item.get('ready')
            if (not isinstance(ready, dict) or not all(
                    isinstance(ready.get(key), dict) and bool(ready[key])
                    for key in ('identity', 'limits', 'precision', 'attributes'))
                    or not isinstance(ready.get('extensions'), list)
                    or not {'vendor', 'renderer', 'version', 'shadingLanguageVersion',
                            'debugExtension', 'unmaskedVendor', 'unmaskedRenderer'} <= ready['identity'].keys()):
                check(label + '/snapshot', 'failed', reason='missing snapshot evidence')
                continue
            equal(label + '/ready-error', 0, ready.get('error'))
            equal(label + '/repeat', ready, item.get('again'))
            life = item.get('lifecycle', {})
            if life.get('status') == 'observed':
                equal(label + '/lost', {'isContextLost': True, 'vendor': None, 'renderer': None,
                      'unmaskedVendor': None, 'unmaskedRenderer': None}, item.get('lost'))
                equal(label + '/restored-live', False, life.get('isContextLost'))
                equal(label + '/restore', ready, item.get('restored'))
            else:
                check(label + '/restore', 'incomplete' if life.get('status') == 'unavailable' else 'failed', evidence=life)

    for api in ('webgl', 'webgl2'):
        baseline = row('default', api).get('ready')
        if not isinstance(baseline, dict):
            check(api + '/comparison', 'incomplete', reason='no default context'); continue
        for mode in MODES[1:]:
            observed = row(mode, api).get('ready')
            if not isinstance(observed, dict):
                check(mode + '/' + api + '/comparison', 'incomplete', reason='no context'); continue
            label = mode + '/' + api
            for field in ('limits', 'extensions', 'precision', 'attributes'):
                equal(label + '/' + field, baseline.get(field), observed.get(field))
            native, actual = baseline.get('identity', {}), observed.get('identity', {})
            if mode != 'compatibility-identity':
                equal(label + '/native-identity', native, actual)
                continue
            for field in ('vendor', 'renderer', 'version', 'shadingLanguageVersion', 'debugExtension'):
                equal(label + '/' + field, native.get(field), actual.get(field))
            expected = [vendor, renderer]
            before = [native.get('unmaskedVendor'), native.get('unmaskedRenderer')]
            after = [actual.get('unmaskedVendor'), actual.get('unmaskedRenderer')]
            software_hint = any(s in str(before[1]).lower() for s in SOFTWARE_MARKERS)
            if not native.get('debugExtension') or not actual.get('debugExtension'):
                check(label + '/custom-identity', 'incomplete', reason='debug renderer extension unavailable')
            elif software_hint:
                equal(label + '/software-guard', before, after)
                check(label + '/custom-identity', 'incomplete', reason='native renderer indicates software; identity must remain native')
            elif before == expected:
                check(label + '/custom-identity', 'incomplete', reason='requested identity equals baseline; opt-in cannot be distinguished')
            elif after == expected:
                check(label + '/custom-identity', 'passed', requested=expected, observed=after)
            elif after == before:
                check(label + '/custom-identity', 'incomplete', requested=expected, observed=after,
                      reason='override not observed: inspect build and per-context guards; CDP is not context attestation')
            else:
                check(label + '/custom-identity', 'failed', requested=expected, observed=after)

    baseline = row('default', 'webgpu')
    for mode in MODES:
        item = row(mode, 'webgpu')
        if item.get('status') != 'observed':
            check(mode + '/webgpu', 'incomplete' if item.get('status') == 'unavailable' else 'failed', evidence=item)
        elif not item.get('identity') or not item.get('limits') or not isinstance(item.get('features'), list):
            check(mode + '/webgpu', 'failed', reason='missing adapter evidence')
        elif baseline.get('status') != 'observed':
            check(mode + '/webgpu/comparison', 'incomplete', reason='no default adapter')
        elif mode != 'default':
            for field in ('identity', 'limits', 'features', 'isFallbackAdapter'):
                equal(mode + '/webgpu/' + field, baseline.get(field), item.get(field))
    status = ('failed' if any(c['status'] == 'failed' for c in checks) else
              'incomplete' if any(c['status'] == 'incomplete' for c in checks) else 'passed')
    return {'status': status, 'checks': checks}


@contextmanager
def local_page():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<!doctype html><meta charset="utf-8"><title>Local GPU identity diagnostic</title>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def collect(browser_path, vendor, renderer, headed=False):
    from playwright.sync_api import sync_playwright

    digest = hashlib.sha256()
    with browser_path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    report = {'schema_version': 1, 'qualification': 'local diagnostic, not hardware or build certification',
              'browser': str(browser_path), 'browser_sha256': digest.hexdigest(),
              'probe_sha256': hashlib.sha256(PROBE.encode()).hexdigest(), 'headless': not headed,
              'requested': {'vendor': vendor, 'renderer': renderer}, 'launches': []}
    with local_page() as url, sync_playwright() as playwright:
        for mode, args in launch_cases(vendor, renderer):
            launch = {'mode': mode, 'args': args, 'status': 'failed'}
            browser = None
            try:
                browser = playwright.chromium.launch(executable_path=str(browser_path), headless=not headed,
                                                     args=args, timeout=30000)
                launch['version'] = browser.version
                try:
                    session = browser.new_browser_cdp_session()
                    try:
                        launch['cdp_gpu'] = session.send('SystemInfo.getInfo').get('gpu')
                        launch['command_line'] = session.send('Browser.getBrowserCommandLine').get('arguments')
                    finally:
                        session.detach()
                except Exception as error:
                    launch['cdp_error'] = f'{type(error).__name__}: {error}'
                context = browser.new_context()
                context.route('**/*', lambda route: route.continue_() if route.request.url == url else route.abort())
                page = context.new_page()
                page.goto(url, timeout=10000)
                launch['probe'] = page.evaluate(PROBE)
                launch['status'] = 'observed'
            except Exception as error:
                launch['error'] = f'{type(error).__name__}: {error}'
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception as error:
                        launch['status'] = 'failed'
                        launch['error'] = f'close: {type(error).__name__}: {error}'
            report['launches'].append(launch)
    report.update(compare_launches(report['launches'], vendor, renderer))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', required=True, type=Path, help='existing matching browser executable; never downloaded')
    parser.add_argument('--vendor', required=True, help='explicit presentation string, not a hardware claim')
    parser.add_argument('--renderer', required=True, help='explicit presentation string, preferably distinct from native')
    parser.add_argument('--output', required=True, type=Path, help='new JSON evidence path')
    parser.add_argument('--headed', action='store_true', help='use a visible browser; requires a graphical session')
    args = parser.parse_args(argv)
    if not args.browser.is_file():
        parser.error('--browser must name an existing executable')
    if args.output.exists():
        parser.error('--output must be new; preserve earlier evidence')
    try:
        launch_cases(args.vendor, args.renderer)
    except ValueError as error:
        parser.error(str(error))
    try:
        report = collect(args.browser.resolve(), args.vendor, args.renderer, args.headed)
    except ImportError as error:
        parser.error(f'Playwright is required (python -m pip install playwright): {error}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'output': str(args.output),
                      'checks': {status: sum(c['status'] == status for c in report['checks'])
                                 for status in ('passed', 'incomplete', 'failed')}}))
    return {'passed': 0, 'failed': 1, 'incomplete': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
