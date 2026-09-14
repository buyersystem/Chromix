# 统一原生 GPU 策略与真实设备矩阵

## 目标

Canvas、WebGL、WebGPU 使用同一份不可变的启动策略，保留原生像素、能力和
资源生命周期；以实际操作证据统计设备覆盖，而不是靠修改 GPU 名称拼出设备。

**本轮实现的是共享原生后端契约，不是共用的跨平台隐私 rasterizer。**
不同 API、power preference、驱动和软件回退仍可合法选择不同后端/适配器。
匹配的 165-patch Chromium 二进制和跨 OS 实体设备验收尚未完成。

## 计划

1. 为避免不同接口分别响应冲突参数，将 GPU/Canvas 策略放入原子发布的 UXR
   快照，先验证配置再发布；非法配置不留下部分状态。
2. 为验证实际能力，执行分配、绘制、复制、读回、销毁和重建，保留原始字节与
   padding；Python oracle 独立计算结果，不信任 JS 的成功标签。
3. 为避免库存冒充覆盖，分开记录 OS 库存、CDP GPU 进程状态和各 API 选择，
   只让已复核的完整设备记录填入实际执行过的矩阵 cell。

## 实施

### 共享启动策略

```text
--uxr-gpu-backend=native
--uxr-gpu-backend=compatibility
```

- `native` 禁止 Canvas 像素/文本噪声、Canvas Bridge、WebGL persona 身份/能力
  路径及 WebGPU feature allowlist 改写；冲突的 synthetic 参数也不能重新开启。
- 未设置或 `compatibility` 保留原有行为，包括原有的 synthetic opt-in。
- 其他值、空值和不同大小写均在 `SetAll` 发布前拒绝；初始化后的快照不可更换。
- 不强制 `--disable-gpu`、ANGLE/Dawn 后端或特定显卡，也不改写驱动能力。
- Python sync/async 和 Node Playwright measured 启动自动使用 `native`。
  普通启动可通过原始 `args` 传入；没有新增同名高层 SDK 参数。
  Puppeteer measured admission 仍未实现。

| 补丁 | 接线 |
|---|---|
| `0158`–`0159` | `UxrGpuBackendPolicy`、验证、加锁读取、不可变快照 |
| `0160`–`0162` | Canvas 读回/导出/文本以及 Bridge endpoint 解析前的策略检查 |
| `0163` | WebGL persona 提前回到 native；扩展、precision、恢复后 context 沿用该策略 |
| `0164` | WebGPU feature 集与真实 `requestDevice` 协商使用同一策略 |
| `0165` | 修复未分配快照的 opaque Canvas 读回，复用上传 alpha 编码 |

`0165` 的原因：opaque HTML Canvas resize 后可能没有 snapshot，原生代码仅零
初始化 ImageData，导致画布内 alpha 也是 0。新 helper 仅在
`!snapshot && !HasAlpha() && !isContextLost()` 时写入画布内 alpha=1，支持
RGBA/BGRA8、F16、F32；64-bit 裁剪并检查 stride/溢出。画布外像素与 padding
保持原样，context loss、已有 GPU snapshot 和脚本持有的源数据不被改写。
这项修复尚未在匹配的新浏览器中验收，不能用 stock 运行证明补丁已生效。

### 探针和准入

设备记录保持 **schema v2**；当前探针为 **probe v4**，压缩 render 内容和
`render.json` 派生证据均为 **version/schema 2**；GPU 子矩阵为 version 1。
以下五份资产按顺序连接并共同哈希：

1. `canvas_chain_probe.js`
2. `render_integration_probe.js`
3. `gpu_backend_probe.js`
4. `device_render_probe.js`
5. `device_probe.js`

旧 probe-v3 记录不能用于当前准入，必须重新采集，不能只改版本或复制摘要。
每个 scope 仍限制解压到 8 MiB，保留重复键、非有限数、尾随流和 hash 校验。

五种 scope 均执行 window、iframe、worker、shared_worker、service_worker 对应矩阵：

