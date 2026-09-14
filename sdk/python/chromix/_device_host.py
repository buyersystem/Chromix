import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from . import device_pool as pool
from . import _gpu_inventory as gpu_inventory

def command_json(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            errors='strict', timeout=60, check=True)
    return json.loads(result.stdout)


def host_inventory():
    host = {'os':{'system':platform.system(), 'release':platform.release(),
                  'version':platform.version(), 'architecture':platform.machine()},
            'cpu':{'logical_cores':os.cpu_count()}, 'gpu':{'status':'unavailable'},
            'memory':{'status':'unavailable'}, 'fonts':{'status':'unavailable'}}
    if os.name == 'nt':
        script = """
        $ErrorActionPreference = 'Stop'
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $cpu = @(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors)
        $gpu = @(Get-CimInstance Win32_VideoController | Sort-Object PNPDeviceID | Select-Object Name,PNPDeviceID,DriverVersion,DriverDate)
        $memory = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
        @{cpu=$cpu;gpu=$gpu;memory=$memory} | ConvertTo-Json -Depth 6 -Compress
        """
        try:
            data = command_json(['powershell', '-NoProfile', '-NonInteractive', '-Command', script])
            host['cpu']['devices'] = data['cpu']
            host['gpu'] = {'status':'observed', 'value':data['gpu'], 'source':'Win32_VideoController'}
            host['memory'] = {'status':'observed', 'bytes':data['memory']}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            host['inventory_error'] = str(error)
        roots = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts',
                 Path(os.environ.get('LOCALAPPDATA', '~')) / 'Microsoft/Windows/Fonts']
        files = sorted({p for root in roots if root.is_dir() for p in root.iterdir()
                        if p.suffix.lower() in ('.ttf', '.ttc', '.otf', '.fon')})
    elif platform.system() == 'Darwin':
        files = []
        try:
            data = command_json(['system_profiler','-json','SPDisplaysDataType'])
            rows = data.get('SPDisplaysDataType')
            if not isinstance(rows,list) or not rows:
                raise ValueError('macOS display inventory missing')
            # Monitors can contain serial numbers and dynamic modes. The adapter
            # entries are sufficient here; physical-display sampling is separate.
            keys = ('_name','sppci_model','spdisplays_vendor','spdisplays_device-id',
                    'spdisplays_revision-id','spdisplays_bus','spdisplays_metal','spdisplays_vram')
            host['gpu'] = {'status':'observed','source':'system_profiler.SPDisplaysDataType',
                           'value':[{k:r[k] for k in keys if k in r} for r in rows]}
            memory = subprocess.run(['sysctl','-n','hw.memsize'],capture_output=True,text=True,timeout=15,check=True)
            host['memory'] = {'status':'observed','bytes':int(memory.stdout.strip())}
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            host['inventory_error'] = str(error)
        for root in (Path('/System/Library/Fonts'),Path('/Library/Fonts'),Path.home()/'Library/Fonts'):
            if root.is_dir():
                files.extend(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.ttf','.ttc','.otf'))
        files = sorted(set(files))
    else:
        files = []
        if platform.system() == 'Linux':
            host['gpu'] = gpu_inventory.linux_inventory()
            try:
                memory = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
                if type(memory) is not int or memory <= 0: raise ValueError('invalid physical memory inventory')
                host['memory'] = {'status':'observed','bytes':memory}
            except (OSError,ValueError) as error:
                host['inventory_error'] = str(error)
        if shutil.which('fc-list'):
            result = subprocess.run(['fc-list', '--format=%{file}\n'], text=True,
                                    capture_output=True, timeout=30, check=True)
            files = sorted({Path(p) for p in result.stdout.splitlines() if p})
    host['gpu']['normalized'] = gpu_inventory.normalize(host['gpu'])
    if files:
        inventory, errors = [], []
        for path in files:
            try:
                inventory.append({'path':str(path), 'sha256':pool.file_hash(path)})
            except OSError as error:
                errors.append({'path':str(path), 'error':str(error)})
        host['fonts'] = {'status':'observed' if not errors else 'incomplete',
                         'files':inventory, 'errors':errors,
                         'glyph_source_verified':False}
    return host
