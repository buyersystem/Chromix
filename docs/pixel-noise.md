# Canvas / WebGL 显式像素噪声

此页描述当前源码与 SDK 的新配置，**需要重新编译浏览器**。已发布的 `154.0.8037.97` 或下载目录中的 `.57` 二进制不会因 SDK 更新而自动获得该补丁。源码/算法测试与原生浏览器验收分别记录。

## 开启方式

```python
from chromix import launch

browser = launch(args=[
    "--fingerprint=42",
    "--fingerprint-pixel-noise=seeded",
])
try:
    page = browser.new_page()
    page.goto("http://127.0.0.1:8000")
finally:
    browser.close()
```

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch({
  args: ['--fingerprint=42', '--fingerprint-pixel-noise=seeded'],
});
try {
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:8000');
} finally {
  await browser.close();
}
```

使用本地已核验的二进制时设置 `CLOAKBROWSER_BINARY_PATH`。本页本地页面示例需要调用者自行启动测试服务器。

- `--fingerprint-pixel-noise=seeded` 显式请求像素扰动；内部别名为 `--uxr-pixel-noise`。
- 未指定时保持原有 native 默认行为，不自动打开 synthetic、远程 Canvas bridge 或 GPU 能力覆盖。
- SDK 普通启动会生成 seed；显式 seed 使用非零十进制 uint64。持久化 profile 复用既有 seed。
- 关闭 SDK 默认参数后，必须显式提供有效 seed。模式只接受 `native`、`seeded`，非法配置启动前拒绝。
- `--fingerprint-noise=false` 和 `--fingerprint=off` 优先关闭像素扰动。
- 显式 `native` 表示不请求这项新扰动；旧 synthetic 测试开关仍按原有规则工作。完全原生对照请同时避免 synthetic 参数。

## “随机”的含义

seed 决定伪随机扰动，而非每次读回重新抽样。同 seed、同原始像素和相同绝对坐标产生相同结果；换 seed 可改变输出，但不保证所有 seed 两两无碰撞。跨硬件、驱动、字体和浏览器版本仍可能有原生像素差异。

RGB 通道采用有界的 ±1 扰动并夹紧至 0–255；保留 alpha，透明 Canvas 像素保持原样。Canvas 使用私有副本，不改变应用绘制表面或共享 encoder 输入。

## 覆盖范围

| 路径 | 新模式行为 |
|---|---|
| Canvas 2D `getImageData`，RGBA/BGRA 8-bit | 按绝对坐标、seed、通道及原始值加噪；裁剪与完整读回采用同一规则 |
| HTML Canvas PNG / Blob 及 OffscreenCanvas export | 复用同一像素变换，在准备编码缓冲区时处理一次 |
| WebGL Canvas 图像 export | 经过共享图像缓冲区的受支持 8-bit export 路径同样受策略影响 |
| WebGL1/2 TypedArray `readPixels` | 仅对明确支持的、不透明 default framebuffer 的 in-bounds RGBA/UNSIGNED_BYTE 读回处理；遵守 pack 布局和目标偏移 |
| WebGL 透明 context、用户 FBO、PBO、FLOAT、shared destination、裁剪越界等 | 保留原生读回，本次不宣称全覆盖 |
| WebGPU | 保持原生，此项不修改 WebGPU 像素、适配器或能力 |

WebGL 使用最多 16 MiB 的暂存读回，检查每个不透明 alpha 字节后才提交 RGB 扰动；不读取/消耗页面 GL 错误队列。没有可靠确认的转移不向调用者写入噪声。其他读回保留原来的 GL 路径。此受限实现不能宣称 TypedArray / PBO / FLOAT 的所有路径互相等价。

某些检测页创建透明 WebGL context 或通过不支持的类型读回，所以开启开关并不意味着它的每个 WebGL 标识都应改变。需要按实际读回 API 验证。

## GPU 型号展示是独立配置

WebGL vendor/renderer 展示使用既有的明确 compatibility 模式，见 [GPU 身份配置](gpu-identity.md)。它不会更换物理 GPU，也不修改纹理上限、扩展或 shader precision。WebGPU 的普通公开身份仍为原生。

像素噪声与 GPU 后端策略分开：显式 pixel-noise 可与真实 GPU 身份并用，也可与允许的 compatibility 字符串配置并用。仅开启 pixel-noise 不自动切换 backend。

## 验证新二进制

依赖 Python Playwright；Canvas PNG 对照另外需要 Pillow。以下工具只使用指定的本地浏览器和本地页面，不下载浏览器，不访问支付服务。

```bash
python3 tools/pixel_noise_diagnostic.py \
  --browser /absolute/path/chrome \
  --output /tmp/chromix-pixel-noise.json
```

这个测试在 Python/Pillow 中解码 export PNG，避免在浏览器中再调用 `getImageData` 对已加噪图像处理第二次。检查项包含：同 seed 重复/重启、不同 seed、crop、PNG/Blob一致性、透明/alpha、幅度上限、off/禁噪。

WebGL 的受限路径由 `tools/tests/test_webgl_seeded_noise.py` 中的 C++ 代码片段测试覆盖 pack、偏移、边界及失败保护；完整 Chromium 构建后的实际 GPU 验证仍需要另行执行，不能用这些测试替代。

```bash
python3 tools/webgl_seeded_noise_diagnostic.py \
  --browser /absolute/path/chrome \
  --output /tmp/chromix-webgl-noise.json
python3 tools/gpu_identity_diagnostic.py --help
```

GPU 诊断单独比较 identity、limits、extensions、shader precision、context loss/restore 和 WebGPU；其结果不能代替像素噪声诊断。BrowserScan 的总分同样不是补丁验收标准，应对比其原始 Canvas / WebGL 数值及真实读回路径。
