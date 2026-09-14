# 功能与指纹后续批次

## 目标

补齐可执行的浏览器/SDK 功能，同时把“源码实现、原生编译、运行行为、真实设备
等价性”分别记账。此批次不是所有指纹缺口已经完成的声明。

## 计划

先完成 Puppeteer、显式 Cookie 迁移、原生 SOCKS5 TCP 认证和字体内容凭据；再把
它们接入匹配二进制门禁。完整渲染隐私、UDP/ICE/TURN、TLS/HTTP persona、真实
GPU/字体/媒体矩阵仍需后续后端工作，不能用展示参数代替。

## 实施

### Puppeteer

```js
import { launchContext } from '@xiaoxiaofeihh/chromix/puppeteer';

const context = await launchContext({
  executablePath: process.env.CHROMIX_BROWSER_PATH,
  args: ['--fingerprint=42'],
});
try {
  const page = await context.newPage();
  await page.goto('https://example.com');
} finally {
  await context.close(); // 同时关闭该入口拥有的浏览器
}
```

按所用 Node 版本安装兼容的 `puppeteer-core`，SDK 不下载驱动附带的浏览器。
入口包括 `launch`、`launchContext`、`launchPersistentContext`、`connect`、
`buildLaunchOptions`。持久化入口与 Playwright 使用同一原子发布的 seed 文件。
默认 `defaultViewport=null`，不偷偷采用 Puppeteer 的 800×600 视口。

`launchOptions` 是 Puppeteer 的原生选项，不是 Playwright 的 `contextOptions`。
`connect` 只连接现有浏览器，拒绝声称能修改已启动 persona 的参数。measured
devicePool 的 Puppeteer 准入尚未实现。HTTP 代理认证也没有通过
`page.authenticate()` 冒充浏览器级支持。humanize 适配了 Puppeteer 的 wheel
对象参数，覆盖启动/连接时已有页面和 SDK 包装的新页面；不承诺自动处理所有弹窗。
连接后的准备失败只 disconnect，不关闭调用者拥有的浏览器。

### 加密 Cookie 迁移

Node 根入口、`/cookies`、`/puppeteer` 均导出 `exportCookies`、`importCookies`。
Python 提供同步/异步的 `export_cookies`、`import_cookies`；安装
`chromix[cookies]` 启用 AES-GCM 依赖。

```python
import os
from chromix import export_cookies, import_cookies

# source 和 destination 是调用者自己的活动 Chromium context。
key = os.environ["COOKIE_PASSPHRASE"]
export_cookies(source, "cookies.enc", passphrase=key)
import_cookies(destination, "cookies.enc", passphrase=key)
```

约定：

- 文件格式 v1：固定 magic/version、16 字节随机 salt、12 字节随机 nonce、
  AES-256-GCM ciphertext + 16 字节 tag；完整 header 为 AAD。
- scrypt：`N=32768, r=8, p=1, dkLen=32`；密码为未经归一化的 UTF-8，12–1024
  字节；文件上限 16 MiB、Cookie 上限 10000。Node/Python 格式互通。
- 从调用者 context 创建的页面使用 `Network.getAllCookies/setCookies`，不以
  `Storage.*` 的默认 browser context 冒充目标 context。这些 Network 方法在
  当前固定 Chromium 中受支持，但已被 CDP 标为 deprecated，升级时须复验。
- 保留 host-only/domain、path、HttpOnly、Secure、SameSite、priority、source
  scheme/port、有效期限及 CHIPS partition key；opaque partition 明确拒绝。
- 只导入空目标，不清除已有 Cookie；过期项跳过；写入后重新读取并逐项比较。
  CDP 不提供整批事务，失败时目标可能部分写入；不会清空其他数据来“回滚”。
- 导出独占发布、不覆盖旧文件。POSIX 使用 0600；Windows 沿用目录 ACL。

这是**活动 context 的显式加密迁移**，不是修改 DPAPI/Keychain/OSCrypt，也没有
实现 `--fingerprint-portable-cookies` 的原生 profile 数据库开关。

### 原生 SOCKS5 TCP 认证

`0154`–`0157` 添加 RFC 1929 握手状态、分段异步读写、长度检查、认证失败与
降级拒绝、网络服务接线及原生 gtest。配置凭据时只协商 method 2，不回退到
method 0。目标域名继续由 SOCKS5 CONNECT 传给代理。

两个 SDK 的启动层从 `proxy` 提取认证，移除 Playwright 不支持的 SOCKS auth
字段，并通过启动环境传给补丁后的网络服务。凭据不写进 argv。配置仅匹配一个
规范化的代理 host/port；不响应 HTTP origin 的认证挑战。

```python
browser = launch(proxy={
    "server": "socks5://127.0.0.1:1080",
    "username": os.environ["PROXY_USERNAME"],
    "password": os.environ["PROXY_PASSWORD"],
})
```

