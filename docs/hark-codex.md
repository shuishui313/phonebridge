# 通过 Hark SSH 使用 PhoneBridge

设计中的首期路径是 Hark → SSH → macOS 或 Linux 主机上的 Codex → stdio MCP → PhoneBridge。主机运行 Codex CLI、PhoneBridge 和 Mock Driver。若主机安装完整 Xcode，也可单独连接 Apple Simulator 诊断驱动。**主机侧链路已有可复跑证据**：带中断客户端时直连与临时 SSH 回环都是 51 项、`host_chain_passed: true`。本机安装的 Hark Mac 应用没有字面 SSH 控件，真实模型的只读 MCP 注入已单独复跑通过。现场步骤和边界见下文与 [测试报告](test-report.md)。

## 主机侧可复跑链路（已验证）

断线重连由 `phonebridge/relay.py` 承担：后端进程持有 `CoreAPI`、审批端点与提交台账状态，在 0700 私有目录的 0600 Unix 套接字上监听；每次 stdio 连接由 `python3 -m phonebridge.relay stdio` 代理并独立完成 MCP 握手。客户端断开后可重新连接同一后端，设备和任务状态保留。空闲连接不会阻塞新连接，后端逐条处理共享核心的请求。`phonebridge/hark.py` 负责发现或指定客户端、构造 inline MCP 配置、包装 SSH 命令、解析结构化事件，并从客户端环境中移除审批令牌。

自动化脚本用 `backend` 子命令启动后端。以下是主机侧合成写入测试使用的同账号配置。真实 Hark 客户端联调请从后面的 Mock 只读步骤开始。

```sh
PB_DIR="$(mktemp -d /tmp/pb-hark.XXXXXX)"
chmod 700 "$PB_DIR"
export PB_HARK_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"

python3 -m phonebridge.cli --driver mock \
  --approval-socket "$PB_DIR/approval.sock" \
  --submission-state "$PB_DIR/submissions.jsonl" \
  --approval-token-env PB_HARK_TOKEN --require-approval-isolation \
  backend --socket "$PB_DIR/mcp.sock"
```

客户端侧的 MCP 启动命令指向 `python3 -m phonebridge.relay stdio --socket "$PB_DIR/mcp.sock"`。令牌只注入后端与可信侧审批 CLI 的环境，不出现在客户端命令行或环境变量里。

完整主机链路用仓库自带的确定性客户端，一次跑完成功、失败、中断、重连：

```sh
python3 scripts/hark-codex-spike.py --deterministic --text 'Harness probe'
python3 scripts/hark-codex-ssh-loopback-spike.py \
  --hark-spike-arg=--deterministic \
  --hark-spike-arg=--text --hark-spike-arg='Harness probe'
python3 scripts/hark-codex-ssh-loopback-spike.py --ssh-precheck-only
```

两条完整命令的期望是 `chain_scope: full`、`check_count: 51`、`host_chain_passed: true`、`hark_client_verified: false`、`hark_ui_verified: false`。只做 SSH 预检时期望 `chain_scope: ssh_precheck`、`ssh_loopback_precheck: true`、`host_chain_passed: false`。回环脚本在 127.0.0.1 上启动临时 key-only `sshd`，使用临时主机密钥、客户端密钥与 `known_hosts`，不修改系统 SSH 配置、不启用 Remote Login。不带 `--ssh-precheck-only` 时，它会自动以 `--transport ssh` 调用 `scripts/hark-codex-spike.py` 并透传 `--hark-spike-arg`。

不传 `--deterministic` 或 `--interruption-codex-path` 时，脚本只跑 5 次会话、33 项判据。这 33 项可以全部为真，此时 `partial_chain_passed: true`，但 `host_chain_passed` 保持 `false`，`interruption.verified` 也是 `false`。差出的 18 项是中断准备、丢弃回执、重连回读和重放拒绝，并没有从脚本里删掉。2026-11-12 之前裸跑也会把 `host_chain_passed` 标成 `true`，所以交接文档里的 51 和现场裸跑的 33 会同时出现。

