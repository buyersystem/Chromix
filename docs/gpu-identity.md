# WebGL / GPU identity：显式配置与本地诊断

## 支持范围与最小 opt-in

当前源码已经支持下面三个公开启动参数，不需要新增 SDK 顶层对象或自动切换模式：

| 参数 | 实际含义 |
|---|---|
| `--fingerprint-gpu-backend=native` | 普通启动的默认策略；忽略 WebGL 自定义名称，保留原生身份。 |
| `--fingerprint-gpu-backend=compatibility` | 显式选择既有兼容策略；允许符合条件的 WebGL context 展示自定义名称。不是独立的 identity-only 后端。 |
| `--fingerprint-gpu-vendor=...` | compatibility 下 `WEBGL_debug_renderer_info.UNMASKED_VENDOR_WEBGL` 的展示字符串。 |
| `--fingerprint-gpu-renderer=...` | compatibility 下 `WEBGL_debug_renderer_info.UNMASKED_RENDERER_WEBGL` 的展示字符串。 |

**只传 vendor/renderer、不传 backend，普通启动仍是 native，不会自动开启伪装。**
Python 与 Node SDK 的 `args` 是现有明确入口；两者归一化校验 backend 为
`native` / `compatibility`，不会因为看到名称就注入 compatibility。名称参数保持原样。
这里没有实现 `gpu_identity` / `gpuIdentity`、`gpu_vendor` / `gpuVendor`，也没有
实现 `--fingerprint-gpu-identity=native|custom`；不要把这些建议名当作已支持 API。

建议**成对指定非空 vendor、renderer**。历史实现允许单边输入，但已知厂商可能补齐
模板中的另一项，未知单边名称可能留下空的另一项；不建议依赖这种行为。参数名是
`renderer`，不是 `render`。字符串含空格时 shell 需引用，SDK 则每个完整参数占一个元素。

### 明确不改变的东西

- 普通启动 native 默认值不变；`--fingerprint=off`、`--uxr-webgl-real`、
  `--disable-gpu-fingerprint` 等禁用路径仍优先，不可用来同时要求自定义身份。
- 软件 context guard 保留：SwiftShader、D3D11 WARP、ANGLE Null、Microsoft Basic
  Render Driver，以及 native renderer 中可识别的 Mesa/软件渲染路径，不呈现自定义名称。
  `WEBGL_debug_renderer_info` 不可用时也不补造扩展。
- 修改的是 WebGL **unmasked 字符串展示**，不是物理显卡、ANGLE/Dawn 后端、驱动或能力。
  普通公开模式不伪造 GL limits、extensions、shader precision；不要开启
  `--uxr-synthetic-device-tests` 或私有能力覆盖来让一组名称看起来“更像”硬件。
- 公共 WebGPU 保留完整原生 Dawn adapter identity、features、limits；WebGL 自定义名
  不应复制进 WebGPU。两 API 本来就可能选择不同适配器，不要求名称相同。
- 本功能不承诺 Canvas/WebGL 像素唯一性，也不把 identity 与 pixel-noise 开关绑定。
  compatibility 是共享的既有兼容策略，可能影响其他旧路径；下方示例不注入 seed，
  不开启噪声、Bridge 或 synthetic 模式。需要原生后端契约时继续使用 native。
- 没有站点特判、Stripe 绕过、软件后端绕过或 `--ignore-gpu-blocklist`。

## 实际 SDK 用法

必须使用**包含对应 native patches 的新构建浏览器**。仅升级 SDK 或修改本仓库脚本
不能给已安装的旧二进制增加能力；stock Chromium 通常会忽略这些自定义参数。
以下名称刻意是诊断标签，不代表真实硬件。换成自己的展示字符串即可。

### Python / Playwright

从仓库安装 `python -m pip install './sdk/python[playwright]'`，并将环境变量
`CLOAKBROWSER_BINARY_PATH` 指向用户指定的匹配构建可执行文件，避免 SDK 下载其他版本。
例如 macOS bundle 内的 `Chromium.app/Contents/MacOS/Chromium`，Windows 用
`chrome.exe` 而不是 `chromix.cmd`。Python SDK 不接受用 `launch(executable_path=...)`
覆盖其内部路径；用该环境变量。

