import test from 'node:test';
import assert from 'node:assert/strict';
import { collectFontSources } from '../_device_pool.js';

for (const mismatch of [false, true]) {
  test(`measured font CDP collection and cleanup (mismatch=${mismatch})`, async () => {
    const trace = [];
    const server = { font_eval: '() => []', font_selector: '#fixture > span', font_cleanup: 'cleanup()' };
    const page = { async evaluate(script) {
      trace.push(script); return [{ family: 'serif', text: 'Aa09' }];
    } };
    const session = {
      async send(method, params) {
        if (method === 'DOM.getDocument') return { root: { nodeId: 1 } };
        if (method === 'DOM.querySelectorAll') {
          assert.equal(params.selector, server.font_selector);
          return { nodeIds: mismatch ? [] : [2] };
        }
        if (method === 'CSS.getPlatformFontsForNode') return { fonts: [
          // Python orders Unicode code points, not UTF-16 code units or locale.
          { familyName: '\u{1f600}', postScriptName: 'astral-face', glyphCount: 1, isCustomFont: false },
          { familyName: '\ue000', postScriptName: 'bmp-face', glyphCount: 1, isCustomFont: false },
          { familyName: 'B', postScriptName: 'B-face', glyphCount: 2, isCustomFont: false },
          { familyName: 'A', postScriptName: 'A-face', glyphCount: 2, isCustomFont: false },
        ] };
        return {};
      },
      async detach() { trace.push('detach'); },
    };
    const context = { async newCDPSession(target) { assert.equal(target, page); return session; } };
    if (mismatch) {
      await assert.rejects(() => collectFontSources(context, page, server), /node count/);
    } else {
      const result = await collectFontSources(context, page, server);
      assert.equal(result.value.fileBinding, 'not_verified');
      assert.deepEqual(result.value.samples[0].platformFonts.map(f => f.familyName), ['A', 'B', '\ue000', '\u{1f600}']);
    }
    assert.deepEqual(trace.slice(-2), ['detach', server.font_cleanup]);
  });
}
