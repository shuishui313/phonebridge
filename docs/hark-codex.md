# 通过 Hark SSH 使用 PhoneBridge

首期路径是 Hark → SSH → macOS 或 Linux 主机上的 Codex → stdio MCP → PhoneBridge。主机运行 Codex CLI、PhoneBridge 和 Mock Driver。若主机安装完整 Xcode，也可单独连接 Apple Simulator 诊断驱动。**主机侧链路（SSH → 客户端 → MCP → 审批与台账）已有可复跑的自动化证据**；Hark 客户端 UI、真实 Codex 模型的自然语言编排与真实 Hark SSH 会话仍待现场验证，验证状态见 [测试报告](test-report.md)。

## 主机侧可复跑链路（已验证）

断线重连由 `phonebridge/relay.py` 承担：后端进程持有 `CoreAPI`、审批端点与提交台账状态，在 0700 私有目录的 0600 Unix 套接字上监听；每次 stdio 连接由 `python3 -m phonebridge.relay stdio` 代理并重建 MCP 协议会话，客户端断开不会丢状态。`phonebridge/hark.py` 负责发现或指定客户端、构造 inline MCP 配置、包装 SSH 命令、解析结构化事件，并从客户端环境中移除审批令牌。

服务以 `backend` 子命令启动，参数与 `serve` 相同，只是把一次性 stdio 换成私有套接字：

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

## 配置主机

通过 SSH 进入你能管理的主机，确保已安装并登录 Codex CLI，再从 PhoneBridge 仓库根目录运行以下命令。配置名称 `phonebridge-v04` 可与之前注册的 v0.3 并存。[Codex MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

```sh
PHONEBRIDGE_ROOT="$PWD"
PHONEBRIDGE_PYTHON="$(command -v python3)"
PHONEBRIDGE_DIR="$(mktemp -d /tmp/pb.XXXXXX)"
PHONEBRIDGE_SOCKET="$PHONEBRIDGE_DIR/approval.sock"

codex mcp add phonebridge-v04 --env "PYTHONPATH=$PHONEBRIDGE_ROOT" -- \
  "$PHONEBRIDGE_PYTHON" -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" serve

printf '%s\n' "$PHONEBRIDGE_SOCKET"
```

从 Hark 的 SSH 会话连接同一主机和账号，运行 `codex`，再用 `/mcp` 查看 `phonebridge-v04` 的 12 项工具。每个同时运行的服务需要一个独立的审批套接字路径。系统清理临时目录后，重新创建私有目录并更新该配置。[Codex MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

## 发送一条模拟消息

本节是现场联调操作手册，尚未在 Hark 客户端中实测；主机侧自动化证据见上文。

1. 在 Hark 打开的 Codex 会话中输入「用 `phonebridge-v04` 打开合成私信场景，给 `contact-01` 拟一条『测试消息』，等我在另一个终端批准。批准后执行一次，再回读合成聊天记录。」
2. 在同一主机的另一个终端中，从仓库根目录运行以下命令。把 `PHONEBRIDGE_SOCKET` 设为上一节输出的路径，核对审批列表中的联系人 ID 和正文，再输入实际审批 ID。需要取消时用 `approvals deny`。

   ```sh
   python3 -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" approvals list
   python3 -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" approvals approve '<审批 ID>'
   ```

3. 在 Hark 的 Codex 会话中继续执行，确认回读中该联系人会话增加一条匹配记录。执行结果会带有 `simulated: true` 和 `real_message_sent: false`。

审批与 Agent 的 MCP 工具分开，套接字仍信任同一个操作系统账号。该配置适用于合成联系人和消息。设备权限、审批隔离与数据处理见 [安全边界](security.md)。

其他 Agent 可以配置同一个 stdio 启动命令，兼容性按客户端分别验证。DeepSeek API 如需使用，由上层 Agent 单独接入。PhoneBridge 执行器只接收 MCP 调用。
