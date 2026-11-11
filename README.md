# PhoneBridge

PhoneBridge 是供 Hark、Codex 等 Agent App 接入的 iPhone 操作能力，也规划自带对话入口的独立 App（[产品需求](docs/product-requirements.md)）。当前 v0.4 提供 Python 虚拟手机、12 个 stdio MCP 工具、独立终端审批和 Apple 官方 Xcode MCP 驱动。消息只写入合成记录；测试 App 为私信与信息流两个自有合成场景。

## 快速开始

使用 macOS 或 Linux 和 Python 3.10+。核心与 MCP 服务仅使用标准库。

```sh
python3 -m unittest discover -s tests -v
python3 -m phonebridge.cli serve
```

第二条命令启动 stdio 服务，等待 MCP 客户端连接。也可在虚拟环境内运行 `python3 -m pip install .`，之后用 `phonebridge serve` 启动。

## 连接 Codex 和 Hark

在运行 Codex 的主机上，从仓库根目录注册 v0.4 服务。

```sh
PHONEBRIDGE_PYTHON="$(command -v python3)"
PHONEBRIDGE_DIR="$(mktemp -d /tmp/pb.XXXXXX)"
PHONEBRIDGE_SOCKET="$PHONEBRIDGE_DIR/approval.sock"

codex mcp add phonebridge-v04 --env "PYTHONPATH=$PWD" -- \
  "$PHONEBRIDGE_PYTHON" -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" serve
```

打开新的 Codex 会话，通过 `/mcp` 确认 12 个工具。每个服务进程需要自己的审批套接字。Hark 可按设计经 SSH 登录同一台主机，再运行已注册 MCP 的 Codex。Hark 客户端联调待现场验证，DeepSeek API 可由上层 Agent 单独接入。[Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) · [Hark 接入步骤](docs/hark-codex.md)

## 试一次模拟发送

1. 在 Codex 中输入「打开合成私信场景，找到 Casey，拟一条『今天测试成功』，等待我审批」。Agent 打开 `contact-02` 会话，聚焦输入框并录入正文，再提交审批提案。同名联系人须先选定固定 ID。
2. 在另一个可信终端中进入仓库根目录，将 `PHONEBRIDGE_SOCKET` 设为注册时的实际路径。核对收件人 ID 和正文，再用实际审批 ID 批准。也可以用 `deny` 拒绝。

   ```sh
   python3 -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" approvals list
   python3 -m phonebridge.cli --approval-socket "$PHONEBRIDGE_SOCKET" approvals approve '<审批 ID>'
   ```

3. 请 Codex 执行一次，然后回读该联系人的模拟记录。结果包含 `real_message_sent: false`。审批五分钟内有效，草稿或页面状态变化会使提案失效。服务退出后，提案和聊天记录清空。

审批入口与 MCP 分离，套接字信任同一操作系统账号。Agent 若能以该账号自由执行命令，也能访问套接字。这个配置只用于受控模拟。[安全边界](docs/security.md)

| MCP 工具 | Mock 行为 |
| --- | --- |
| `phone_status`、`phone_inspect` | 读取驱动能力、页面、当前联系人和模拟记录 |
| `phone_open_app(app)` | 打开 `messaging` 或 `settings`；`wechat` 作为历史别名仍可调用 |
| `phone_home`、`phone_back` | 返回上一级或主屏幕 |
| `phone_tap(x, y)`、`phone_swipe(start_x, start_y, end_x, end_y)` | 用 0–1 坐标操作合成界面，聊天输入区为 `y >= 0.82` |
| `phone_type_text(text)` | 将文本追加到已聚焦的输入框 |
| `contacts_list`、`contacts_open(contact)` | 列出合成联系人，按精确名称或固定 ID 打开聊天 |
| `message_prepare(recipient_id, message)` | 校验当前联系人与已录入的正文，生成待审批提案 |
| `phone_execute_operation(approval_id)` | 单次执行仍有效且已批准的模拟操作 |

`wechat_list_contacts`、`wechat_open_chat`、`wechat_prepare_message` 作为历史别名继续可调用，但不出现在 `tools/list` 中。v0.3 入口 `run_phonebridge.py` 保持原有 8 工具协议不变。

## Apple Simulator 和 iPhone

在安装完整 Xcode、iOS Simulator 运行时和 XcodeGen 的 Mac 上运行以下命令。

```sh
./scripts/simctl-preflight.sh
./scripts/apple-simulator-test.sh '<SIMULATOR_UDID>'
python3 -m phonebridge.cli --driver simctl --sim-udid '<SIMULATOR_UDID>' serve
```