| API | 新增原始证据 |
|---|---|
| Canvas | HTML/Offscreen，sRGB/P3，unorm8/F16，alpha 和 `willReadFrequently` 组合，bitmap roundtrip/close、源稳定性、resize 清空 |
| WebGL1/2 | 原生 identity/extensions，色域，RGBA8；支持时 F16/F32；client stride/offset/padding，WebGL2 PBO/pack skip，loss/restoration 与旧 texture 失效 |
| WebGPU | default/low/high/fallback 请求分别实测；RGBA/BGRA8、F16/F32、适用的 1/4 MSAA；1024-byte buffer 完整读回与 256-byte offset/row padding；外部图像、色域/alpha、resize/unconfigure、destroy/map/recreate |

可选能力缺失保留 gap；已宣称支持却执行失败不能改称 unavailable。
WebGPU 已销毁设备上的 mapping 预期为 **AbortError**，与固定 Chromium
`GPUBuffer::OnMapAsyncCallback` 的 Aborted 分支一致，不接受任意异常名称。
Canvas 半透明颜色沿用既有 visible-premultiplied 容差（max 2、mean 0.6、alpha 1）；
resize 清空值必须精确，不能用透明 alpha 隐藏非零 RGB。原 Canvas chain 的
lossless、alpha、OOB 和独立 codec 检查没有降低标准。

浏览器级 `CDP.SystemInfo.getInfo` 的原始 GPU 记录独立保存；稳定投影只移除动态
计数，保留设备/驱动、feature status 和后端字段。它**不是**每个 API 到物理
适配器的绑定证明。OS 库存来自 Windows CIM、Linux PCI sysfs 或 macOS
`system_profiler`；Apple vendor label 不被补造为 numeric device ID。
Linux 非 PCI GPU 未取到时继续 unavailable。`hardware_candidate` 不等于物理认证。

### 审计与矩阵命令

为单独验证 native 策略的优先级，审计依次启动 A、A restart、B conflict；B 去掉
冗余的 fingerprint-off/WebGL-real/disable-noise，加入冲突名称、seed、feature
allowlist 和无效 Bridge endpoint。原始观察必须在三次启动中稳定。

```powershell
python -X utf8 tools/gpu_backend_audit.py `
  --browser C:/matching-chromix/chrome.exe --output gpu-audit-new.json
python -X utf8 tools/collect_device.py `
  --browser C:/matching-chromix/chrome.exe --output device-new
python -X utf8 tools/fingerprint_corpus_review.py reviewed-corpus.json --output review-new.json
python -X utf8 tools/gpu_device_matrix.py `
  --matrix docs/gpu-device-matrix.json --review reviewed-corpus.json --output matrix-new.json
```

输出文件/设备目录必须是新路径。`--review` 接收已有的人工 review **manifest**，
不是上一条命令生成的汇总；工具重新校验 manifest、三次原始观测、证据和文件
hash。不生成 reviewer、过期时间或实体设备标签。无合格数据也可直接运行矩阵：

```powershell
python -X utf8 tools/gpu_device_matrix.py `
  --matrix docs/gpu-device-matrix.json --output matrix-empty-new.json
```

仓库计划包含 **33 个目标 cell**，不是 33 份设备样本：

| OS / architecture | Vendor families（每组分别要求 WebGL1、WebGL2、WebGPU） |
|---|---|
| Windows x64 / arm64 | x64: Intel、AMD、NVIDIA；arm64: Qualcomm |
| macOS x64 / arm64 | x64: Intel、AMD；arm64: Apple |
| Linux x64 / arm64 | x64: Intel、AMD、NVIDIA；arm64: NVIDIA |

每个 cell 按 OS/arch/API/vendor 统计去重 device tag，三次启动的五 scope 交集
必须完整。WebGPU cell 使用 default adapter 的实际操作；low/high/fallback
另作诊断。库存存在但 API 没有执行到的 GPU 不计数。controls、fixtures、虚拟/
软件/fallback 适配器和身份歧义不计数。同一实体可覆盖多个 cell，不能把 cell
计数相加当作实体总数。失败的 review 不留下部分覆盖。
`physical_attestation` 和 `exact_adapter_binding` 始终为 false：此处是经
复核记录与 API vendor-family 的关联，不是签名认证或精确适配器证明。

