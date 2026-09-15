/* Owned browser diagnostics. These functions observe APIs; they do not replace them. */
(() => {
  'use strict';
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const bounded = (promise, ms = 12000) => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('backend probe timed out')), ms);
    promise.then(resolve, reject).finally(() => clearTimeout(timeout));
  });
  const hash = async array => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
    new Uint8Array(array.buffer, array.byteOffset, array.byteLength))), b => b.toString(16).padStart(2, '0')).join('');
  const same = (a, b) => a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const queries = {
    dark: '(prefers-color-scheme: dark)', contrast: '(prefers-contrast: more)',
    forced: '(forced-colors: active)', motion: '(prefers-reduced-motion: reduce)',
    transparency: '(prefers-reduced-transparency: reduce)', inverted: '(inverted-colors: inverted)',
    fine: '(pointer: fine)', coarse: '(pointer: coarse)', none: '(pointer: none)',
    anyFine: '(any-pointer: fine)', anyCoarse: '(any-pointer: coarse)', hover: '(hover: hover)',
  };
  const lifecycle = [];
  if (typeof document !== 'undefined') {
    for (const name of ['pageshow', 'pagehide', 'freeze', 'resume']) {
      const target = ['freeze', 'resume'].includes(name) ? document : globalThis;
      target.addEventListener(name, event => lifecycle.push({name, persisted: event.persisted ?? null,
        trusted:event.isTrusted, timestamp: event.timeStamp, now: performance.now()}));
    }
  }

  function preferences() {
    let style = document.getElementById('backend-probe-style');
    if (!style) {
      style = document.createElement('style'); style.id = 'backend-probe-style';
      style.textContent = ':root{color-scheme:light dark} #backend-color{background:rgb(3,7,11);color:rgb(241,239,233)}' +
        '#backend-queries{' + Object.keys(queries).map(key => '--' + key + ':0').join(';') + '}' +
        Object.entries(queries).map(([key, query]) => '@media ' + query + '{#backend-queries{--' + key + ':1}}').join('');
      document.head.append(style);
      for (const [id, tag] of [['backend-queries','div'], ['backend-color','div'], ['backend-input','input']]) {
        const element = document.createElement(tag); element.id = id;
        if (tag === 'input') element.style.cssText = 'position:fixed;top:10px;left:10px;width:160px;height:50px';
        document.body.append(element);
      }
      globalThis.backendInputEvents = [];
      for (const name of ['pointerdown','mousedown','touchstart','keydown'])
        document.getElementById('backend-input').addEventListener(name, event => {
          backendInputEvents.push({name, trusted:event.isTrusted, pointerType:event.pointerType ?? null,
            key:event.key ?? null, code:event.code ?? null, timestamp:event.timeStamp});
        });
    }
    const computed = getComputedStyle(document.getElementById('backend-queries'));
    const element = id => {
      const s = getComputedStyle(document.getElementById(id));
      return {background:s.backgroundColor, color:s.color, colorScheme:s.colorScheme};
    };
    return {queries:Object.fromEntries(Object.entries(queries).map(([key,q]) => [key, matchMedia(q).matches])),
      styles:Object.fromEntries(Object.keys(queries).map(key => [key, computed.getPropertyValue('--' + key).trim() === '1'])),
      maxTouchPoints:navigator.maxTouchPoints, touchEvent:'ontouchstart' in globalThis,
      author:element('backend-color'), input:element('backend-input')};
  }

  async function clocks() {
    const samples = [];
    for (let i = 0; i < 6; i++) {
      samples.push({wall:Date.now(), now:performance.now(), origin:performance.timeOrigin,
        event:new Event('clock-probe').timeStamp,
        temporal:typeof Temporal === 'undefined' ? null : String(Temporal.Now.instant().epochNanoseconds)});
      await delay(13);
    }
    const formatter = new Intl.DateTimeFormat('en-US', {hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
    const dates = ['2026-03-08T06:59:00Z','2026-03-08T07:00:00Z','2026-11-01T05:59:00Z','2026-11-01T06:00:00Z'];
    const callbacks = {};
    if (typeof requestAnimationFrame === 'function')
      callbacks.raf = await bounded(new Promise(resolve => requestAnimationFrame(timestamp =>
        resolve({timestamp, now:performance.now()}))));
    if (typeof requestIdleCallback === 'function')
      callbacks.idle = await bounded(new Promise(resolve => requestIdleCallback(deadline =>
        resolve({remaining:deadline.timeRemaining(), now:performance.now(), timedOut:deadline.didTimeout}), {timeout:2000})));
    const locale = new Intl.DateTimeFormat('en-US-u-hc-h23-nu-latn', {hour:'2-digit'}).resolvedOptions();
    return {samples, callbacks, locale, timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,
      dst:dates.map(value => formatter.format(new Date(value))), offsets:dates.map(value => new Date(value).getTimezoneOffset())};
  }

  async function workers() {
    const out = {};
    const worker = new Worker('/clock-dedicated.js');
    try { out.dedicated = await bounded(new Promise((resolve, reject) => {
      worker.onmessage = event => resolve(event.data); worker.onerror = event => reject(new Error(event.message));
      worker.postMessage('clock');
    })); } finally { worker.terminate(); }
    if (typeof SharedWorker === 'undefined') out.shared = {status:'unavailable'};
    else {
      const shared = new SharedWorker('/clock-shared.js');
      try { out.shared = await bounded(new Promise((resolve, reject) => {
        shared.port.onmessage = event => resolve(event.data); shared.onerror = event => reject(new Error(event.message));
        shared.port.start(); shared.port.postMessage('clock');
      })); } finally { shared.port.close(); }
    }
    if (!navigator.serviceWorker) out.service = {status:'unavailable'};
    else {
      const registration = await navigator.serviceWorker.register('/clock-service.js');
      const channel = new MessageChannel();
      try {
        const ready = await bounded(navigator.serviceWorker.ready);
        out.service = await bounded(new Promise(resolve => {
          channel.port1.onmessage = event => resolve(event.data);
          ready.active.postMessage('clock', [channel.port2]);
        }));
      } finally { channel.port1.close(); await registration.unregister(); }
    }
    return out;
  }

  async function audio() {
    const count = 4096, rate = 44100;
    const context = new OfflineAudioContext(1, count, rate);
    await context.audioWorklet.addModule('/audio-worklet.js');
    const source = context.createBufferSource(); source.buffer = context.createBuffer(1, count, rate);
    const input = source.buffer.getChannelData(0);
    for (let i = 0; i < count; i++) input[i] = Math.sin(i / 37) * 0.47;
    const analyser = context.createAnalyser(); analyser.fftSize = 2048;
    const tap = new AudioWorkletNode(context, 'backend-tap', {processorOptions:{start:0, frames:count}});
    const tapped = new Float32Array(count); let received = 0;
    let resolveTap; const tapDone = new Promise(resolve => { resolveTap = resolve; });
    tap.port.onmessage = event => {
      tapped.set(event.data.samples, event.data.offset); received += event.data.samples.length;
      if (received === count) resolveTap();
    };
    source.connect(analyser).connect(tap).connect(context.destination); source.start();
    const rendered = await context.startRendering(); await bounded(tapDone);
    const samples = rendered.getChannelData(0).slice(), copied = new Float32Array(count);
    rendered.copyFromChannel(copied, 0);
    const analyserData = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(analyserData);
    rendered.getChannelData(0)[0] = 0.25;
    const mutable = rendered.getChannelData(0)[0] === 0.25;
    rendered.getChannelData(0)[0] = samples[0];
    tap.port.close(); source.disconnect(); analyser.disconnect(); tap.disconnect();
    const playback = new AudioContext({sampleRate:rate});
    let playbackResult;
    try {
      await playback.audioWorklet.addModule('/audio-worklet.js'); await playback.resume();
      const start = Math.ceil((playback.currentTime + 0.05) * rate / 128) * 128;
      const sink = new AudioWorkletNode(playback, 'backend-tap', {processorOptions:{start, frames:count}});
      const muted = playback.createGain(); muted.gain.value = 0;
      const realtime = new Float32Array(count); let total = 0;
      const complete = new Promise(resolve => { sink.port.onmessage = event => {
        realtime.set(event.data.samples, event.data.offset); total += event.data.samples.length;
        if (total === count) resolve();
      }; });
      const player = playback.createBufferSource(); player.buffer = rendered;
      player.connect(sink).connect(muted).connect(playback.destination); player.start(start / rate);
      await bounded(complete);
      playbackResult = {state:playback.state, sampleRate:playback.sampleRate, frames:total,
        inputHash:await hash(realtime), sameSamples:same(samples, realtime),
        baseLatency:playback.baseLatency, outputLatency:playback.outputLatency ?? null};
      player.disconnect(); sink.disconnect(); sink.port.close();
    } finally { await playback.close(); }
    return {sampleRate:rendered.sampleRate, channels:rendered.numberOfChannels, frames:count,
      samples:Array.from(samples), hash:await hash(samples), sourceHash:await hash(input),
      copyHash:await hash(copied), workletHash:await hash(tapped), mutable,
      analyserMatches:same(samples.slice(-analyser.fftSize), analyserData), playback:playbackResult};
  }

  async function capabilities() {
    let uvpaa;
    try { uvpaa = {status:'observed', value:await bounded(PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable())}; }
    catch (error) { uvpaa = {status:'unavailable', error:error.name}; }
    const voices = () => speechSynthesis.getVoices().map(v => ({name:v.name, lang:v.lang, local:v.localService, default:v.default}));
    let speech = [];
    if (typeof speechSynthesis !== 'undefined') {
      if (!voices().length) await new Promise(resolve => {
        let timer;
        const done = () => { clearTimeout(timer); speechSynthesis.removeEventListener('voiceschanged', done); resolve(); };
        speechSynthesis.addEventListener('voiceschanged', done); timer = setTimeout(done, 1500);
      });
      speech = voices();
    }
    let keyboard;
    try { keyboard = {status:'observed', entries:Array.from(await bounded(navigator.keyboard.getLayoutMap())).sort()}; }
    catch (error) { keyboard = {status:'unavailable', error:error.name}; }
    return {uvpaa, keyboard, speech:speech.sort((a,b) => JSON.stringify(a).localeCompare(JSON.stringify(b))), pdf:navigator.pdfViewerEnabled,
      plugins:Array.from(navigator.plugins, p => ({name:p.name, filename:p.filename, types:Array.from(p, m => m.type)}))};
  }

  async function localFonts() {
    if (typeof queryLocalFonts !== 'function') return {status:'unavailable'};
    try { return {status:'observed', families:Array.from(new Set((await bounded(queryLocalFonts())).map(f => f.family))).sort()}; }
    catch (error) { return {status:'unavailable', error:error.name}; }
  }

  async function authorFont() {
    const face = new FontFace('BackendAuthor', 'url(/author.ttf)');
    await bounded(face.load()); document.fonts.add(face);
    const sample = document.createElement('span'); sample.id = 'backend-author-font';
    sample.style.cssText = 'font:32px BackendAuthor'; sample.textContent = 'Aa09'; document.body.append(sample);
    await document.fonts.ready;
    return {status:face.status, width:sample.getBoundingClientRect().width};
  }

  globalThis.chromixBackendProbe = {preferences, clocks, workers, audio, capabilities, localFonts, authorFont, lifecycle, bounded, delay};
})();

