"""Independent native GPU resource oracle, shared by collection and live admission.

No universal adapter or cross-device pixel identity is assumed. CDP's process
inventory is separate from each API's selection and is not physical attestation.
"""
from __future__ import annotations

import math
import struct

W, H = 3, 2
REQUESTS = ('default', 'low-power', 'high-performance', 'fallback')
FORMATS = ('rgba8unorm', 'bgra8unorm', 'rgba16float', 'rgba32float')
SYSTEM_AUX = ('glVendor', 'glRenderer', 'glVersion', 'glImplementationParts',
              'displayType', 'skiaBackendType', 'passthroughCmdDecoder',
              'inProcessGpu', 'sandboxed', 'optimus', 'amdSwitchable')
SYSTEM_DEVICE = ('vendorId', 'deviceId', 'subSysId', 'revision', 'vendorString',
                 'deviceString', 'driverVendor', 'driverVersion')


def validate_native_args(arguments):
    _require(isinstance(arguments,list) and all(isinstance(a,str) for a in arguments),
             'native GPU launch arguments missing')
    backend = [a for a in arguments if a.split('=',1)[0] == '--uxr-gpu-backend']
    _require(backend == ['--uxr-gpu-backend=native'], 'measured launch requires one native GPU backend policy')
    diagnostic = {'--disable-gpu','--disable-software-rasterizer','--use-gl','--use-angle','--use-vulkan',
                  '--enable-unsafe-webgpu','--enable-webgpu-developer-features','--use-webgpu-adapter',
                  '--enable-dawn-features','--disable-dawn-features','--ignore-gpu-blocklist',
                  '--force-high-performance-gpu','--force-low-power-gpu'}
    _require(not any(a.split('=',1)[0] in diagnostic for a in arguments),
             'diagnostic/forced GPU launch cannot qualify as a native device configuration')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _reason(row):
    return isinstance(row.get('reason'), str) and 0 < len(row['reason']) <= 2048


def _numbers(values, count, *, integral=False):
    try:
        return (isinstance(values, list) and len(values) == count and
                all(type(v) in ((int,) if integral else (int, float)) and math.isfinite(v)
                    and (0 <= v <= 255 if integral else True) for v in values))
    except OverflowError:
        # JSON integers can be much larger than a C++/GPU numeric component.
        return False


def _integers_equal(values, expected):
    return (isinstance(values,list) and all(type(v) is int for v in values) and
            values == expected)


def _pixels(actual, color, count=W*H, *, floating=False):
    if not _numbers(actual, count*4, integral=not floating):
        return False
    # The fixture uses exactly representable constant colors, not scene hashes.
    tolerance = 1/255 + 0.0001 if floating else 1
    return all(abs(v-color[i % 4]) <= tolerance for i, v in enumerate(actual))


def _canvas_pixels(actual, color, count=W*H, *, floating=False):
    # Same visible-premultiplied lossless/alpha limits as the existing Canvas
    # chain. P3 CPU rasterization may round unpremultiplied red 255 to 253 at
    # half alpha; that is one visible unit, not a changed input or alpha policy.
    if not _numbers(actual,count*4,integral=not floating): return False
    if floating and any(abs(v) > 65504 for v in actual): return False
    scale = 255 if floating else 1
    differences = []
    for i in range(0,len(actual),4):
        alpha, expected_alpha = actual[i+3]*scale, color[3]*scale
        if abs(alpha-expected_alpha) > 1: return False
        for k in range(3):
            differences.append(abs(actual[i+k]*scale*alpha/255 - color[k]*scale*expected_alpha/255))
    return max(differences) <= 2 and sum(differences)/len(differences) <= 0.6


def _identity(value, api):
    _require(isinstance(value, dict), api + ': missing native identity')
    if api == 'webgpu':
        _require(set(value) == {'vendor','architecture','device','description','isFallbackAdapter'} and
                 all(isinstance(value[k], str) for k in ('vendor','architecture','device','description')) and
                 (value['isFallbackAdapter'] is None or type(value['isFallbackAdapter']) is bool),
                 'invalid native WebGPU identity')
    else:
        _require(set(value) == {'vendor','renderer','version','shadingLanguage'} and
                 all(value[k] is None or isinstance(value[k], str) for k in ('vendor','renderer')) and
                 all(isinstance(value[k], str) and value[k] for k in ('version','shadingLanguage')),
                 'invalid native WebGL identity')


