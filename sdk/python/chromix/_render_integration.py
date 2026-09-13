"""Shared bitmap/ownership/GPU contract checks; not hardware attestation."""
import math

def _assess(observation):
    if not isinstance(observation, dict) or observation.get('schema_version') != 1:
        return ['missing render observation'], []
    errors = list(observation.get('errors', []))
    unavailable = list(observation.get('unavailable', []))
    seen = observation.get('observed', {})
    for name in ('bitmap', 'exports', 'workerOwnership', 'webgl', 'webgl2', 'webgpu'):
        if name not in seen:
            errors.append('missing render path: ' + name)
    if seen.get('bitmap') != dict.fromkeys(('crop', 'resize', 'flip', 'bitmaprenderer', 'ownership'), True):
        errors.append('bitmap operation/ownership evidence missing')
    if seen.get('workerOwnership') != {'detached': True, 'pixels': True}:
        errors.append('worker transfer evidence missing')
    if seen.get('exports') != [{'kind': kind, 'snapshots': 3, 'sourceMutation': True} for kind in ('html', 'offscreen')]:
        errors.append('concurrent export snapshot evidence missing')
    for api in ('webgl', 'webgl2'):
        value = seen.get(api, {})
        if value.get('status') in ('unavailable', 'partial'):
            unavailable.append(api)
            continue
        if value.get('status') != 'observed' or value.get('contextRestored') is not True:
            errors.append(api + ': context restoration unverified')
        for phase in ('before', 'after'):
            pixels = value.get(phase, [])
            if len(pixels) != 16 or any(type(v) is not int or abs(v - (64,128,191,255)[i % 4]) > 1 for i, v in enumerate(pixels)):
                errors.append(api + ': native clear/readback pixels disagree')
    gpu = seen.get('webgpu', {})
    if gpu.get('status') == 'unavailable':
        unavailable.append('webgpu')
    else:
        limits, boundaries = gpu.get('limits', {}), gpu.get('boundaries', [])
        features, enabled = gpu.get('features'), gpu.get('enabledFeatures')
        if (gpu.get('status') != 'observed' or not isinstance(features, list) or not isinstance(enabled, list) or
                not set(features) <= set(enabled) or not limits or not boundaries):
            errors.append('WebGPU advertised feature/limit requests not verified')
        if len(boundaries) != len({b['name'] for b in boundaries}) or {b['name'] for b in boundaries} != set(limits):
            errors.append('WebGPU boundary matrix incomplete')
        for b in boundaries:
            value, invalid, accepted = b['advertised'], b['requested'], b.get('accepted')
            if (limits.get(b['name']) != value or type(value) is not int or
                    type(accepted) not in (int, float) or not math.isfinite(accepted) or b.get('rejected') is not True or
                    (not invalid < value or not accepted <= value if b['name'].startswith('min')
                     else not invalid > value or not accepted >= value)):
                errors.append('WebGPU valid/invalid boundary mismatch: ' + b['name'])
    return errors, unavailable


def assess(observation):
    try:
        return _assess(observation)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as error:
        return ['malformed render evidence: ' + str(error)], []
