# 通过 Hark SSH 使用 PhoneBridge

首期路径是 Hark → SSH → macOS 或 Linux 主机上的 Codex → stdio MCP → PhoneBridge。主机运行 Codex CLI、PhoneBridge 和 Mock Driver。若主机安装完整 Xcode，也可单独连接 Apple Simulator 诊断驱动。**主机侧链路（SSH → 客户端 → MCP → 审批与台账）已有可复跑的自动化证据**；Hark 客户端 UI、真实 Codex 模型的自然语言编排与真实 Hark SSH 会话仍待现场验证，验证状态见 [测试报告](test-report.md)。

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

两个脚本产出成功、失败、中断、重连四类证据：

```sh
python3 scripts/hark-codex-spike.py --codex-path <客户端路径> \
  --interruption-codex-path <中断客户端路径> --text 'Harness probe'
python3 scripts/hark-codex-spike.py --transport ssh --ssh-destination user@host \
  --ssh-key <私钥> --ssh-known-hosts <known_hosts> --codex-path <客户端路径>
python3 scripts/hark-codex-ssh-loopback-spike.py --ssh-precheck-only
```

回环脚本在 127.0.0.1 上启动临时 key-only `sshd`，使用临时主机密钥、客户端密钥与 `known_hosts`，不修改系统 SSH 配置、不启用 Remote Login。不带 `--ssh-precheck-only` 时，它会自动以 `--transport ssh` 调用 `scripts/hark-codex-spike.py` 并透传 `--hark-spike-arg`；只加预检开关时仅验证密钥登录与端口可达。`--transport ssh` 把每个阶段包装成独立 SSH 会话；`--interruption-codex-path` 指定在派发 `phone_execute_operation` 后丢弃回执并立即退出的客户端，用来复现回执丢失后重连会不会重复提交。

自动化测试 `tests/test_hark_spike.py` 用确定性客户端（不调用真实模型）分别跑直连和 SSH 回环两条完整链路，`tests/test_relay.py` 覆盖重连状态、stdio 代理、套接字权限与 MCP 握手。证据数值见 [测试报告](test-report.md)。

**边界**：这些证据只能证明主机侧链路，不能证明 Hark 客户端可发出自然语言指令、真实 Codex 模型可稳定发现并调用 MCP 工具，也不能证明真机或真实第三方 App 能力。证据中 `hark_client_verified` 与 `hark_ui_verified` 恒为 `false`。

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