XCUITest 在自有 SwiftUI App 内验证私信与信息流两个合成场景。`simctl` 驱动只支持状态、启动夹具和本地截图。

`xcode-mcp` 驱动接入 Apple 官方 `xcrun mcpbridge`，只使用 6 个设备交互／诊断工具白名单：

```sh
python3 -m phonebridge.cli --driver xcode-mcp --sim-udid '<SIMULATOR_UDID>' serve
# 需要受控交互时，由操作者在可信终端显式追加：--allow-xcode-interactions
```

`--fixture-bundle` 指定自有夹具，`--bridge-timeout` 设置握手与调用超时（默认 15 秒），`--session-id` 可显式指定会话标识（默认每进程唯一）。`scripts/xcode-mcp-draft-spike.py --sim-udid <UDID>` 可复跑步骤 0b 拟稿试跑并输出 G1 证据。

`CoreAPI.dispatch`（`phonebridge/api.py`）是 MCP 壳与未来独立 App 壳共用的稳定内部入口。`--task-timeout`（默认 300 秒，从首个状态改变动作计时）、`--action-budget`（默认 40 次）、`--max-read-retries`（默认 5 次）和 `--max-retries`（默认 2 次）限定单任务投入；状态改变后按当次证据回读，结果不明返回 `verification.outcome: unclear` 并**持久暂停所有写动作**，只读仍可用，须由可信侧 `resume_after_readback()` 绑定一次成功的当次回读才恢复。`phone_status` 返回任务快照（含 `paused`），重置任务窗口与解除暂停都不暴露为 MCP 工具。

`--submission-state` 指定 0700 目录内的 0600 JSONL 台账，持久化操作 ID、收件人 ID、正文 SHA-256、派发前记录基线与状态迁移，**不存明文正文**；台账是审批状态的唯一事实来源，派发状态在设备调用前落盘，重启后只接受可归属到该收件人的新增记录为完成证据，无法确认即暂停且不自动重发。`--approval-token-env` 从操作者终端环境读取审批令牌，是本地可用的隔离手段；`--approval-uid` 的对端凭据校验仅适用于外部托管端点加 OS ACL，不单独构成隔离；`--require-approval-isolation` 只认实际存在的令牌并同时要求持久化台账。`phone_status` 的 `submissions.approval_isolated` 与 `approval_isolation_mode` 披露当前状态。未配置令牌时仅用于 Mock 演示，详见 [安全边界](docs/security.md)。

真实驱动在每次交互前按新鲜层级定位启用的提交控件，通用点击／滑动命中即在派发交互命令前返回 `submission_requires_approval`；`scripts/xcode-mcp-submit-guard-spike.py --sim-udid <UDID>` 可在常规启动（提交按钮启用）下复验该拦截，运行后会把夹具恢复为禁用提交。真实驱动的完整可写验收仍是 G2 阻塞项，见 [实现计划](docs/implementation-plan.md)。驱动准入会双重确认目标是 Apple Simulator、每次交互前重新观察并确认前台是夹具，断线即释放会话。状态、观察（本地截图 + UI 层级）和激活已安装夹具默认可用；点击、滑动、输入、Home 键默认拒绝，发送消息始终禁用。Xcode 的 Agent 批准不等于 PhoneBridge 的操作审批，详见 [安全边界](docs/security.md)。普通 iOS App 受系统沙箱约束，真机与真实第三方 App 验证属于独立阶段。[Apple Simulator 指南](docs/apple-simulator.md) · [iOS 可行性](docs/ios-feasibility.md)

## 历史版本与开发

- `run_phonebridge.py` 提供原 v0.3 入口、8 个工具和兼容参数。已有 MCP 配置可继续使用。
- `legacy/v0.2/` 保留九个原始文件。Mock 测试用 `python3 -m unittest discover -s legacy/v0.2 -v`。旧 FastMCP 服务的依赖与独立验证入口见测试报告。
- `artifacts/` 保存两个原始 ZIP；维护中的 v0.4 源码位于 `phonebridge/`、`tests/`、`simulator/` 和 `scripts/`。[源码清单与 SHA-256](docs/artifact-inventory.md)

[测试报告](docs/test-report.md) · [架构](docs/architecture.md) · [实现计划](docs/implementation-plan.md) · [产品需求](docs/product-requirements.md) · [阶段验收](docs/acceptance.md) · [合并记录](docs/merge-existing-github.md) · [开发交接](HANDOFF.md)
