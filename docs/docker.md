# Linux Docker 镜像

此镜像将 [v154.0.8037.57 已发布 Linux 包](https://github.com/xiaozhou26/Chromix/releases/tag/v154.0.8037.57) 装入 Ubuntu 24.04，不重新编译 Chromium。提供简单的浏览器 CLI，**不预装 Python/Node SDK、Playwright 或远程浏览器服务**。保留发布包中的字体、资源及许可证，入口通过包内 `chromix` 脚本配置字体。

发布目标（工作流成功发布、管理员设置公共权限之后可用）：

- `ghcr.io/xiaozhou26/chromix:154.0.8037.57`
- `ghcr.io/xiaozhou26/chromix:latest`
- 两个标签均为 `linux/amd64` + `linux/arm64` 多架构 manifest；Docker 自动选择本机架构。生产环境建议使用版本标签或镜像 digest。

## 快速开始

只查看版本不启动渲染进程，不需要额外 sandbox 配置：

```sh
docker pull ghcr.io/xiaozhou26/chromix:154.0.8037.57
docker run --rm ghcr.io/xiaozhou26/chromix:154.0.8037.57 --version
```

无参数也等价于 `--version`。浏览器报告 Chromium 版本 `154.0.8037.57`。镜像默认以 `chromix` 用户（UID/GID `10001:10001`）运行，参数原样传给浏览器，没有隐式添加 `--no-sandbox` 或 `--disable-setuid-sandbox`。

### 默认保留 sandbox 的 headless DOM

Chromium 的非特权 user-namespace sandbox 需要宿主内核允许用户命名空间；Docker 默认 seccomp 常会阻止这些调用。请获取本仓库 `docker/seccomp.json`，不要仅为了启动浏览器就使用 `--privileged`、`--cap-add=SYS_ADMIN` 或 `seccomp=unconfined`。

以下命令从仓库根目录执行：

```sh
docker run --rm --init --shm-size=1g \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --security-opt="seccomp=$PWD/docker/seccomp.json" \
  ghcr.io/xiaozhou26/chromix:154.0.8037.57 \
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
  ghcr.io/xiaozhou26/chromix:154.0.8037.57 \
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
  ghcr.io/xiaozhou26/chromix:154.0.8037.57 \
  --headless --disable-gpu --no-sandbox --dump-dom \
  'data:text/html,<p>isolated-test-only</p>'
```

## 本地构建与验证

构建上下文是 `docker/`，不是整个 Chromium 源码仓库：

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
2. `--version` 含准确的发布版本；
3. `--network=none` 下读取只读挂载的本地 HTML，JavaScript 修改 DOM 后必须出现指定标记；
4. `chrome://sandbox` 必须显示 namespace 和 seccomp-BPF sandbox 均启用。

每次容器调用有超时，异常时也清理容器；不会自动用 `--no-sandbox` 重试。单元测试使用替身验证入口参数、失败退出、摘要核对和 smoke 判定逻辑，**不能替代实际容器 smoke**。在另一架构上构建可指定 `--platform=linux/amd64` 或 `linux/arm64`，但本地模拟执行不是原生架构验收；发布工作流使用两个原生 runner。

## 固定资产与完整性核对

`docker/download.sh` 固定下载版本、资产名与 SHA-256，先检查发布方 manifest 中对应资产的摘要与固定值一致，再实际运行 `sha256sum --check --strict`，通过后才解压。

| Docker 架构 | 发布资产 | 校验 manifest | 固定 SHA-256 |
| --- | --- | --- | --- |
| `linux/amd64` | `chromix-linux-x64.zip` | `SHA256SUMS` | `9b769a5b151b0778a42e6883dd12454817fcd0bef0268b1a93008b25052e0669` |
| `linux/arm64` | `chromix-linux-arm64.zip` | `SHA256SUMS-linux-arm64` | `26be9806543e2957ed82469830c17d4b38bc018b5c85dcaefd434fa2e50c2a60` |

主 `SHA256SUMS` 只有 x64，不作为 arm64 的依据。未知架构、下载失败、缺失/重复/不匹配摘要、ZIP 损坏均使 build 失败。基础 Ubuntu 标签和 apt 安全更新未按 digest 锁定，因此是浏览器资产固定，而不是整个镜像逐字节可复现。

`docker/seccomp.json` 来自 Microsoft Playwright 提交 [`ae935a43d9e376e4759548f6b3c6905c7b282333`](https://github.com/microsoft/playwright/blob/ae935a43d9e376e4759548f6b3c6905c7b282333/utils/docker/seccomp_profile.json)，许可保存在 `docker/seccomp.LICENSE`。在上游策略基础上增加了 `clone3 → ENOSYS (38)` 规则，让现代 glibc 回退到已经允许的 `clone`，避免默认 EPERM 导致线程创建失败；没有把默认拒绝策略改成全放行。

## GHCR 发布及管理员准备

`.github/workflows/publish-docker.yml` 独立于浏览器编译、SDK 发布工作流：

- 支持 `workflow_dispatch`，仅允许主仓库 `main` 分支执行发布；首次将变更合入 `main` 后即可手动运行。
- 自动触发仅限 `main` 上的 `docker/**`、`docs/docker.md` 和该工作流本身的变更。
- `ubuntu-22.04` 构建 `linux/amd64`，`ubuntu-24.04-arm` 构建 `linux/arm64`；不使用 QEMU。
- 每个 runner 先 build/load，再执行真实的版本、本地页面及 sandbox smoke。仅通过后登录 GHCR，推送**同一个已测试镜像**到带 run ID/attempt/架构的临时标签，并保存 registry digest。
- 两个架构均成功后，按 digest 合并 `154.0.8037.57` 和 `latest`，检查 manifest 恰好包含这两个 Linux 架构，再使用空 Docker 凭据配置验证匿名拉取。
- 使用 `GITHUB_TOKEN`，发布 jobs 声明 `contents: read` 和 `packages: write`，不需要额外长期 PAT。整个发布并发组串行运行，避免旧任务覆盖新标签。

**包可见性不是 Dockerfile/OCI 标签或 `packages: write` 能自动保证的。仓库管理员需处理：**

1. 确认仓库允许 GitHub Actions 创建/写入 GHCR packages，并允许所用 Docker Actions；已有 `chromix` package 时，在 Package settings → Manage Actions access 授予 `xiaozhou26/Chromix` 写入权限，或正确关联仓库并继承权限。
2. 首次推送后，在 `ghcr.io/xiaozhou26/chromix` 对应 GitHub Package settings 中把 visibility 改为 **Public**；仓库公开不代表新容器包自动公开。该操作应由有权限的管理员完成。
3. 工作流最后的匿名 pull 若因首次默认 Private 而失败，已推送的镜像/manifest 不会自动回滚。设置 Public 后重跑工作流并确认匿名检查通过，再宣布发布可用。
4. 可在未登录机器上执行上方 `docker pull`，并检查 `docker buildx imagetools inspect ghcr.io/xiaozhou26/chromix:154.0.8037.57` 显示两个平台。必要时先使用空 `DOCKER_CONFIG` 排除本机凭据掩盖权限问题。
5. 中间 `build-*` 标签可后续清理，但不要让清理策略删除当前版本/latest 所引用的镜像 digest。

本指南描述发布方式，不代表某次发布已经执行或当前 registry 权限已经配置完毕。
