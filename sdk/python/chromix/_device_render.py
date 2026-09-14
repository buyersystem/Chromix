"""Recheck compressed render evidence, then bind it to the whole measured host.

Hashes prove byte integrity, not physical hardware, glyph provenance or reviewers.
No hash is required to be unique across profiles or devices. Backend identities
belong to each context/API; hybrid GPUs need not report one universal adapter.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import re
import zlib

from . import _canvas_chain as chain
from ._render_integration import assess as assess_integration
from . import _gpu_backend as gpu_backend

SCOPES = chain.SCOPES
MAX_BYTES = 8 * 1024 * 1024
SHA = re.compile(r'[0-9a-f]{64}')
SCENES = ('paths', 'gradient', 'composite', 'latin', 'cjk', 'emoji')
PRECISIONS = ('LOW_FLOAT', 'MEDIUM_FLOAT', 'HIGH_FLOAT', 'LOW_INT', 'MEDIUM_INT', 'HIGH_INT')
GL_LIMITS = ('MAX_TEXTURE_SIZE', 'MAX_CUBE_MAP_TEXTURE_SIZE', 'MAX_RENDERBUFFER_SIZE',
             'MAX_VERTEX_ATTRIBS', 'MAX_VERTEX_TEXTURE_IMAGE_UNITS', 'MAX_TEXTURE_IMAGE_UNITS',
             'MAX_COMBINED_TEXTURE_IMAGE_UNITS', 'MAX_VERTEX_UNIFORM_VECTORS',
             'MAX_FRAGMENT_UNIFORM_VECTORS', 'MAX_VARYING_VECTORS', 'SUBPIXEL_BITS',
             'ALIASED_POINT_SIZE_RANGE', 'ALIASED_LINE_WIDTH_RANGE', 'MAX_VIEWPORT_DIMS')
GL2_LIMITS = ('MAX_SAMPLES', 'MAX_3D_TEXTURE_SIZE', 'MAX_ARRAY_TEXTURE_LAYERS',
              'MAX_DRAW_BUFFERS', 'MAX_COLOR_ATTACHMENTS', 'MAX_UNIFORM_BUFFER_BINDINGS',
              'MAX_UNIFORM_BLOCK_SIZE', 'MAX_VERTEX_UNIFORM_COMPONENTS', 'MAX_FRAGMENT_UNIFORM_COMPONENTS')


def unpack(item):
    if not isinstance(item, dict) or item.get('status') != 'observed':
        raise ValueError('render probe failed or missing: ' + str(item))
    value = item.get('value')
    if (not isinstance(value, dict) or set(value) != {'encoding', 'bytes', 'sha256', 'data'} or
            value['encoding'] != 'gzip-json-v1' or type(value['bytes']) is not int or
            not 0 < value['bytes'] <= MAX_BYTES or not isinstance(value['sha256'], str) or
            not SHA.fullmatch(value['sha256']) or not isinstance(value['data'], str) or
            not 0 < len(value['data']) <= 2 * MAX_BYTES):
        raise ValueError('invalid bounded render envelope')
    try:
        compressed = base64.b64decode(value['data'], validate=True)
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(compressed, MAX_BYTES + 1)
        if (len(raw) != value['bytes'] or not decoder.eof or decoder.unused_data or
                decoder.unconsumed_tail or hashlib.sha256(raw).hexdigest() != value['sha256']):
            raise ValueError('render length/hash/stream mismatch')
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise ValueError('duplicate render JSON key')
                result[key] = item
            return result
        def invalid(_):
            raise ValueError('nonfinite render JSON number')
        def finite_float(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                invalid(value)
            return parsed
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=invalid, parse_float=finite_float)
    except (zlib.error, UnicodeError, RecursionError) as error:
        raise ValueError('malformed render payload') from error


def pixels(value, width, height):
    return (isinstance(value, list) and len(value) == width * height * 4 and
            all(type(v) is int and 0 <= v <= 255 for v in value))


def close_pixels(actual, expected, width, height):
    """Visible premultiplied difference, not undefined RGB beneath zero alpha."""
    if not pixels(actual, width, height) or not pixels(expected, width, height):
        return False
    differences = []
    for i in range(0, len(actual), 4):
        if abs(actual[i + 3] - expected[i + 3]) > 1:
            return False
        differences.extend(abs(actual[i+k] * actual[i+3] / 255 -
                               expected[i+k] * expected[i+3] / 255) for k in range(3))
    return max(differences) <= 2 and sum(differences) / len(differences) <= 0.6


def input_pattern():
    return [v for y in range(16) for x in range(16) for v in (x*17, y*17, (x ^ y)*17, 255)]


def flip_rows(value):
    return [v for y in range(15, -1, -1) for v in value[y*64:(y+1)*64]]


def _observed(item, label, *, optional=False):
    if not isinstance(item, dict):
        raise ValueError(label + ': missing result')
    if optional and item.get('status') == 'unavailable' and isinstance(item.get('reason'), str) and item['reason']:
        return None
    if item.get('status') != 'observed' or not isinstance(item.get('value'), dict):
        raise ValueError(label + ': ' + str(item.get('reason', 'missing evidence')))
    return item['value']


def scene_errors(value):
    errors = []
    try:
        if not isinstance(value, dict) or value.get('version') != 1 or (value.get('width'), value.get('height')) != (64,48):
            raise ValueError('missing scene matrix/version')
        source = input_pattern()
        if value.get('source') != source:
            raise ValueError('cross-path source pattern mismatch')
        canvas = _observed(value.get('canvas'), 'Canvas')
        if canvas.get('glyphFileBinding') != 'not_verified':
            raise ValueError('Canvas cannot certify glyph source files')
        rows = canvas.get('rows')
        if not isinstance(rows, list) or [r.get('id') for r in rows] != list(SCENES):
            raise ValueError('incomplete rich Canvas scene matrix')
        for row in rows:
            ref = row.get('pixels')
            if (not pixels(ref, 64, 48) or len({tuple(ref[i:i+4]) for i in range(0, len(ref), 4)}) < 2 or
                    row.get('repeat') != ref):
                errors.append(row['id'] + ': empty/unstable raster evidence')
                continue
            for field in ('bitmap', 'png'):
                if not close_pixels(row.get(field), ref, 64, 48):
                    errors.append(row['id'] + ': ' + field + ' raster mismatch')
            metrics = row.get('metrics')
            if row['id'] in ('latin', 'cjk', 'emoji'):
                if (not isinstance(metrics, dict) or not metrics.get('text') or not metrics.get('font') or
                        any(type(metrics.get(k)) not in (int,float) or not math.isfinite(metrics[k])
                            for k in ('width','left','right','ascent','descent')) or metrics['width'] <= 0):
                    errors.append(row['id'] + ': missing glyph raster metrics')
            elif metrics is not None:
                errors.append(row['id'] + ': unexpected text metrics')
        gpu = value.get('webgl')
        if not isinstance(gpu, dict) or set(gpu) != {'webgl','webgl2'}:
            raise ValueError('both WebGL APIs must be probed')
        for api, item in gpu.items():
            gl = _observed(item, api, optional=True)
            if gl is None:
                continue
            identity = gl.get('identity', {})
            if (not identity.get('version') or not identity.get('shadingLanguage') or
                    any(identity.get(k) is not None and not isinstance(identity[k], str) for k in ('vendor','renderer'))):
                errors.append(api + ': missing context identity')
            attrs = gl.get('attributes', {})
            if (attrs.get('alpha') is not False or attrs.get('antialias') is not False or
                    attrs.get('preserveDrawingBuffer') is not True):
                errors.append(api + ': context attributes differ from request')
            limits = gl.get('limits', {})
            expected_limits = set(GL_LIMITS + (GL2_LIMITS if api == 'webgl2' else ()))
            if set(limits) != expected_limits:
                errors.append(api + ': incomplete native limits')
            for name, limit in limits.items():
                array = name in ('ALIASED_POINT_SIZE_RANGE','ALIASED_LINE_WIDTH_RANGE','MAX_VIEWPORT_DIMS')
                values = limit if array and isinstance(limit, list) and len(limit) == 2 else [limit]
                if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in values):
                    errors.append(api + ': invalid limit ' + name)
            extensions = gl.get('extensions')
            if not isinstance(extensions, list) or any(not isinstance(x, str) for x in extensions) or extensions != sorted(set(extensions)):
                errors.append(api + ': invalid extension inventory')
            precision = gl.get('precision', {})
            if set(precision) != {'VERTEX_SHADER','FRAGMENT_SHADER'}:
                raise ValueError(api + ': missing precision stages')
            for stage in precision.values():
                if set(stage) != set(PRECISIONS):
                    raise ValueError(api + ': incomplete precision matrix')
                for limits_ in stage.values():
                    if set(limits_) != {'rangeMin','rangeMax','precision'} or any(type(v) is not int or v < 0 for v in limits_.values()):
                        raise ValueError(api + ': invalid native precision')
            shaders = gl.get('shaders', [])
            if [r.get('mode') for r in shaders] != ['mediump','highp']:
                raise ValueError(api + ': missing shader matrix')
            for row in shaders:
                advertised = precision['FRAGMENT_SHADER']['HIGH_FLOAT' if row['mode'] == 'highp' else 'MEDIUM_FLOAT']['precision']
                if advertised == 0 and row.get('status') == 'unavailable' and row.get('reason'):
                    continue
                ref = row.get('pixels')
                if (row.get('status') != 'observed' or not pixels(ref,16,16) or row.get('repeat') != ref or
                        len({tuple(ref[i:i+4]) for i in range(0,len(ref),4)}) < 2 or any(a != 255 for a in ref[3::4])):
                    errors.append(api + ': shader execution/readback mismatch')
            formats = gl.get('formats', [])
            if [r.get('format') for r in formats] != ['rgba8','rgb565','rgba4','rgb5a1']:
                raise ValueError(api + ': incomplete texture format matrix')
            if any(row.get('pixels') != [255,0,255,255] * 256 for row in formats):
                errors.append(api + ': packed texture format readback mismatch')
            cross = gl.get('cross', {})
            if (cross.get('origin') != 'bottom-left' or cross.get('pixels') != flip_rows(source) or
                    not close_pixels(cross.get('bitmap'), source, 16,16)):
                errors.append(api + ': Canvas/ImageBitmap/WebGL roundtrip mismatch')
            msaa = gl.get('msaa', {})
            if msaa.get('status') == 'observed':
                supported, count, actual = msaa.get('supported'), msaa.get('samples'), msaa.get('actualSamples')
                if (api != 'webgl2' or not isinstance(supported, list) or
                        any(type(n) is not int or n < 1 for n in supported) or
                        type(count) is not int or not 1 < count <= 4 or count not in supported or
                        actual != count or type(actual) is not int or count > limits.get('MAX_SAMPLES',0) or
                        not close_pixels(msaa.get('pixels'), [32,96,160,255]*256,16,16)):
                    errors.append(api + ': MSAA allocation/resolve mismatch')
            elif msaa.get('status') != 'unavailable' or not msaa.get('reason'):
                errors.append(api + ': missing MSAA probe')
            elif api == 'webgl2' and any(1 < n <= 4 for n in msaa.get('supported', [])):
                errors.append(api + ': available MSAA count was not exercised')
        gpu = _observed(value.get('webgpu'), 'WebGPU', optional=True)
        if gpu is not None and gpu != {'adapter':None}:
            identity = gpu.get('identity', {})
            if (set(identity) != {'vendor','architecture','device','description','isFallbackAdapter'} or
                    any(not isinstance(identity.get(k), str) for k in ('vendor','architecture','device','description')) or
                    identity.get('isFallbackAdapter') not in (True,False,None)):
                errors.append('WebGPU: missing adapter identity')
            rows = gpu.get('rows', [])
            if [(r.get('format'),r.get('samples')) for r in rows] != [(f,n) for f in ('rgba8unorm','bgra8unorm') for n in (1,4)]:
                raise ValueError('WebGPU: incomplete shader/format/MSAA matrix')
            for row in rows:
                expected = source if row['format'] == 'rgba8unorm' else [source[i + (2,1,0,3)[k]] for i in range(0,len(source),4) for k in range(4)]
                if row.get('origin') != 'top-left' or row.get('pixels') != expected:
                    errors.append('WebGPU: external bitmap/shader/format readback mismatch')
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        errors.append(str(error))
    return errors


def assess_observation(observation):
    # Cache only checked summaries, keyed by the complete envelopes, not by a
    # supplied digest alone. Restart/profile rechecks cannot trust forged hashes.
    try:
        envelopes = tuple(json.dumps(observation.get(s, {}).get('render'), sort_keys=True,
                                     separators=(',', ':'), allow_nan=False) for s in SCOPES)
        result = deepcopy(_assess_envelopes(envelopes))
        try:
            gpu_backend.system_projection(observation.get('window', {}).get('gpuSystem'))
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            result['errors'].append('native GPU system inventory: ' + str(error))
        return result
    except (ValueError, TypeError, AttributeError) as error:
        return {'errors':['malformed render observation: ' + str(error)], 'unavailable':[],
                'canvas_skipped':[], 'canvas_comparisons':0, 'codec_quality':[]}


@lru_cache(maxsize=8)
def _assess_envelopes(envelopes):
    try:
        from PIL import features
        if not features.check('littlecms2') or not features.check('webp'):
            raise ValueError('Pillow LittleCMS and WebP decoders are required for measured admission')
    except ImportError as error:
        raise ValueError('install chromix[measured] to independently check render evidence') from error
    decoded, errors, unavailable, backends = {}, [], [], {}
    for scope, envelope in zip(SCOPES, envelopes):
        try:
            raw = unpack(json.loads(envelope))
            if (not isinstance(raw, dict) or set(raw) != {'version','chain','scenes','integration','gpuBackend'} or
                    type(raw['version']) is not int or raw['version'] != 2):
                raise ValueError('missing render families')
            decoded[scope] = raw
            checked_backend = gpu_backend.assess(raw['gpuBackend'], scope)
            errors.extend(scope + ': GPU backend: ' + e for e in checked_backend['errors'])
            unavailable.extend(scope + ': GPU backend: ' + e for e in checked_backend['gaps'])
            backends[scope] = checked_backend
            errors.extend(scope + ': ' + e for e in scene_errors(raw['scenes']))
            if scope in ('window','iframe'):
                failures, missing = assess_integration(raw['integration'])
                errors.extend(scope + ': ' + str(e) for e in failures)
                unavailable.extend(scope + ': ' + str(e) for e in missing)
            elif raw['integration'] != {'status':'not_applicable', 'reason':'DOM ownership tests run in window/iframe'}:
                raise ValueError('worker DOM tests must be explicitly not applicable')
            for api, item in raw['scenes']['webgl'].items():
                if item.get('status') == 'unavailable':
                    unavailable.append(scope + ': ' + api)
            if raw['scenes']['webgpu'].get('status') == 'unavailable' or raw['scenes']['webgpu'].get('value') == {'adapter':None}:
                unavailable.append(scope + ': webgpu')
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            errors.append(scope + ': ' + str(error))
    checked = chain.evaluate({scope:item['chain'] for scope,item in decoded.items()},
                             require_taint=False, check_cross_context=False)
    # JPEG/WebP quality thresholds are diagnostics, not Web-platform contracts.
    # Preserve native lossy/color/coordinate differences instead of demanding
    # universal bytes across codecs or independently selected context backends.
    errors.extend(e for e in checked['errors'] if not e.endswith(': codec-quality mismatch'))
    return {'errors':errors, 'unavailable':sorted(set(unavailable)), 'gpu_backends':backends,
            'canvas_skipped':checked['skipped'], 'canvas_comparisons':len(checked['comparisons']),
            'codec_quality':[c for c in checked['comparisons']
                             if c.get('category') == 'codec-quality' and not c['pass']]}


def build_evidence(host, browser):
    from .device_pool import digest
    from ._device_probe import PROBE_VERSION, probe_hash
    from ._device_fonts import font_errors
    if browser.get('probe_sha256') != probe_hash():
        raise ValueError('render evidence requires the current complete probe bundle; recollect')
    gpu_backend.validate_native_args(browser.get('launch_args'))
    observations = browser.get('observations', [])
    if len(observations) != 3:
        raise ValueError('render evidence requires three launches')
    runs = []
    # Repeated launches are checked, not synthesized from a single observation.
    for index, observation in enumerate(observations):
        if any(type(observation.get(scope, {}).get('probeVersion')) is not int or
               observation[scope]['probeVersion'] != PROBE_VERSION for scope in SCOPES):
            raise ValueError('render evidence requires probe v4 in every context')
        checked = assess_observation(observation)
        checked['errors'].extend(font_errors(observation.get('window', {}).get('fontBackend')))
        if checked['errors']:
            raise ValueError('render run ' + str(index) + ': ' + '; '.join(checked['errors']))
        runs.append({**checked, 'payload_sha256':{s:observation[s]['render']['value']['sha256'] for s in SCOPES},
                     'font_backend_sha256':digest(observation['window']['fontBackend'])})
    return {'schema_version':2, 'probe_sha256':browser['probe_sha256'],
            'browser_sha256':browser['binary']['sha256'],
            'host_sha256':digest(host), 'gpu_inventory_sha256':digest(host.get('gpu')),
            'font_inventory_sha256':digest(host.get('fonts')), 'runs':runs,
            'gpu_backend_policy':'native', 'gpu_backend_matrix_version':1,
            'physical_backend_equivalence':'not_verified', 'font_file_to_glyph_binding':'not_verified',
            'taint':'standalone_audit_only', 'profile_distinct_pixels':'not_required'}
