"""Independent resource-oracle regressions; generated fixtures are not hardware."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'sdk/python'))
from chromix import _gpu_backend as gpu, _gpu_inventory as inventory
from chromix import _device_launch as launch, _device_render as render, device_pool as pool
from gpu_backend_fixtures import backend_fixture, system_fixture


@pytest.mark.parametrize('scope',pool.SCOPES)
def test_all_resource_families_pass_only_with_raw_evidence(scope):
    result = gpu.assess(backend_fixture(scope),scope)
    assert result['errors'] == [] and result['gaps'] == []
    assert result['identities']['webgl']['webgl']['renderer'] == 'not a physical GPU'


@pytest.mark.parametrize('path',[
    ('canvas',0,'value','before','pixels',0),('canvas',0,'value','bitmapPixels',0),
    ('canvas',0,'value','resized','pixels',0),('canvas',0,'value','bitmapClosed',0),
    ('webgl','webgl','value','formats',0,'pixels',0),('webgl','webgl2','value','formats',1,'pixels',0),
    ('webgl','webgl','value','layout','client','bytes',0),('webgl','webgl','value','layout','client','bytes',8),
    ('webgl','webgl2','value','layout','pbo','bytes',36),('webgl','webgl2','value','layout','pbo','bytes',44),
    ('webgl','webgl','value','lifecycle','lostPixels',0),('webgl','webgl2','value','lifecycle','after',0),
    ('webgpu',0,'value','formats',0,'bytes',0),('webgpu',0,'value','formats',0,'bytes',256),
    ('webgpu',0,'value','formats',4,'bytes',257),('webgpu',0,'value','formats',6,'bytes',259),
    ('webgpu',0,'value','formats',0,'bytes',268),('webgpu',0,'value','formats',0,'bytes',1023),
    ('webgpu',0,'value','external',0,'readback','bytes',256),
    ('webgpu',0,'value','canvases',0,'resized',0),('webgpu',0,'value','lifecycle','recreated',0),
])
def test_altered_pixels_padding_and_resource_state_never_pass(path):
    value = backend_fixture(); node = value
    for key in path[:-1]: node = node[key]
    node[path[-1]] = 7 if node[path[-1]] != 7 else 200
    assert gpu.assess(value,'window')['errors']


@pytest.mark.parametrize('path,bad',[
    (('width',),3.0), (('height',),2.0),
    (('canvas',0,'value','resized','width'),True),
    (('canvas',0,'value','bitmapClosed',0),False),
    (('canvas',0,'value','before','height'),2.0),
    (('webgl','webgl','value','formats',0,'framebufferStatus'),36053.0),
    (('webgl','webgl','value','lifecycle','lostError'),37442.0),
    (('webgpu',0,'value','formats',0,'samples'),True),
    (('webgpu',0,'value','formats',1,'samples'),4.0),
    (('webgpu',0,'value','external',0,'bitmapClosed',0),False),
    (('webgpu',0,'value','canvases',0,'resized',0),True),
    (('webgpu',0,'value','lifecycle','recreated',0),17.0),
    (('webgl','webgl2','value','formats',1,'pixels',0),10**1000),
    (('canvas',4,'value','before','pixels',0),10**308),
    (('webgl','webgl2','value','formats',1,'pixels',0),float('nan')),
    (('webgpu',0,'value','formats',0,'bytes',0),float('inf')),
])
def test_numeric_type_confusion_and_nonfinite_or_huge_components_are_rejected(path,bad):
    value=backend_fixture();node=value
    for key in path[:-1]:node=node[key]
    node[path[-1]]=bad
    assert gpu.assess(value,'window')['errors']


@pytest.mark.parametrize('field',('canvas','webgl','webgpu'))
@pytest.mark.parametrize('bad',(None,True,{},[],{'status':'passed'}))
def test_missing_malformed_and_success_only_families_rejected(field,bad):
    value = backend_fixture(); value[field] = bad
    assert gpu.assess(value,'window')['errors']


@pytest.mark.parametrize('fault',('unmapped','lost','stale','recreated_map','fallback','duplicate','advertised','canvas_fallback'))
def test_failure_states_cannot_be_reclassified_as_success_or_optional(fault):
    value = backend_fixture(); first = value['webgpu'][0]['value']
    if fault == 'unmapped': first['formats'][0]['mappedBytesAfterUnmap'] = 1024
    elif fault == 'lost': first['lifecycle']['reason'] = 'unknown'
    elif fault == 'stale': first['lifecycle']['staleMapError'] = None
    elif fault == 'recreated_map': first['lifecycle']['recreatedMapBytesAfterUnmap'] = 16
    elif fault == 'fallback': value['webgpu'][3]['value']['identity']['isFallbackAdapter'] = False
    elif fault == 'duplicate': value['webgpu'].append(deepcopy(value['webgpu'][0]))
    elif fault == 'advertised':
        value['webgl']['webgl2']['value']['formats'][1] = {'format':'rgba16f','status':'unavailable','reason':'hidden failure'}
    else: value['canvas'][0]['value']['supported'] = False; value['canvas'][0]['value']['reason'] = 'hidden failure'
    assert gpu.assess(value,'window')['errors']


def test_different_actual_api_and_request_adapters_are_kept_not_equated():
    value = backend_fixture()
    value['webgl']['webgl2']['value']['beforeIdentity']['renderer'] = 'another real API selection'
    value['webgpu'][1]['value']['identity']['vendor'] = 'other native adapter'
    value['webgpu'][0]['value']['lifecycle']['afterIdentity']['vendor'] = 'native replacement'
    assert gpu.assess(value,'window')['errors'] == []


def test_unavailable_fallback_is_a_gap_not_a_manufactured_device():
    value = backend_fixture()
    value['webgpu'][3] = {'request':'fallback','status':'observed','value':{'available':False,'reason':'native null adapter'}}
    result = gpu.assess(value,'window')
    assert result['errors'] == [] and result['gaps'] == ['webgpu:fallback']
    assert 'fallback' not in result['identities']['webgpu']


def test_process_gpu_inventory_is_not_context_or_hardware_attestation():
    value = system_fixture(); projected = gpu.system_projection(value)
    assert 'processCrashCount' not in projected['auxAttributes']
    assert projected['adapter_binding'] == 'not_verified'
    assert gpu.system_projection({'status':'observed','value':projected}) == projected
    value['value']['gpu']['devices'][0]['deviceId'] = True
    with pytest.raises(ValueError): gpu.system_projection(value)


@pytest.mark.parametrize('args',([],['--uxr-gpu-backend'],['--uxr-gpu-backend=compatibility'],
    ['--uxr-gpu-backend=native']*2,['--uxr-gpu-backend=native','--disable-gpu'],
    ['--uxr-gpu-backend=native','--use-angle=swiftshader'],['--uxr-gpu-backend=native','--ignore-gpu-blocklist']))
def test_forced_diagnostic_backend_cannot_be_admitted_as_native(args):
    with pytest.raises(ValueError): gpu.validate_native_args(args)


def test_native_policy_keeps_gpu_enabled():
    gpu.validate_native_args(launch.NATIVE_ARGS)
    assert '--disable-gpu' not in launch.NATIVE_ARGS


@pytest.mark.parametrize('asynchronous',(False,True))
@pytest.mark.parametrize('fault',(False,True))
@pytest.mark.parametrize('detach_fault',(False,True))
def test_system_collection_detaches_owned_browser_cdp_session(asynchronous,fault,detach_fault):
    import asyncio
    trace=[]
    def send(method):
        trace.append(method)
        if fault: raise ValueError('fixture CDP failure')
        return {'gpu':system_fixture()['value']['gpu']}
    def detach():
        trace.append('detach')
        if detach_fault: raise ValueError('fixture detach failure')
    session=SimpleNamespace(send=send,detach=detach)
    context=SimpleNamespace(browser=SimpleNamespace(new_browser_cdp_session=lambda:session))
    if asynchronous:
        async def create(): return session
        async def send_async(method): return send(method)
        async def detach_async(): detach()
        session.send,session.detach=send_async,detach_async
        context.browser.new_browser_cdp_session=create
        result=asyncio.run(gpu.collect_system_async(context))
    else: result=gpu.collect_system(context)
    assert result['status'] == ('error' if fault or detach_fault else 'observed')
    if detach_fault: assert 'detach failed' in result['reason']
    if fault: assert 'fixture CDP failure' in result['reason']
    assert trace == ['SystemInfo.getInfo','detach']


@pytest.mark.parametrize('field,bad', [('driverVersion',True),('driverVendor',[]),('revision',False),('subSysId',1.0)])
def test_optional_cdp_identity_fields_are_not_untyped_metadata(field,bad):
    value=system_fixture();value['value']['gpu']['devices'][0][field]=bad
    with pytest.raises(ValueError):gpu.system_projection(value)


@pytest.mark.parametrize('bad',(None,True,[],42))
def test_nonobject_cdp_value_is_a_validation_failure(bad):
    with pytest.raises(ValueError):gpu.system_projection({'status':'observed','value':bad})


def test_cim_distinguishes_virtual_software_and_reported_pci_candidates():
    raw={'status':'observed','source':'Win32_VideoController','value':[
        {'Name':'fixture NVIDIA','PNPDeviceID':'PCI\\VEN_10DE&DEV_1234\\test','DriverVersion':'1'},
        {'Name':'fixture Virtual Display','PNPDeviceID':'ROOT\\DISPLAY\\0000','DriverVersion':'2'},
        {'Name':'fixture software','PNPDeviceID':'PCI\\VEN_1414&DEV_008C\\test','DriverVersion':'3'}]}
    result=inventory.normalize(raw)
    assert {r['classification'] for r in result} == {'hardware_candidate','virtual','software'}
    assert all(r['physical_attestation'] == 'not_verified' for r in result)
    assert inventory.normalize({'status':'unavailable'}) == []


def test_linux_inventory_uses_sysfs_ids_not_browser_strings(tmp_path):
    # The collector treats slot as an opaque OS label. A portable fixture name
    # avoids Windows colon restrictions without skipping the sysfs reader.
    root=tmp_path/'devices'; gpu_path=root/'fixture-slot'; gpu_path.mkdir(parents=True)
    for key,value in {'class':'0x030200','vendor':'0x10de','device':'0x1234'}.items():
        (gpu_path/key).write_text(value)
    result=inventory.linux_inventory(root,tmp_path/'modules')
    assert result['status'] == 'observed'
    normalized=inventory.normalize(result)
    assert normalized[0]['vendor_family'] == 'nvidia' and normalized[0]['name'] is None
    assert normalized[0]['driver_version'] is None
    assert result['value'][0]['slot'] == 'fixture-slot'


def test_apple_vendor_label_is_not_a_fabricated_numeric_device_id():
    result=inventory.normalize({'status':'observed','source':'system_profiler.SPDisplaysDataType','value':[
        {'sppci_model':'unit Apple GPU','spdisplays_vendor':'sppci_vendor_Apple','spdisplays_bus':'spdisplays_builtin'}]})
    assert result[0]['vendor_family'] == 'apple' and result[0]['vendor_id'] is None and result[0]['device_id'] is None


def test_pool_rechecks_backend_data_not_only_the_scene_or_supplied_summary(tmp_path):
    from test_device_render import bundle, observation, pack
    record=bundle(tmp_path)
    assert pool.validate_record(record,tmp_path) == record
    value=observation(); raw=render.unpack(value['worker']['render'])
    raw['gpuBackend']['webgpu'][0]['value']['formats'][0]['bytes'][0]=0
    value['worker']['render']=pack(raw)
    assert render.assess_observation(value)['errors']
    value=observation(); del value['window']['gpuSystem']
    assert render.assess_observation(value)['errors']


def test_stable_gpu_inventory_drops_counters_but_keeps_driver_changes():
    from test_device_render import observation
    before=observation(); after=deepcopy(before)
    after['window']['gpuSystem']['value']['gpu']['auxAttributes']['processCrashCount']=123
    assert pool.stable_observation(before) == pool.stable_observation(after)
    after['window']['gpuSystem']['value']['gpu']['devices'][0]['driverVersion']='changed'
    assert pool.stable_observation(before) != pool.stable_observation(after)
