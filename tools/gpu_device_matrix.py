#!/usr/bin/env python3
"""Review actual OS/architecture/API/vendor coverage without inventing samples.

Controls, virtual adapters, fallback adapters and unexercised inventory entries
never fill coverage cells. Counts reflect checked evidence plus supplied review
metadata, not authenticated physical-device or exact-adapter attestation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import device_pool as pool
import fingerprint_corpus_review as corpus
from chromix import _gpu_backend as backend, _gpu_inventory as inventory
from chromix._device_render import unpack
from chromix._device_probe import probe_hash

ARCHITECTURES = {'amd64':'x64','x86_64':'x64','x64':'x64','aarch64':'arm64','arm64':'arm64'}
FAMILIES = ('intel','amd','nvidia','apple','qualcomm')


def family_hint(identity, api):
    if not isinstance(identity,dict): return None
    text = ' '.join(str(identity.get(k) or '') for k in ('vendor','renderer','description')).lower()
    if any(token in text for token in (*inventory.SOFTWARE,*inventory.VIRTUAL)): return None
    if api == 'webgpu' and identity.get('isFallbackAdapter') is not False: return None
    # These are native API labels, not substituted hardware IDs. Ambiguous
    # labels remain unknown; names alone cannot prove an exact adapter binding.
    aliases = {'intel':r'\bintel\b','amd':r'\bamd\b|\bati\b','nvidia':r'\bnvidia\b',
               'apple':r'\bapple\b','qualcomm':r'\bqualcomm\b|\badreno\b'}
    found = [name for name, pattern in aliases.items() if re.search(pattern,text)]
    return found[0] if len(found) == 1 else None


def exercised(observation):
    """Return API/vendor -> observed scopes, only after rechecking raw work."""
    groups = {}
    for scope in pool.SCOPES:
        raw = unpack(observation[scope]['render'])['gpuBackend']
        checked = backend.assess(raw,scope)
        if checked['errors']: raise ValueError('; '.join(checked['errors']))
        identities = checked['identities']
        for api in ('webgl','webgl2','webgpu'):
            identity = (identities.get('webgpu',{}).get('default') if api == 'webgpu' else
                        identities.get('webgl',{}).get(api))
            family = family_hint(identity,api)
            if family: groups.setdefault((api,family),set()).add(scope)
    return groups


def read_plan(path):
    plan = pool.load_json(path)
    if (not isinstance(plan,dict) or set(plan) != {'schema_version','cells'} or
            type(plan['schema_version']) is not int or plan['schema_version'] != 1 or
            not isinstance(plan['cells'],list) or not 1 <= len(plan['cells']) <= 256):
        raise ValueError('GPU matrix plan requires schema v1 and 1..256 cells')
    seen, tuples = set(), set()
    for cell in plan['cells']:
        if (not isinstance(cell,dict) or set(cell) != {'id','os','architecture','api','vendor_family','min_devices'} or
                not corpus.label(cell['id']) or cell['id'] in seen or cell['os'] not in ('Windows','Linux','Darwin') or
                cell['architecture'] not in ('x64','arm64') or cell['api'] not in ('webgl','webgl2','webgpu') or
                cell['vendor_family'] not in FAMILIES or type(cell['min_devices']) is not int or not 1 <= cell['min_devices'] <= 100):
            raise ValueError('invalid or duplicate GPU matrix cell')
        key = tuple(cell[k] for k in ('os','architecture','api','vendor_family'))
        if key in tuples: raise ValueError('duplicate GPU matrix requirement')
        seen.add(cell['id']); tuples.add(key)
    return plan


def build_matrix(plan_path, review_paths=(), control_paths=(), *, now=None):
    plan_path = Path(plan_path)
    before = pool.file_hash(plan_path); expected_probe = probe_hash()
    plan = read_plan(plan_path)
    cells = [{**cell,'devices':set(),'observed_scopes':set()} for cell in plan['cells']]
    report = {'schema_version':1,'plan_sha256':before,'probe_sha256':expected_probe,
              'reviews':[],'controls':[],'errors':[], 'unique_reviewed_devices':0,
              'qualification':{'physical_attestation':False,'exact_adapter_binding':False,
                               'counts':'checked bundles plus supplied physical-device review metadata',
                               'controls_counted':False,'unexercised_inventory_counted':False}}
    device_ids, record_ids = set(), set()
    for review_path in review_paths:
        review_path = Path(review_path)
        try:
            checked = corpus.review(review_path,now=now)
            report['reviews'].append({'path':str(review_path),'manifest_sha256':checked['manifest_sha256'],
                                      'status':checked['status'],'errors':checked['errors']})
            if checked['status'] != 'passed':
                raise ValueError('corpus review did not pass: '+str(review_path))
            manifest = pool.load_json(review_path)
            pending_devices, pending_records, fingerprints = set(), set(), {}
            pending_cells = [{'devices':set(),'scopes':set()} for _ in cells]
            for entry in checked['samples']:
                if entry['status'] != 'review_checks_passed': continue
                sample = manifest['samples'][entry['index']]
                if sample['record_id'] in record_ids or sample['record_id'] in pending_records:
                    raise ValueError('record repeated across GPU reviews')
                pending_records.add(sample['record_id'])
                path = corpus.paths._path(review_path.resolve().parent,sample['path'])
                if pool.file_hash(path) != sample['record_sha256']:
                    raise ValueError('record changed after corpus review')
                fingerprints[path] = sample['record_sha256']
                record = pool.validate_record(pool.load_json(path),path.parent)
                host = record['device']['host']; native = inventory.normalize(host['gpu'])
                candidates = {r['vendor_family'] for r in native if r['classification'] == 'hardware_candidate'}
                browser_path = corpus.paths._path(path.parent,record['evidence']['browser']['path'])
                browser = pool.load_json(browser_path)
                if pool.file_hash(browser_path) != record['evidence']['browser']['sha256']:
                    raise ValueError('browser evidence changed after corpus review')
                for evidence in record['evidence'].values():
                    fingerprints[corpus.paths._path(path.parent,evidence['path'])] = evidence['sha256']
                # Three launches were independently validated above. Use their
                # intersection so one lucky launch cannot fill a device cell.
                groups = [exercised(o) for o in browser['observations']]
                architecture = ARCHITECTURES.get(host['os']['architecture'].lower())
                pending_devices.add(sample['device_id'])
                for cell, pending in zip(cells,pending_cells):
                    if (host['os']['system'],architecture) != (cell['os'],cell['architecture']): continue
                    if cell['vendor_family'] not in candidates: continue
                    key = (cell['api'],cell['vendor_family'])
                    scopes = set(pool.SCOPES).intersection(*(g.get(key,set()) for g in groups))
                    pending['scopes'].update(scopes)
                    if scopes == set(pool.SCOPES): pending['devices'].add(sample['device_id'])
            if pool.file_hash(review_path) != checked['manifest_sha256']:
                raise ValueError('review manifest changed during matrix review')
            if any(pool.file_hash(path) != digest for path,digest in fingerprints.items()):
                raise ValueError('reviewed bundle changed during matrix review')
            # Publish one review atomically, only after every sample and final
            # file hash check succeeds. Failed reviews cannot leave covered cells.
            device_ids.update(pending_devices); record_ids.update(pending_records)
            for cell,pending in zip(cells,pending_cells):
                cell['observed_scopes'].update(pending['scopes'])
                cell['devices'].update(pending['devices'])
        except (ValueError,OSError,KeyError,TypeError,AttributeError,corpus.paths.ApplyError) as error:
            report['errors'].append(str(error))
    for path in control_paths:
        try:
            from gpu_backend_audit import assess
            path = Path(path); digest = pool.file_hash(path); raw = pool.load_json(path)
            errors, gaps = assess(raw)
            host = raw.get('host_before',{})
            row = {'path':str(path),'sha256':digest,'status':'excluded','reason':'control, not a reviewed device record',
                   'browser_sha256':raw.get('browser_sha256'),'browser_version':raw.get('browser_version'),
                   'oracle_errors':errors,'oracle_gaps':gaps,'native_inventory':inventory.normalize(host.get('gpu')),
                   'api_identities':{}}
            for run in raw.get('runs',[]):
                for scope, value in run.get('observation',{}).items():
                    # Even a failed control may contain useful independent API
                    # results. Never use them as eligible device counts.
                    observed = backend.assess(value,scope)
                    row['api_identities'][scope] = observed['identities']
            report['controls'].append(row)
            if pool.file_hash(path) != digest: raise ValueError('control changed during review')
        except (ValueError,OSError,TypeError,KeyError,AttributeError) as error:
            report['errors'].append('invalid control: '+str(error))
    report['unique_reviewed_devices'] = len(device_ids)
    report['cells'] = [{**{k:v for k,v in cell.items() if k not in ('devices','observed_scopes')},
                        'observed_devices':len(cell['devices']),'observed_scopes':sorted(cell['observed_scopes']),
                        'status':'covered' if len(cell['devices']) >= cell['min_devices'] else
                                 'incomplete' if cell['observed_scopes'] else 'not_sampled'} for cell in cells]
    if pool.file_hash(plan_path) != before or probe_hash() != expected_probe:
        report['errors'].append('GPU matrix plan or packaged probe changed during review')
    report['status'] = 'failed' if report['errors'] else 'passed' if all(c['status'] == 'covered' for c in report['cells']) else 'incomplete'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix',type=Path,required=True)
    parser.add_argument('--review',type=Path,action='append',default=[])
    parser.add_argument('--control-report',type=Path,action='append',default=[])
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    if args.output.exists(): parser.error('output must be a new file')
    try:
        if len(args.review) > 32 or len(args.control_report) > 32: raise ValueError('at most 32 reviews/controls')
        report = build_matrix(args.matrix,args.review,args.control_report)
    except (ValueError,OSError,TypeError,KeyError) as error:
        report = {'schema_version':1,'status':'failed','errors':[str(error)]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2,ensure_ascii=True,allow_nan=False)
    print(json.dumps({'status':report['status'],'errors':report['errors'],'output':str(args.output)}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
