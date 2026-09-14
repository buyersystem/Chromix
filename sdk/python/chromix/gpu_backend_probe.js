/* Native resource operations, not renderer-name emulation or hardware attestation.
 * Every allocation is small; every asynchronous GPU operation is bounded.
 * Keep raw pixels, padding and lifecycle outcomes for the independent validator.
 */
globalThis.chromixGpuBackendProbe = async () => {
  const W = 3, H = 2, sentinel = 165;
  const absent = reason => ({status:'unavailable', reason});
  const require = (ok, why) => { if (!ok) throw Error(why); };
  const timeout = async (promise, ms = 8000) => {
    let timer;
    try { return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Error('GPU backend operation timed out')), ms);
    })]); } finally { clearTimeout(timer); }
  };
  const capture = async fn => {
    try { return {status:'observed', value:await fn()}; }
    catch (e) { return {status:'error', reason:String(e).slice(0, 2048)}; }
  };
  const make = (kind = 'offscreen') => kind === 'html'
    ? Object.assign(document.createElement('canvas'), {width:W, height:H})
    : new OffscreenCanvas(W, H);
  const glIdentity = gl => {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return {vendor:ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
      renderer:ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
      version:gl.getParameter(gl.VERSION), shadingLanguage:gl.getParameter(gl.SHADING_LANGUAGE_VERSION)};
  };
  const gpuIdentity = adapter => {
    const i = adapter.info;
    return {vendor:i.vendor, architecture:i.architecture, device:i.device,
      description:i.description, isFallbackAdapter:i.isFallbackAdapter ?? null};
  };
  const result = {version:1, width:W, height:H, canvas:[], webgl:{}, webgpu:[]};

  // Native CPU/GPU preferences are requests, not a proof of physical rendering.
  const kinds = typeof document === 'undefined' ? ['offscreen'] : ['html','offscreen'];
  for (const kind of kinds) for (const colorSpace of ['srgb','display-p3'])
    for (const colorType of ['unorm8','float16']) for (const alpha of [true,false])
      for (const willReadFrequently of [false,true]) {
        const request = {kind,colorSpace,colorType,alpha,willReadFrequently};
        const row = await capture(async () => {
          const canvas = make(kind), ctx = canvas.getContext('2d', request);
          require(ctx, '2D context missing');
          const attributes = ctx.getContextAttributes();
          if (attributes.colorSpace !== colorSpace || attributes.colorType !== colorType)
            return {supported:false, attributes, reason:'requested color space/type not provided'};
          ctx.fillStyle = colorSpace === 'srgb' ? 'rgba(255,0,0,0.5)' : 'color(display-p3 1 0 0 / 0.5)';
          ctx.fillRect(0,0,W,H);
          const pixelFormat = colorType === 'float16' ? 'rgba-float16' : 'rgba-unorm8';
          const read = () => {
            const data = ctx.getImageData(0,0,canvas.width,canvas.height,{colorSpace,pixelFormat});
            return {width:data.width,height:data.height,colorSpace:data.colorSpace,
              pixelFormat:data.pixelFormat, type:data.data.constructor.name, pixels:Array.from(data.data)};
          };
          const before = read();
          const bitmap = await createImageBitmap(canvas);
          let bitmapPixels;
          try {
            const target = make(), copy = target.getContext('2d',{colorSpace,colorType,alpha});
            copy.drawImage(bitmap,0,0);
            const image = copy.getImageData(0,0,W,H,{colorSpace,pixelFormat});
            bitmapPixels = Array.from(image.data);
          } finally { bitmap.close(); }
          const after = read();
          canvas.width = 1; const resized = read();
          return {supported:true,attributes,before,after,bitmapPixels,resized,
            bitmapClosed:[bitmap.width,bitmap.height]};
        });
        result.canvas.push({request,...row});
      }

  for (const api of ['webgl','webgl2']) {
    const canvas = make();
    const gl = canvas.getContext(api,{alpha:true,antialias:false,preserveDrawingBuffer:true});
    if (!gl) { result.webgl[api] = absent('native context unavailable'); continue; }
    result.webgl[api] = await capture(async () => {
      const beforeIdentity = glIdentity(gl), extensions = gl.getSupportedExtensions().sort(), formats = [], colorSpaces = [];
      const clear = () => { gl.clearColor(0.25,0.5,0.75,1); gl.clear(gl.COLOR_BUFFER_BIT); };
      const readDefault = () => {
        gl.bindFramebuffer(gl.FRAMEBUFFER,null); gl.viewport(0,0,W,H); gl.disable(gl.DITHER); clear();
        const pixels = new Uint8Array(W*H*4); gl.readPixels(0,0,W,H,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
        require(gl.getError() === gl.NO_ERROR, 'default framebuffer readback failed');
        return Array.from(pixels);
      };
      try {
        for (const colorSpace of ['srgb','display-p3']) {
          if (!('drawingBufferColorSpace' in gl)) {
            colorSpaces.push({colorSpace,...absent('drawingBufferColorSpace unavailable')}); continue;
          }
          gl.drawingBufferColorSpace = colorSpace;
          colorSpaces.push({colorSpace,status:'observed',actual:gl.drawingBufferColorSpace,pixels:readDefault()});
        }
        if ('drawingBufferColorSpace' in gl) gl.drawingBufferColorSpace = 'srgb';
        for (const format of api === 'webgl2' ? ['rgba8','rgba16f','rgba32f'] : ['rgba8']) {
          if (format !== 'rgba8' && !gl.getExtension('EXT_color_buffer_float')) {
            formats.push({format,...absent('EXT_color_buffer_float unavailable')}); continue;
          }
          const texture = gl.createTexture(), fbo = gl.createFramebuffer();
          try {
            gl.bindTexture(gl.TEXTURE_2D,texture);
            gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
            const floating = format !== 'rgba8';
            const internal = api === 'webgl' ? gl.RGBA :
              format === 'rgba8' ? gl.RGBA8 : format === 'rgba16f' ? gl.RGBA16F : gl.RGBA32F;
            gl.texImage2D(gl.TEXTURE_2D,0,internal,W,H,0,gl.RGBA,floating ? gl.FLOAT : gl.UNSIGNED_BYTE,null);
            gl.bindFramebuffer(gl.FRAMEBUFFER,fbo);
            gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,texture,0);
            const framebufferStatus = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
            require(framebufferStatus === gl.FRAMEBUFFER_COMPLETE, 'advertised format is not renderable: '+format);
            gl.viewport(0,0,W,H); gl.disable(gl.DITHER); clear();
            const pixels = floating ? new Float32Array(W*H*4) : new Uint8Array(W*H*4);
            gl.readPixels(0,0,W,H,gl.RGBA,floating ? gl.FLOAT : gl.UNSIGNED_BYTE,pixels);
            formats.push({format,status:'observed',framebufferStatus,pixels:Array.from(pixels),error:gl.getError()});
          } finally { gl.bindFramebuffer(gl.FRAMEBUFFER,null); gl.deleteFramebuffer(fbo); gl.deleteTexture(texture); }
        }
        const before = readDefault();
        gl.pixelStorei(gl.PACK_ALIGNMENT,8);
        const packed = new Uint8Array(48).fill(sentinel);
        gl.readPixels(0,0,W,H,gl.RGBA,gl.UNSIGNED_BYTE,packed.subarray(8,36));
        const layout = {client:{bytes:Array.from(packed),error:gl.getError()},pbo:null};
        if (api === 'webgl2') {
          const buffer = gl.createBuffer();
          try {
            gl.bindBuffer(gl.PIXEL_PACK_BUFFER,buffer);
            gl.bufferData(gl.PIXEL_PACK_BUFFER,new Uint8Array(128).fill(sentinel),gl.STREAM_READ);
            gl.pixelStorei(gl.PACK_ROW_LENGTH,7); gl.pixelStorei(gl.PACK_SKIP_ROWS,1); gl.pixelStorei(gl.PACK_SKIP_PIXELS,1);
            gl.readPixels(0,0,W,H,gl.RGBA,gl.UNSIGNED_BYTE,8);
            const bytes = new Uint8Array(128); gl.getBufferSubData(gl.PIXEL_PACK_BUFFER,0,bytes);
            layout.pbo = {bytes:Array.from(bytes),error:gl.getError()};
          } finally {
            gl.bindBuffer(gl.PIXEL_PACK_BUFFER,null); gl.deleteBuffer(buffer);
            gl.pixelStorei(gl.PACK_ROW_LENGTH,0); gl.pixelStorei(gl.PACK_SKIP_ROWS,0); gl.pixelStorei(gl.PACK_SKIP_PIXELS,0);
          }
        }
        gl.pixelStorei(gl.PACK_ALIGNMENT,4);
        const extension = gl.getExtension('WEBGL_lose_context');
        if (!extension) return {beforeIdentity,extensions,formats,colorSpaces,layout,before,
          lifecycle:absent('WEBGL_lose_context unavailable')};
        const oldTexture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D,oldTexture);
        const oldTextureBefore = gl.isTexture(oldTexture);
        const lostEvent = new Promise(resolve => canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();resolve(e.type);},{once:true}));
        extension.loseContext(); const event = await timeout(lostEvent);
        const isLost = gl.isContextLost(), lostError = gl.getError();
        const lostPixels = new Uint8Array(W*H*4).fill(sentinel);
        gl.readPixels(0,0,W,H,gl.RGBA,gl.UNSIGNED_BYTE,lostPixels);
        await new Promise(resolve=>setTimeout(resolve,0));
        const restoredEvent = new Promise(resolve=>canvas.addEventListener('webglcontextrestored',e=>resolve(e.type),{once:true}));
        extension.restoreContext(); const restored = await timeout(restoredEvent);
        const oldTextureAfter = gl.isTexture(oldTexture);
        const afterIdentity = glIdentity(gl), after = readDefault();
        return {beforeIdentity,extensions,formats,colorSpaces,layout,before,lifecycle:{status:'observed',event,restored,
          isLost,lostError,lostPixels:Array.from(lostPixels),oldTextureBefore,oldTextureAfter,
          isLostAfter:gl.isContextLost(),afterIdentity,after}};
      } finally { gl.getExtension('WEBGL_lose_context')?.loseContext(); }
    });
  }

  const requests = [['default',{}],['low-power',{powerPreference:'low-power'}],
    ['high-performance',{powerPreference:'high-performance'}],['fallback',{forceFallbackAdapter:true}]];
  for (const [request,options] of requests) {
    if (!navigator.gpu) { result.webgpu.push({request,...absent('WebGPU unavailable')}); continue; }
    const row = await capture(async () => {
      const adapter = await timeout(navigator.gpu.requestAdapter(options));
      if (!adapter) return {available:false,reason:'native requestAdapter returned null'};
      const identity = gpuIdentity(adapter), features = [...adapter.features].sort();
      const device = await timeout(adapter.requestDevice()), resources = [];
      const keep = value => { resources.push(value); return value; };
      const uncaptured = [];
      device.addEventListener('uncapturederror', e=>{e.preventDefault();uncaptured.push(String(e.error.message).slice(0,1024));});
      const readTexture = async (texture,format,width=W,height=H) => {
        const buffer = keep(device.createBuffer({size:1024,mappedAtCreation:true,
          usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ}));
        new Uint8Array(buffer.getMappedRange()).fill(sentinel); buffer.unmap();
        const encoder = device.createCommandEncoder();
        encoder.copyTextureToBuffer({texture},{buffer,offset:256,bytesPerRow:256,rowsPerImage:height},[width,height]);
        device.queue.submit([encoder.finish()]); await timeout(buffer.mapAsync(GPUMapMode.READ));
        const mapped = buffer.getMappedRange(), bytes = Array.from(new Uint8Array(mapped));
        buffer.unmap();
        return {format,width,height,bytes,mappedBytesAfterUnmap:mapped.byteLength};
      };
      const render = async (format,samples) => {
        const texture = keep(device.createTexture({size:[W,H],format,
          usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.COPY_SRC}));
        const multisample = samples === 1 ? texture : keep(device.createTexture({size:[W,H],format,sampleCount:samples,
          usage:GPUTextureUsage.RENDER_ATTACHMENT}));
        const encoder = device.createCommandEncoder();
        const color = {view:multisample.createView(),loadOp:'clear',storeOp:'store',clearValue:[0.25,0.5,0.75,1]};
        if (samples !== 1) color.resolveTarget = texture.createView();
        encoder.beginRenderPass({colorAttachments:[color]}).end(); device.queue.submit([encoder.finish()]);
        return {samples,...await readTexture(texture,format)};
      };
      try {
        device.pushErrorScope('validation');
        const formats = [];
        for (const format of request === 'default' ? ['rgba8unorm','bgra8unorm','rgba16float','rgba32float'] : ['rgba8unorm'])
          for (const samples of request === 'default' && format !== 'rgba32float' ? [1,4] : [1])
            formats.push(await render(format,samples));
        const external = [], canvases = [];
        if (request === 'default') {
          for (const colorSpace of ['srgb','display-p3']) for (const premultipliedAlpha of [false,true]) {
            const source = make(), ctx = source.getContext('2d',{colorSpace});
            require(ctx, 'external-image source context missing');
            if (ctx.getContextAttributes().colorSpace !== colorSpace) {
              external.push({colorSpace,premultipliedAlpha,...absent('source color space unavailable')}); continue;
            }
            const pixels = new Uint8ClampedArray(W*H*4);
            for (let i=0;i<pixels.length;i+=4) pixels.set([255,0,0,128],i);
            ctx.putImageData(new ImageData(pixels,W,H,{colorSpace}),0,0);
            const before = Array.from(ctx.getImageData(0,0,W,H,{colorSpace}).data);
            const bitmap = await createImageBitmap(source,{colorSpaceConversion:'none'});
            const texture = keep(device.createTexture({size:[W,H],format:'rgba8unorm',
              usage:GPUTextureUsage.COPY_DST|GPUTextureUsage.COPY_SRC|GPUTextureUsage.RENDER_ATTACHMENT}));
            try { device.queue.copyExternalImageToTexture({source:bitmap},{texture,colorSpace,premultipliedAlpha},[W,H]); }
            finally { bitmap.close(); }
            external.push({colorSpace,premultipliedAlpha,status:'observed',before,
              after:Array.from(ctx.getImageData(0,0,W,H,{colorSpace}).data),
              bitmapClosed:[bitmap.width,bitmap.height],readback:await readTexture(texture,'rgba8unorm')});
          }
          for (const colorSpace of ['srgb','display-p3']) for (const alphaMode of ['opaque','premultiplied']) {
            const canvas = make(), context = canvas.getContext('webgpu');
            require(context, 'WebGPU canvas context missing');
            const format = navigator.gpu.getPreferredCanvasFormat();
            context.configure({device,format,colorSpace,alphaMode,usage:GPUTextureUsage.RENDER_ATTACHMENT|GPUTextureUsage.COPY_SRC});
            const texture = context.getCurrentTexture();
            const encoder = device.createCommandEncoder();
            encoder.beginRenderPass({colorAttachments:[{view:texture.createView(),loadOp:'clear',storeOp:'store',
              clearValue:[0.25,0.5,0.75,1]}]}).end(); device.queue.submit([encoder.finish()]);
            const readback = await readTexture(texture,format);
            canvas.width = 1; const resized = context.getCurrentTexture();
            const size = [resized.width,resized.height];
            context.unconfigure(); let unconfiguredError = null;
            try { context.getCurrentTexture(); } catch (e) { unconfiguredError = e.name; }
            canvases.push({colorSpace,alphaMode,format,readback,resized:size,unconfiguredError});
          }
        }
        const validation = await timeout(device.popErrorScope());
        require(!validation, validation?.message);
        const stale = keep(device.createBuffer({size:16,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ}));
        const lostPromise = device.lost; device.destroy(); const lost = await timeout(lostPromise);
        let staleMapError = null;
        try { await timeout(stale.mapAsync(GPUMapMode.READ)); stale.unmap(); }
        catch (e) { staleMapError = e.name; }
        const freshAdapter = await timeout(navigator.gpu.requestAdapter(options));
        require(freshAdapter, 'adapter unavailable after explicit device destruction');
        const freshDevice = await timeout(freshAdapter.requestDevice());
        let recreated, recreatedMapBytesAfterUnmap;
        try {
          const buffer = freshDevice.createBuffer({size:16,mappedAtCreation:true,usage:GPUBufferUsage.COPY_SRC});
          const view = new Uint32Array(buffer.getMappedRange()); view.set([17,34,51,68]);
          buffer.unmap();
          const copy = freshDevice.createBuffer({size:16,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});
          try {
            const commands = freshDevice.createCommandEncoder(); commands.copyBufferToBuffer(buffer,0,copy,0,16);
            freshDevice.queue.submit([commands.finish()]); await timeout(copy.mapAsync(GPUMapMode.READ));
            const mapped = copy.getMappedRange(); recreated = Array.from(new Uint32Array(mapped));
            copy.unmap(); recreatedMapBytesAfterUnmap = mapped.byteLength;
          } finally { buffer.destroy(); copy.destroy(); }
        } finally { freshDevice.destroy(); }
        return {available:true,identity,features,formats,external,canvases,uncaptured,
          lifecycle:{reason:lost.reason,staleMapError,recreated,recreatedMapBytesAfterUnmap,afterIdentity:gpuIdentity(freshAdapter)}};
      } finally { for (const resource of resources) resource.destroy(); device.destroy(); }
    });
    result.webgpu.push({request,...row});
  }
  return result;
};
