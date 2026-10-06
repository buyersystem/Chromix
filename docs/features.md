# 功能指南

本页面向使用 Chromix Python / Node SDK 的用户，说明功能、配置入口、默认值和限制。
描述依据是**当前仓库源码及配套 SDK**，不代表下载到的旧浏览器已包含这些功能，也不代表已发布或通过完整原生验收。需要浏览器补丁的功能必须配合匹配构建；仅升级 SDK 不够。

导航：[公开指纹参数](fingerprint-flags.md) · [后端策略](backend-policy.md) · [实测设备模式](device-pool.md) · [原生验收](fingerprint-acceptance.md) · [功能跟进与历史证据](functionality-followup.md)。构建及验证状态见 [FINGERPRINT_STATUS.md](../FINGERPRINT_STATUS.md)，安装和完整 SDK 入口见 [Python](../sdk/python/README.md)、[Node](../sdk/node/README.md)。

## 1. 选择启动方式

Chromix 由带补丁的 Chromium 和启动封装组成。浏览器侧负责启动身份与后端策略，SDK 负责参数、持久化 seed、代理元数据解析和部分输入包装。一次浏览器启动只有一份固定 persona（身份配置），renderer / Worker 共享；同一浏览器里的多个 BrowserContext **不是独立的指纹身份**。

| 用途 | Python | Node | 默认与限制 |
|---|---|---|---|
| 返回浏览器，自行创建 context / page | `launch()`、`launch_async()` | `launch()` | 默认 `headless=True/true`、启用默认 seed；关闭 browser 释放资源。 |
| 创建临时 context | `launch_context()`、`launch_context_async()` | `launchContext()` | 返回 Playwright BrowserContext；关闭它也关闭该入口拥有的 browser。 |
| 保存 profile | `launch_persistent_context(path, ...)`、`launch_persistent_context_async(user_data_dir=path, ...)` | `launchPersistentContext({ userDataDir, ... })` | 保留 profile 存储，并在默认模式下复用 seed。不要让多个浏览器同时使用同一 profile 目录。 |
| 使用 Puppeteer | 无 Python 入口 | `@xiaoxiaofeihh/chromix/puppeteer` | 原生 Puppeteer 适配器，需自行安装兼容的 `puppeteer-core` 或 `puppeteer`；选项不等于 Playwright。 |

### 最小启动

先安装当前 checkout 对应 SDK 与驱动。以下例子默认使用 SDK 的二进制选择逻辑；验证当前补丁时，应预先将 `CLOAKBROWSER_BINARY_PATH` 指向已核验的本机构建。这个环境变量是两端共用的路径入口，不是构建完整性证明。

```python
from chromix import launch

browser = launch(args=["--fingerprint=42"])
try:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
finally:
    browser.close()
```

```javascript
import { launch } from '@xiaoxiaofeihh/chromix';

const browser = await launch({ args: ['--fingerprint=42'] });
try {
  const page = await browser.newPage();
  await page.goto('https://example.com');
  console.log(await page.title());
} finally {
  await browser.close();
}
```

Python 异步入口使用 `await`；持久化异步入口的目录必须写为 `user_data_dir=...`，不能照搬同步入口的位置参数。Node 默认入口是 Playwright，驱动由调用者安装。

### 选项放在哪里

- 两端均通过 `args: ["--key=value"]` 传浏览器参数，不把值拆成第二个参数。公开 `--fingerprint-*` 参数由 SDK 校验、浏览器归一化；高级 `--uxr-*` 同字段通常优先于公开别名，避免混用。
- Python 的 `launch(**kwargs)` 额外参数传给 Playwright 启动；`launch_context(**kwargs)` 的额外 context 参数传给 `new_context()`，如 `storage_state`、`permissions`，不是所有启动选项都能放在那里。
- Node 使用顶层 Chromix 选项，以及 `launchOptions` / `contextOptions` 分别配置 Playwright。建议将 persona 参数统一放在顶层 `args`、`locale`、`timezone`；嵌套 `launchOptions.args` 会优先于顶层 `args`，不是自动拼接。
- `stealth_args=False` / `stealthArgs: false` 只是关闭 SDK 自动 seed / platform 等默认参数，不等于卸载浏览器补丁。需要原生 persona 对照时用 `--fingerprint=off`。
- `license_key` / `licenseKey` 仅保留调用兼容性并被忽略；不提供参考项目中的授权层级或检测效果承诺。

