# CloakBrowser 公开功能对照

本表用于 API 兼容性与运行验收，不以参数数量或补丁数量推导功能等价。**当前仍有明确差距，不能声称全部对齐。**

## 对照基线

- 审阅日期：2026-10-08。
- CloakBrowser 公开 README 固定提交：[`3c842adc9fffa65f9a39c11c6c355e47aafde15d`](https://github.com/CloakHQ/CloakBrowser/blob/3c842adc9fffa65f9a39c11c6c355e47aafde15d/README.md)。该文档标注 SDK `0.5.12`、Chromium `154.0.8037.57.1` 和 87 个原生补丁。
- 它公开提供 SDK 和功能说明；该对照不是对其专有浏览器补丁的源码移植，也不是本项目完成了相同网站测试的声明。
- Chromix 当前 `patches/series` 为 224 个补丁；源码版本固定在 `build/ungoogled-revisions.psd1`。已发布 Linux/Windows 包与后续新源码要分别核对。
- 下载目录 macOS `.57` 包的本地及 BrowserScan 测试未观察到多项原生参数覆盖。该结果不能替代其他平台验收，但意味着不能把 SDK 接受参数当成这个包已实现对应功能。

## 状态如何解释

| 状态 | 含义 |
|---|---|
| SDK 实现 | 包装层提供对应入口；驱动、浏览器和版本限制仍适用 |
| 原生源码实现 | 补丁已接入；必须检查匹配二进制的运行报告 |
| 受限实现 | 支持的路径有明确边界，未覆盖路径保持原样或拒绝 |
| 待实现 | 没有对应能力，不能靠传入同名参数代替 |
| 第三方服务 | 对方的账户、许可或管理产品，不属于浏览器补丁兼容性 |

## 功能对照

| CloakBrowser 公开功能 | Chromix 当前状态 | 边界或下一步 |
|---|---|---|
| Python 同步/异步 Playwright、Node Playwright | SDK 实现 | 启动、context、持久化入口；一个浏览器进程共享原生身份 |
| Node Puppeteer | SDK 实现，受限 | 独立入口；measured-device 准入和浏览器级 HTTP 代理认证仍未对齐 |
| 自动下载、通道、缓存、已有 executable | SDK 实现 | Chromix 独立 Release 与校验；按平台通道固定版本，不调用第三方许可服务 |
| 固定 seed、profile 持久化 | SDK/源码实现 | 固定 seed 不消除 GPU、字体、主机和版本的真实差异 |
| 鼠标、键盘和滚动 humanize | SDK 实现，受限 | 本轮完善逐次配置与 selector 操作，准确操作清单见 Python/Node README；Locator/ElementHandle 全覆盖不能据此推导 |
| Node buildLaunchOptions/humanizeBrowser | SDK 实现 | 组合接口已经存在；底层原生指纹仍由二进制决定 |
| UA、CH、CPU/RAM、屏幕、时区、locale | 原生源码实现 | 下载 macOS `.57` 曾观测参数不生效；需要校验正确构建的原生行为及跨作用域一致性 |
| GeoIP/代理解析 | SDK 实现 | 通过有效代理请求元数据，显式配置优先；不生成网络出口或 UDP 路由 |
| 零参数生成整套自动变化设备身份 | 刻意不同，未等价 | 普通模式保留较多 native 策略，不宣称跨 OS 实体设备仿真 |
| Canvas 按种子噪声 | 新源码，待新二进制验证 | `0217`–`0224` 显式 pixel-noise，8-bit读回/导出；已发布包不自动更新，见 [范围](pixel-noise.md) |
| WebGL 像素噪声 | 新源码，受限 | 不透明默认 framebuffer RGBA8 TypedArray；PBO/FLOAT/FBO/透明 context 保持原生 |
| WebGL vendor/renderer | 原生源码实现，受限 | 显式 compatibility；软件上下文保留真实身份；见 [GPU identity](gpu-identity.md) |
| GPU 能力与 WebGPU | 未做完整设备模拟 | 普通公开模式保留 limits/extensions/precision 和真实 Dawn 身份；对方新版说明也包含 Windows 原生 GPU passthrough |
| Audio noise | 原生隔离策略，受限 | graph output-bus 处理不等于完整跨 OS DSP/实体音频模拟，不能称为所有音频读回等价 |
| ClientRects noise | 未完成同等覆盖 | 没有完整已验收的独立布局噪声引擎，不把字体度量覆盖算成全部路径完成 |
| 字体 metrics / 字体 persona | 受限实现 | 需要真实字体，不能仅凭 family 名模拟 DirectWrite/CoreText/FreeType 字形与栅格 |
| noise=false / fingerprint=off | 原生/SDK 实现 | 新 pixel-noise 同样遵守；旧包需实测，独立 locale/timezone 设置仍适用 |
| WebRTC IP/SDP/stats | 展示及策略实现 | 地址展示不等于 ICE/socket/TURN 实际路由，需额外出口验收 |
| SOCKS5 原生认证 | TCP 源码/SDK 实现 | UDP ASSOCIATE、QUIC/HTTP3 经 SOCKS5 尚未实现 |
| transparent-proxy | 待实现 | 没有可执行的等价协议契约，参数存在不能当成支持 |
| portable-cookies 原生数据库加密 | 待实现 | 已有活动 context AES-GCM 导入/导出，但不是 profile 数据库跨机器加密模式 |
| 代理痕迹移除、网络时序处理 | 未验收等价 | TLS/H2/H3 使用实际 Chromium 栈；无任意网络协议 persona 配置 |
| 第三方 Cookie、FakeShadowRoot | 原生源码实现 | 保留 SameSite/Secure、UA 内部 shadow root 等边界 |
| 扩展加载 / Widevine | SDK/原生集成 | 组件可用性与平台 DRM 需单独测试 |
| Docker 浏览器 CLI | 已发布 | Linux amd64/arm64 `.97`；保留非 root 和 sandbox 验证 |
| Docker CDP 服务 / Compose | 本轮源码扩展 | 仅受控本机入口；新镜像构建及部署须单独执行，不代表已发布旧标签自动获得新命令 |
| .NET / C# SDK | 待实现 | 当前维护 Python / Node；未创建空 NuGet 包宣称对齐 |
| Manager 图形管理应用 | 待实现 | 项目主页不是浏览器 profile 管理应用 |
| Pro 许可、会话额度、license-through-proxy | 第三方服务 | 不复刻第三方账户/许可协议，不计作缺失指纹补丁 |
| 第三方网站评分和通过率 | 未建立等价证据 | 当前对照未运行对方二进制；不能用 README 宣传替代同环境实测 |

## 输入接口的具体覆盖

| 入口 | Python 同步/异步 | Node Playwright |
|---|---|---|
| 逐次配置 | `human_config` | `humanConfig` |
| 低层 mouse/keyboard | move/click/dblclick/wheel、type/press | move/click/dblclick/wheel、type/press |
| Selector 操作检查 | page.click/hover/type/fill | page.click/dblclick 的有限子集 |
| force/trial | 委托原生调用 | 委托原生调用 |
| 配置合并 | 每次调用复制，不改变页面基础配置 | 每次调用复制，不改变页面基础配置 |
| Locator/Frame/ElementHandle | 保持原生，不新增逐次配置 | 保持原生，不新增逐次配置 |
| fill | 原生赋值，额外等待 editable | 保持原生 |
| timeout | 准备与原生操作共享预算，支持默认 timeout | 显式正数 timeout 共享预算；未指定时保留驱动默认 |

Puppeteer 本轮只改善低层包装和驱动参数兼容，没有新增 selector-level actionability；其真实浏览器验收尚未执行。以上是有限兼容范围，不代表完全替换对方 humanize 的 Locator/ElementHandle 与全部配置 schema。

## 本轮补齐的方向

1. 输入包装的逐次配置与元素操作检查：准确限制、原生选项语义和测试见两个 SDK README。
2. 容器 CDP/Compose 本地服务入口：见 [Docker](docker.md)，保持 sandbox 和非 root，不默认暴露到公网。
3. 图形预检统一入口：`tools/browser_feature_check.py` 运行 Canvas、WebGL 和 GPU identity 的独立诊断，绑定同一可执行文件 SHA-256，`incomplete` 不算通过。
4. 将新图形回归测试接入跨平台 contracts 工作流；这是源码/提取方法测试，不是完整 Chromium 编译。

以上本轮源码变更要在提交、构建、发布后才会进入新发行物，不修改正在运行的旧提交构建。

## 验证顺序

```bash
python -m pip install playwright Pillow
python tools/browser_feature_check.py \
  --browser /absolute/path/to/chromix/chrome \
  --output-dir /tmp/chromix-feature-check-new
```

- 必须指定本地已核验的 executable，不自动下载、不通过 init scripts 伪造检测输出。
- 输出目录必须新建，保存三份独立 JSON、日志和 summary。
- 未生效、超时、错误、缺报告均不能通过；覆盖缺失保留 `incomplete`。
- 预检仅覆盖限定图形机制，不能代替 [完整验收](fingerprint-acceptance.md)、[五作用域采集](fingerprint-coverage-matrix.md) 或真实设备矩阵。
- 下一步首先编译匹配的新二进制并复验 macOS 参数生效，再评估 WebGL 未覆盖路径、音频、布局、字体和代理路由。

旧审阅基线 `ba6e2c5e3be217bbcd67a8c58a5966c7508a8db4` 对应文档和历史批次证据在 Git 历史与 [functionality-followup.md](functionality-followup.md) 中保留，不能当成本轮结果。