自定义客户端仍可使用：

```sh
python3 scripts/hark-codex-spike.py --codex-path <客户端路径> \
  --interruption-codex-path <中断客户端路径> --text 'Harness probe'
```

自动化测试 `tests/test_hark_spike.py` 覆盖完整直连、缺中断客户端的部分链路，以及 SSH 回环上的 `--deterministic`。`tests/test_relay.py` 覆盖重连状态、stdio 代理、套接字权限与 MCP 握手。证据数值见 [测试报告](test-report.md)。

**边界**：确定性客户端证明的是传输、审批、中断和防重。它不证明 Hark 窗口里能发出自然语言，也不证明真机或真实第三方 App。`hark_client_verified` 与 `hark_ui_verified` 恒为 `false`。真实 Codex CLI 的只读工具注入另见下一节，那一项已经在本机复跑，不并进这 51 项。

## 2026-11-12 本机可执行路径

复核机器是用户的 Mac（worker phonebridge-mac），代码基线 `b0aeafc`。Xcode 27.2，已启动的模拟器是 iPhone 18 Pro。下面三条路径里，前两条已经在这台机器上跑通；第三条停在必须由用户发出的一条合成 Hark 任务。

### 已跑通 · 确定性主机链路

```sh
python3 scripts/hark-codex-spike.py --deterministic --text 'Harness probe'
```

2026-11-12 的直接复跑与 `tests/test_hark_spike.py` 都得到 `check_count: 51`、`host_chain_passed: true`、9 次会话、四类证据的 `verified` 都为真。SSH 回环加上 `--hark-spike-arg=--deterministic` 同样是 51 项。脚本复制仓库给真实 Codex 当工作目录时会跳过 `build/`。本机 `build/` 在 XCUITest 之后约 1.7GB，旧的整仓复制会把一次链路拖到几十秒，并在 Xcode 还在写 DerivedData 时卡住。

### 已跑通 · 真实 Codex CLI 的 MCP 注入（与防重分开）

Codex CLI 在 ChatGPT.app 内，版本 `codex-cli 0.167.0-alpha.3.2`，`codex login status` 为已登录，本次没有新的登录。`phonebridge.hark.build_codex_command` 把 relay 的可执行文件和参数分开，命令里只有一个 Python。对常驻 Mock 后端连续三次 `codex exec --json --ephemeral`，提示只调用 `phone_status`：三次都是退出码 0，事件里有 `phonebridge` / `phone_status`，返回 `ok: true`、`driver: mock`。同一次探测里，不经过模型、直接对 relay 做 MCP 握手，`tools/list` 为 14 个工具，`phone_status` 同样是 mock。

对照实验把 MCP 配回 `b0aeafc` 之前的形状：`command` 是 Python，`args` 又以同一个 Python 开头。模型回合退出码仍是 0，但 `tools` 为空。工具没被注入，请求没有进入 PhoneBridge，因此和去重、防重放无关。stderr 里的 `failed to refresh available models: request timed out` 在成功的三次里也出现过，没有挡住 `phone_status`。

同一种配置再要求连续调用 `phone_status`、`intent_run`（`contacts.lookup`）和 `phone_inspect`：退出码 0，耗时 57.5 秒。状态是 mock，查到 4 个合成联系人，`messages` 长度为 0，三次调用的 server 都是 `phonebridge`。

这条路径证明：在当前命令形状下，真实 Codex 能发现并调用 PhoneBridge 的只读工具，结果能从 `codex exec --json` 读回。它不证明 Hark 窗口、不证明中断后的防重，也不证明可写提交。

### 本机 Hark 表面

`/Applications/Hark.app` 的 bundle id 是 `com.harklabs.hark.mac`，短版本 `0.1.23`，可执行文件是 Tauri 壳 `hark-desktop`。二进制里有 `device_link`、`hark-device`、`This Mac`、`Computer` 和设备权限问答，会把本机工具以 MCP 帧交给 Hark 页面。没有 `OpenSSH`、`Remote Login` 或 `Codex` 这些字样。已启动的 iPhone 18 Pro Simulator 里没有 Hark。单实例套接字 `/tmp/com_harklabs_hark_mac_si.sock` 能连上，但 1 秒内没有协议横幅，不能把它当成 PhoneBridge 的 MCP。

