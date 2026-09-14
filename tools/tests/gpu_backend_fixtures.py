"""Invented oracle inputs. These are NOT measured hardware or corpus records."""
from copy import deepcopy
import struct

from chromix import _gpu_backend as gpu

GL_ID = {'vendor':'unit-fixture','renderer':'not a physical GPU',
         'version':'WebGL fixture','shadingLanguage':'GLSL fixture'}
GPU_ID = {'vendor':'unit-fixture','architecture':'fixture','device':'',
          'description':'not a physical adapter','isFallbackAdapter':False}


def system_fixture():
    return {'status':'observed','value':{'source':'CDP.SystemInfo.getInfo','gpu':{
        'devices':[{'vendorId':4318,'deviceId':4660,'vendorString':'unit fixture',
                    'deviceString':'invented test device','driverVendor':'fixture','driverVersion':'1.2.3'}],
        'featureStatus':{'gpu_compositing':'enabled','webgl':'enabled','webgpu':'enabled'},
        'auxAttributes':{'glRenderer':'invented test renderer','skiaBackendType':'GaneshGL',
                         'processCrashCount':0,'sandboxed':True}}}}


def readback(format='rgba8unorm',color=(0.25,0.5,0.75,1)):
    raw = [165]*1024
    if format == 'rgba16float': pixel = list(struct.pack('<4e',*color))
    elif format == 'rgba32float': pixel = list(struct.pack('<4f',*color))
    else:
        pixel = [round(v*255) for v in color]
        if format == 'bgra8unorm': pixel = [pixel[i] for i in (2,1,0,3)]
    for y in range(gpu.H): raw[256+y*256:256+y*256+len(pixel)*gpu.W] = pixel*gpu.W
    return {'format':format,'width':gpu.W,'height':gpu.H,'bytes':raw,'mappedBytesAfterUnmap':0}


def backend_fixture(scope='window'):
    result = {'version':1,'width':gpu.W,'height':gpu.H,'canvas':[],'webgl':{},'webgpu':[]}
    for request in gpu.canvas_requests(scope):
        floating, alpha = request['colorType'] == 'float16', request['alpha']
        color = ([1,0,0,0.5] if alpha else [0.5,0,0,1]) if floating else ([255,0,0,128] if alpha else [128,0,0,255])
        def data(width,color):
            return {'width':width,'height':gpu.H,'colorSpace':request['colorSpace'],
                    'pixelFormat':'rgba-float16' if floating else 'rgba-unorm8',
                    'type':'Float16Array' if floating else 'Uint8ClampedArray','pixels':color*(width*gpu.H)}
        before = data(gpu.W,color)
        result['canvas'].append({'request':request,'status':'observed','value':{
            'supported':True,'attributes':{k:v for k,v in request.items() if k != 'kind'},
            'before':before,'after':deepcopy(before),'bitmapPixels':color*(gpu.W*gpu.H),
            'resized':data(1,[0,0,0,0 if alpha else 1 if floating else 255]),'bitmapClosed':[0,0]}})
    pixels = [64,128,191,255]*(gpu.W*gpu.H)
    for api in ('webgl','webgl2'):
        client, pbo = [165]*48,[165]*128
        for y in range(gpu.H):
            client[8+y*16:8+y*16+12] = [64,128,191,255]*gpu.W
            pbo[44+y*32:44+y*32+12] = [64,128,191,255]*gpu.W
        formats = ['rgba8','rgba16f','rgba32f'] if api == 'webgl2' else ['rgba8']
        result['webgl'][api] = {'status':'observed','value':{
            'beforeIdentity':deepcopy(GL_ID),'extensions':['EXT_color_buffer_float','WEBGL_lose_context'],
            'formats':[{'format':f,'status':'observed','framebufferStatus':36053,'error':0,
                        'pixels':pixels[:] if f == 'rgba8' else [0.25,0.5,0.75,1]*(gpu.W*gpu.H)} for f in formats],
            'colorSpaces':[{'colorSpace':c,'status':'observed','actual':c,'pixels':pixels[:]} for c in ('srgb','display-p3')],
            'layout':{'client':{'bytes':client,'error':0},'pbo':{'bytes':pbo,'error':0} if api == 'webgl2' else None},
            'before':pixels[:], 'lifecycle':{'status':'observed','event':'webglcontextlost','restored':'webglcontextrestored',
                'isLost':True,'isLostAfter':False,'lostError':37442,'lostPixels':[165]*(gpu.W*gpu.H*4),
                'oldTextureBefore':True,'oldTextureAfter':False,'afterIdentity':deepcopy(GL_ID),'after':pixels[:]}}}
    for request in gpu.REQUESTS:
        identity = {**GPU_ID,'isFallbackAdapter':request == 'fallback'}
        formats = [(f,n) for f in gpu.FORMATS for n in ((1,) if f == 'rgba32float' else (1,4))] if request == 'default' else [('rgba8unorm',1)]
        value = {'available':True,'identity':identity,'features':[],
                 'formats':[dict(samples=n,**readback(f)) for f,n in formats],
                 'external':[],'canvases':[],'uncaptured':[],
                 'lifecycle':{'reason':'destroyed','staleMapError':'AbortError','recreated':[17,34,51,68],
                              'recreatedMapBytesAfterUnmap':0,'afterIdentity':deepcopy(identity)}}
        if request == 'default':
            for color in ('srgb','display-p3'):
                for alpha in (False,True):
                    value['external'].append({'colorSpace':color,'premultipliedAlpha':alpha,'status':'observed',
                        'before':[255,0,0,128]*(gpu.W*gpu.H),'after':[255,0,0,128]*(gpu.W*gpu.H),'bitmapClosed':[0,0],
                        'readback':readback(color=(128/255 if alpha else 1,0,0,128/255))})
                for alpha in ('opaque','premultiplied'):
                    value['canvases'].append({'colorSpace':color,'alphaMode':alpha,'format':'rgba8unorm',
                        'readback':readback(),'resized':[1,gpu.H],'unconfiguredError':'InvalidStateError'})
        result['webgpu'].append({'request':request,'status':'observed','value':value})
    return result
