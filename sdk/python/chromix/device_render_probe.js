/* Fixed inputs, actual raster/shader/texture operations, no seed-dependent pixels. */
globalThis.chromixFontNodes = async () => {
  document.getElementById('chromix-device-fonts')?.remove();
  const root = document.createElement('div'); root.id = 'chromix-device-fonts';
  root.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none';
  for (const family of ['serif','sans-serif','monospace','system-ui'])
    for (const text of ['Aa09','\u4e2d\u6587','\u{1f600}','\u2211','\u0378']) {
      const span = document.createElement('span'); span.textContent = text;
      span.style.font = `16px ${family}`; span.style.display = 'block'; root.appendChild(span);
    }
  document.body.appendChild(root); await document.fonts.ready;
  // Layout must resolve fallback faces before CSS.getPlatformFontsForNode.
  return [...root.children].map(node => {
    node.getBoundingClientRect(); return {text:node.textContent,family:node.style.fontFamily};
  });
};

globalThis.chromixSceneProbe = async () => {
  const W = 64, H = 48;
  const require = (ok, message) => { if (!ok) throw Error(message); };
  const absent = reason => ({status:'unavailable', reason});
  const capture = async fn => {
    try { return {status:'observed', value:await fn()}; }
    catch (e) { return {status:'error', reason:e.name + ': ' + e.message}; }
  };
  const canvas = (w = W, h = H) => new OffscreenCanvas(w, h);
  const read = c => Array.from(c.getContext('2d').getImageData(0, 0, c.width, c.height).data);
  const bitmapPixels = async source => {
    const bitmap = await createImageBitmap(source);
    try {
      const out = canvas(bitmap.width, bitmap.height);
      out.getContext('2d').drawImage(bitmap, 0, 0);
      return read(out);
    } finally { bitmap.close(); }
  };
  // Opaque nonuniform input makes orientation, swizzles and lost uploads visible.
  const source = canvas(16, 16), sourceContext = source.getContext('2d');
  const pattern = sourceContext.createImageData(16, 16);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++)
    pattern.data.set([x * 17, y * 17, (x ^ y) * 17, 255], (y * 16 + x) * 4);
  sourceContext.putImageData(pattern, 0, 0);
  const output = {version:1, width:W, height:H, source:Array.from(pattern.data)};
  output.canvas = await capture(async () => {
    const rows = [];
    for (const id of ['paths', 'gradient', 'composite', 'latin', 'cjk', 'emoji']) {
      const c = canvas(), ctx = c.getContext('2d', {colorSpace:'srgb'});
      require(ctx, '2D context missing');
      ctx.fillStyle = '#eddec9'; ctx.fillRect(0, 0, W, H);
      let metrics = null;
      if (id === 'paths') {
        ctx.save(); ctx.translate(31.25, 23.75); ctx.rotate(0.17);
        ctx.fillStyle = '#18559bbb'; ctx.strokeStyle = '#cf293f'; ctx.lineWidth = 1.75;
        ctx.beginPath(); ctx.moveTo(-25, 15); ctx.bezierCurveTo(-18, -33, 11, 23, 27, -16);
        ctx.quadraticCurveTo(8, 20, -25, 15); ctx.fill('evenodd'); ctx.stroke(); ctx.restore();
      } else if (id === 'gradient') {
        const g = ctx.createRadialGradient(19.5, 12.5, 1, 33, 24, 35);
        g.addColorStop(0, '#092851'); g.addColorStop(0.43, '#c53579a0'); g.addColorStop(1, '#dfcd39');
        ctx.fillStyle = g; ctx.fillRect(1.25, 2.75, 59.5, 41.25);
      } else if (id === 'composite') {
        ctx.fillStyle = '#f35119a0'; ctx.fillRect(3.5, 4.5, 39, 29);
        ctx.globalCompositeOperation = 'multiply'; ctx.fillStyle = '#2983dba0';
        ctx.beginPath(); ctx.ellipse(39, 25, 21.3, 16.7, 0.4, 0, Math.PI * 2); ctx.fill();
        ctx.globalCompositeOperation = 'source-over'; ctx.shadowColor = '#16253390';
        ctx.shadowBlur = 2.5; ctx.strokeStyle = '#172a4b'; ctx.strokeRect(8.5, 9.5, 44, 28);
      } else {
        const text = {latin:'Ag09 fi\u00e9', cjk:'\u4e2d\u6587\u3042\ud55c', emoji:'\ud83d\ude00\u2764\ufe0f\u2211\u0378'}[id];
        ctx.font = '17px sans-serif'; ctx.fontKerning = 'normal';
        ctx.fillStyle = '#1d2f59'; ctx.fillText(text, 1.25, 23.5);
        ctx.font = '13px serif'; ctx.fillText(text, 2.5, 43.25);
        const m = ctx.measureText(text);
        metrics = {text, font:ctx.font, width:m.width, left:m.actualBoundingBoxLeft,
          right:m.actualBoundingBoxRight, ascent:m.actualBoundingBoxAscent, descent:m.actualBoundingBoxDescent};
      }
      const pixels = read(c), bitmap = await bitmapPixels(c);
      const png = await c.convertToBlob({type:'image/png'});
      require(png.type === 'image/png', 'PNG encoder unavailable');
      rows.push({id, pixels, bitmap, png:await bitmapPixels(png), repeat:read(c), metrics});
    }
    return {rows, glyphFileBinding:'not_verified'};
  });
  output.webgl = {};
  for (const api of ['webgl', 'webgl2']) {
    const target = canvas(16, 16), gl = target.getContext(api,
      {alpha:false, antialias:false, preserveDrawingBuffer:true, powerPreference:'default'});
    if (!gl) { output.webgl[api] = absent('context unavailable'); continue; }
    output.webgl[api] = await capture(async () => {
      const resources = [];
      const keep = (kind, obj) => { require(obj, kind + ' allocation failed'); resources.push([kind, obj]); return obj; };
      try {
        const ext = gl.getExtension('WEBGL_debug_renderer_info'), limits = {}, precision = {};
        const names = ['MAX_TEXTURE_SIZE', 'MAX_CUBE_MAP_TEXTURE_SIZE', 'MAX_RENDERBUFFER_SIZE',
          'MAX_VERTEX_ATTRIBS', 'MAX_VERTEX_TEXTURE_IMAGE_UNITS', 'MAX_TEXTURE_IMAGE_UNITS',
          'MAX_COMBINED_TEXTURE_IMAGE_UNITS', 'MAX_VERTEX_UNIFORM_VECTORS',
          'MAX_FRAGMENT_UNIFORM_VECTORS', 'MAX_VARYING_VECTORS', 'SUBPIXEL_BITS',
          'ALIASED_POINT_SIZE_RANGE', 'ALIASED_LINE_WIDTH_RANGE', 'MAX_VIEWPORT_DIMS'];
        if (api === 'webgl2') names.push('MAX_SAMPLES', 'MAX_3D_TEXTURE_SIZE', 'MAX_ARRAY_TEXTURE_LAYERS',
          'MAX_DRAW_BUFFERS', 'MAX_COLOR_ATTACHMENTS', 'MAX_UNIFORM_BUFFER_BINDINGS',
          'MAX_UNIFORM_BLOCK_SIZE', 'MAX_VERTEX_UNIFORM_COMPONENTS', 'MAX_FRAGMENT_UNIFORM_COMPONENTS');
        for (const name of names) {
          const value = gl.getParameter(gl[name]);
          limits[name] = ArrayBuffer.isView(value) ? Array.from(value) : value;
        }
        for (const stage of ['VERTEX_SHADER', 'FRAGMENT_SHADER']) {
          precision[stage] = {};
          for (const name of ['LOW_FLOAT','MEDIUM_FLOAT','HIGH_FLOAT','LOW_INT','MEDIUM_INT','HIGH_INT']) {
            const p = gl.getShaderPrecisionFormat(gl[stage], gl[name]);
            precision[stage][name] = {rangeMin:p.rangeMin, rangeMax:p.rangeMax, precision:p.precision};
          }
        }
        const extensions = gl.getSupportedExtensions().sort();
        const program = fragment => {
          const vertex = 'attribute vec2 position; varying vec2 uv; void main(){uv=(position+1.0)*0.5;gl_Position=vec4(position,0.0,1.0);}';
          const p = keep('Program', gl.createProgram());
          for (const [type, text] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
            const shader = keep('Shader', gl.createShader(type)); gl.shaderSource(shader, text); gl.compileShader(shader);
            require(gl.getShaderParameter(shader, gl.COMPILE_STATUS), gl.getShaderInfoLog(shader)); gl.attachShader(p, shader);
          }
          gl.linkProgram(p); require(gl.getProgramParameter(p, gl.LINK_STATUS), gl.getProgramInfoLog(p));
          gl.useProgram(p); const location = gl.getAttribLocation(p, 'position');
          gl.bindBuffer(gl.ARRAY_BUFFER, keep('Buffer', gl.createBuffer()));
          gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
          gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
          return p;
        };
        const pixels = () => {
          const out = new Uint8Array(16 * 16 * 4); gl.readPixels(0,0,16,16,gl.RGBA,gl.UNSIGNED_BYTE,out);
          require(gl.getError() === gl.NO_ERROR && !gl.isContextLost(), 'GL draw/readback failed'); return Array.from(out);
        };
        gl.viewport(0,0,16,16); gl.disable(gl.DITHER); gl.disable(gl.BLEND);
        const shaders = [];
        for (const mode of ['mediump', 'highp']) {
          if (precision.FRAGMENT_SHADER[mode === 'highp' ? 'HIGH_FLOAT' : 'MEDIUM_FLOAT'].precision === 0) {
            shaders.push({mode, ...absent('fragment precision unavailable')}); continue;
          }
          program(`precision ${mode} float; varying vec2 uv; void main(){vec2 p=uv*2.0-1.0;
            float a=sin(dot(p,vec2(2.7,4.1)));float b=sqrt(abs(p.x*p.y));
            gl_FragColor=vec4(a*0.4+0.5,b,uv.x*uv.y,1.0);}`);
          gl.drawArrays(gl.TRIANGLES,0,3); const first = pixels(); gl.drawArrays(gl.TRIANGLES,0,3);
          shaders.push({mode, status:'observed', pixels:first, repeat:pixels()});
        }
        const sampler = program('precision mediump float; varying vec2 uv; uniform sampler2D image; void main(){gl_FragColor=texture2D(image,uv);}');
        gl.uniform1i(gl.getUniformLocation(sampler, 'image'), 0);
        const texture = keep('Texture', gl.createTexture()); gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        const formats = [];
        for (const [format, type, data] of [
          ['rgba8', gl.UNSIGNED_BYTE, new Uint8Array([255,0,255,255])],
          ['rgb565', gl.UNSIGNED_SHORT_5_6_5, new Uint16Array([0xf81f])],
          ['rgba4', gl.UNSIGNED_SHORT_4_4_4_4, new Uint16Array([0xf0ff])],
          ['rgb5a1', gl.UNSIGNED_SHORT_5_5_5_1, new Uint16Array([0xf83f])]]) {
          const layout = format === 'rgb565' ? gl.RGB : gl.RGBA;
          gl.texImage2D(gl.TEXTURE_2D,0,layout,1,1,0,layout,type,data);
          gl.drawArrays(gl.TRIANGLES,0,3); formats.push({format, pixels:pixels()});
        }
        // ImageBitmap orientation is fixed at creation; WebGL ignores unpack flip
        // for ImageBitmap inputs. readPixels remains bottom-left, not top-left.
        const bitmap = await createImageBitmap(source, {imageOrientation:'flipY',
          premultiplyAlpha:'none', colorSpaceConversion:'none'});
        let cross;
        try {
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
          gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,bitmap);
          gl.drawArrays(gl.TRIANGLES,0,3);
          cross = {origin:'bottom-left', pixels:pixels(), bitmap:await bitmapPixels(target)};
        } finally { bitmap.close(); }
        let msaa = absent('WebGL2 resolve unavailable');
        if (api === 'webgl2') {
          const supported = Array.from(gl.getInternalformatParameter(gl.RENDERBUFFER, gl.RGBA8, gl.SAMPLES));
          const samples = supported.filter(n => n > 1 && n <= 4).sort((a,b) => b-a)[0];
          if (samples) {
            const readFbo = keep('Framebuffer', gl.createFramebuffer()), drawFbo = keep('Framebuffer', gl.createFramebuffer());
            gl.bindFramebuffer(gl.FRAMEBUFFER, readFbo);
            gl.bindRenderbuffer(gl.RENDERBUFFER, keep('Renderbuffer', gl.createRenderbuffer()));
            gl.renderbufferStorageMultisample(gl.RENDERBUFFER,samples,gl.RGBA8,16,16);
            const actualSamples = gl.getRenderbufferParameter(gl.RENDERBUFFER, gl.RENDERBUFFER_SAMPLES);
            gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.RENDERBUFFER,
              gl.getParameter(gl.RENDERBUFFER_BINDING));
            require(gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE, 'MSAA framebuffer');
            gl.clearColor(32/255,96/255,160/255,1); gl.clear(gl.COLOR_BUFFER_BIT);
            gl.bindFramebuffer(gl.FRAMEBUFFER, drawFbo);
            gl.bindRenderbuffer(gl.RENDERBUFFER, keep('Renderbuffer', gl.createRenderbuffer()));
            gl.renderbufferStorage(gl.RENDERBUFFER,gl.RGBA8,16,16);
            gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.RENDERBUFFER,
              gl.getParameter(gl.RENDERBUFFER_BINDING));
            require(gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE, 'resolve framebuffer');
            gl.bindFramebuffer(gl.READ_FRAMEBUFFER,readFbo); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER,drawFbo);
            gl.blitFramebuffer(0,0,16,16,0,0,16,16,gl.COLOR_BUFFER_BIT,gl.NEAREST);
            gl.bindFramebuffer(gl.FRAMEBUFFER,drawFbo);
            msaa = {status:'observed', supported, samples, actualSamples, pixels:pixels()};
          } else msaa = {supported, ...absent('no supported multisample count <= 4')};
        }
        return {identity:{vendor:ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
          renderer:ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
          version:gl.getParameter(gl.VERSION), shadingLanguage:gl.getParameter(gl.SHADING_LANGUAGE_VERSION)},
          attributes:gl.getContextAttributes(), limits, precision, extensions, shaders, formats, cross, msaa};
      } finally {
        for (const [kind, resource] of resources.reverse()) gl['delete' + kind](resource);
        gl.getExtension('WEBGL_lose_context')?.loseContext();
      }
    });
  }
  output.webgpu = !navigator.gpu ? absent('WebGPU unavailable') : await capture(async () => {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return {adapter:null};
    const info = adapter.info, device = await adapter.requestDevice();
    const resources = [];
    const keep = obj => { resources.push(obj); return obj; };
    try {
      device.pushErrorScope('validation');
      const input = keep(device.createTexture({size:[16,16], format:'rgba8unorm',
        usage:GPUTextureUsage.COPY_DST | GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.RENDER_ATTACHMENT}));
      const bitmap = await createImageBitmap(source);
      try { device.queue.copyExternalImageToTexture({source:bitmap}, {texture:input}, [16,16]); }
      finally { bitmap.close(); }
      const module = device.createShaderModule({code:`
        @group(0) @binding(0) var src: texture_2d<f32>;
        @vertex fn vs(@builtin(vertex_index) i:u32) -> @builtin(position) vec4<f32> {
          var p = array<vec2<f32>,3>(vec2(-1.0,-1.0),vec2(3.0,-1.0),vec2(-1.0,3.0));
          return vec4(p[i],0.0,1.0);
        }
        @fragment fn fs(@builtin(position) p:vec4<f32>) -> @location(0) vec4<f32> {
          return textureLoad(src,vec2<i32>(p.xy),0);
        }`});
      const rows = [];
      for (const format of ['rgba8unorm','bgra8unorm']) for (const samples of [1,4]) {
        const texture = keep(device.createTexture({size:[16,16], format,
          usage:GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC}));
        const multisample = samples === 1 ? texture : keep(device.createTexture({size:[16,16], format,
          sampleCount:samples, usage:GPUTextureUsage.RENDER_ATTACHMENT}));
        const pipeline = await device.createRenderPipelineAsync({layout:'auto',
          vertex:{module,entryPoint:'vs'}, fragment:{module,entryPoint:'fs',targets:[{format}]},
          multisample:{count:samples}});
        const group = device.createBindGroup({layout:pipeline.getBindGroupLayout(0),
          entries:[{binding:0, resource:input.createView()}]});
        const buffer = keep(device.createBuffer({size:256*16, usage:GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ}));
        const encoder = device.createCommandEncoder();
        const color = {view:multisample.createView(), loadOp:'clear', storeOp:'store', clearValue:[0,0,0,1]};
        if (samples !== 1) color.resolveTarget = texture.createView();
        const pass = encoder.beginRenderPass({colorAttachments:[color]});
        pass.setPipeline(pipeline); pass.setBindGroup(0,group); pass.draw(3); pass.end();
        encoder.copyTextureToBuffer({texture}, {buffer,bytesPerRow:256}, [16,16]);
        device.queue.submit([encoder.finish()]); await buffer.mapAsync(GPUMapMode.READ);
        const raw = new Uint8Array(buffer.getMappedRange()), pixels = [];
        for (let y = 0; y < 16; y++) pixels.push(...raw.subarray(y*256,y*256+64));
        buffer.unmap(); rows.push({format,samples,pixels,origin:'top-left'});
      }
      const error = await device.popErrorScope(); require(!error, error?.message);
      return {identity:{vendor:info.vendor, architecture:info.architecture, device:info.device,
        description:info.description, isFallbackAdapter:info.isFallbackAdapter ?? null},
        features:[...adapter.features].sort(), rows};
    } finally { for (const resource of resources) resource.destroy(); device.destroy(); }
  });
  return output;
};

// Compression bounds transport size; Python rechecks length, hash and every
// pixel contract after bounded decompression. A success label alone never passes.
globalThis.chromixPackedRenderProbe = async () => {
  const value = {version:2, chain:await canvasChainProbe({taint:false}),
    gpuBackend:await chromixGpuBackendProbe(),
    scenes:await chromixSceneProbe(), integration:typeof document === 'undefined'
      ? {status:'not_applicable', reason:'DOM ownership tests run in window/iframe'}
      : await chromixRenderProbe()};
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  if (bytes.length > 8 * 1024 * 1024) throw Error('render evidence exceeds 8 MiB');
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    v => v.toString(16).padStart(2,'0')).join('');
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
  const packed = new Uint8Array(await new Response(stream).arrayBuffer());
  let binary = '';
  for (let i = 0; i < packed.length; i += 4096) binary += String.fromCharCode(...packed.subarray(i,i+4096));
  return {encoding:'gzip-json-v1', bytes:bytes.length, sha256, data:btoa(binary)};
};
