# PhoneBridge

PhoneBridge 是供 Hark、Codex 等 Agent App 接入的 iPhone 操作能力，也规划自带对话入口的独立 App（[产品需求](docs/product-requirements.md)）。当前 v0.4 提供 Python 虚拟手机、14 个 stdio MCP 工具、独立终端审批和 Apple 官方 Xcode MCP 驱动。消息只写入合成记录；测试 App 为私信与信息流两个自有合成场景。

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

打开新的 Codex 会话，通过 `/mcp` 确认 14 个工具（原 12 项设备与消息工具，加上 `intents_list` / `intent_run` 两个通道 A 只读意图工具）。每个服务进程需要自己的审批套接字。SSH 链路需要跨断线保持状态时，改用 `backend --socket <私有 0700 目录>/mcp.sock` 启动常驻后端，再让客户端以 `python3 -m phonebridge.relay stdio --socket <同一路径>` 接入；每次连接重建 MCP 会话，审批与台账状态留在后端。`scripts/hark-codex-spike.py` 与 `scripts/hark-codex-ssh-loopback-spike.py` 可复跑成功／失败／中断／重连四类主机侧证据（直连与临时 key-only 本机 SSH 回环，确定性客户端，不调用真实模型）。Hark 客户端 UI、真实 SSH 会话与真实 Codex 模型的工具编排仍待现场验证，DeepSeek API 可由上层 Agent 单独接入。[Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) · [Hark 接入步骤](docs/hark-codex.md) · [测试报告](docs/test-report.md)

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
| `intents_list`、`intent_run(capability)` | 列出显式公开的只读合成意图；按名称执行并回读验证，写意图与未公开能力一律拒绝 |

`wechat_list_contacts`、`wechat_open_chat`、`wechat_prepare_message` 作为历史别名继续可调用，但不出现在 `tools/list` 中。v0.3 入口 `run_phonebridge.py` 保持原有 8 工具协议不变。

## 通道 A：意图优先、通道 B 兜底

`intents_list` 返回显式公开的能力清单与路由策略（`phone_status` 的 `routing` 也披露同一策略）；`intent_run` 只执行登记为无副作用的合成只读意图并回读验证。未公开的能力只返回「可走 B」的路由决策，**不自动执行任何设备动作**；A 失败且确认无副作用才允许回退 B，结果不明则持久暂停写动作、不自动重试，须由可信侧成功回读后才恢复。通道 B 原有的逐笔审批、提交门与通用交互拦截全部保持生效。

能力清单目前只含自有合成夹具的 `device.status` / `contacts.lookup` / `chat.readback`；`message.send` 标记为必须审批、不可直接调用。**本实现没有调用真实 App Intents 或快捷指令，也不宣称任何系统或第三方 App 动作可用**。`scripts/intent-channel-spike.py` 可复跑 13 项判据并输出 `channel_a_passed`。[架构](docs/architecture.md) · [实现计划](docs/implementation-plan.md) · [iOS 可行性](docs/ios-feasibility.md)

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

`--submission-state` 指定 0700 目录内的 0600 JSONL 台账，持久化操作 ID、收件人 ID、正文 SHA-256、派发前记录基线与状态迁移，**不存明文正文**；台账是审批状态的唯一事实来源，派发状态在设备调用前落盘，重启后只接受可归属到该收件人的新增记录为完成证据，无法确认即暂停且不自动重发。`--approval-token-env` 从操作者终端环境读取审批令牌，是本地可用的隔离手段；`--approval-uid` 单独使用时仍披露为 `external_uid_unverified`，因为另一个 UID 默认连不上私有目录与套接字；`--approval-hosted` 才显式开启外部托管端点（具名 ACL + 逐连接对端 UID 校验）；`--require-approval-isolation` 只认实际存在的令牌并同时要求持久化台账。`phone_status` 的 `submissions.approval_isolated` 与 `approval_isolation_mode` 披露当前状态。未配置令牌时仅用于 Mock 演示，详见 [安全边界](docs/security.md)。

默认审批套接字由服务账号独占（0700 目录 / 0600 套接字）。需要另一个系统账号执行审批时，由操作者显式开启外部托管模式；服务启动时给**一个具名审批 UID** 的目录加 `search`、套接字加 `read,write` 文件系统 ACL，`ls -lde` 可核验，授权失败立即拒绝启动。POSIX 模式不放宽，每次连接仍校验对端 UID 与令牌，隔离状态升级为 `token+external_uid`。

