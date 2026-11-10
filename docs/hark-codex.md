# 通过 Hark SSH 使用 PhoneBridge

首期路径是 Hark → SSH → macOS 或 Linux 主机上的 Codex → stdio MCP → PhoneBridge。主机运行 Codex CLI、PhoneBridge 和 Mock Driver。若主机安装完整 Xcode，也可单独连接 Apple Simulator 诊断驱动。Hark 客户端的 SSH 与整条路径有待现场验证，验证状态见 [测试报告](test-report.md)。

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

1. 在 Hark 打开的 Codex 会话中输入「用 `phonebridge-v04` 打开合成私信场景，给 `contact-01` 拟一条『测试消息』，等我在另一个终端批准。批准后执行一次，再回读合成聊天记录。」
2. 在同一主机的另一个终端中，从仓库根目录运行以下命令。把 `PHONEBRIDGE_SOCKET` 设为上一节输出的路径，核对审批列表中的联系人 ID 和正文，再输入实际审批 ID。需要取消时用 `approvals deny`。

   ```sh
   python3 -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" approvals list
   python3 -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" approvals approve '<审批 ID>'
   ```

3. 在 Hark 的 Codex 会话中继续执行，确认回读中该联系人会话增加一条匹配记录。执行结果会带有 `simulated: true` 和 `real_message_sent: false`。

审批与 Agent 的 MCP 工具分开，套接字仍信任同一个操作系统账号。该配置适用于合成联系人和消息。设备权限、审批隔离与数据处理见 [安全边界](security.md)。

其他 Agent 可以配置同一个 stdio 启动命令，兼容性按客户端分别验证。DeepSeek API 如需使用，由上层 Agent 单独接入。PhoneBridge 执行器只接收 MCP 调用。
