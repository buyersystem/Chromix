import { test } from 'node:test';
import assert from 'node:assert/strict';
import { collectGpuSystem } from '../_device_pool.js';

for (const failure of [false, true]) for (const detachFailure of [false, true])
test(`GPU system evidence keeps raw CDP fields and detaches (${failure}, ${detachFailure})`, async () => {
  const trace = [], gpu = { devices: [{ vendorId: 4318, deviceId: 4660 }],
    auxAttributes: { processCrashCount: 7 }, featureStatus: { webgpu: 'enabled' } };
  const session = { send: async method => {
    trace.push(method);
    if (failure) throw new Error('fixture failure');
    return { gpu };
  }, detach: async () => {
    trace.push('detach');
    if (detachFailure) throw new Error('fixture detach failure');
  } };
  const context = { browser: () => ({ newBrowserCDPSession: async () => session }) };
  const result = await collectGpuSystem(context);
  assert.equal(result.status, failure || detachFailure ? 'error' : 'observed');
  if (!failure && !detachFailure) assert.deepEqual(result.value, { source: 'CDP.SystemInfo.getInfo', gpu });
  if (failure) assert.match(result.reason, /fixture failure/);
  if (detachFailure) assert.match(result.reason, /detach failed/);
  assert.deepEqual(trace, ['SystemInfo.getInfo', 'detach']);
});