def _bytes_layout(values, size, offset, stride, color):
    if not _numbers(values, size, integral=True):
        return False
    touched = set()
    for y in range(H):
        start = offset+y*stride
        if not _pixels(values[start:start+W*4], color, W):
            return False
        touched.update(range(start, start+W*4))
    return all(v == 165 for i, v in enumerate(values) if i not in touched)


def _gpu_readback(row, expected):
    _require(isinstance(row, dict) and row.get('format') in FORMATS and
             type(row.get('width')) is int and row['width'] == W and
             type(row.get('height')) is int and row['height'] == H and
             type(row.get('mappedBytesAfterUnmap')) is int and row['mappedBytesAfterUnmap'] == 0,
             'GPU texture geometry/format/mapping metadata mismatch')
    raw = row.get('bytes')
    _require(_numbers(raw, 1024, integral=True), 'GPU readback requires full bounded buffer bytes')
    fmt = row['format']
    component = 2 if fmt == 'rgba16float' else 4 if fmt == 'rgba32float' else 1
    touched = set()
    for y in range(H):
        start, length = 256+y*256, W*4*component
        touched.update(range(start, start+length))
        data = raw[start:start+length]
        if component != 1:
            data = list(struct.unpack('<'+('e' if component == 2 else 'f')*W*4, bytes(data)))
        elif fmt == 'bgra8unorm':
            data = [data[i+k] for i in range(0,len(data),4) for k in (2,1,0,3)]
        color = expected if component != 1 else [round(v*255) for v in expected]
        _require(_pixels(data, color, W, floating=component != 1), 'GPU texture values differ from independent color oracle')
    _require(all(v == 165 for i,v in enumerate(raw) if i not in touched),
             'GPU readback changed offset/row/suffix padding')


def canvas_requests(scope):
    return [dict(kind=kind,colorSpace=color,colorType=fmt,alpha=alpha,willReadFrequently=frequent)
            for kind in (('html','offscreen') if scope in ('window','iframe') else ('offscreen',))
            for color in ('srgb','display-p3') for fmt in ('unorm8','float16')
            for alpha in (True,False) for frequent in (False,True)]


def _canvas(rows, scope, gaps):
    _require(isinstance(rows, list) and [r.get('request') for r in rows] == canvas_requests(scope),
             'incomplete Canvas backend request matrix')
    for row in rows:
        request = row['request']
        _require(type(request['alpha']) is bool and type(request['willReadFrequently']) is bool,
                 'Canvas request flags must be boolean')
        _require(row.get('status') == 'observed', 'Canvas operation failed: '+str(row.get('reason')))
        value = row.get('value', {})
        attrs = value.get('attributes', {})
        _require(attrs.get('alpha') is request['alpha'] and
                 attrs.get('willReadFrequently') is request['willReadFrequently'], 'Canvas attributes mismatch')
        if value.get('supported') is False:
            _require(_reason(value) and (request['colorSpace'] != 'srgb' or request['colorType'] != 'unorm8') and
                     (attrs.get('colorSpace') != request['colorSpace'] or attrs.get('colorType') != request['colorType']),
                     'available or mandatory Canvas format was not exercised')
            gaps.append('Canvas: '+str(request))
            continue
        _require(value.get('supported') is True and all(attrs.get(k) == request[k] for k in ('colorSpace','colorType')),
                 'Canvas color-space/type request mismatch')
        floating = request['colorType'] == 'float16'
        expected = ([1,0,0,0.5] if request['alpha'] else [0.5,0,0,1]) if floating else (
            [255,0,0,128] if request['alpha'] else [128,0,0,255])
        before, after, resized = value.get('before',{}), value.get('after'), value.get('resized',{})
        for data, width in ((before,W),(resized,1)):
            _require(type(data.get('width')) is int and data['width'] == width and
                     type(data.get('height')) is int and data['height'] == H and
                     data.get('colorSpace') == request['colorSpace'] and
                     data.get('pixelFormat') == ('rgba-float16' if floating else 'rgba-unorm8') and
                     data.get('type') == ('Float16Array' if floating else 'Uint8ClampedArray'),
                     'Canvas readback storage metadata mismatch')
        _require(before == after and _canvas_pixels(before.get('pixels'),expected,floating=floating) and
                 _canvas_pixels(value.get('bitmapPixels'),expected,floating=floating),
                 'Canvas render/bitmap/source-stability mismatch')
        black = [0,0,0,0 if request['alpha'] else 1 if floating else 255]
        # Reset pixels have an exact zero/one representation in both formats.
        # Visible-premultiplied tolerance must not hide nonzero RGB at alpha 0.
        _require(_numbers(resized.get('pixels'),H*4,integral=not floating) and
                 resized['pixels'] == black*H and _integers_equal(value.get('bitmapClosed'),[0,0]),
                 'Canvas resize/bitmap ownership mismatch')


