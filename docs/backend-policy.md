# 后端策略与验收

当前 Chromium **152.0.7977.82** 补丁栈共 **191 个补丁**。`0166`–`0191`
补齐能力查询、CSS/input、时钟、音频图、codec 和字体池的后端接线。配置按浏览器
启动固定，并传给 renderer/Worker；同一进程内的 BrowserContext 不拥有独立策略。
更新 SDK 不会让旧浏览器获得这些功能，需要重新构建。

## 默认行为与可选策略

| 范围 | 当前实现 | 仍需验证或实现 |
|---|---|---|
| Canvas / WebGL / WebGPU | 普通启动默认采用共享 `native` 策略；Canvas 噪声、Bridge 和 GPU 能力/名称覆盖不生效。显式 `compatibility` 保留旧行为，synthetic 测试未指定策略时仍沿用兼容模式。SDK 不再自动传入 `--ignore-gpu-blocklist`。 | 共同的跨 API 隐私 rasterizer、跨 profile 像素隔离、实体设备矩阵。原生默认保证一致性，不保证像素唯一性。 |
| WebAuthn / PDF / 语音 / 键盘 | 普通使用查询真实认证器、PDF viewer、语音库存和键盘后端，保留异步回调、权限拒绝和错误。伪造能力/语音表与固定美式映射只允许显式 synthetic 测试。 | 实体认证器操作、实际 PDF 显示、语音合成、物理按键布局。 |
| CSS / input | 偏好、强制颜色、触摸点数和 pointer/hover 写入 WebPreferences，再发布 settings；旧的 MediaValues 单独返回值覆盖已移除。 | 控件/滚动条、DevTools emulation 优先级和实体设备切换的匹配构建验收。HDR 保留真实 ScreenInfo。 |
| 时钟 | `Date` / Temporal wall clock 和 Blink TimeClamper 使用同一毫秒量化配置；Performance、事件、RAF、Idle 的公开值沿用其原生调用链。内部调度时钟不变。 | 原生 Worker、BFCache、冻结恢复和更多时区/历法矩阵。 |
| 音频 | 可选 `isolated` 模式在 AudioHandler 的实际 output bus 上处理一次，再交给下游节点。静音、符号零、异常浮点值、未处理帧和可写 AudioBuffer 语义保留。 | 实时音频性能、实体采集/输出、跨 OS DSP 等价。没有新增虚拟麦克风或扬声器。 |
| Codec | 五个视频 codec 家族共享禁用策略；覆盖原生支持查询、DecoderSelector、WebCodecs encoder、MediaRecorder 查询/启动/默认选择及 WebRTC 软件和硬件工厂。 | DRM、Media Foundation/远程 decoder 和 utility/GPU 进程中不经过这些入口的路径。不能声称所有解码后端都已隔离。 |
| 字体 | `restricted` 检查实际解析出的 family，含 `src:local`；缺字只能在显式池中查找。池缺失时使用空 Skia face 作为必要的 last resort，不泄漏其它宿主字体。Local Font Access 在原权限处理后筛选 family。下载的 author fonts 保留。 | 原生 Skia 空字体路径、可变字体、逐 glyph 文件绑定、DirectWrite/跨平台栅格等价。family 名不是文件 hash 证明。 |
| 代理 / 协议 | SDK 和直接启动在 proxy-server/PAC/auto-detect 下默认限制 non-proxied UDP，显式 native policy 或 no-proxy 优先。TLS/H2/H3 保留真实 Chromium 协议行为，并提供新的实测入口。 | SOCKS UDP ASSOCIATE、实际 DNS/IPv6/ICE/TURN/外网出口、H2 负载下优先级/流控、QUIC migration/0-RTT/Alt-Svc。 |

## 参数

以下参数的 `--uxr-*` 原名和 `--fingerprint-*` alias 均可通过 SDK `args`
或直接启动使用。显式 raw 参数优先；使用 `--key=value`。

| 参数 | 值及限制 |
|---|---|
| `--fingerprint-gpu-backend` | `native` / `compatibility`。普通启动默认 `native`。 |
| `--fingerprint-timer-resolution` | 十进制整数 **0–1000 毫秒**；0 或未指定保留原生精度。 |
| `--fingerprint-audio-render` | `native` / `isolated`，默认 native。 |
| `--fingerprint-audio-seed` | 非零十进制 uint64；isolated 未指定时使用 fingerprint seed。noise=false 或 fingerprint=off 会关闭隔离处理。 |
| `--fingerprint-font-policy` | `native` / `restricted`；restricted 要求 whitelist。 |
| `--fingerprint-font-whitelist` | 1–256 个逗号分隔的已安装 family；ASCII 忽略大小写，Unicode 原样保存。Linux `fonts_dir` / `fontsDir` 可提供实际字体和默认名单。 |
| `--fingerprint-codec-h264/vp8/vp9/av1/hevc` | 每个家族单独设 `native` / `disabled`；显式空值也表示禁用。旧 `supported[,smooth][,power-efficient]` 值只向下限制能力结果，不能安装 codec。 |
| `--fingerprint-max-touch-points` | 十进制整数 0–16。fine + 正 touch 数表示鼠标与触摸共存；none + 正 touch、coarse + 0 被拒绝。 |
| `--fingerprint-pointer` / `--fingerprint-hover` | `fine/coarse/none` 与 `hover/none`；none + hover 被拒绝。 |
| `--fingerprint-color-scheme` / `--fingerprint-preferred-contrast` | `light/dark` 与 `no-preference/more/less`。 |
| `--fingerprint-forced-colors` | `active/none`，亦接受 `true/false/1/0`。 |
| `--fingerprint-reduced-motion/reduced-transparency/inverted-colors` | 每项独立布尔值；SDK 与直接启动使用一致的布尔规范化。 |
| `--fingerprint-hdr` | 仅 `native`；配置一个查询字符串不能提供 HDR 显示后端。 |
| `--fingerprint-keyboard-layout` | 普通使用仅 `native`；`us/en-US` 需要 synthetic 测试。 |