原生环境协议为 `CHROMIX_SOCKS5_AUTH`，JSON 字段恰为
`version=1, host, port, username, password`，最大 4096 字节；用户名和密码分别
为 1–255 UTF-8 字节。SDK 从无关启动中清除继承的认证变量，不修改调用者进程
环境。认证是浏览器启动级，不接受 Node `contextOptions.proxy` 的独立认证。
保留变量名的大小写别名也会拒绝/清除；Python 字体环境合并不会重新引入已清除
的继承凭据，measured 启动也清除无关认证。

**必须使用包含此批次补丁的二进制。** 此实现仅限 TCP CONNECT，尚无浏览器
UDP ASSOCIATE、QUIC/WebRTC UDP 接线或外部路由验收。

### 字体内容凭据

`0151`–`0153` 扩展 DevTools `CSS.getPlatformFontsForNode`：

- 按实际 shaped-run 的 native typeface ID 分组，不再只按同名 PostScript 字体
  合并；输出可选 `fontTableHash` / `fontTableHashAlgorithm`。
- 算法 `chromix-font-tables-v1-sha256`：SHA-256 输入以
  `chromix-font-tables-v1\0` 开头，随后按 tag 排序，每表为四字节 tag、八字节
  大端长度、表内容。忽略 DSIG，清零 `head.checkSumAdjustment`，保留渲染表。
- 有界读取，缺失/异常表不生成摘要；不向网页 JS 暴露路径，不更改字体选择。
- `_font_provenance.py` 独立解析 SFNT/TTC，将使用中的字体表内容与文件 SHA-256
  和 collection face index 关联。多路径匹配保留全部候选，不任意选一个路径。

这能补“名字不是文件证据”的缺口，但不是每字形映射、变量字体实例、hinting、
DirectWrite/FreeType 栅格等价性证明。字体采样同时修正为先完成布局/字体加载，
避免刚插入 DOM 就读取空的 shaped-run 列表。
字体表解析模块继续支持不依赖包上下文的单文件加载；启动时才导入凭据环境
处理，既修复 standalone import 回归，也保留对继承认证变量的清理。

### 构建验证与远端更新

保留并合入远端 `aa57cbe` 的 Windows ARM64 工作，没有覆盖该批平台支持。
合并后修复 ZIP 验证器的跨平台差异：Python 在 Windows 上会规范化反斜杠，
现在检查 `orig_filename` 并拒绝规范化别名；测试夹具保留原始 ZIP 名称，另加
可在 POSIX 上重现 Windows 解码行为的测试。未放宽目录、链接、哈希或 PE 校验。

同步旧构建测试夹具中的 source verification 阶段，并增加失败必须阻断 GN
bootstrap/编译的回归。缓存策略仍为 push 优先使用、显式请求必须使用；旧
Canvas 摘要断言按 `5271cf4` 的原生 alpha 路径修复更新，不回退该功能。

## 验证

匹配二进制门禁由七套扩至十套，新增 `sdk_cookies`、`socks_auth`、
`font_provenance`；依旧固定 executable/source receipts，并重新校验原始观测。

```powershell
python -X utf8 tools/sdk_cookie_audit.py --browser C:/build/chrome.exe --output cookie-new.json
python -X utf8 tools/socks5_browser_audit.py --browser C:/build/chrome.exe --output socks-new.json
python -X utf8 tools/font_provenance_audit.py --browser C:/build/chrome.exe --output fonts-new.json
```

Cookie 探针使用两个独立进程和 sibling context；SOCKS 探针只服务自有回环
fixture，检查 window/iframe/worker、错误密码和认证降级，不解析或转发任意目标。
字体探针记录原生类型摘要与文件证据；名字相同不构成匹配。
门禁重新计算字体未匹配/多文件匹配等 gap，不信任保存的 binding 汇总；也验证
20 项 family/script 矩阵、正 glyph count、文件/face 凭据。Cookie 检查具体属性，
不只检查名字与两次输出相等。SOCKS 负向路径会拒绝任何成功认证/CONNECT 证据。

本地证据目录：`tmp_build/full-followup-20260914/`。

- Windows 最终聚焦回归 **1127 passed、18 skipped**（`focused-final-05.log`），
  覆盖 Python SDK、新功能探针、原生方法 shims、Canvas 验源、补丁应用/门禁及
  Windows ARM64 构建脚本与 PE/ZIP 测试夹具；不是 ARM64 编译。上轮
  缺少的独立 Canvas 上游文件已从固定 tag 补齐并匹配原记录 SHA-256，没有跳过该检查。
- Linux 全量 `tools/tests + sdk/python/tests` **4898 passed、343 skipped**，
  另有 5091 个 subtests 通过（`linux-final-regression-05.log/.json`）。在 Debian
  WSL 原生文件系统中使用隔离 Git 树、Python 3.13 和固定 Ninja 1.12.1；没有修改
  全局依赖或放行不受支持的 Ninja v7 缓存哈希。此结果包含合入的 ARM64 更新。