def _webgl(item, api, gaps):
    if item.get('status') == 'unavailable':
        _require(_reason(item), 'WebGL unavailable without reason')
        gaps.append(api); return None
    _require(item.get('status') == 'observed', api+': '+str(item.get('reason','missing observation')))
    value = item['value']; _identity(value.get('beforeIdentity'),api)
    extensions = value.get('extensions')
    _require(isinstance(extensions, list) and all(isinstance(e,str) for e in extensions) and
             extensions == sorted(set(extensions)), 'missing native extension inventory')
    rows = value.get('formats')
    _require(isinstance(rows, list) and [r.get('format') for r in rows] ==
             (['rgba8','rgba16f','rgba32f'] if api == 'webgl2' else ['rgba8']), 'incomplete WebGL format matrix')
    for row in rows:
        floating = row['format'] != 'rgba8'
        if row.get('status') == 'unavailable':
            _require(floating and 'EXT_color_buffer_float' not in extensions and _reason(row),
                     'advertised WebGL format was not exercised')
            gaps.append(api+':'+row['format']); continue
        _require(row.get('status') == 'observed' and type(row.get('framebufferStatus')) is int and
                 row['framebufferStatus'] == 36053 and
                 type(row.get('error')) is int and row['error'] == 0 and
                 _pixels(row.get('pixels'),[0.25,0.5,0.75,1] if floating else [64,128,191,255],floating=floating),
                 'WebGL framebuffer allocation/readback failed')
    colors = value.get('colorSpaces')
    _require(isinstance(colors,list) and [r.get('colorSpace') for r in colors] == ['srgb','display-p3'],
             'incomplete WebGL color-space matrix')
    for row in colors:
        if row.get('status') == 'unavailable':
            _require(_reason(row), 'WebGL color-space gap without reason'); gaps.append(api+':'+row['colorSpace'])
        else:
            _require(row.get('status') == 'observed' and row.get('actual') == row['colorSpace'] and
                     _pixels(row.get('pixels'),[64,128,191,255]), 'WebGL drawing-buffer color-space mismatch')
    layout = value.get('layout', {}); client = layout.get('client',{})
    _require(type(client.get('error')) is int and client['error'] == 0 and
             _bytes_layout(client.get('bytes'),48,8,16,[64,128,191,255]), 'WebGL client pack stride/offset/padding mismatch')
    pbo = layout.get('pbo')
    _require((api == 'webgl' and pbo is None) or (api == 'webgl2' and isinstance(pbo,dict) and
             type(pbo.get('error')) is int and pbo['error'] == 0 and
             _bytes_layout(pbo.get('bytes'),128,44,32,[64,128,191,255])), 'WebGL PBO pack/skip layout mismatch')
    _require(_pixels(value.get('before'),[64,128,191,255]), 'WebGL pre-loss pixels missing')
    life = value.get('lifecycle', {})
    if life.get('status') == 'unavailable':
        _require(_reason(life) and 'WEBGL_lose_context' not in extensions, 'available WebGL lifecycle not exercised')
        gaps.append(api+':lifecycle')
    else:
        _require(life.get('status') == 'observed' and life.get('event') == 'webglcontextlost' and
                 life.get('restored') == 'webglcontextrestored' and life.get('isLost') is True and
                 life.get('isLostAfter') is False and type(life.get('lostError')) is int and life['lostError'] == 37442 and
                 life.get('oldTextureBefore') is True and life.get('oldTextureAfter') is False and
                 _numbers(life.get('lostPixels'),W*H*4,integral=True) and life['lostPixels'] == [165]*(W*H*4) and
                 _pixels(life.get('after'),[64,128,191,255]), 'WebGL loss/restoration/resource-generation mismatch')
        _identity(life.get('afterIdentity'),api)
    return value['beforeIdentity']