```python
from chromix import launch

browser = launch(
    stealth_args=False,
    args=[
        "--fingerprint-gpu-backend=compatibility",
        "--fingerprint-gpu-vendor=Diagnostic Vendor",
        "--fingerprint-gpu-renderer=Diagnostic Renderer",
    ],
)
try:
    page = browser.new_page()
    page.goto("about:blank")
    print(page.evaluate("""() => {
      const gl = document.createElement('canvas').getContext('webgl');
      const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
      return ext ? {
        vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
        renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
      } : {unavailable: true};
    }"""))
finally:
    browser.close()
```

`stealth_args=False` 只关闭 SDK 默认 seed/persona 注入，不等于 `--fingerprint=off`。
显式 WebGL 名称不要求 seed。异步 API 同样传 `args`；measured/device-pool 启动仍坚持
native，不应为了接受自定义名称而放宽其准入条件。

### Node / Playwright

从仓库安装 `npm install ./sdk/node playwright-core`。保存为 `.mjs`：

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch({
  stealthArgs: false,
  launchOptions: { executablePath: '/absolute/path/to/matching/chromix/chrome' },
  args: [
    '--fingerprint-gpu-backend=compatibility',
    '--fingerprint-gpu-vendor=Diagnostic Vendor',
    '--fingerprint-gpu-renderer=Diagnostic Renderer',
  ],
});
try {
  const page = await browser.newPage();
  await page.goto('about:blank');
  console.log(await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? {
      vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
      renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
    } : { unavailable: true };
  }));
} finally {
  await browser.close();
}
```

根 Playwright 入口的路径是 `launchOptions.executablePath`，不是顶层
`executablePath`。也可以使用 `CLOAKBROWSER_BINARY_PATH`。`args` 是用户启动参数入口；
不要误用内部 `buildArgs` 的 `extraArgs` 名称作为公开 launch 参数。

## 与显式像素噪声组合

包含新增 `0217`–`0224` 补丁的浏览器可在上述 `args` 中再加入：

```text
--fingerprint=42
--fingerprint-pixel-noise=seeded
```

这两个参数控制 seed 与受支持的读回像素，不改变 GPU 型号字符串或能力列表。只需要像素扰动且希望保留真实 GPU 身份时，不传 compatibility 和 vendor/renderer。WebGL 读回覆盖的是受限不透明 RGBA8 路径，透明 context、PBO、FLOAT、用户 FBO 保持原生，详见 [像素噪声范围](pixel-noise.md)。此组合需要新浏览器构建与本地诊断，旧包不会因为 SDK 接受参数而获得新能力。

## 本地实测工具

`tools/gpu_identity_diagnostic.py` 不调用 SDK，不下载浏览器，不访问测试网站。
它启动仅绑定 `127.0.0.1` 的临时 HTTP 页面，提供 WebGPU 所需的可信本地 origin，
并阻止页面访问其他 URL。浏览器后台联网只用 Chromium 的 disable-background-networking
选项抑制，不宣称操作系统级网络隔离。需要 Python 3.9+ 与 Playwright 驱动：

```bash
python -m pip install playwright
python tools/gpu_identity_diagnostic.py \
  --browser '/absolute/path/to/matching/chromix/chrome' \
  --vendor 'Diagnostic Vendor' \
  --renderer 'Diagnostic Renderer' \
  --output gpu-identity-new.json
