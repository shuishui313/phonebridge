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

`intents_list` 按当前驱动公布通道 A 的合成只读能力。`chat.readback` 只在 Mock 已选中合成联系人时读取该会话。Xcode MCP 和 simctl 暂无已公开的 A 能力，调用时只返回 B 的路由建议，不触发设备观察。写意图单独列为禁用。A 结果不明时暂停当前后端的写动作，且不建议重试。通道 B 的写入依然逐笔审批。

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

`CoreAPI.dispatch`（`phonebridge/api.py`）是 MCP 壳与未来独立 App 壳共用的稳定内部入口。`--task-timeout`（默认 300 秒，从首个状态改变动作计时）、`--action-budget`（默认 40 次）、`--max-read-retries`（默认 5 次）和 `--max-retries`（默认 2 次）限定单任务投入。回读结果不明时暂停写动作，只读仍可用。`phone_status` 显示 `paused` 与未决提交数量。MCP 工具不提供解除暂停的权限。

`--submission-state` 指定 0700 目录内的 0600 JSONL 台账，持久化操作 ID、收件人 ID、正文 SHA-256、派发前记录基线与状态迁移，**不存明文正文**；台账是审批状态的唯一事实来源，派发状态在设备调用前落盘，重启后只接受可归属到该收件人的新增记录为完成证据，无法确认即暂停且不自动重发。`--approval-token-env` 从操作者终端环境读取审批令牌，是本地可用的隔离手段；`--approval-uid` 单独使用时仍披露为 `external_uid_unverified`，因为另一个 UID 默认连不上私有目录与套接字；`--approval-hosted` 才显式开启外部托管端点（具名 ACL + 逐连接对端 UID 校验）；`--require-approval-isolation` 只认实际存在的令牌并同时要求持久化台账。`phone_status` 的 `submissions.approval_isolated` 与 `approval_isolation_mode` 披露当前状态。未配置令牌时仅用于 Mock 演示，详见 [安全边界](docs/security.md)。

开启外部托管审批时，为审批套接字准备一个独立、空的 0700 目录，且目录没有预设 ACL。macOS 具名 ACL 允许审批账号连接套接字，也授予该目录的条目增删权限，因此台账、审计和 MCP 套接字须使用服务账号独占的其他目录。端点退出或授权失败时撤销目录权限。审批客户端在发送令牌前验证已连接的服务 UID，服务也逐连接验证审批 UID 和令牌。

```sh
# 服务侧。PB_APPROVER 为另一个 macOS 审批账号的短名。
PB_APPROVAL_DIR="$(mktemp -d /tmp/pb-approval.XXXXXX)"
PB_STATE_DIR="$(mktemp -d /tmp/pb-state.XXXXXX)"
python3 -m phonebridge.cli --approval-socket "$PB_APPROVAL_DIR/approval.sock" \
  --approval-token-env PB_HARK_TOKEN --approval-uid "$(id -u "$PB_APPROVER")" \
  --approval-hosted --require-approval-isolation \
  --submission-state "$PB_STATE_DIR/submissions.jsonl" serve

# 审批账号侧。PB_APPROVAL_SOCKET 和 PB_SERVICE_UID 由服务操作者提供。
python3 -m phonebridge.cli --approval-socket "$PB_APPROVAL_SOCKET" \
  --approval-hosted approvals list --socket-owner-uid "$PB_SERVICE_UID" \
  --token-env PB_HARK_TOKEN
```

使用过旧版托管配置时，先用 `ls -lde` 检查原共用目录，核对并撤销旧审批账号的具名 ACL，再继续使用其中的台账。异常退出后若审批目录还有旧套接字或权限条目，重新启动时使用新的专用目录。

有未决提交时，服务每次启动都从台账恢复写入暂停，并在服务端启动日志列出待核验操作 ID。在 Simulator 自有 App 内由操作者打开目标会话，确认回读后，在可信审批终端将上面命令的 `approvals list` 换成 `approvals reconcile "$PB_OPERATION_ID"`。程序会核对目标会话和记录增量。只结清对应操作，全部未决操作处理完才恢复写入，旧操作不会重发。Mock 按目标 ID 直接核对内存记录。

`scripts/hosted-approval-spike.py` 可复跑 14 项判据并输出 `hosted_acl_verified`。该脚本用注入的对端凭据函数验证判定逻辑，自身的 `cross_uid_process_verified` 恒为 `false`，不冒充跨账号进程验收；上一个版本的真实进程验收见下文。具名 ACL 目前只在 macOS 实现，其他平台返回不支持并拒绝启动。[安全边界](docs/security.md) · [测试报告](docs/test-report.md)

