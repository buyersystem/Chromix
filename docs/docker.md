# Linux Docker 镜像

此镜像将已发布的 Linux 包装入 Ubuntu 24.04，不重新编译 Chromium。当前固定 **amd64 和 arm64 均使用 [v154.0.8037.97](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.97)**。默认提供浏览器 CLI，也可显式使用 `serve`（别名 `cdp`）启动 CDP server。**不预装 Python/Node SDK 或 Playwright**；新增运行依赖为 Ubuntu 的 `python3`（仅标准库，用于进程监督和健康检查）及 `socat`（HTTP/WebSocket TCP 透明转发）。保留发布包中的字体、资源及许可证，入口通过包内 `chromix` 脚本配置字体。此处新增的 server/Compose 需要从当前源码构建；不表示 registry 中现有 `.97`/`latest` 已包含这些改动。

本次工作流发布的标签策略如下（镜像前缀均为 `ghcr.io/xiaozhou26/chromix`）：

| 标签 | `linux/amd64` 浏览器版本 | `linux/arm64` 浏览器版本 | 更新策略 |
| --- | --- | --- | --- |
| `154.0.8037.97` | `154.0.8037.97` | `154.0.8037.97` | 双架构 manifest，由原先仅 amd64 补齐 ARM64 `.97` |
| `amd64-154.0.8037.97` | `154.0.8037.97` | **不提供** | 同一 amd64 镜像的明确架构别名 |
| `arm64-154.0.8037.97` | **不提供** | `154.0.8037.97` | 同一 arm64 镜像的明确架构别名 |
| `latest` | `154.0.8037.97` | `154.0.8037.97` | 与 `.97` 相同的双架构镜像 |
| `154.0.8037.57` | `154.0.8037.57` | `154.0.8037.57` | 保留原有已发布双架构标签，**不覆盖** |

Docker 按平台选择镜像，当前 `.97` 与 `latest` 的两个架构均为同一浏览器版本。OCI `org.opencontainers.image.version` 写在各架构镜像配置中，amd64 和 arm64 均为 `154.0.8037.97`。生产环境建议使用版本标签 `:154.0.8037.97`、明确架构别名或镜像 digest。