`--control-report gpu-audit.json` 可附 stock 诊断，永不增加设备计数。
空矩阵为 `incomplete` / `not_sampled`，退出非零，不会变成 passed。

## 验证

本地证据目录：`tmp_build/gpu-backend-20260914/`（原始主机证据不提交进仓库）。

| 本轮回归 | 实际结果 |
|---|---|
| Linux `tools/tests + sdk/python/tests` | **5113 passed、343 skipped**，另 5091 subtests；`linux-full-02.log/.json` |
| Windows 契约 CI 同范围聚焦 | **1296 passed、6 skipped**，另 302 subtests；`windows-contracts-01.log/.xml` |
| Node 全套 | Windows **372 passed、4 skipped**；Linux **376 passed、0 skipped** |
| 打包/隔离安装 | Python wheel + sdist、Node tarball，全部包模块/五份资产一致，三个 Node 子路径及新 CDP 采集入口通过；`packaging-02/verification.json` |
| 静态检查 | 165 patches、186 Python 源文件、六份 JS、workflow YAML 与 12-source 清单通过 |

Linux 使用 WSL 原生文件系统中的当前 Git 树快照、Python 3.13、Clang 和固定
Ninja 1.12.1，不使用 Windows/mixed 源树冒充独立 upstream。最后只补文档，
可执行源与测试对应 `source-current-02.tree`。Windows 仍没有全仓全绿的声明；
上述 skips 是缺失平台/源码前提，不是通过或硬件样本。云端契约 CI 也不是
Chromium build。早期失败日志保留，最终结果不叠加旧批次数量。

- `verified-165.json`：完整 165-patch affected-file 树反向/正向验证通过。
  `0158`–`0165` 零 fuzz/offset；这是补丁结构凭据，不是 Chromium 编译。
- `gpu_backend_sources.json` 固定四份独立 tag `152.0.7977.82` 源文件、core 前置
  片段和八份新补丁 preimage/output hash。契约 CI 与原有输入去重后独立取
  **12 份**源文件，缺失或 hash 不符直接失败。
- 原生方法依赖 shims 检查不可变 policy、并发读取、实际最终 Canvas noise/
  ImageDataBuffer 构造和 feature negotiation；alpha helpers 的 256 组格式/
  裁剪/极值检查启用 ASan/UBSan。平台宏模拟不是跨平台硬件测试。
- 本机 OS 库存：RTX 4060 Laptop GPU `10de:28a0`、AMD Radeon Graphics
  `1002:164e`，另有三个 ROOT 虚拟显示。没有将双 GPU 库存当作双 GPU 覆盖。
- `stock-gpu-02.json`：Chrome **153.0.8010.37** 完成三次 × 五 scope；六个
  window/iframe Canvas resize/ownership 检查失败。WebGL/WebGPU 原始操作无
  reported error，fallback adapter 为 null、保持 gap。default/low/high WebGPU
  均观察到 NVIDIA/lovelace。这不是目标 Chromium 152，也没有运行新补丁。
- `stock-collector-01/`：完整采集仍被 GPU opaque resize 及既有 Canvas
  alpha/OOB/lossless 失败拒绝，保留 raw browser/host/failure，不生成 record。
- `device-matrix-01.json`：**0 个 reviewed devices、33 个 not_sampled cell**，
  stock control 明确 excluded。没有新增合格实体设备记录。

GPU 审计作为第 **11** 套接入 [匹配二进制门禁](fingerprint-acceptance.md)，
不删除旧十套。仍需匹配新源的 Chromium 编译/运行、真实跨 OS 设备采集与 review；
完整共用后端级隐私、驱动覆盖、真实切换/故障和物理适配器绑定不在本轮完成声明内。