检查时应用已经停在既有登录会话里，界面是聊天和项目。这次没有新登录、没有在输入框打字、没有发任何消息。系统 22 端口上已有 launchd 拉起的 `SSH-2.0-OpenSSH_10.3`。这次没有打开 Remote Login，也没有改 SSH 配置，更没有用该端口登录。Hark Mac 应用的设备连接走的是它自己的 device link，不是这条系统 SSH。

### 留给用户的一步

要补上「从 Hark 窗口读回结果」，在已经打开的 Hark Mac 会话里发一条合成任务即可。不要新登录，不要开 Remote Login，不要连接真机，不要打开真实聊天或支付应用。

1. 在仓库根目录另开终端，只启动 Mock 只读后端：

```sh
PB_ROOT="$PWD"
PB_PYTHON="$(command -v python3)"
PB_MCP_DIR="$(mktemp -d /tmp/pb-hark.XXXXXX)"
chmod 700 "$PB_MCP_DIR"
echo "PB_MCP_DIR=$PB_MCP_DIR" >&2
"$PB_PYTHON" -m phonebridge.cli --driver mock backend --socket "$PB_MCP_DIR/mcp.sock"
```

2. 后端会占住那个终端。另开一个终端，把目录指回仓库和上一步 `mktemp` 打印的路径，再打印 Codex 参数。它使用当前已登录的 Codex，不往 `~/.codex/config.toml` 写入服务器。

```sh
export PB_ROOT="/path/to/phonebridge"
export PB_MCP_DIR="/tmp/pb-hark.XXXXXX"
cd "$PB_ROOT"
python3 - << 'PY'
import os, shlex, sys
from phonebridge.hark import build_codex_command, find_codex
root = os.environ["PB_ROOT"]
sock = os.environ["PB_MCP_DIR"] + "/mcp.sock"
prompt = (
    "Use ONLY the phonebridge MCP tools. Call phone_status once. "
    "Call intent_run with capability contacts.lookup. Call phone_inspect once. "
    "Do not change device state."
)
print(shlex.join(build_codex_command(
    find_codex(None), sys.executable, root, sock, prompt, root, trusted_project=root)))
PY
```

如果 Hark 里能看到 Computer 或「这台 Mac」，把打印出的这一整行交给它在本机执行，并在 Hark 里读回结果。期望三次工具都属于 `phonebridge`，`phone_status` 的 `driver` 为 `mock`，`contacts.lookup` 读到合成联系人，`phone_inspect` 的 `messages` 仍为空。同一条命令在普通终端里已经跑通，见上一节。

3. 如果 Hark 里没有 Computer / 这台 Mac，或它不能在本机跑命令，就停在这一步。上面的 Codex CLI 只读注入已经是可复跑结果。不要为了凑 SSH 路径去打开 Remote Login。

这一步如果做成，证明的是：既有 Hark 会话能让本机 Codex 调用 PhoneBridge 只读工具，并把结果留在 Hark 对话里。它不证明系统 SSH、不证明 51 项里的丢回执防重、不证明真实消息发送。防重仍以 `--deterministic` 的 51 项为准。

## Hark 现场第一步 · Mock 拟稿与只读回读