(() => {
  'use strict';
  const {bounded, delay} = chromixBackendProbe;
  const specs = {
    h264:{codec:'avc1.42001E', mime:'video/mp4;codecs=avc1.42001E'},
    vp8:{codec:'vp8', mime:'video/webm;codecs=vp8'},
    vp9:{codec:'vp09.00.10.08', mime:'video/webm;codecs=vp9'},
    av1:{codec:'av01.0.04M.08', mime:'video/webm;codecs=av01.0.04M.08'},
    hevc:{codec:'hvc1.1.6.L93.B0', mime:'video/mp4;codecs=hvc1.1.6.L93.B0'},
  };
  const config = spec => ({codec:spec.codec, width:64, height:64, bitrate:200000, framerate:30});
  const b64 = bytes => {
    let s = ''; for (let i=0;i<bytes.length;i+=8192) s += String.fromCharCode(...bytes.subarray(i, i+8192));
    return btoa(s);
  };
  const bytes = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
  const canvas = () => {
    const c = document.createElement('canvas'); c.width = c.height = 64;
    c.getContext('2d').fillRect(0,0,64,64); return c;
  };
  const supported = async (api, settings) => {
    try { return (await api.isConfigSupported(settings)).supported; } catch { return false; }
  };

  async function encode(spec) {
    const chunks = []; let description = null, error = null, encoder;
    try {
      encoder = new VideoEncoder({output:(chunk, metadata) => {
        const data = new Uint8Array(chunk.byteLength); chunk.copyTo(data);
        chunks.push({data:b64(data), type:chunk.type, timestamp:chunk.timestamp, duration:chunk.duration});
        if (metadata.decoderConfig) description = {...metadata.decoderConfig,
          description:metadata.decoderConfig.description ? b64(new Uint8Array(metadata.decoderConfig.description)) : null};
      }, error:e => { error = e.name; }});
      encoder.configure(config(spec));
      const frame = new VideoFrame(canvas(), {timestamp:0});
      try { encoder.encode(frame, {keyFrame:true}); } finally { frame.close(); }
      await bounded(encoder.flush());
      return {status:error ? 'rejected' : 'encoded', error, chunks,
        config:description || {codec:spec.codec, codedWidth:64, codedHeight:64}};
    } catch (e) { return {status:'rejected', error:error || e.name, chunks}; }
    finally { if (encoder && encoder.state !== 'closed') encoder.close(); }
  }

  async function decode(fixture) {
    if (!fixture || fixture.status !== 'encoded' || !fixture.chunks.length) return {status:'unavailable'};
    let decoder, error = null; const frames = [];
    try {
      decoder = new VideoDecoder({output:frame => {
        frames.push({width:frame.displayWidth, height:frame.displayHeight}); frame.close();
      }, error:e => { error = e.name; }});
      const settings = {...fixture.config};
      if (settings.description) settings.description = bytes(settings.description);
      else delete settings.description;
      decoder.configure(settings);
      for (const chunk of fixture.chunks) decoder.decode(new EncodedVideoChunk({...chunk, data:bytes(chunk.data)}));
      await bounded(decoder.flush());
      return {status:error ? 'rejected' : 'decoded', frames, error};
    } catch (e) { return {status:'rejected', frames, error:error || e.name}; }
    finally { if (decoder && decoder.state !== 'closed') decoder.close(); }
  }

  async function record(mime) {
    const c = canvas(), stream = c.captureStream(30); let recorder;
    const chunks = [];
    try {
      recorder = new MediaRecorder(stream, mime ? {mimeType:mime} : {});
      const done = new Promise((resolve, reject) => {
        recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
        recorder.onerror = e => reject(new Error(e.error?.name || 'recorder error')); recorder.onstop = resolve;
      });
      done.catch(() => {});
      recorder.start();
      for (let i=0;i<3;i++) {
        c.getContext('2d').fillStyle = ['red','green','blue'][i]; c.getContext('2d').fillRect(0,0,64,64);
        stream.getVideoTracks()[0].requestFrame(); await delay(60);
      }
      recorder.stop(); await bounded(done);
      const blob = new Blob(chunks, {type:recorder.mimeType});
      if (blob.size > 1048576) throw new Error('recording fixture exceeded 1 MiB');
      return {status:'recorded', bytes:blob.size, mime:recorder.mimeType, data:b64(new Uint8Array(await blob.arrayBuffer()))};
    } catch (e) { return {status:'rejected', bytes:chunks.reduce((n,b) => n+b.size,0), error:e.name + ':' + e.message}; }
    finally {
      if (recorder?.state === 'recording') recorder.stop();
      stream.getTracks().forEach(track => track.stop());
    }
  }

  async function play(fixture, mse = false) {
    if (fixture?.status !== 'recorded' || !fixture.bytes) return {status:'unavailable'};
    const video = document.createElement('video'); video.muted = true;
    let url, mediaSource;
    try {
      const loaded = new Promise((resolve, reject) => {
        video.onloadeddata = resolve;
        video.onerror = () => reject(new Error('MediaError:' + video.error?.code));
      });
      // Attach a rejection handler before the MSE setup can also fail.
      loaded.catch(() => {});
      if (mse) {
        mediaSource = new MediaSource(); url = URL.createObjectURL(mediaSource);
        const opened = new Promise(resolve => mediaSource.addEventListener('sourceopen', resolve, {once:true}));
        video.src = url; await bounded(opened);
        const buffer = mediaSource.addSourceBuffer(fixture.mime);
        const appended = new Promise((resolve, reject) => {
          buffer.addEventListener('updateend', resolve, {once:true});
          buffer.addEventListener('error', () => reject(new Error('MSE append failed')), {once:true});
        });
        buffer.appendBuffer(bytes(fixture.data)); await bounded(appended); mediaSource.endOfStream();
      } else {
        url = URL.createObjectURL(new Blob([bytes(fixture.data)], {type:fixture.mime}));
        video.src = url; video.load();
      }
      await bounded(loaded);
      return {status:'decoded', width:video.videoWidth, height:video.videoHeight};
    } catch (error) { return {status:'rejected', error:error.name + ':' + error.message}; }
    finally { video.removeAttribute('src'); video.load(); if (url) URL.revokeObjectURL(url); }
  }

  async function rtc(disabled) {
    const send = new RTCPeerConnection({iceServers:[]}), receive = new RTCPeerConnection({iceServers:[]});
    const c = canvas(), stream = c.captureStream(30), queuedSend = [], queuedReceive = [];
    const errors = []; let timer;
    const video = document.createElement('video'); video.muted = true; video.autoplay = true;
    receive.ontrack = event => { video.srcObject = event.streams[0]; video.play().catch(e => errors.push(e.name)); };
    const candidate = (peer, queue, value) => {
      if (peer.remoteDescription) peer.addIceCandidate(value).catch(e => errors.push(e.name));
      else queue.push(value);
    };
    send.onicecandidate = event => { if (event.candidate) candidate(receive, queuedReceive, event.candidate); };
    receive.onicecandidate = event => { if (event.candidate) candidate(send, queuedSend, event.candidate); };
    try {
      send.addTrack(stream.getVideoTracks()[0], stream);
      const offer = await send.createOffer();
      const formats = Array.from(offer.sdp.matchAll(/a=rtpmap:\d+ ([^/]+)/g), m => m[1].toLowerCase());
      if (disabled) return {status:'offered', formats};
      await send.setLocalDescription(offer); await receive.setRemoteDescription(offer);
      const answer = await receive.createAnswer();
      await receive.setLocalDescription(answer); await send.setRemoteDescription(answer);
      await Promise.all(queuedSend.map(c => send.addIceCandidate(c)));
      await Promise.all(queuedReceive.map(c => receive.addIceCandidate(c)));
      timer = setInterval(() => { c.getContext('2d').fillStyle = 'rgb(' + (Date.now()%255) + ',7,11)';
        c.getContext('2d').fillRect(0,0,64,64); stream.getVideoTracks()[0].requestFrame(); }, 30);
      for (let i=0;i<80;i++) {
        const stats = await receive.getStats();
        const inbound = Array.from(stats.values()).find(row => row.type === 'inbound-rtp' && row.kind === 'video' && row.framesDecoded > 0);
        if (inbound) return {status:'decoded', formats, frames:inbound.framesDecoded,
          bytes:inbound.bytesReceived, codec:stats.get(inbound.codecId)?.mimeType, errors};
        await delay(100);
      }
      return {status:'failed', formats, errors};
    } catch (error) { return {status:'rejected', error:error.name}; }
    finally { clearInterval(timer); video.srcObject = null; send.close(); receive.close(); stream.getTracks().forEach(track => track.stop()); }
  }

  async function audioRecord() {
    const context = new AudioContext(); let recorder;
    const destination = context.createMediaStreamDestination(), oscillator = context.createOscillator();
    try {
      await context.resume(); oscillator.connect(destination); oscillator.start();
      recorder = new MediaRecorder(destination.stream, {mimeType:'audio/webm;codecs=opus'});
      const chunks = [];
      const done = new Promise((resolve, reject) => {
        recorder.ondataavailable = event => chunks.push(event.data); recorder.onstop = resolve;
        recorder.onerror = event => reject(new Error(event.error?.name || 'audio recorder error'));
      });
      done.catch(() => {});
      recorder.start(); await delay(200); recorder.stop(); await bounded(done);
      return {status:'recorded', bytes:chunks.reduce((n,b) => n+b.size,0), mime:recorder.mimeType};
    } catch (error) { return {status:'failed', error:error.name}; }
    finally {
      if (recorder?.state === 'recording') recorder.stop();
      oscillator.stop(); destination.stream.getTracks().forEach(track => track.stop()); await context.close();
    }
  }

  async function codecs({fixtures = {}, disabled = []} = {}) {
    const out = {};
    const denied = new Set(disabled);
    const senders = RTCRtpSender.getCapabilities('video').codecs, receivers = RTCRtpReceiver.getCapabilities('video').codecs;
    for (const [name, spec] of Object.entries(specs)) {
      const forbidden = denied.has(name);
      const encoderSupported = typeof VideoEncoder !== 'undefined' && await supported(VideoEncoder, config(spec));
      const decoderSupported = typeof VideoDecoder !== 'undefined' && await supported(VideoDecoder,
        {codec:spec.codec, codedWidth:64, codedHeight:64});
      const encoded = encoderSupported || forbidden ? await encode(spec) : {status:'unavailable'};
      const recorderSupported = MediaRecorder.isTypeSupported(spec.mime);
      const mseSupported = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(spec.mime);
      const encoding = await navigator.mediaCapabilities.encodingInfo({type:'record',
        video:{contentType:spec.mime, width:64, height:64, bitrate:200000, framerate:30}}).catch(() => null);
      const decoding = await navigator.mediaCapabilities.decodingInfo({type:'file',
        video:{contentType:spec.mime, width:64, height:64, bitrate:200000, framerate:30}}).catch(() => null);
      const rtcName = name === 'hevc' ? 'h265' : name;
      const recorded = recorderSupported || forbidden ? await record(spec.mime) : {status:'unavailable'};
      const recording = forbidden ? fixtures[name]?.recorded : recorded;
      out[name] = {encoderSupported, decoderSupported, recorderSupported, mseSupported,
        canPlay:document.createElement('video').canPlayType(spec.mime), encoding, decoding, encoded,
        decoded:await decode(forbidden ? fixtures[name]?.encoded : encoded), recorded,
        playback:await play(recording), mse:mseSupported || forbidden ? await play(recording, true) : {status:'unavailable'},
        rtcSend:senders.filter(c => c.mimeType.toLowerCase() === 'video/' + rtcName).length,
        rtcReceive:receivers.filter(c => c.mimeType.toLowerCase() === 'video/' + rtcName).length};
    }
    const defaultRecorder = await record('');
    defaultRecorder.playback = await play(defaultRecorder);
    return {families:out, defaultRecorder, rtc:await rtc(denied.size === Object.keys(specs).length), audio:await audioRecord(),
      apis:{encoder:typeof VideoEncoder !== 'undefined', decoder:typeof VideoDecoder !== 'undefined',
        recorder:typeof MediaRecorder !== 'undefined', mse:typeof MediaSource !== 'undefined'}};
  }
  chromixBackendProbe.codecs = codecs;
})();