现场真实进程验收入口是 `scripts/cross-uid-approval-spike.py`：在具名 ACL 之上，以真实的另一个 UID 运行审批客户端并核对 OS 对端凭据，令牌从具名可读文件加载、不进子命令。**脚本本体不要用 `sudo` 跑**（那会让 MCP 服务以 root 运行，不等于两个非特权账号分离）。推荐用一次交互认证的常驻代理模式，sudo 只会向终端提示一次密码：

```sh
cd /path/to/phonebridge
python3 scripts/cross-uid-approval-spike.py --approver nobody --authenticate
```

该模式启动一次 `sudo -u nobody` 代理，之后的批准／重放／缺令牌请求都走本地 socket，不依赖 sudo 票据在父子进程间共享；密码由 sudo 直接读取，脚本不读取、不存储、不转发。默认模式仍用 `sudo -n` 逐次调用，若非交互 sudo 不可用就如实输出 `outcome: blocked` 与上面的现场命令；检测到以 root 运行时也直接拒绝。**不绕过提权，也不把替身彩排记为真实进程验收**。

**上一版本现场验收结果（2026-11-11）**：以 `--authenticate` 取得 `outcome: "passed"`、`cross_uid_process_verified: true`、`approver_process_real: true`、`real_model_calls: false`，15 项判据全过，覆盖令牌文件与共享路径 ACL、目录／套接字 ACL 条目、0700/0600 布局、`token+external_uid` 披露、令牌不进子命令、真实第二 UID 批准、恰好一条可归属记录，以及重放、缺令牌、服务账号自身 UID 三类反例。原始输出含本机 UID 与绝对路径，按提交约定不入库，仓库只登记匿名化结论，见 [测试报告](docs/test-report.md)。保存现场 JSON 后可用 `python3 scripts/cross-uid-approval-spike.py --verify-evidence <PATH>`（`-` 读标准输入）做只读结构复核：它检查规定字段、判据与批准及重放响应，拒绝把连接故障当作重放被业务层拒绝。该复核不启动进程、不重新执行验收，也不能证明文件来源。**边界**：只覆盖 macOS 本机两个非特权账号之间的外部托管审批端点，不等于真机、真实第三方 App、独立审批设备、Hark 客户端端到端、真实模型编排或真实 App Intents；真实消息发送仍禁用。

真实驱动在每次交互前按新鲜层级定位启用的提交控件，通用点击／滑动命中即在派发交互命令前返回 `submission_requires_approval`；`scripts/xcode-mcp-submit-guard-spike.py --sim-udid <UDID>` 可在常规启动（提交按钮启用）下复验该拦截。`scripts/xcode-mcp-g2-writable-spike.py --sim-udid <UDID>` 则跑 G2 完整可写验收（36 项判据：未审批拒绝、目标／正文／上下文变更、批准后恰好一条可归属记录、重放、重启不重发），运行后把夹具恢复为 `pb-disable-submit` 基线。两个脚本都只作用于自有合成夹具，**真实消息发送始终禁用**。 [测试报告](docs/test-report.md) · [实现计划](docs/implementation-plan.md)

`--allow-fixture-virtual-submit` 是操作者启动服务时显式给出的第二个开关，只允许唯一提交门在自有合成夹具内生成一条合成记录；CLI 强制它同时具备 `--driver xcode-mcp`、`--allow-xcode-interactions` 与 `--require-approval-isolation`（实际令牌 + 持久化台账）。默认关闭，Agent 无法自行开启，夹具以 `pb-disable-submit` 启动时也不生效。驱动准入会双重确认目标是 Apple Simulator、每次交互前重新观察并确认前台是夹具，断线即释放会话。状态、观察（本地截图 + UI 层级）和激活已安装夹具默认可用；点击、滑动、输入、Home 键默认拒绝。Xcode 的 Agent 批准不等于 PhoneBridge 的操作审批，详见 [安全边界](docs/security.md)。普通 iOS App 受系统沙箱约束，真机与真实第三方 App 验证属于独立阶段。[Apple Simulator 指南](docs/apple-simulator.md) · [iOS 可行性](docs/ios-feasibility.md)

## 历史版本与开发

- `run_phonebridge.py` 提供原 v0.3 入口、8 个工具和兼容参数。已有 MCP 配置可继续使用。
- `legacy/v0.2/` 保留九个原始文件。Mock 测试用 `python3 -m unittest discover -s legacy/v0.2 -v`。旧 FastMCP 服务的依赖与独立验证入口见测试报告。
- `artifacts/` 保存两个原始 ZIP；维护中的 v0.4 源码位于 `phonebridge/`、`tests/`、`simulator/` 和 `scripts/`。[源码清单与 SHA-256](docs/artifact-inventory.md)

[测试报告](docs/test-report.md) · [架构](docs/architecture.md) · [实现计划](docs/implementation-plan.md) · [产品需求](docs/product-requirements.md) · [阶段验收](docs/acceptance.md) · [合并记录](docs/merge-existing-github.md) · [开发交接](HANDOFF.md)
