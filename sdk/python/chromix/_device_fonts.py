"""Observe actual platform glyph faces through CDP, not font-name availability.

CDP gives face names and glyph counts, not file handles. Installed-file inventory
is bound separately; ambiguous file-to-face mapping remains explicitly unverified.
"""
FONT_EVAL = '() => chromixFontNodes()'
FONT_SELECTOR = '#chromix-device-fonts > span'
FONT_CLEANUP = "document.getElementById('chromix-device-fonts')?.remove()"
FAMILIES = ('serif', 'sans-serif', 'monospace', 'system-ui')
TEXTS = ('Aa09', '\u4e2d\u6587', '\U0001f600', '\u2211', '\u0378')


def result(samples):
    value = {'status':'observed', 'value':{'source':'CDP.CSS.getPlatformFontsForNode',
             'samples':samples, 'fileBinding':'not_verified'}}
    errors = font_errors(value)
    if errors:
        raise ValueError('; '.join(errors))
    return value


def font_errors(item):
    try:
        if not isinstance(item, dict) or item.get('status') != 'observed':
            return ['missing platform glyph-face evidence']
        value = item['value']
        if value.get('source') != 'CDP.CSS.getPlatformFontsForNode' or value.get('fileBinding') != 'not_verified':
            return ['platform face evidence cannot certify font files']
        samples = value['samples']
        if [(s['family'],s['text']) for s in samples] != [(f,t) for f in FAMILIES for t in TEXTS]:
            return ['incomplete platform glyph-face matrix']
        for sample in samples:
            faces = sample.get('platformFonts')
            if not isinstance(faces, list) or not faces:
                return ['no actual platform glyph face was reported']
            for face in faces:
                if (not isinstance(face, dict) or not isinstance(face.get('familyName'), str) or
                        not face['familyName'] or not isinstance(face.get('postScriptName'), str) or
                        face.get('isCustomFont') is not False or type(face.get('glyphCount')) is not int or
                        face['glyphCount'] <= 0):
                    return ['invalid platform face/glyph-count evidence']
        return []
    except (KeyError, TypeError, AttributeError):
        return ['malformed platform glyph-face evidence']


def collect(context, page):
    session = None
    try:
        samples = page.evaluate(FONT_EVAL)
        session = context.new_cdp_session(page)
        session.send('DOM.enable'); session.send('CSS.enable')
        root = session.send('DOM.getDocument')['root']['nodeId']
        nodes = session.send('DOM.querySelectorAll', {'nodeId':root, 'selector':FONT_SELECTOR})['nodeIds']
        if len(nodes) != len(samples):
            raise ValueError('platform font node count changed')
        for sample, node in zip(samples, nodes):
            sample['platformFonts'] = sorted(session.send('CSS.getPlatformFontsForNode', {'nodeId':node})['fonts'],
                                             key=lambda f:(f['familyName'],f['postScriptName'],f['glyphCount']))
        return result(samples)
    finally:
        try:
            if session is not None:
                session.detach()
        finally:
            page.evaluate(FONT_CLEANUP)


async def collect_async(context, page):
    session = None
    try:
        samples = await page.evaluate(FONT_EVAL)
        session = await context.new_cdp_session(page)
        await session.send('DOM.enable'); await session.send('CSS.enable')
        root = (await session.send('DOM.getDocument'))['root']['nodeId']
        nodes = (await session.send('DOM.querySelectorAll', {'nodeId':root, 'selector':FONT_SELECTOR}))['nodeIds']
        if len(nodes) != len(samples):
            raise ValueError('platform font node count changed')
        for sample, node in zip(samples, nodes):
            fonts = (await session.send('CSS.getPlatformFontsForNode', {'nodeId':node}))['fonts']
            sample['platformFonts'] = sorted(fonts, key=lambda f:(f['familyName'],f['postScriptName'],f['glyphCount']))
        return result(samples)
    finally:
        try:
            if session is not None:
                await session.detach()
        finally:
            await page.evaluate(FONT_CLEANUP)