原 `.57` 双架构发布已有[原生构建与匿名拉取验证](https://github.com/xiaozhou26/Chromix/actions/runs/37442214640)。本次双架构 `.97`/`latest` 的实际发布状态须以新的工作流运行及 registry 检查为准；修改配置不等于已经发布成功。

## 快速开始

只查看版本不启动渲染进程，不需要额外 sandbox 配置：

```sh
# AMD64 原生宿主：自动选择 linux/amd64。
docker pull ghcr.io/xiaozhou26/chromix:154.0.8037.97
docker run --rm ghcr.io/xiaozhou26/chromix:154.0.8037.97 --version

# ARM64 原生宿主：同一版本标签自动选择 linux/arm64。
docker pull ghcr.io/xiaozhou26/chromix:154.0.8037.97
docker run --rm ghcr.io/xiaozhou26/chromix:154.0.8037.97 --version
```

无参数也等价于 `--version`。两个架构的浏览器均报告 Chromium `154.0.8037.97`。ARM64 原生拉取 `.97` 标签会得到新版 ARM64 镜像，而不是旧版 `.57`。镜像默认以 `chromix` 用户（UID/GID `10001:10001`）运行。除 `serve`/`cdp` 子命令外，参数原样传给浏览器；所有模式都没有隐式添加 `--no-sandbox` 或 `--disable-setuid-sandbox`。

### 默认保留 sandbox 的 headless DOM

Chromium 的非特权 user-namespace sandbox 需要宿主内核允许用户命名空间；Docker 默认 seccomp 常会阻止这些调用。请获取本仓库 `docker/seccomp.json`，不要仅为了启动浏览器就使用 `--privileged`、`--cap-add=SYS_ADMIN` 或 `seccomp=unconfined`。

以下命令从仓库根目录执行，使用按本机架构选择镜像的 `latest`；需要固定版本时请按上表替换标签：

```sh
docker run --rm --init --shm-size=1g \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --security-opt="seccomp=$PWD/docker/seccomp.json" \
  ghcr.io/xiaozhou26/chromix:latest \
  --headless --disable-gpu --dump-dom https://example.com
```

这里 `--disable-gpu` 仅选择 headless 软件渲染，不关闭 sandbox。使用独立的 1 GiB `/dev/shm`，无需共享宿主 IPC。退出后容器自动删除；不自动开放 CDP 调试端口。

离线本地页面示例（`--network=none`，页面必须对 UID 10001 可读）：

```sh
mkdir -p /tmp/chromix-pages
printf '%s\n' '<!doctype html><p>chromix-local-ok</p>' > /tmp/chromix-pages/index.html
chmod 755 /tmp/chromix-pages
chmod 644 /tmp/chromix-pages/index.html
docker run --rm --init --network=none --shm-size=1g \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --security-opt="seccomp=$PWD/docker/seccomp.json" \
  --mount type=bind,src=/tmp/chromix-pages,dst=/pages,readonly \
  ghcr.io/xiaozhou26/chromix:latest \
  --headless --disable-gpu --dump-dom file:///pages/index.html
```

### Ubuntu 24.04+ 的 AppArmor 限制

若宿主启用了 `kernel.apparmor_restrict_unprivileged_userns=1`，仅 seccomp 配置可能仍报 `Operation not permitted` / `No usable sandbox`。由宿主管理员审阅后加载提供的专用配置：

```sh
sudo apparmor_parser --replace --skip-cache docker/apparmor.profile
```

然后在上述 `docker run` 的镜像名之前额外加入：

```sh
--security-opt=apparmor=chromix-docker
```

**安全边界说明：** 此配置使用 AppArmor `flags=(unconfined)` 并明确允许 `userns`，因此不保留 Docker 默认 AppArmor 文件访问限制；Docker namespaces、capability 限制、seccomp 和 Chromium 内部 sandbox 仍保留。严格生产环境应让管理员将所需 `userns` 权限整合进自己的受限 AppArmor 策略，而不是未经审查直接套用此配置。不要全局关闭宿主 AppArmor/userns 安全策略。若宿主禁用了非特权 userns 或 `user.max_user_namespaces=0`，此配置无法绕过内核限制；rootless Docker、嵌套容器也可能有额外限制。

`chrome-sandbox` 属于 root，但不设置 setuid；此镜像依赖非特权 userns sandbox。运行失败时不会自动降级为无 sandbox。可以把 URL 换成 `chrome://sandbox`，并加入 `--allow-chrome-scheme-url`，查看 `Layer 1 Sandbox: Namespace`、`PID namespaces: Yes`、`Network namespaces: Yes` 和 `Seccomp-BPF sandbox: Yes`。

### 仅用于可信、额外隔离环境的显式无 sandbox 模式

如果环境禁止 userns 且无法调整，只有在信任页面、使用额外隔离且不挂载敏感数据时，才考虑显式传入 `--no-sandbox`。**容器隔离不能替代浏览器 sandbox，不要用此模式访问不可信站点。** 示例仍以非 root 运行，只访问无网络的内联页面：

```sh
docker run --rm --init --network=none --shm-size=1g \
  --cap-drop=ALL --security-opt=no-new-privileges \
  ghcr.io/xiaozhou26/chromix:latest \
  --headless --disable-gpu --no-sandbox --dump-dom \
  'data:text/html,<p>isolated-test-only</p>'
```

## CDP server（显式启用）

从仓库根目录构建，使用独立的 2 GiB `/dev/shm`、现有 seccomp 规则及非 root 用户：

```sh
docker buildx build --load -t chromix:local docker
docker run --rm --name chromix-cdp --init --shm-size=2g \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --security-opt="seccomp=$PWD/docker/seccomp.json" \
  --publish=127.0.0.1:9222:9222 \
  --mount type=volume,src=chromix-profile,dst=/home/chromix/profile \
  --health-cmd='python3 /usr/local/lib/chromix/server.py healthcheck' \
  --health-interval=5s --health-timeout=3s --health-start-period=30s --health-retries=3 \
  chromix:local serve
```

需要 AppArmor 配置的宿主仍须按照上文加载策略并添加 `--security-opt=apparmor=chromix-docker`。不要使用 `--network=host` 或 `--ipc=host`。

另一个终端等待运行时健康，再访问发现接口（端口开放不等于浏览器就绪）：

```sh
for attempt in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' chromix-cdp)" = healthy ] && break
  [ "$(docker inspect --format '{{.State.Running}}' chromix-cdp)" = true ] || exit 1
  sleep 1
done
[ "$(docker inspect --format '{{.State.Health.Status}}' chromix-cdp)" = healthy || exit 1
curl --fail http://127.0.0.1:9222/json/version
# 停止 server，并回收浏览器和转发进程。
docker stop --time=10 chromix-cdp
```

端口路径固定为 **宿主 `127.0.0.1:9222` → 容器 `0.0.0.0:9222`（socat）→ 容器 `127.0.0.1:9223`（Chromium）**。仅发布 `9222`，不要发布内部 `9223`。不依赖 Chromium 的 `--remote-debugging-address`；Chromium 保持默认 loopback DevTools 监听。socat 原样保留 HTTP `Host` 以及 WebSocket Upgrade/数据帧，Chromium 据请求 Host 生成 `/json/version` 中的 `webSocketDebuggerUrl`。因此改用 `--publish=127.0.0.1:19222:9222` 并访问 `http://127.0.0.1:19222/json/version` 时，返回的 WebSocket URL 也应使用宿主 `19222`，无需手工替换 `9223`。请使用 `127.0.0.1` 或 `localhost`；Chromium 默认拒绝非 IP/非 localhost 的发现请求 Host，Compose 服务名不是通用发现地址。

宿主客户端可消费发现响应中的 `webSocketDebuggerUrl`，或使用 Playwright `chromium.connect_over_cdp("http://127.0.0.1:9222")`、Puppeteer `connect({browserURL: "http://127.0.0.1:9222"})`；这些 SDK 需在宿主另行安装。本模式是 Chromium CDP，不是 Playwright `launchServer` 的专有协议，不使用 Playwright `connect()`，也不是 WebDriver 服务。

**CDP 无鉴权，能控制浏览器、读取 profile 及其可访问资源。** 容器内 bridge 监听所有 IPv4 接口是为支持 Docker NAT，不代表允许公网访问。示例和 Compose 均只发布宿主 `127.0.0.1`；不要改为 `-p 9222:9222`、`0.0.0.0`、host networking，亦不要把不可信容器加入同一网络（同网容器仍可访问 bridge）。远程操作建议通过 SSH 本地隧道访问宿主 loopback；真正的多用户远程服务必须另行部署经过审查的认证、TLS 和访问控制，本镜像不提供这些能力。旧版 Docker（28.0.0 之前）有 localhost 发布端口可能被同一二层网络访问的限制，请使用维护中的 Docker 并结合宿主防火墙。

默认不添加 `--remote-allow-origins`。常见后端 CDP 客户端无需发送 Origin；如果客户端发送 Origin 并被 Chromium 拒绝，只为可信客户端显式传入精确来源，例如 `serve --remote-allow-origins=http://127.0.0.1:3000`。server 拒绝通配符 `*`；Origin 允许列表不是身份认证，不要把它当成 CDP 的安全边界。

### 参数、profile 与生命周期

- `serve` 和 `cdp` 等价，默认添加 `--headless`、`--no-first-run`、`--no-default-browser-check`、固定 CDP 端口和独立 profile；其他浏览器参数（例如指纹参数）按原 argv 传递。
- server 管理 `--remote-debugging*`、`--headless` 和 `--user-data-dir`，拒绝用户覆盖；需要自定义底层接线时使用普通 CLI 模式。不会自动关闭 sandbox；显式 `--no-sandbox` 的风险与前文一致。
- `CHROMIX_USER_DATA_DIR`：默认 `/home/chromix/profile`，必须为绝对路径。命名卷首次挂载空卷时继承镜像中该目录的 `10001:10001` 所有权。已有卷不会自动修复权限；宿主 bind mount 目录须预先让 UID/GID `10001:10001` 可读写（例如管理员执行 `mkdir -p ./chromix-profile && sudo chown 10001:10001 ./chromix-profile`）。不要用 `chmod 777` 或 root 浏览器绕过权限；一个 profile 同时只能供一个实例使用。
- `CHROMIX_STARTUP_TIMEOUT`：正数秒数，默认 `30`；在 bridge 上探测 `/json/version` 及其浏览器 WebSocket URL 成功后才输出 ready。启动失败或任一子进程退出都会使 server 非零退出，不降级。若调大此值，也应相应调整 healthcheck `start_period`。
- `CHROMIX_BROWSER_BINARY`：默认 `/opt/chromix/chromix`，仅用于显式替换浏览器可执行文件/本地测试；生产使用默认包装脚本保留字体配置。
- SIGTERM/SIGINT 转发至 browser/bridge 的进程组，最多等待 5 秒后强制回收，再 wait 直接子进程；退出码分别为 `143`/`130`。`--init` 负责回收孤儿后代，建议 Docker stop 宽限至少 10 秒。
- 健康检查通过 bridge 检查真实浏览器响应，不只检查 TCP listener。仅在 server 模式配置检查；镜像没有全局 HEALTHCHECK，避免把普通长时间 CLI 任务错误标为 unhealthy。检查不代替完整 WebSocket smoke，也不会自动重启 unhealthy 容器。

### Docker Compose

```sh
# 从仓库根目录执行；默认本地构建，不假定远端镜像已发布该功能。
docker compose -f docker/compose.yml up --build --detach --wait --wait-timeout 60
curl --fail http://127.0.0.1:9222/json/version
docker compose -f docker/compose.yml logs chromix
docker compose -f docker/compose.yml down
```

`docker/compose.yml` 复用 `docker/seccomp.json`，默认 UID/GID 10001、`cap_drop: ALL`、`no-new-privileges`、独立 2 GiB shm、init 和运行时健康检查；没有共享宿主 IPC、privileged 或关闭 sandbox。可用 `CHROMIX_HOST_PORT=19222 docker compose -f docker/compose.yml up --build --detach --wait` 改宿主端口，绑定地址仍固定为 `127.0.0.1`。需要 AppArmor 时，在本地 Compose override 中将 `apparmor=chromix-docker` 追加到 `security_opt`，保留其他安全选项并用 `docker compose ... config` 核对最终配置。

Compose 的 `profile` 命名卷在 `down` 后保留；`down --volumes` 会删除 profile、登录态等数据。不要将含凭据的 profile 共享给其他用户或非可信容器。

## 本地构建与验证

构建上下文是 `docker/`，不是整个 Chromium 源码仓库。默认按本机架构选择下载版本及 OCI 标签，无需手动传版本 build arg：

```sh
docker buildx build --load -t chromix:local docker
python3 -m unittest discover -s docker/tests -v
python3 docker/smoke.py chromix:local
```

如果需要上述 AppArmor 配置，加载之后运行：

```sh
python3 docker/smoke.py chromix:local --apparmor-profile chromix-docker
```

`smoke.py` 实际启动容器，不以 build 成功代替运行测试，检查：

1. 默认 UID 是 10001；
2. 本地镜像为受支持的 Linux 架构，OCI `org.opencontainers.image.version` 和 `--version` 均匹配该架构固定版本（amd64 和 arm64 均为 `.97`）；
3. `--network=none` 下读取只读挂载的本地 HTML，JavaScript 修改 DOM 后必须出现指定标记；
4. `chrome://sandbox` 必须显示 namespace 和 seccomp-BPF sandbox 均启用；
5. 使用相同 sandbox 策略启动 `serve`，随机发布到宿主 `127.0.0.1` 端口，等待 Docker healthcheck healthy，核对 `/json/version` 返回宿主端口，使用该 URL 完成真实 WebSocket Upgrade、`Browser.getVersion`、新建 target 和 JavaScript 求值，最后验证 SIGTERM 正常退出。该步骤需要 Docker bridge 网络，但测试页面不依赖外部站点。

`smoke.py` 默认从本地镜像配置读取架构，也可传 `--arch amd64` 或 `--arch arm64` 强制检查预期平台，防止测错镜像；工作流始终传入原生 runner 的架构。每次容器调用有超时，异常时也清理容器；不会自动用 `--no-sandbox` 重试。单元测试使用替身验证入口参数、失败退出、摘要核对和 smoke 判定逻辑；server 测试实际启动 fake browser/bridge 进程，以 Python HTTP/TCP mock 验证启动等待、Host 保留、WebSocket Upgrade/字节转发、健康检查和 SIGTERM/SIGINT/子进程故障清理（测试需本机 `9222`、`9223` 空闲）。这些测试**不能替代实际容器 smoke**。在另一架构上构建可指定 `--platform=linux/amd64` 或 `linux/arm64`，但本地模拟执行不是原生架构验收；发布工作流使用两个原生 runner。

本次新增 server 的本机验证已通过 fake browser/bridge 的进程与 HTTP/WebSocket 测试，以及 macOS Chrome `154.0.8037.98` 替代浏览器配合 Python TCP bridge 的发现 URL（包括不同 Host 端口）、CDP JavaScript、healthcheck 和 SIGTERM 检查。本机 Docker daemon 不可用且未安装 socat，**真实 socat、Linux `.97` 双架构镜像、Compose 容器运行及远端镜像完整 smoke 尚未执行**；本地替代验证不代表这些验收已经完成，也没有触发发布。

## 固定资产与完整性核对

`docker/download.sh` 按架构固定下载 release 版本、资产名与 SHA-256，先检查发布方 manifest 中对应资产的摘要与固定值一致，再实际运行 `sha256sum --check --strict`，通过后才解压。

| Docker 架构 | release 版本 | 发布资产 | 校验 manifest | 固定 SHA-256 |
| --- | --- | --- | --- | --- |
| `linux/amd64` | `154.0.8037.97` | `chromix-linux-x64.zip` | `SHA256SUMS` | `dc7dfd45d0dc1c1eea52f36a2dcdc4111e2780540392d30913107c50bf353aa5` |
| `linux/arm64` | `154.0.8037.97` | `chromix-linux-arm64.zip` | `SHA256SUMS-linux-arm64` | `2a6982052a74571e506bd171a058a114f8a4bc3f296b851440e96e4dae5f55fd` |

AMD64 下载地址为 `https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/chromix-linux-x64.zip`；ARM64 下载地址为 `https://github.com/xiaozhou26/Chromix/releases/download/v154.0.8037.97/chromix-linux-arm64.zip`。两个校验 manifest 均从同一个 `.97` release 下载，不跨版本复用。

Release 发布时，主 `SHA256SUMS` 合并 x64 与 arm64 两行，同时提供 `SHA256SUMS-linux-arm64`。Docker 的 amd64 下载器从主文件精确选取 x64 行，arm64 下载器使用独立 manifest；构建前须确保 ARM64 ZIP 与独立 manifest 已发布。未知架构、下载失败、缺失/重复/不匹配摘要、ZIP 损坏均使 build 失败。基础 Ubuntu 标签和 apt 安全更新未按 digest 锁定，因此是浏览器资产固定，而不是整个镜像逐字节可复现。

`docker/seccomp.json` 来自 Microsoft Playwright 提交 [`ae935a43d9e376e4759548f6b3c6905c7b282333`](https://github.com/microsoft/playwright/blob/ae935a43d9e376e4759548f6b3c6905c7b282333/utils/docker/seccomp_profile.json)，许可保存在 `docker/seccomp.LICENSE`。在上游策略基础上增加了 `clone3 → ENOSYS (38)` 规则，让现代 glibc 回退到已经允许的 `clone`，避免默认 EPERM 导致线程创建失败；同时允许 `chroot` 系统调用，供 Chromium 在自身用户命名空间内收紧根目录；这不会授予宿主 `CAP_SYS_CHROOT`，内核仍检查调用进程的权限。默认拒绝策略保持不变。

## GHCR 发布及管理员准备

`.github/workflows/publish-docker.yml` 独立于浏览器编译、SDK 发布工作流：

- 支持 `workflow_dispatch`，仅允许主仓库 `main` 分支执行发布；首次将变更合入 `main` 后即可手动运行。
- 自动触发仅限 `main` 上的 `docker/**`、`docs/docker.md` 和该工作流本身的变更。
- `ubuntu-22.04` 构建 `linux/amd64`，`ubuntu-24.04-arm` 构建 `linux/arm64`；不使用 QEMU。
- 每个 runner 先 build/load，再检查架构及 OCI version，并通过现有 `docker/smoke.py` 执行真实的对应版本、本地页面、sandbox 及新增 CDP server smoke。仅通过后登录 GHCR，推送**同一个已测试镜像**到带 run ID/attempt/架构的临时标签，并保存 registry digest。
- 两个架构均成功且固定版本一致后，用两个 `.97` 已测 digest 合并发布 `154.0.8037.97` 和 `latest`；另发布各自单架构的 `amd64-154.0.8037.97`、`arm64-154.0.8037.97` 别名。不写入原 `154.0.8037.57` 标签，并比较发布前后的旧标签 manifest 确认未变。
- 检查 `.97` 与 `latest` 均恰好包含 Linux amd64、arm64 两个平台，架构别名仅包含对应平台，且各 manifest 的 digest 与已测试镜像完全相同。使用空 Docker 凭据配置匿名拉取 `.97` 与 `latest` 各自的两个平台，以及两个架构别名，逐一核对架构正确且 OCI version 均为 `.97`，并检查旧 `.57` manifest 仍可匿名访问。合并 runner 不执行跨架构浏览器，实际运行验收仍由前面的原生 runner 完成。
- 使用 `GITHUB_TOKEN`，发布 jobs 声明 `contents: read` 和 `packages: write`，不需要额外长期 PAT。整个发布并发组串行运行，避免旧任务覆盖新标签。

**包可见性不是 Dockerfile/OCI 标签或 `packages: write` 能自动保证的。仓库管理员需处理：**

1. 确认仓库允许 GitHub Actions 创建/写入 GHCR packages，并允许所用 Docker Actions；已有 `chromix` package 时，在 Package settings → Manage Actions access 授予 `xiaozhou26/Chromix` 写入权限，或正确关联仓库并继承权限。
2. 首次推送后，在 `ghcr.io/xiaozhou26/chromix` 对应 GitHub Package settings 中把 visibility 改为 **Public**；仓库公开不代表新容器包自动公开。该操作应由有权限的管理员完成。
3. 工作流最后的匿名 pull 若因首次默认 Private 而失败，已推送的镜像/manifest 不会自动回滚。设置 Public 后重跑工作流并确认匿名检查通过，再宣布发布可用。
4. 可在未登录机器上执行上方 `docker pull`，并用 `docker buildx imagetools inspect` 检查 `ghcr.io/xiaozhou26/chromix:154.0.8037.97`、`:latest` 和保留的 `:154.0.8037.57` 均各有 `linux/amd64`、`linux/arm64` 两个平台。必要时先使用空 `DOCKER_CONFIG` 排除本机凭据掩盖权限问题。
5. 中间 `build-*` 标签可后续清理，但不要让清理策略删除当前版本/latest 所引用的镜像 digest。

原 `.57` 首次发布已通过两个原生架构的版本、页面 JavaScript、namespace/seccomp sandbox 检查，以及最终标签的匿名拉取检查。后续版本仍以对应工作流和 registry 中实际 manifest 为准。
