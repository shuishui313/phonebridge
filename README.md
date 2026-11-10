# PhoneBridge

PhoneBridge 是一个通过 MCP 控制虚拟 iPhone 的原型。当前版本提供桌面端可运行的模拟后端，内置虚拟微信、联系人和消息记录。每个 MCP 服务进程各有一台虚拟手机，关闭进程后状态清空。

这版从空仓库独立实现。此前对话提到的 v0.2 源文件未能取得，项目也没有使用 Mika、Poki 或 Pinky 的代码或素材。

## 连接 Codex

需要 macOS 或 Linux、Python 3.10+ 和支持本地 stdio MCP 服务的 Codex。从仓库根目录执行：

```sh
PHONEBRIDGE_ENTRY="$PWD/run_phonebridge.py"
PHONEBRIDGE_PYTHON="$(command -v python3)"
PHONEBRIDGE_DIR="$(mktemp -d /tmp/pb.XXXXXX)"
PHONEBRIDGE_SOCKET="$PHONEBRIDGE_DIR/approval.sock"
codex mcp add phonebridge -- "$PHONEBRIDGE_PYTHON" "$PHONEBRIDGE_ENTRY" \
  --approval-socket "$PHONEBRIDGE_SOCKET"
```

打开新 Codex 会话，在 `/mcp` 中查看 PhoneBridge。为每个独立的服务进程使用不同的审批路径。同一路径同一时间只能被一个进程占用。如果系统清理了临时目录，重新执行注册步骤并使用新的路径。[Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

如果你的 Hark 连接的是同一台主机上的 Codex 会话，在运行 Codex 的主机上完成注册，再从 Hark 打开该会话。支持本地 stdio MCP 的客户端也可以用同样的 Python 启动命令。Hark 尚未实测。

## 试一次模拟发送

1. 在 Codex 中说「用 PhoneBridge 打开模拟微信，打开联系人小明，拟一条『测试消息』，等待我审批」。联系人可用完整显示名或固定 ID。两个 Alex 会返回可选 ID，由用户指定。
2. 在注册服务的终端运行以下命令。核对显示的收件人、联系人 ID 和正文，然后把下方的占位符替换为实际操作 ID。也可用 `deny` 拒绝。

   ```sh
   "$PHONEBRIDGE_PYTHON" "$PHONEBRIDGE_ENTRY" \
     --approval-socket "$PHONEBRIDGE_SOCKET" approvals list
   "$PHONEBRIDGE_PYTHON" "$PHONEBRIDGE_ENTRY" \
     --approval-socket "$PHONEBRIDGE_SOCKET" approvals approve '<操作 ID>'
   ```

3. 请 Codex 执行已批准的操作，再读取当前聊天记录。执行只写入虚拟记录。切换屏幕或成功执行另一条操作后，尚未执行的提案会失效。

审批命令通过同一台主机上的私有 Unix 套接字工作，MCP 工具仅能拟稿和执行已批准的操作。审批通道信任当前操作系统用户。同一用户下有终端权限的其他进程也能访问，因此它适合受控的模拟演示。

## MCP 工具

| 工具 | 行为 |
| --- | --- |
| `phone_inspect` | 读取虚拟手机当前页面、选中的联系人及消息记录 |
| `phone_open_app` | 打开虚拟微信（`wechat`）或设置（`settings`） |
| `phone_home`、`phone_back` | 回到主屏幕或返回上一层 |
| `wechat_list_contacts` | 读取虚拟微信联系人 |
| `wechat_open_chat` | 按完整名称或固定联系人 ID 打开聊天 |
| `wechat_prepare_message` | 为当前聊天建立待审批提案 |
| `phone_execute_operation` | 单次执行已审批且仍有效的提案 |

## 验证

```sh
python3 -m unittest discover -s tests -v
```

- [测试报告](docs/test-report.md)
- [Apple Simulator 验证步骤](docs/apple-simulator.md)使用独立的 SwiftUI 测试应用。目前 PhoneBridge 的 MCP 服务连接的是 Python 模拟后端。