音频隔离使用 `2^-20` sample grid 和 seed 决定的稳定舍入阈值，每次处理对样本的
误差不超过 `2^-20`。已经位于 grid 上的样本再次处理不变；块切分不改变结果。
各 DSP 节点的后续运算仍可产生差异，这不是整个图或跨系统的总误差界。

```python
from chromix import launch

browser = launch(
    fonts_dir="/path/to/installed-fonts",
    args=["--fingerprint=42", "--fingerprint-font-policy=restricted",
          "--fingerprint-audio-render=isolated", "--fingerprint-timer-resolution=7",
          "--fingerprint-codec-hevc=disabled"],
)
```

## 浏览器验收入口

[总验收器](fingerprint-acceptance.md) 现运行 **15 项**，新增：

- `backend_policy`：native、isolated、同 seed 重启、另一 seed 四次启动；检查计算后
  CSS、强制颜色、CDP touch/key 事件、window/iframe/OOPIF/三类 Worker 的时钟与 DST、
  真实 freeze/resume 事件、BFCache 时钟连续性、重载、AudioBuffer/Analyser/Worklet/静音实时播放链、字体池、下载
  author font 和能力库存。BFCache 未实际恢复、Temporal/Local Font Access 等缺失
  以及空语音/键盘库存保留 gap；多次运行返回相同的无效库存也不能通过。
  这里的 CDP 输入和静音图 tap 不证明实体输入/音频设备。
- `media_policy`：native、五类全部禁用、仅保留 VP8；查询 WebCodecs、MediaRecorder、
  MSE、MediaCapabilities 和 RTC，再实际编码、录制、解码、MSE append 与本地 RTC
  传输。用 native 生成的数据再次启动禁用配置，验证实际拒绝。VP8 是必需控制；
  缺少原生硬件 codec fixture 不计作已完成的拒绝测试。音频独立录制必须保持可用。
- `transport_lifecycle`：服务器记录 ClientHello/连接绑定和真实 TLS 1.3 session
  state，经 H2 GOAWAY 后要求重连恢复，再验证请求复用；full/resumed 分开比较。
- `quic`：固定 aioquic 1.2.0，在自有回环 origin 强制 QUIC，读取实际 QUIC v1/H3
  协商、transport parameters、SETTINGS、伪首部与连接复用。未知参数保存长度和
  摘要，不保存 ticket/key/CID/token 内容。该路径不代表代理或外网 UDP 已通过。

```sh
python3 tools/fingerprint_backend_audit.py --browser /path/to/chrome --output /tmp/backend-new.json
python3 tools/fingerprint_media_policy_audit.py --browser /path/to/chrome --output /tmp/media-new.json
python3 tools/fingerprint_transport_lifecycle_audit.py --browser /path/to/chrome --output /tmp/tls-new.json
python3 tools/fingerprint_quic_audit.py --browser /path/to/chrome --output /tmp/quic-new.json
```

输出路径必须是新文件。总验收器还要求匹配的源码凭据与 executable SHA256，重新
核验原始观测，不信任保存的 `passed` 或 gap 摘要。超时和缺数据属于失败。

## 已有验证与边界

`tools/tests/fixtures/backend_completion_sources.json` 保存固定 Chromium 输入、
core 前置片段和新增补丁前后 hash。`fingerprint-contracts.yml` 独立下载并校验
这些 Chromium 文件，在 Linux/Windows/macOS 跑最终方法契约与 SDK 回归。
带 sanitizer 的 C++ 测试执行实际提取的方法，依赖仍是 shim，不能替代 Chromium 编译。
LocalFontFaceSource 的 `src:local` 经 FontCache 解析后才应用 variation settings，
因此会经过新增的 resolved-family 检查；下载的字体走独立 author-font 路径。

本次本地环境为 FreeBSD 14.3、Python 3.11、Node 22.22.2。C++ 夹具使用 GCC 13，
保留 sanitizer 和 `-Werror`，通过独立进程的 ASLR 设置运行。初始新增后端方法、
源码和协议批次 **263 passed**；最终验收校验器、发布门禁、快照/GPU 策略复核
**257 passed / 29 skipped**，GPU 兼容性与 seed 回归 **786 passed / 36 skipped**。
完整 Python SDK **433 passed / 1 skipped**，完整 Node SDK 串行执行
**443 passed / 0 skipped**。191 个补丁的 lint、修改文件语法与 workflow YAML
检查通过；这些批次有重叠，不能相加为独立测试总数。

扩展工具回归仍有宿主限制：FreeBSD 缺少当前快照发布要求的原子 no-replace 实现，
Linux 专用对象图检查不能在此运行，set-ID 夹具的权限修改被宿主拒绝。GNU
`readlink` / `timeout` 的前置条件已在局部 PATH 中修正并复核，没有改变生产平台
门禁。具体批次、剩余失败和跳过情况记录在
[FINGERPRINT_STATUS.md](../FINGERPRINT_STATUS.md)，不声称全仓库测试通过。

尚无匹配 191 个补丁的可执行 Chromium。已执行的 Python/OpenSSL、aioquic
真实回环交换不等于新增浏览器探针的 native 验收。跨平台 CI 已接入新源码清单和
回归入口，本地没有运行该 CI。实体设备矩阵仍是 **0 reviewed devices / 33
unsampled cells**，不得用这些工具测试补填。
