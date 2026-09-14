#!/usr/bin/env python3
"""Three-launch native GPU resource and conflicting-override policy audit.

An explicit executable is required. No browser download, software workaround or
physical device claim. All raw observations remain available on oracle failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sdk/python'))
from chromix import _device_launch as launch, _gpu_backend as gpu
from chromix._device_host import host_inventory
from chromix._device_probe import probe_hash
from chromix import device_pool as pool

PROFILES = ('a','a','b-native-policy-conflict')
CONFLICTS = ['--uxr-fingerprint-enabled=true','--uxr-synthetic-device-tests=true',
             '--uxr-canvas-seed=42','--uxr-webgl-vendor=GPU-POLICY-CONFLICT',
             '--uxr-webgl-renderer=GPU-POLICY-CONFLICT','--uxr-webgl-extensions=GPU_POLICY_CONFLICT',
             '--uxr-webgpu-features=GPU_POLICY_CONFLICT','--uxr-webgpu-vendor=GPU-POLICY-CONFLICT',
             '--uxr-canvas-bridge=invalid://must-not-be-parsed','--uxr-canvas-bridge-unsafe']


def arguments(index):
    if index != 2: return list(launch.NATIVE_ARGS)
    redundant = {'--fingerprint=off','--uxr-webgl-real','--uxr-disable-fingerprint-noise'}
    return [arg for arg in launch.NATIVE_ARGS if arg not in redundant] + CONFLICTS


def assess(report):
    errors, gaps = [], []
    try:
        if report.get('probe_sha256') != probe_hash():
            errors.append('GPU audit used a different packaged probe bundle')
        runs = report['runs']
        if [r.get('profile') for r in runs] != list(PROFILES):
            errors.append('GPU restart/profile/policy matrix did not complete')
        signatures = []
        for index, run in enumerate(runs):
            if run.get('launch_args') != arguments(index):
                errors.append('GPU native/conflicting policy arguments differ')
            observation = run.get('observation',{})
            if set(observation) != set(pool.SCOPES):
                errors.append('GPU context matrix incomplete')
            for scope in pool.SCOPES:
                checked = gpu.assess(observation.get(scope),scope)
                errors.extend(f'run {index} {scope}: {e}' for e in checked['errors'])
                gaps.extend(f'run {index} {scope}: {e}' for e in checked['gaps'])
            native = gpu.system_projection(run.get('gpu_system'))
            signatures.append(pool.digest({'observation':observation,'gpu_system':native}))
        if len(set(signatures)) != 1:
            errors.append('native GPU resource evidence changed across restart/profile/conflicting overrides')
        if report.get('host_before') != report.get('host_after') or not report.get('host_before'):
            errors.append('native host inventory changed or is missing')
    except (ValueError,KeyError,TypeError,AttributeError) as error:
        errors.append('malformed GPU audit: '+str(error))
    return errors, sorted(set(gaps))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--headed',action='store_true')
    args = parser.parse_args(argv)
    if args.output.exists(): parser.error('output must be a new file')
    report = {'schema_version':1,'collected_at':datetime.now(timezone.utc).isoformat(),
              'probe_sha256':probe_hash(),'runs':[],'errors':[],'gaps':[],
              'headless':not args.headed,
              'qualification':{'physical_devices':'not_attested','adapter_binding':'not_verified',
                               'native_build':'requires_matching_source_and_executable_receipts'}}
    try:
        from playwright.sync_api import sync_playwright
        binary = args.browser.resolve(strict=True)
        report['browser_sha256'] = pool.file_hash(binary)
        report['host_before'] = host_inventory()
        with launch.probe_server(probe_family='gpu-backend') as origin, tempfile.TemporaryDirectory(prefix='gpu-backend-') as temp:
            with sync_playwright() as pw:
                for index, profile in enumerate(PROFILES):
                    context = pw.chromium.launch_persistent_context(str(Path(temp)/profile),executable_path=str(binary),
                        headless=not args.headed,args=arguments(index),chromium_sandbox=True,no_viewport=True,service_workers='allow')
                    run = {'profile':profile,'launch_args':arguments(index),'observation':{}}
                    report['runs'].append(run)
                    try:
                        version = context.browser.version
                        if 'browser_version' in report and report['browser_version'] != version:
                            raise ValueError('browser version changed between launches')
                        report['browser_version'] = version
                        page = context.new_page(); page.goto(origin,wait_until='load',timeout=120000)
                        script = launch.bounded('() => chromixGpuBackendProbe()')
                        for scope, target in (('window',page),('iframe',page.frame(url=origin+'/frame'))):
                            run['observation'][scope] = target.evaluate(script)
                        for scope in pool.SCOPES[2:]:
                            run['observation'][scope] = page.evaluate(launch.WORKER_EVAL,scope)
                        run['gpu_system'] = gpu.collect_system(context)
                    except Exception as error:
                        report['errors'].append(f'run {index}: {error}')
                    finally:
                        context.close()
        report['host_after'] = host_inventory()
        if pool.file_hash(binary) != report['browser_sha256']:
            report['errors'].append('browser executable changed during GPU audit')
        checked, report['gaps'] = assess(report)
        report['errors'].extend(checked)
    except Exception as error:
        report['errors'].append(str(error))
    report['status'] = 'failed' if report['errors'] else 'incomplete' if report['gaps'] else 'passed'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2,ensure_ascii=True,allow_nan=False)
    print(json.dumps({'status':report['status'],'errors':report['errors'],'gaps':report['gaps'],'output':str(args.output)}))
    return int(report['status'] == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