```

不需要执行 `playwright install`。Windows 直接传 `--browser 'C:\Chromix\chrome.exe'`。
默认 headless；需要图形会话时显式加 `--headed`。不要添加强制软件/ANGLE 后端或忽略
GPU blocklist 的参数来把 unavailable 改成成功。建议使用不同于本机原生名称的诊断标签，
否则无法区分“覆盖成功”与“完全没生效”。输出必须是新路径，已有证据不会被覆盖。

一次运行启动五个隔离浏览器实例，浏览器路径和运行模式保持相同：

| case | identity 参数 | backend |
|---|---|---|
| `default` | 无 | 未指定，观察实际默认 |
| `default-identity` | vendor + renderer | 未指定，验证不能隐式 opt-in |
| `native-identity` | vendor + renderer | 显式 native，验证优先级 |
| `compatibility` | 无 | 显式 compatibility，控制组 |
| `compatibility-identity` | vendor + renderer | 显式 compatibility，观察覆盖 |

每次采集：

- WebGL1、WebGL2 分别创建 context，立即及下一任务重复读取身份；记录常见 WebGL1/2
  limits、完整 supported-extension 名称列表、12 组 shader precision 和 context attributes。
  这是所列能力的对比，不是所有扩展特有参数或实际 GPU 运算能力认证。
- 用 `WEBGL_lose_context` 实际触发 loss/restoration。lost 时标准与 unmasked 查询应为
  null；restored 后重新获取 debug extension，重新采集身份/能力。等待有超时，缺少扩展
  是 gap，宣称可用但恢复失败是失败，不伪造恢复结果。
- WebGPU 请求默认 adapter，保留原生公开 identity 标量字段、limits、features 和可用的
  fallback 标志；此工具不创建 GPUDevice、不覆盖 low/high/fallback adapter 请求矩阵。
- 记录用户指定文件的 SHA256、浏览器版本、探针 SHA256、启动 args，以及可获取时的
  CDP `SystemInfo.getInfo` GPU 信息和实际命令行。CDP 数据是浏览器级旁证，**不能认证某个
  WebGL context 使用哪块 GPU**；CDP 不可用会单独记错，不替代页面观测。

### 如何解读结果

JSON 保存五次原始 `launches` 与逐项 `checks`。退出码：

| 退出码 / status | 含义 |
|---|---|
| `0 / passed` | 该浏览器/本次运行观察到明确 opt-in，默认/native 保持身份，采集能力不变，生命周期和 WebGPU 对比通过。不是匹配源码证明或实体设备认证。 |
| `1 / failed` | 启动、探针、恢复失败，或观察到默认被改写、错误/部分身份、能力改变、WebGPU 改写等差异。需要查看 before/after 与原始记录。 |
| `2 / incomplete` | 没有失败，但有 context/extension/adapter 缺失、软件 guard、覆盖未观察到，或名称与基线相同无法区分等证据缺口。不能当成支持成功。CLI 输入错误也由 argparse 返回 2。 |

软件 renderer 明确可识别时，必须保留原生身份，custom case 记 incomplete；不会为满足
请求而删除 guard。名称仍与基线一致、但没有可识别软件 renderer 时，也只报告“覆盖未
观察到”：可能是旧/stock build，也可能是仅 C++ ContextInfo 才能判定的 guard，不能靠
JS 名称猜测硬件。跨启动/恢复如果真的切换了后端，能力对比可能失败；应调查原始记录，
不能直接把所有差异归因于伪装实现，更不能通过修改 limits/precision 去消除差异。

## 源码检查与验证边界

已有实现链路：

- `0036`、`0176`：公共 vendor/renderer/backend 参数归一化到内部配置。
- `0092`、`0163`、`0172`–`0173`：persona 配置、共享 native 策略及普通启动 native 默认。
- `0101`、`0147`：每次 identity 查询读取当前 `drawing_buffer_->ContextInfo()` 和原生
  `GL_RENDERER`，再运行软件 guard；不存在需要维持或恢复的自定义 context identity 缓存。
  lost context 的 `getParameter` 在进入该分支之前返回 null。
- `0093`–`0095`：公开非 synthetic 模式保留原生 WebGL limits/extensions/precision。
- `0148`：公共 WebGPU 保留完整 Dawn 原生身份。

本次源码研究没有发现需要额外 `0220` 修复的 identity ready/restore 生命周期缺陷，
因此没有编造新 patch，没有修改 SDK、base/chrome、Canvas 或 noise 实现。
新增的 `tools/tests/test_gpu_identity_diagnostic.py` 验证参数矩阵、SDK 归一化不隐式
切换、差异判定、软件 guard/gap、生命周期失败、WebGPU 保持原生、输出保护和 JS 语法。
它们是 helper fixtures；现有 C++ stub 回归也不是完整 Chromium 构建或真实 GPU 验收。

没有用户指定的匹配浏览器路径时，本次只进行 helper/源码验证，不自动挑选机器上的
stock 浏览器冒充功能验证。要确认实际发布的 browser 已支持，仍必须对**包含上述补丁的
新 build**运行本工具，保留 JSON；跨 OS/驱动支持和实体设备验收见
[GPU 后端与设备矩阵](gpu-backend.md)，公开参数总表见 [fingerprint flags](fingerprint-flags.md)。