```sh
# 服务侧（macOS 具名 ACL，需实际令牌 + 与服务账号不同的审批 UID）
python3 -m phonebridge.cli --approval-socket "$PB_DIR/approval.sock" \
  --approval-token-env PB_HARK_TOKEN --approval-uid "$(id -u nobody)" \
  --approval-hosted --submission-state "$PB_DIR/submissions.jsonl" serve

# 审批账号侧，socket 属主仍是服务账号
python3 -m phonebridge.cli --approval-socket "$PB_DIR/approval.sock" \
  --approval-hosted --socket-owner-uid <SERVICE_UID> \
  --token-env PB_HARK_TOKEN approvals list
```

`scripts/hosted-approval-spike.py` 可复跑 13 项判据并输出 `hosted_acl_verified`。**真实以另一个 UID 运行审批 CLI 需要现场授权**；脚本用注入的对端凭据函数验证判定逻辑，`cross_uid_process_verified` 恒为 `false`，不冒充跨账号进程验收。具名 ACL 目前只在 macOS 实现，其他平台返回不支持并拒绝启动。[安全边界](docs/security.md) · [测试报告](docs/test-report.md)

现场真实进程验收入口是 `scripts/cross-uid-approval-spike.py`：在具名 ACL 之上，以真实的另一个 UID 运行审批客户端并核对 OS 对端凭据，令牌从具名可读文件加载、不进子命令。**脚本本体不要用 `sudo` 跑**（那会让 MCP 服务以 root 运行，不等于两个非特权账号分离）；只需先缓存凭据，再以普通用户运行：

```sh
cd /path/to/phonebridge
sudo -v
python3 scripts/cross-uid-approval-spike.py --approver nobody
```

脚本内部的 `sudo -n -u nobody` 会复用缓存凭据。本机非交互式 `sudo` 需要密码，脚本会如实输出 `outcome: blocked` 与带仓库绝对路径的现场命令；检测到以 root 运行时也直接拒绝并给出同一命令。**不索取密码、不绕过提权，也不把替身彩排记为真实进程验收**。

真实驱动在每次交互前按新鲜层级定位启用的提交控件，通用点击／滑动命中即在派发交互命令前返回 `submission_requires_approval`；`scripts/xcode-mcp-submit-guard-spike.py --sim-udid <UDID>` 可在常规启动（提交按钮启用）下复验该拦截。`scripts/xcode-mcp-g2-writable-spike.py --sim-udid <UDID>` 则跑 G2 完整可写验收（36 项判据：未审批拒绝、目标／正文／上下文变更、批准后恰好一条可归属记录、重放、重启不重发），运行后把夹具恢复为 `pb-disable-submit` 基线。两个脚本都只作用于自有合成夹具，**真实消息发送始终禁用**。 [测试报告](docs/test-report.md) · [实现计划](docs/implementation-plan.md)

`--allow-fixture-virtual-submit` 是操作者启动服务时显式给出的第二个开关，只允许唯一提交门在自有合成夹具内生成一条合成记录；CLI 强制它同时具备 `--driver xcode-mcp`、`--allow-xcode-interactions` 与 `--require-approval-isolation`（实际令牌 + 持久化台账）。默认关闭，Agent 无法自行开启，夹具以 `pb-disable-submit` 启动时也不生效。驱动准入会双重确认目标是 Apple Simulator、每次交互前重新观察并确认前台是夹具，断线即释放会话。状态、观察（本地截图 + UI 层级）和激活已安装夹具默认可用；点击、滑动、输入、Home 键默认拒绝。Xcode 的 Agent 批准不等于 PhoneBridge 的操作审批，详见 [安全边界](docs/security.md)。普通 iOS App 受系统沙箱约束，真机与真实第三方 App 验证属于独立阶段。[Apple Simulator 指南](docs/apple-simulator.md) · [iOS 可行性](docs/ios-feasibility.md)

## 历史版本与开发

- `run_phonebridge.py` 提供原 v0.3 入口、8 个工具和兼容参数。已有 MCP 配置可继续使用。
- `legacy/v0.2/` 保留九个原始文件。Mock 测试用 `python3 -m unittest discover -s legacy/v0.2 -v`。旧 FastMCP 服务的依赖与独立验证入口见测试报告。
- `artifacts/` 保存两个原始 ZIP；维护中的 v0.4 源码位于 `phonebridge/`、`tests/`、`simulator/` 和 `scripts/`。[源码清单与 SHA-256](docs/artifact-inventory.md)

[测试报告](docs/test-report.md) · [架构](docs/architecture.md) · [实现计划](docs/implementation-plan.md) · [产品需求](docs/product-requirements.md) · [阶段验收](docs/acceptance.md) · [合并记录](docs/merge-existing-github.md) · [开发交接](HANDOFF.md)