在主机上用 Hark 将登录的 SSH 账号完成 Codex CLI 登录，再从仓库根目录注册 stdio relay，并保持 Mock 后端运行。这个阶段无需配置审批端点或令牌。 [Codex MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

```sh
PB_ROOT="$PWD"
PB_PYTHON="$(command -v python3)"
PB_MCP_DIR="$(mktemp -d /tmp/pb-hark.XXXXXX)"

codex mcp add phonebridge-v04 --env "PYTHONPATH=$PB_ROOT" -- \
  "$PB_PYTHON" -m phonebridge.relay stdio --socket "$PB_MCP_DIR/mcp.sock"

"$PB_PYTHON" -m phonebridge.cli --driver mock \
  backend --socket "$PB_MCP_DIR/mcp.sock"
```

1. 从 Hark 的 SSH 会话连接同一主机和账号，运行 `codex`，用 `/mcp` 查看 `phonebridge-v04`。其 MCP 启动命令应指向上面正在运行的后端套接字。
2. 发出自然语言任务「用 `phonebridge-v04` 确认当前是 Mock，查找合成联系人 `contact-01`，在回复里拟一条『测试消息』，回读当前 Mock 屏幕和记录数。只调用 `phone_status`、`intent_run` 的 `contacts.lookup` 和 `phone_inspect`，不要改变设备状态、创建审批或提交消息。」
3. 在 Hark 中核对 Codex 实际调用的工具、返回的合成联系人和回读结果。退出这次 SSH/Codex 会话，保持主机后端运行，从 Hark 重连并再次回读。记录工具发现、真实工具调用、回读和重连的结果。若 SSH、Codex 工具发现或模型调用失败，记录具体失败环节。

## Hark 现场第二步 · 独立账号审批的 Mock 提交

第一步通过后，在已验收独立账号审批通道的 macOS 主机上，用 Mock 单独跑合成提交。Hark/Codex 使用服务账号，审批在另一个 macOS 账号完成。先退出已连接的 Codex 会话并停止第一步的 Mock 后端。保持 `PB_MCP_DIR` 不变，服务用同一 relay 地址重新启动。审批套接字使用独立的空目录，台账使用仅服务账号可访问的目录。服务与审批终端分别从可信来源载入同一 `PB_HARK_TOKEN`，Codex 会话无需该变量。

在服务账号终端把 `PB_APPROVER` 设为审批账号的短名，再启动后端。

```sh
PB_APPROVAL_DIR="$(mktemp -d /tmp/pb-approval.XXXXXX)"
PB_STATE_DIR="$(mktemp -d /tmp/pb-state.XXXXXX)"

"$PB_PYTHON" -m phonebridge.cli --driver mock \
  --approval-socket "$PB_APPROVAL_DIR/approval.sock" \
  --approval-hosted --approval-uid "$(id -u "$PB_APPROVER")" \
  --approval-token-env PB_HARK_TOKEN --require-approval-isolation \
  --submission-state "$PB_STATE_DIR/submissions.jsonl" \
  backend --socket "$PB_MCP_DIR/mcp.sock"
```

1. 在 Hark 中重新打开 Codex，使用 `phone_status` 核对 `driver: mock`、`submissions.approval_isolated: true` 和 `submissions.approval_isolation_mode: token+external_uid`，再让它在合成会话中为 `contact-01` 输入「测试消息」并创建待审批提案。
2. 在审批账号终端使用 `python3 -m phonebridge.cli --approval-socket "$PB_APPROVAL_SOCKET" --approval-hosted approvals list --socket-owner-uid "$PB_SERVICE_UID" --token-env PB_HARK_TOKEN` 核对联系人与正文。`PB_APPROVAL_SOCKET` 是服务侧的审批套接字绝对路径，`PB_SERVICE_UID` 是服务账号 UID。确认后把 `list` 换为 `approve "$PB_APPROVAL_ID"`，或用 `deny "$PB_APPROVAL_ID"` 拒绝提案。
3. 在 Hark 中指示 Codex 执行已批准的操作一次，并回读该联系人会话。核对刚好新增一条匹配记录，且结果包含 `simulated: true`、`real_message_sent: false`。回执不明时先回读，仍不明就交给操作者核验。

两个 Hark 现场步骤均待实际运行。跨 UID 审批与主机侧自动化的已有证据见 [测试报告](test-report.md)。

其他 Agent 可以配置同一个 stdio 启动命令，兼容性按客户端分别验证。DeepSeek API 如需使用，由上层 Agent 单独接入。PhoneBridge 执行器只接收 MCP 调用。
