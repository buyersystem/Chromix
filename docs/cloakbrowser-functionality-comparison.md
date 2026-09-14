# CloakBrowser 公开功能对照

## 目标与对照基线

按公开接口和行为核对功能，不把参数存在、源码实现、原生构建通过和真实设备
验收混为一谈。审阅快照为：

- [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser/tree/ba6e2c5e3be217bbcd67a8c58a5966c7508a8db4)，固定提交
  `ba6e2c5e3be217bbcd67a8c58a5966c7508a8db4`。
- 该快照 README：SDK `0.5.10`，宣称 87 个 C++ 补丁；Stable 浏览器
  `151.0.7922.108.6`，macOS 为 `151.0.7922.108.3`。
- 完整公开 Git 树有 255 个条目，没有 `.patch` 文件。公开内容包括 MIT SDK
  包装层和功能说明；其
  [BINARY-LICENSE](https://github.com/CloakHQ/CloakBrowser/blob/ba6e2c5e3be217bbcd67a8c58a5966c7508a8db4/BINARY-LICENSE.md)
  将构建配置、补丁及分发二进制列为其专有部分。
- 因此，下表是**公开功能对照，不是 87 份 C++ 源码移植清单**。Canvas
  `0149`/`0150` 是基于 Chromium `152.0.7977.82` 原生路径的独立修复。

## 实施对照

“已有”表示当前源码或 SDK 中有相应实现，不自动表示新浏览器二进制已通过验收。
参数取值和完整边界见 [fingerprint-flags.md](fingerprint-flags.md)。

| 公开功能 | Chromix 当前状态 | 实现位置与边界 |
|---|---|---|
| Python / Node Playwright 启动、上下文、持久化配置 | 已有 | `sdk/python/chromix/api.py`、`sdk/node/index.js`；一个浏览器启动共享一份 persona，不是每个 BrowserContext 一份身份 |
| 异步 Python、Node camelCase API | 已有 | Python `launch_async` 等；Node `launchContext`、`launchPersistentContext` 等 |
| Puppeteer 专用入口 | 尚未提供 | 没有对应 `cloakbrowser/puppeteer` 的独立导出模块；Playwright API 兼容不等于 Puppeteer 即插即用 |
| 自动下载、版本选择、缓存 | 已有，分发方式不同 | 下载 Chromix Release 并校验可用的归档清单，不调用 CloakBrowser 分发/许可服务 |
| `humanize` 行为包装 | 已有 SDK 实现 | 鼠标、键盘和滚动包装；不据此宣称通过特定检测或取得某个评分 |
| 稳定种子和持久化 profile | 已有 | `--fingerprint`；持久化目录保存种子，显式种子优先 |
| 平台、品牌、版本、UA 与 Client Hints | 已有 | `0004`、`0036` 等；描述浏览器身份，不替换 OS 或浏览器引擎 |
| CPU/RAM 数值 | 已有 | `0014`、`0015`；不是 CPU 核数、内存或 V8 限额的实体模拟 |
| 屏幕、可用区域、taskbar | 已有后端实现，待匹配构建验收 | `0125`–`0128`；显示几何、窗口初始化和子 frame 配置，不仅替换 getter |
| 时区、语言、代理 GeoIP | 已有 | 使用有效代理解析启动配置；显式值优先，失败不偷偷改走直连 |
| Canvas 种子噪声/不同 profile 生成不同像素 | 刻意不同 | public 模式保留原生渲染；旧噪声和 Bridge 只在显式 synthetic 测试下启用，不声称完整设备模拟 |
| Canvas 读、写、导出一致性 | 本次补齐两条原生路径 | `0149` 为不透明上传生成私有 opaque 数据；`0150` 裁剪 GPU 读回并保留调用者步长，见 [Canvas 修复](canvas-chain.md#native-upload-and-readback-repair-2026-09-14) |
| 自动 GPU 模板与 WebGL vendor/renderer | 刻意不同 | public 的 seed-only 模式保留原生标识；显式 WebGL 展示受真实上下文约束，软件上下文不假称硬件 GPU |
| WebGPU 身份和能力 | 刻意不同 | `0148` 保留实际 Dawn adapter 的完整身份；不把 WebGL 和 WebGPU 强行绑定为同一 GPU |
| `--fingerprint-noise=false` | 已有 | 保留身份 seed，关闭已有扰动路径；不是承诺有四套完整的 Canvas/WebGL/audio/client-rect 噪声引擎 |
| `--fingerprint=off` | 已有 | 原生 persona 调试入口；显式 timezone/locale 等独立设置仍然生效 |
| 存储配额、Storage Buckets、legacy quota | 已有后端实现，待匹配构建验收 | `0129`；统一 quota policy，保留真实使用量、磁盘不足、错误及 DevTools 优先级 |
| Windows 字体 metrics | 已有有限对齐 | `0135`–`0138`；需要实际匹配字体，不等于完整 DirectWrite 栅格、hinting 或 shaping |
| Windows SAPI voices 与 opt-out | 已有展示控制 | 真实 Windows 使用原生 inventory；跨 OS 列表不安装语音引擎、不创造可合成声音 |
| WebRTC IP、SDP、stats 与 `auto` | 已有展示及启动解析 | `0139`–`0145`；不更改真实 ICE/socket/TURN 路由，远端数据不替换 |
| 第三方 Cookie opt-in | 已有后端策略 | `0130`；不改变 SameSite/Secure 或站点专属设置 |
| `FakeShadowRoot` | 已有 | `0131`–`0134`；显式开放 author closed roots，保留 UA 内部 roots 的原生边界 |
| 扩展目录与 Widevine | 已有，不重复实现 | `extension_paths` / `extensionPaths`、Widevine 发现和组件集成；实际组件/平台能力仍需运行检查 |
| `--fingerprint-transparent-proxy` | 尚未实现，公开语义不足 | 参考快照只有行为描述，没有协议/补丁实现；不能添加一个空参数就标记完成 |
| `--fingerprint-portable-cookies` | 尚未实现 | 需要真实 Cookie 加解密、跨机器 profile 迁移和旧数据兼容测试；SDK 拷贝目录不等于此能力 |
| 原生浏览器 SOCKS5 认证 / UDP ASSOCIATE | 尚未补齐参考能力 | SDK **元数据查询客户端**的 SOCKS 认证支持，不等于 Chromium 浏览器 socket 后端支持 |
| Pro 许可、会话数、`--license-through-proxy` | 不属于本项目接口 | 不把第三方商业许可协议包装成浏览器指纹功能 |

参考参数表固定在
[该提交的 Additional Flags](https://github.com/CloakHQ/CloakBrowser/blob/ba6e2c5e3be217bbcd67a8c58a5966c7508a8db4/README.md#additional-flags)，
不是随 `main` 漂移的版本。公开 README 的检测成功、评分和“完全一致”等表述是
对方的说明，不是 Chromix 的测试结果。

## 验证与下一步

1. **先修正原生像素契约。** 不透明 Canvas 必须实际写入 opaque alpha；GPU
   越界读回必须与软件路径一样裁剪，保留透明 padding 和真实 stride。
2. **再验证匹配二进制。** 使用固定 Chromium 152 构建运行独立诊断、完整
   Canvas chain、五作用域采集及设备池准入。源码测试不能替代此步骤。
3. **缺口单独立项。** Puppeteer 适配、portable cookies、浏览器 SOCKS5/UDP
   后端需要各自的实现与验收；透明代理先建立可观测协议契约。

快速对照命令及本次仍失败的 stock Chrome 证据见 [canvas-chain.md](canvas-chain.md)。
没有降低原有准入阈值，没有新增“合格设备”记录，也没有将 synthetic 或 CPU-only
输出当作真实设备通过证据。