def _webgpu(rows, gaps):
    _require(isinstance(rows, list) and [r.get('request') for r in rows] == list(REQUESTS),
             'incomplete native WebGPU adapter-request matrix')
    identities = {}
    for row in rows:
        request = row['request']
        if row.get('status') == 'unavailable':
            _require(_reason(row), 'WebGPU unavailable without reason'); gaps.append('webgpu:'+request); continue
        _require(row.get('status') == 'observed', 'WebGPU operation failed: '+str(row.get('reason')))
        value = row.get('value',{})
        if value.get('available') is False:
            _require(_reason(value), 'WebGPU null adapter without reason'); gaps.append('webgpu:'+request); continue
        _require(value.get('available') is True, 'missing WebGPU availability')
        _identity(value.get('identity'),'webgpu'); identities[request] = value['identity']
        fallback = value['identity']['isFallbackAdapter']
        if request == 'fallback':
            _require(fallback is not False, 'forceFallbackAdapter returned a non-fallback adapter')
            if fallback is None: gaps.append('webgpu:fallback-classification-unavailable')
        features = value.get('features')
        _require(isinstance(features,list) and all(isinstance(f,str) for f in features) and
                 features == sorted(set(features)), 'invalid WebGPU feature inventory')
        formats = value.get('formats')
        expected = [(f,n) for f in FORMATS for n in ((1,) if f == 'rgba32float' else (1,4))] if request == 'default' else [('rgba8unorm',1)]
        _require(isinstance(formats,list) and [(r.get('format'),r.get('samples')) for r in formats] == expected,
                 'incomplete WebGPU render/format/MSAA matrix')
        for sample in formats:
            _require(type(sample.get('samples')) is int, 'invalid WebGPU sample count')
            _gpu_readback(sample,[0.25,0.5,0.75,1])
        external, canvases = value.get('external'), value.get('canvases')
        if request != 'default':
            _require(external == [] and canvases == [], 'unexpected secondary-adapter detailed matrix')
        else:
            _require(isinstance(external,list) and [(r.get('colorSpace'),r.get('premultipliedAlpha')) for r in external] ==
                     [(c,a) for c in ('srgb','display-p3') for a in (False,True)], 'incomplete WebGPU external-image matrix')
            for image in external:
                _require(type(image['premultipliedAlpha']) is bool, 'invalid alpha request')
                if image.get('status') == 'unavailable':
                    _require(image['colorSpace'] == 'display-p3' and _reason(image), 'mandatory external-image source unavailable')
                    gaps.append('webgpu:external-p3'); continue
                _require(image.get('status') == 'observed' and _pixels(image.get('before'),[255,0,0,128]) and
                         image.get('before') == image.get('after') and _integers_equal(image.get('bitmapClosed'),[0,0]),
                         'WebGPU external-image source/ownership changed')
                _gpu_readback(image.get('readback'),[128/255 if image['premultipliedAlpha'] else 1,0,0,128/255])
            _require(isinstance(canvases,list) and [(r.get('colorSpace'),r.get('alphaMode')) for r in canvases] ==
                     [(c,a) for c in ('srgb','display-p3') for a in ('opaque','premultiplied')], 'incomplete WebGPU canvas configuration matrix')
            for canvas in canvases:
                _require(canvas.get('format') in ('rgba8unorm','bgra8unorm') and
                         canvas.get('readback',{}).get('format') == canvas['format'] and
                         _integers_equal(canvas.get('resized'),[1,H]) and
                         canvas.get('unconfiguredError') == 'InvalidStateError', 'WebGPU resize/unconfigure contract failed')
                _gpu_readback(canvas['readback'],[0.25,0.5,0.75,1])
        _require(value.get('uncaptured') == [], 'uncaptured WebGPU errors')
        life = value.get('lifecycle',{})
        # A destroyed device aborts mapping; this is not a validation error.
        # Blink GPUBuffer::OnMapAsyncCallback distinguishes Aborted -> AbortError
        # from Error -> OperationError. Do not accept an arbitrary rejection.
        _require(life.get('reason') == 'destroyed' and life.get('staleMapError') == 'AbortError' and
                 _integers_equal(life.get('recreated'),[17,34,51,68]) and type(life.get('recreatedMapBytesAfterUnmap')) is int and
                 life['recreatedMapBytesAfterUnmap'] == 0, 'WebGPU destroyed-device/mapping/recreation contract failed')
        _identity(life.get('afterIdentity'),'webgpu')
    return identities


