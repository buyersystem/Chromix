"""OS-reported GPU inventory. Labels are not proof of physical hardware.

Keep raw OS evidence and derive bus/vendor/driver candidates without borrowing
an adapter name from a browser, a template or a different API.
"""
from __future__ import annotations

from pathlib import Path
import re

VENDORS = {'1002':'amd', '1022':'amd', '10de':'nvidia', '8086':'intel', '106b':'apple',
           '17cb':'qualcomm', '5143':'qualcomm'}
VIRTUAL_VENDORS = {'15ad', '1af4', '1b36', '1234', '80ee', '5853'}
VIRTUAL = ('vmware','virtualbox','virtio','parallels','virtual gpu','virtual display','vbox')
SOFTWARE = ('swiftshader','llvmpipe','softpipe','lavapipe','software rasterizer',
            'software renderer','basic render driver','d3d11 warp')


def _hex(value, width=4):
    if not isinstance(value,str): return None
    text = value.lower()
    if text.startswith('0x'): text = text[2:]
    return text.zfill(width) if re.fullmatch(r'[0-9a-f]{1,'+str(width)+'}',text) else None


def _mac_hex(value):
    if not isinstance(value,str): return None
    found = re.search(r'0x([0-9a-fA-F]{4})(?:\b|\))',value)
    return found[1].lower() if found else None


def normalize(inventory):
    if not isinstance(inventory,dict) or inventory.get('status') != 'observed':
        return []
    source, raw = inventory.get('source'), inventory.get('value')
    if not isinstance(raw,list) or len(raw) > 256:
        raise ValueError('GPU inventory must be a bounded list')
    result = []
    for item in raw:
        if not isinstance(item,dict): raise ValueError('invalid native GPU inventory row')
        name = bus = driver = None
        vendor = device = None
        family = 'unknown'
        if source == 'Win32_VideoController':
            name, driver = item.get('Name'), item.get('DriverVersion')
            pnp = item.get('PNPDeviceID', '')
            if not isinstance(pnp,str): raise ValueError('GPU PNPDeviceID must be a string')
            match = re.match(r'PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})',pnp,re.I)
            if match: vendor, device = (s.lower() for s in match.groups())
            bus = pnp.split('\\',1)[0].lower() if pnp else 'unknown'
        elif source == 'linux-sysfs-pci':
            vendor, device = _hex(item.get('vendor')), _hex(item.get('device'))
            name, driver, bus = item.get('name'), item.get('driver_version'), 'pci'
        elif source == 'system_profiler.SPDisplaysDataType':
            vendor, device = _mac_hex(item.get('spdisplays_vendor')), _hex(item.get('spdisplays_device-id'))
            name = item.get('sppci_model',item.get('_name'))
            # A ROM revision is not a driver version. Preserve it only in raw OS data.
            driver = None
            bus = item.get('spdisplays_bus', 'unknown')
            if item.get('spdisplays_vendor') == 'sppci_vendor_Apple': family = 'apple'
        else:
            # Unrecognized collector data cannot become a hardware candidate.
            name = item.get('name',item.get('Name'))
            bus = 'unknown'
        if name is not None and not isinstance(name,str): raise ValueError('GPU name must be a string')
        if driver is not None and not isinstance(driver,str): raise ValueError('GPU driver version must be a string')
        if not isinstance(bus,str): raise ValueError('GPU bus must be a string')
        family = VENDORS.get(vendor, family if vendor is None else 'other')
        text = (name or '').lower()
        kind = 'unknown'
        if any(token in text for token in SOFTWARE) or (vendor,device) == ('1414','008c'):
            kind = 'software'
        elif bus == 'root' or vendor in VIRTUAL_VENDORS or any(token in text for token in VIRTUAL):
            kind = 'virtual'
        elif (vendor is not None and bus == 'pci') or (source == 'system_profiler.SPDisplaysDataType' and bus in ('spdisplays_builtin','spdisplays_pcie','spdisplays_pci')):
            kind = 'hardware_candidate'
        result.append({'name':name, 'vendor_id':vendor, 'device_id':device,
                       'vendor_family':family, 'bus':bus, 'driver_version':driver,
                       'classification':kind, 'physical_attestation':'not_verified'})
    return sorted(result,key=lambda r:tuple(str(r[k]) for k in ('vendor_id','device_id','name','bus','driver_version')))


def linux_inventory(sysfs=Path('/sys/bus/pci/devices'), modules=Path('/sys/module')):
    rows, errors = [], []
    try:
        devices = sorted(Path(sysfs).iterdir())
        if len(devices) > 4096: raise ValueError('PCI inventory exceeds bound')
        for path in devices:
            def read(name, required=True):
                file = path/name
                if not file.exists() and not required: return None
                with file.open('r',encoding='ascii') as stream: text = stream.read(4097)
                if len(text) > 4096: raise ValueError('oversized sysfs GPU attribute')
                return text.strip()
            klass = read('class')
            if not re.fullmatch(r'0x03[0-9a-fA-F]{4}',klass): continue
            row = {k:read(k,k in ('vendor','device','class')) for k in
                   ('vendor','device','class','subsystem_vendor','subsystem_device','boot_vga')}
            row['slot'] = path.name
            row['name'] = None  # sysfs IDs are evidence; do not invent a marketing name.
            driver = path/'driver'
            row['driver'] = driver.resolve(strict=True).name if driver.exists() else None
            version = Path(modules)/(row['driver'] or '_missing')/'version'
            row['driver_version'] = version.read_text(encoding='ascii').strip() if version.is_file() and version.stat().st_size <= 4096 else None
            rows.append(row)
    except (OSError,ValueError,UnicodeError) as error:
        errors.append(str(error))
    return {'status':'incomplete' if errors else 'observed' if rows else 'unavailable',
            'source':'linux-sysfs-pci','value':rows,'errors':errors}