- Node 全套：Windows **368 passed、4 skipped**（`node-final-05.log`），Linux
  **372 passed、0 skipped**（`linux-node-final-03.log`）。此前一次缺少
  OpenSSL PATH 的失败保留在 `node-final-02.log`，补齐测试工具 PATH 后通过。
- 157 份补丁的 affected-file 树完整 reverse/forward 校验通过；新增七份在
  独立取得的 Chromium 152 文件上零 fuzz、零 offset 应用。
- SOCKS 完整原生握手方法在 transport/IO shims 上运行 135 种组合，包括同步、
  异步、混合完成及不同分片；ASan/UBSan 开启。这不是 Chromium 编译或真实网络。
- 字体摘要的原生 canonicalization 输入在 ASan/UBSan 下与独立 Python 编码器
  对照；测试 digest shim 收集输入字节，不假称测试了 Blink/BoringSSL 链接。
- Stock Chrome `153.0.8010.37` Cookie 迁移通过；Puppeteer-core `25.10.0` 的真实
  启动、连接/断开、持久化、humanize wheel 与 Cookie 回导 smoke 通过
  （`runtime-final-02/puppeteer-stock.json`、`cookies-stock-02.json`）。这只是 SDK/对照证据。
- Stock Chrome 的 SOCKS 原生认证探针按预期失败；字体文件采集 377 个、shaped-run
  观测 20 个，缺新增 DevTools 字段，保持 incomplete。
- Python wheel 和 npm tarball 在 scratch 构建并检查，新增模块与当前源文件一致；
  隔离安装后的 API/standalone 字体解析导入、三个 Node 子路径，以及 Python →
  Node / Node → Python 加密格式往返通过，未改动原有 `sdk/python/build/`。
  修复导入回归并合入 ARM64 更新后重新打包；凭据在
  `packaging-final-03/verification.json`。新 CDP 字段也通过
  PDL parser 检查；这些都不是原生 Chromium 编译。
- 首次 Linux 全量的 38 个失败 case 中，37 个在隔离 `405dedc` 基线复现；
  剩余字体 standalone import 是本批回归，已修复。旧夹具/缓存环境修正后全量
  通过；对照在 `linux-baseline-comparison-02.json`。隔离依赖重建曾缺少 psutil，
  collection 错误保留在 `linux-final-regression-02.log`，补齐后重新全量执行。
- Windows **尚无全仓全绿结果**。历史全量尝试为 4207 passed、709 failed、366
  skipped、12 errors；其中旧补丁数量断言已更新。POSIX fixture、Windows 缺少
  `O_NOFOLLOW`/chmod 语义和 CRLF 等代表性失败在隔离的 `405dedc` 基线复现，
  未逐项归因其余 Windows 失败；不能用聚焦或 Linux 通过替代 Windows 全仓通过。

本次尚无匹配 157 份补丁的原生浏览器验收，也没有新增合格真实设备记录。

新增 `.github/workflows/fingerprint-contracts.yml`，在 Linux/Windows/macOS
分别运行 SDK、探针验证器及 ASan/UBSan extracted-method 测试；独立下载并校验
九个固定 Chromium 源文件，避免把缺少 preimage 的 skip 当作 CI 通过。它不下载
浏览器、不重复调度 Chromium 构建，并覆盖字体单文件导入与 Windows ZIP 原始
名称校验；不等于三平台 native acceptance。新增 workflow 的实际运行结果另行
记录，不能从本地测试推定云端通过。

源码提交 `e993efa` 的三平台契约 CI 已实际通过：
[fingerprint-contracts / 34822278845](https://github.com/xiaozhou26/Chromix/actions/runs/34822278845)。
三套 artifact 都包含九个独立 preimage 的哈希凭据和无 failure/error 的 JUnit；
本地归档为 `ci-e993efa/`。这不改变原生浏览器/真实设备尚未验收的状态。
同次 SDK 发布流水线的 Python asset 枚举遗漏了远端新增的 `win-arm64`，在测试
前失败；跟进修复补齐该项，并增加直接执行发布检查脚本的四项正反向回归，
继续拒绝缺失、错配和额外平台，而不是取消枚举校验。

### 尚未完成的总清单

1. 匹配构建与十套 native acceptance、跨 OS/真实 GPU 的运行验收。
2. Canvas/WebGL/WebGPU 共用后端级隐私机制、完整色域/格式/设备丢失矩阵。
3. 字体完整 shaping/rasterization；真实设备池的 reviewed 全设备样本。
4. 屏幕物理切换、媒体/音频图/权限/编解码的统一实际设备模型。
5. UDP ASSOCIATE、真实 ICE/TURN/DNS/IPv6 路由和 packet/process 归因。
6. TLS/HTTP persona、H3/QUIC、恢复/复用、H2 流控完整策略。
7. 原生 portable-cookie 数据库模式、transparent-proxy 可观测协议及后端。
8. Intl/DST/配额写入、统一 timer quantization 和完整生命周期矩阵。