Puppeteer 子路径提供 `launch`、`launchContext`、`launchPersistentContext`、`connect` 和 `buildLaunchOptions`。默认 `defaultViewport=null`。它拒绝 Playwright `contextOptions`、顶层 `userAgent/colorScheme`、measured `devicePool`，以及不能由原生路由处理的代理凭据；`connect` 不能更改已有浏览器的启动 persona。详见[跟进文档](functionality-followup.md#puppeteer)。

## 2. Profile、seed 与身份作用域

| 配置 | 行为 | 不代表什么 |
|---|---|---|
| 普通 SDK 启动，不指定 seed | 生成非零随机 32 位 seed；platform 默认跟随宿主 Linux / Windows / macOS。 | 不是每次生成另一台真实设备；Linux 不会默认伪装 Windows。 |
| `args=["--fingerprint=42"]` | 显式非零十进制 uint64 seed，固定依赖该 seed 的行为。 | 同 seed 不保证跨版本、驱动、字体、显示器、网络和权限状态的所有观测相同。 |
| 持久化目录，不指定 seed | 两端共用 `.chromix-fingerprint-seed`；原子创建、随后读取，文件损坏会报错而非悄悄换身份。 | profile 存储持久化和指纹稳定是不同问题，不能保证站点登录永久有效。 |
| 持久化目录加显式 `--fingerprint=...` | 本次使用显式值，不改写保存的默认 seed。 | 不会自动更新旧 profile 的全部配置；调用者应保持配置一致。 |
| `--fingerprint=off` | 原生 persona 调试模式，移除身份覆盖；显式 locale / timezone 仍保留。 | 不是未修改的官方 Chrome，也不撤销其它独立功能开关。 |

```python
from chromix import launch_persistent_context

context = launch_persistent_context(
    "./profiles/demo",
    headless=False,
    locale="zh-CN",
    timezone="Asia/Shanghai",
)
try:
    page = context.new_page()
    page.goto("https://example.com")
finally:
    context.close()
```

```javascript
import { launchPersistentContext } from '@xiaoxiaofeihh/chromix';

const context = await launchPersistentContext({
  userDataDir: './profiles/demo',
  headless: false,
  locale: 'zh-CN',
  timezone: 'Asia/Shanghai',
});
try {
  const page = await context.newPage();
  await page.goto('https://example.com');
} finally {
  await context.close();
}
```

要同时使用不同身份，应启动不同浏览器并使用不同 profile；仅新建 BrowserContext 不会重新生成浏览器 persona。仅保存 Playwright `storage_state` 也不保存 Chromix seed 或完整 profile。

## 3. UA、语言、时区与硬件展示

| 功能 / 用途 | 配置入口 | 默认与限制 |
|---|---|---|
| 操作系统身份 | `--fingerprint-platform=windows/macos/linux` | SDK 跟随宿主。跨 OS 设置影响展示身份，不会更换内核、字体栅格器或实际硬件。 |
| UA 与 Client Hints 品牌 | `--fingerprint-brand=Chrome/Edge/Opera/Vivaldi`、`--fingerprint-brand-version`、`--fingerprint-platform-version` | 品牌/版本展示不安装对应厂商功能。优先使用引擎身份配置，避免 UA 与 CH 不一致。 |
| 手工 UA | Python context 的 `user_agent`；Node Playwright 的 `userAgent` | 属于 Playwright 仿真，可能与原生 Client Hints 分离；不是推荐的整套身份配置方式。 |
| 语言与时区 | 两端顶层 `locale="zh-CN"`、`timezone="Asia/Shanghai"`，或对应 `--fingerprint-*` 参数 | 未显式配置且 GeoIP 关闭时保留原生配置；timezone 使用 IANA 名称。Node `contextOptions.locale/timezoneId` 被忽略并警告，使用顶层参数。 |
| IP 推导语言 / 时区 | `geoip=True/true`，通常与 `proxy` 配合 | 默认关闭。按元数据国家码映射 locale，不是用户语言检测；显式参数优先。会发出网络请求，失败中止启动。 |
| CPU / RAM 展示 | `--fingerprint-hardware-concurrency`、`--fingerprint-device-memory` | 启用 seed 时默认 8 核、8 GB 展示；核数 1–128，内存正数至 32、按支持桶取值。不会分配 CPU、改变 Wasm/SIMD 或 V8 heap limit。 |
| 存储配额策略 | `--fingerprint-storage-quota` | seed 模式默认 102400 MiB；整数 MiB，0 表示不允许新增分配。不是物理磁盘预留，实际使用量、磁盘耗尽和原生错误仍有效。 |

更多取值、别名与范围见[公开参数](fingerprint-flags.md)。`user_agent`、viewport、locale 等 Playwright 仿真仍可能覆盖部分接口，配置后应核对页面、Worker 与 HTTP 请求中的身份是否一致。

## 4. Display、viewport 与输入偏好

- `--fingerprint-screen-width/height` 设置启动显示几何，单位为 DIP；seed 模式 Windows/Linux 默认 1920×1080，macOS 默认 1440×900。两维都要求 1–32768 的整数。
- `--fingerprint-taskbar-height` 设置工作区保留高度，默认 Windows 48、macOS 95、Linux 0。它不会改变真实任务栏或 Dock。单独指定一维或工作区字段时，其余屏幕维度按平台默认补齐。
- **screen、原生窗口 outer bounds 和网页 viewport 是不同对象。** SDK 默认不注入固定页面 viewport；Python `viewport=None`、Node `viewport: null` 明确选择无 viewport 仿真。需要页面布局测试时才传 `viewport={width, height}`；改变 viewport 不等于换了一块显示器。
- 当前显示补丁通过显示状态与 IPC 传播配置，不仅改 `screen` getter；缩放、DPR、OOPIF、屏幕切换仍需匹配构建验收，不应宣称完成物理显示器模拟。
- `--fingerprint-max-touch-points`（0–16）、`--fingerprint-pointer`（fine/coarse/none）、`--fingerprint-hover`（hover/none）控制有效输入偏好。相互矛盾的组合会拒绝；注入触摸事件不构成实体触屏。
- `--fingerprint-color-scheme`、`--fingerprint-preferred-contrast`、`--fingerprint-forced-colors`、reduced-motion / reduced-transparency / inverted-colors 参数配置有效偏好；HDR、色域仍依赖原生显示。普通 `--fingerprint-hdr`、`--fingerprint-keyboard-layout` 仅接受 `native`。
- `--fingerprint-timer-resolution=7` 表示公开时钟按 7 毫秒量化，可取 0–1000；0 / 未设保留原生精度。它不改变内部调度时钟，也不是每次添加随机延迟。

参数表与实际样式、输入、时钟验收边界见[后端策略](backend-policy.md)。

## 5. Graphics：原生、兼容模板与 measured 不是同一种功能

| 模式 | 配置 / 用途 | 能做什么与不能做什么 |
|---|---|---|
| 普通原生 GPU | 默认 `--fingerprint-gpu-backend=native` | Canvas / WebGL / WebGPU 共享原生策略，抑制旧噪声、Bridge、GPU 名称/能力覆盖。保留驱动限制与软件回退；SDK 不再自动加 `--ignore-gpu-blocklist`。不保证跨 profile 像素唯一。 |
| 兼容展示 | `--fingerprint-gpu-backend=compatibility` 加 `--fingerprint-gpu-vendor/renderer` | 在允许的 WebGL context 上呈现显式名称；检测到软件后端时保留真实名称。公共 WebGPU 仍保留完整 Dawn 身份；改字符串不会换 GPU 或增加 GL 能力。 |
| Synthetic 模板 | 显式 `--uxr-synthetic-device-tests=true` | 为合成测试保留 GPU、独立硬件/显示、旧字体/Canvas 等测试路径。GPU 模板及权重不是实测设备或市场占有率，不能作为生产设备库证据。 |
| Measured 原生准入 | Playwright context API 的 `device_pool` / `devicePool` | 校验实测全设备记录与当前原生环境后启动；不是拼字段、跨 OS 仿真或远程 GPU 切换。 |

普通启动**不会因为随机 seed 自动挑选 GPU 模板**。公共图形默认保留原生执行，`--fingerprint-noise=false` 可显式关闭指纹扰动路径，并同时关闭可选音频隔离；它不是“消除所有指纹”。混合 GPU 系统的 WebGL / WebGPU 可以合法选择不同适配器。

### 使用 measured 模式前

1. 按[设备采集指南](device-pool.md)用明确的 executable 采集、验证 schema-v2 / probe-v4 完整证据；仓库不附带已审阅的真实设备池。
2. 设置 `CLOAKBROWSER_BINARY_PATH` 为采集时的同一 executable。SDK 校验 executable hash、当前 host、运行模式和证据，默认 host 证据时效为 24 小时。
3. 仅通过 Python 同步/异步 context、persistent-context 或 Node Playwright context、persistent-context 启动；返回 browser 的 `launch` 和 Puppeteer 都拒绝 measured 模式。

```python
from chromix import launch_context

context = launch_context(device_pool={
    "host": ".chromix-local-build/device-a/record.json",
    "records": [".chromix-local-build/device-a/record.json"],
    "seed": "4294967297",
})
try:
    print(context._chromix_device_profile["runtime_verified"])
finally:
    context.close()
```

上述路径必须是已采集并验证的本地 bundle，而非示例模板。Node 对应入口为 `launchContext({ devicePool: { python, host, records, seed } })`，需要在指定 Python 解释器安装匹配的 Python SDK measured 依赖；超过 JS 安全整数的 seed 用字符串或 BigInt。

measured 模式禁止代理、args、UA、locale、viewport、字体等逐字段覆盖，保留 `--fingerprint=off` 与 native GPU / viewport。没有匹配候选时验证当前原生 host，不拼接候选字段；持久化目录通过 `.chromix-device-profile.json` 绑定记录、executable 与 seed，改变绑定会失败。运行返回前的验证只覆盖本次启动，不认证未来权限或设备变化。实测摘要、校验和不等于实体硬件证明，详见[GPU 后端](gpu-backend.md)与[设备池限制](device-pool.md)。

## 6. 字体、音频与媒体

| 功能 / 用途 | 配置入口 | 默认与限制 |
|---|---|---|
| 加载真实字体目录 | Python `fonts_dir`；Node `fontsDir` | Linux 上接入 Fontconfig，显式目录替换 bundle 字体目录；未指定时，有配套 `fonts.conf.template` 的 bundle 才会自动接入。Windows/macOS 不因此安装字体。 |
| 限制原生字体池 | `--fingerprint-font-policy=restricted` 与 `--fingerprint-font-whitelist` | 默认 native；restricted 要求 1–256 个已安装 family。SDK 可从指定目录直接包含的 TTF/OTF/TTC 生成默认名单；仅传目录不等于开启 restricted。 |
| Windows 字体度量对齐 | `--fingerprint-windows-font-metrics` | 默认不启用；仅 Linux → Windows persona 且命中真实 Windows 字体时有效。只对齐部分度量，不复刻 DirectWrite 栅格化。 |
| 音频图隔离 | `--fingerprint-audio-render=isolated`，可加 `--fingerprint-audio-seed` | 默认 native；isolated 需要非零 uint64 音频 seed 或 fingerprint seed，在实际 output bus 处理。`noise=false` / off 禁用隔离；不提供虚拟麦克风/扬声器，不保证跨 OS DSP 一致。 |
| 限制视频 codec | 每个 `--fingerprint-codec-h264/vp8/vp9/av1/hevc=native/disabled` | 默认原生支持。禁用限制查询和相应操作，不安装 codec、不增加硬件加速；DRM、远程 decoder 等仍有验收缺口。 |
| 媒体设备、语音及原生能力 | 浏览器原权限 / 设备后端；普通语音、WebAuthn、PDF 能力保持原生 | 不凭 seed 生成可工作的摄像头、SAPI 声音或认证器。synthetic 表只用于夹具，不能充当真实功能。 |
| Widevine CDM 接入 | `CLOAKBROWSER_WIDEVINE_CDM`；`CLOAKBROWSER_WIDEVINE=0` 关闭自动发现 | SDK 在可识别的 CDM 目录存在时传入后端参数；可发现路径依平台而异。仍依赖 CDM、构建、平台及站点许可，不保证任意 DRM 播放。 |

restricted 字体策略限制解析后的 native family、fallback、`src:local` 与 Local Font Access；下载的 CSS author fonts 仍可用，Local Font Access 仍需原权限。family 名不证明某个文件供应了每个 glyph；缺失字体也不能靠填写名字补齐。

字体目录应由调用者合法提供，并检查所需 CJK / emoji / 数学字符覆盖。不要把“已列出字体名称”解释为“渲染与另一个系统完全一致”。具体后端及未完成项见[后端策略](backend-policy.md)，字体内容取证见[跟进文档](functionality-followup.md#字体内容凭据)。

## 7. Proxy、GeoIP 与 WebRTC

代理配置决定实际请求路由；GeoIP / WebRTC IP 配置是另一层，不能互相替代。

| 需求 | 配置入口 | 默认与限制 |
|---|---|---|
| HTTP / HTTPS 代理 | 两端 `proxy` URL 或 `{server, username, password, bypass}` | 默认不由 SDK 配置代理。Playwright 走驱动支持的代理入口；Puppeteer 适配器不以 `page.authenticate()` 假冒浏览器级 HTTP 代理认证。 |
| SOCKS5 TCP 认证 | 启动级 `proxy`，`server="socks5://..."` 加用户名 / 密码 | 需带原生认证补丁的浏览器；SDK 通过受限启动环境传凭据，不写入 argv，不应手工设置 `CHROMIX_SOCKS5_AUTH`。仅 TCP CONNECT，没有 UDP ASSOCIATE。 |
| 按出口填充 locale / timezone / WebRTC IP | `geoip=True/true` | 默认关闭；SDK 经有效代理请求 `http://ip-api.com`，没有代理则直连。显式时区、语言、IP 优先；GeoIP 与 IP 复用同次查询。 |
| 只解析 WebRTC 展示 IP | `args=["--fingerprint-webrtc-ip=auto"]` | 启动前解析一次；不需要 GeoIP 数据库。显式 IPv4/IPv6 字面量则无需查 IP，不能填域名。 |
| 限制非代理 UDP | `--force-webrtc-ip-handling-policy` | 代理启动默认 `disable_non_proxied_udp`，含 raw PAC / auto-detect；显式 native policy 优先。可能影响通话可用性，不等于路由已验收。 |

SDK 元数据查询默认总超时 10 秒，可通过 `CLOAKBROWSER_GEOIP_TIMEOUT_SECONDS` 设为 `(0, 60]`；不跟随重定向、不自动读取环境代理、不失败直连或换代理。PAC、多路由与冲突 raw proxy 配置会拒绝用于元数据查询。HTTP 元数据服务能观察出口 IP，返回值不是经过独立认证的网络位置。

```python
import os
from chromix import launch

browser = launch(
    proxy={
        "server": "socks5://127.0.0.1:1080",
        "username": os.environ["PROXY_USERNAME"],
        "password": os.environ["PROXY_PASSWORD"],
    },
    geoip=True,
    args=["--fingerprint=42"],
)
try:
    page = browser.new_page()
    page.goto("https://example.com")
finally:
    browser.close()
```

示例要求本机有可用代理，并允许访问元数据服务。Node Playwright 使用同名 `proxy`、`geoip` 与 `args`，不要把已认证 SOCKS5 放到 `contextOptions.proxy`；认证属于 browser launch，不是逐 context 的身份。

`--fingerprint-webrtc-ip` 仅修改本地候选、SDP、stats 的展示副本，不修改远端候选、不伪造成功 STUN，也保留零占位和 TURN relay 地址。向对端发布另一个 IP 不会让该 IP 变得可达；实际 socket、proxy、TURN 才决定通信。以后创建 context 并更换代理，也不会改写已冻结的 persona/IP。

直接启动 executable 的 `auto` 使用 `https://api.ipify.org`，不是 SDK 的元数据路径；凭据、系统代理及超时边界见[公开参数的路由说明](fingerprint-flags.md#webrtc-ip-and-proxy-resolution)。不能把元数据客户端支持的 SOCKS 别名误写成浏览器后端支持，也不能据此保证 DNS / IPv6 / QUIC / ICE 无绕行。

## 8. Humanize：输入包装，不是所有交互的替代实现

默认关闭。Python 同步入口可设 `humanize=True`、`human_preset="default"/"careful"`、`human_config={...}`；Node 为 `humanize`、`humanPreset`、`humanConfig`。

现有包装主要替换 `page.mouse.move/click/dblclick/wheel` 和 `page.keyboard.type/press`：曲线移动、停顿、逐字输入、可选错字纠正、分步滚动。并未实现参考项目所宣称的全 Locator、ElementHandle、`page.fill/type/click` 覆盖；高层 driver 方法可能绕过包装。弹窗与另行 CDP 连接也不能假定已自动包装。

```python
from chromix import launch

browser = launch(
    humanize=True,
    human_preset="careful",
    human_config={"typing_delay": 100, "mistype_chance": 0},
)
try:
    page = browser.new_page()
    page.set_content('<input id="name" aria-label="name">')
    page.locator("#name").focus()
    page.keyboard.type("Chromix")
    page.mouse.move(200, 120)
finally:
    browser.close()
```

Node 对应自定义字段是 `humanConfig: { typingDelay: 100, mistype: 0 }`，不是 Python 的 snake_case 字段。humanize 的 `seed` 独立于浏览器 fingerprint seed。

**Python 异步限制：**启动 API 虽接受 humanize 参数，目前仍复用同步 `patch_page`，其中输入调用和等待没有异步适配。不要将其当作已可用的异步 humanize；异步使用者应关闭它。各入口对新页/已有页的包装范围也有差异。Node 包装使用异步输入调用，Puppeteer 另适配了 wheel 的对象参数。任何一端都不保证原生交互选项完全等价、站点兼容或检测通过率。

## 9. Cookies 与会话迁移

区分三种需求：

1. **同机延续会话：**使用持久化 profile；临时 context 可用 Playwright `storage_state` / `storageState` 保存支持的站点状态。后者不是整个 profile，也不是下述加密格式。
2. **活动 context 间加密迁移 Cookie：**Python `export_cookies/import_cookies`（及 `_async` 版本）；Node 根入口、`/cookies`、`/puppeteer` 的 `exportCookies/importCookies`。Python 需安装 `chromix[cookies]` 对应依赖；当前源码可用 `python -m pip install "./sdk/python[cookies]"`。
3. **允许第三方 Cookie：**显式 `--fingerprint-allow-3p-cookies`，默认不放宽第三方 Cookie 策略；不绕过 SameSite / Secure、站点内容规则或站点自身的登录检查。

```python
import os
from chromix import launch_context, export_cookies, import_cookies

passphrase = os.environ["COOKIE_PASSPHRASE"]
source = launch_context()
try:
    source.add_cookies([{
        "name": "demo", "value": "example", "url": "https://example.com/",
    }])
    export_cookies(source, "cookies-demo.enc", passphrase=passphrase)
finally:
    source.close()

destination = launch_context()
try:
    result = import_cookies(destination, "cookies-demo.enc", passphrase=passphrase)
    print(result["imported"])
finally:
    destination.close()
```

`cookies-demo.enc` 必须尚不存在；示例只演示自建 Cookie，不是登录迁移证明。密码应独立安全保存，为 12–1024 UTF-8 字节。Node 调用形状是 `await exportCookies(source, path, { passphrase })` / `await importCookies(destination, path, { passphrase })`，两端格式互通。

- 格式使用 scrypt 与 AES-256-GCM；上限 16 MiB、10000 个 Cookie。导出不覆盖旧文件，POSIX 为 0600，Windows 依赖目录 ACL。
- 导入要求目标 context **没有 Cookie**，不会先清除已有数据；跳过过期项，回读核对属性。支持的属性包括 host-only/domain、HttpOnly、Secure、SameSite、priority 和有效 CHIPS partition key，opaque partition 拒绝。
- CDP 不提供整批事务，失败可能留下部分写入；应丢弃失败目标 context，不假定已经回滚。迁移不包含 localStorage、缓存或其它 profile 数据，也不绕过服务端失效策略。
- 这是活动 context 的显式加密导出/导入，**不是 `--fingerprint-portable-cookies` 原生数据库模式**；没有修改 DPAPI / Keychain / OSCrypt，也不能承诺复制 profile 跨机器保留登录。

格式与历史验证见[加密 Cookie 迁移](functionality-followup.md#加密-cookie-迁移)。

## 10. 使用前核对与排错

- **参数存在不等于二进制包含实现。**先记录实际 executable 路径、完整版本与 hash，再核对构建来源；`binary_info()` / `binaryInfo()` 是 SDK 缓存/通道信息，不是任意自定义 executable 的补丁凭据。需要精确复现时固定已验证路径，勿把通道选择当作任意版本的精确 pin。
- **先从原生对照定位。**用 `--fingerprint=off`，关闭 GeoIP，不加 locale / timezone / Playwright 仿真；再逐项增加配置。off 不恢复所有 ungoogled Chromium 改动。
- **GPU 名称没变：**检查是否仍是默认 native，或实际回退到了软件 renderer；不要为展示名字关闭原生保护来冒充硬件支持。
- **字体缺失或布局不同：**检查真实字体、Fontconfig、family/fallback 与授权，不只检查 whitelist。缺少字体文件不能由 seed 修复。
- **代理与通话异常：**分开测试页面请求、元数据查询与 ICE/TURN，注意默认 UDP 限制、代理认证补丁与 SDK 入口差异。查询成功不能证明全部出口路径正确。
- **需要验收结论：**使用[原生回归门禁](fingerprint-acceptance.md)与匹配源码凭据，按[后端策略](backend-policy.md)记录缺项。单元测试、独立方法 shim、stock Chrome 对照和历史流水线结果都不是当前完整浏览器验收。

本指南不提供检测通过率、跨平台完全兼容或“每个 profile 等于另一台真实电脑”的保证。当前实现边界与待完成项应结合[功能跟进](functionality-followup.md)和[状态记录](../FINGERPRINT_STATUS.md)阅读。