def assess(value, scope):
    """Recompute every family independently; a forged success summary is ignored."""
    errors, gaps, identities = [], [], {}
    try:
        _require(scope in ('window','iframe','worker','shared_worker','service_worker') and
                 isinstance(value,dict) and set(value) == {'version','width','height','canvas','webgl','webgpu'} and
                 type(value['version']) is int and value['version'] == 1 and
                 _integers_equal([value['width'],value['height']],[W,H]),
                 'missing/versioned GPU backend matrix')
    except (ValueError, TypeError) as error:
        return {'errors':[str(error)], 'gaps':[], 'identities':{}}
    families = [('canvas',lambda:_canvas(value['canvas'],scope,gaps)),
                ('webgl',lambda:_check_gl_map(value['webgl'],gaps)),
                ('webgpu',lambda:_webgpu(value['webgpu'],gaps))]
    for name, check in families:
        try:
            result = check()
            if result is not None: identities[name] = result
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, struct.error) as error:
            errors.append(name+': '+str(error))
    return {'errors':errors, 'gaps':sorted(set(gaps)), 'identities':identities}


def _check_gl_map(value, gaps):
    _require(isinstance(value,dict) and set(value) == {'webgl','webgl2'}, 'both WebGL context APIs must be observed')
    return {api:_webgl(value[api],api,gaps) for api in ('webgl','webgl2')}


def system_projection(item):
    """Drop volatile process counters, not backend/driver/capability differences."""
    _require(isinstance(item,dict) and item.get('status') == 'observed', 'CDP GPU inventory missing or failed')
    value = item.get('value',{})
    _require(isinstance(value,dict), 'CDP GPU inventory value must be an object')
    # Stable snapshots are projected more than once during exact-host matching.
    # Validate the projected representation through the same raw-data path.
    if value.get('adapter_binding') == 'not_verified' and set(value) == {
            'source','devices','featureStatus','auxAttributes','adapter_binding'}:
        value = {'source':value['source'],'gpu':{k:value[k] for k in ('devices','featureStatus','auxAttributes')}}
    _require(value.get('source') == 'CDP.SystemInfo.getInfo' and isinstance(value.get('gpu'),dict),
             'CDP GPU inventory source missing')
    gpu = value['gpu']; devices = gpu.get('devices')
    _require(isinstance(devices,list) and 0 < len(devices) <= 32, 'CDP native GPU device inventory missing or oversized')
    for device in devices:
        _require(isinstance(device,dict) and all(type(device.get(k)) is int and 0 <= device[k] <= 0xffffffff
                 for k in ('vendorId','deviceId')) and
                 all(isinstance(device.get(k),str) for k in ('vendorString','deviceString')) and
                 all(type(device[k]) is int and 0 <= device[k] <= 0xffffffff for k in ('subSysId','revision') if k in device) and
                 all(isinstance(device[k],str) for k in ('driverVendor','driverVersion') if k in device),
                 'invalid CDP GPU device identity')
    status, aux = gpu.get('featureStatus'), gpu.get('auxAttributes')
    _require(isinstance(status,dict) and status and all(isinstance(k,str) and isinstance(v,str) for k,v in status.items()) and
             isinstance(aux,dict), 'CDP GPU feature/backend status missing')
    return {'source':value['source'], 'devices':[{k:d[k] for k in SYSTEM_DEVICE if k in d} for d in devices],
            'featureStatus':status, 'auxAttributes':{k:aux[k] for k in SYSTEM_AUX if k in aux},
            'adapter_binding':'not_verified'}


def collect_system(context):
    session = None
    try:
        session = context.browser.new_browser_cdp_session()
        gpu = session.send('SystemInfo.getInfo')['gpu']
        result = {'status':'observed','value':{'source':'CDP.SystemInfo.getInfo','gpu':gpu}}
        system_projection(result)
    except Exception as error:
        result = {'status':'error','reason':str(error)[:2048]}
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception as error:
                prior = result.get('reason','')
                result = {'status':'error','reason':((prior+'; ' if prior else '')+
                          'CDP session detach failed: '+str(error))[:2048]}
    return result


async def collect_system_async(context):
    session = None
    try:
        session = await context.browser.new_browser_cdp_session()
        gpu = (await session.send('SystemInfo.getInfo'))['gpu']
        result = {'status':'observed','value':{'source':'CDP.SystemInfo.getInfo','gpu':gpu}}
        system_projection(result)
    except Exception as error:
        result = {'status':'error','reason':str(error)[:2048]}
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception as error:
                prior = result.get('reason','')
                result = {'status':'error','reason':((prior+'; ' if prior else '')+
                          'CDP session detach failed: '+str(error))[:2048]}
    return result
