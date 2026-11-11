# PhoneBridge 验证报告

2026 年 11 月 10 日（香港时间）。Python 与 Codex 测试在 macOS 27.0.1（26A434）、arm64、Python 3.10.10、Codex CLI 0.167.0-alpha.3.2 上执行。Python 套接字测试使用允许本机 Unix 域套接字的执行环境。

| 验证范围 | 实际结果 |
| --- | --- |
| 原 GitHub 基线 `4ff8df5` | 18 通过，0 失败，0 跳过。 |
| v0.4 原始 ZIP 独立解压 | 52 通过，0 失败，0 跳过。包含独立进程审批与模拟发送回读。 |
| 整合后的 Python 套件 | 79 通过，0 失败，0 跳过。其中 18 项为 v0.3 兼容回归，61 项为 v0.4。涵盖过期、拒绝、并发防重、MCP 错误恢复、审计、假 `simctl` 和实际 stdio 子进程。 |
| v0.2 原始源码 | 10 通过，0 失败，0 跳过。离线演示四步完成，回报 `simulated: true`、`message_sent: false`。 |
| v0.2 FastMCP 进程 | MCP Python SDK 1.30.0 发现 8 个工具。完成导航、手势、草稿与确认，原型返回 `blocked`，重放被拒绝。 |
| 打包和 CLI | `python3 -m pip install .` 构建并安装 0.4.0 wheel。在仓库外执行 `phonebridge --help` 成功。 |
| 本机 Codex MCP | 用隔离的 `codex app-server` 会话直接调用。v0.3 发现 8 项、v0.4 发现 12 项。两版均经独立审批终端写入并回读一条模拟消息。这次验证覆盖客户端和工具调用，Hark 和自然语言模型回合另行验收。 |
| 本机 Swift／脚本检查 | 3 份 Swift 文件通过解析，3 份 Shell 脚本通过 Bash 语法检查。用 XcodeGen 2.46.0 生成含 XCUITest scheme 的工程，`plutil -lint` 通过。 |
| GitHub Actions · Python | Ubuntu 的 Python 3.10 和 3.13 均成功安装 CLI，各通过 79 项整合测试和 10 项 v0.2 测试，0 失败，0 跳过。历史 FastMCP 冒烟按配置只在 3.10 作业运行且通过。[CI 运行记录](https://github.com/shuishui313/phonebridge/actions/runs/42773456200) |
| GitHub Actions · Apple Simulator | Xcode 27.1（27A9275），iPhone 18 Pro Simulator，iOS 27.0。首轮首屏冒烟通过，两个 UI 测试中一个超时。修正 SwiftUI 无障碍标识后，首屏冒烟和两项 XCUITest 全通过。[首轮](https://github.com/shuishui313/phonebridge/actions/runs/42773418532) · [通过的重跑](https://github.com/shuishui313/phonebridge/actions/runs/42774343133) |
| Apple Xcode MCP | 本机 Xcode 27.2 包含 `mcpbridge`，导出的 Claude 插件配置指向同一个命令。已开启 Xcode Intelligence 的 MCP 权限并完成 stdio 握手，枚举出 54 个工具，含设备交互类；详细结果见下节。独立的 XCUITest 和 PhoneBridge 的 MCP 运行证据分别见本表。 |
| Hark、真机与真实第三方 App | 未运行。本次验证未连接实体 iPhone 或真实第三方 App。 |

## 2026-11-10 晚间补充 · 本机 Apple Simulator 实测

本机安装完整 Xcode 27.2（27B52）与 iOS 27.2 Simulator runtime（含 24B91016s 变体），`xcode-select` 指向完整 Xcode。测试设备为 iPhone 18 Pro Simulator，macOS 27.0.1、arm64。记录来自 [本机验证提交](https://github.com/shuishui313/phonebridge/commit/31c5b952dd549a869df8c445e1123116bb794f81)。

| 验证项 | 实际结果 |
| --- | --- |
| `scripts/simctl-preflight.sh` | 通过。xcodebuild 27.2、simctl 可用、设备列表正常。 |
| `scripts/apple-simulator-test.sh` | `APPLE SIMULATOR XCTEST PASS`。两项 XCUITest 通过（0 失败）：同名联系人标识区分、从合成联系人聊天输入并发送并读回。结果包在 `build/ios-ui.*/PhoneBridgeUITests.xcresult`。 |
| `scripts/simulator-smoke.sh` | 通过。夹具 App 在真实 Simulator 上启动并写出 `onAppear` 标记。 |
| simctl Driver 直调 | `AppleSimulatorDriver.status()` 正确返回 Booted 的 iPhone 18 Pro。`open_app("fixture")` 启动夹具成功。`observe()` 截图成功并返回本地路径与 SHA-256。 |
| 修复记录 | 首次运行因 XcodeGen 2.46.0 + Xcode 27 生成的工程缺少 `PRODUCT_NAME` 而构建失败（"Multiple commands produce '.app'"）。在 `simulator/project.yml` 的 `settings.base` 显式加 `PRODUCT_NAME: $(TARGET_NAME)` 后通过。 |

Xcode 27 环境下 XcodeGen 需要显式 `PRODUCT_NAME` 与 `ALWAYS_SEARCH_USER_PATHS: NO`，后续 GitHub Actions 的 macOS runner 同样适用。Hark、真机与真实第三方 App 仍未验证，本节结论只覆盖 Simulator 与 simctl Driver 能力。

追加验证（同日）：夹具扩展为两个合成测试 App 场景，私信对标 WhatsApp（联系人、草稿、发送、回读），信息流对标 X（撰写、发布、回读）。在 Xcode 27.2 / iPhone 18 Pro（iOS 27.2）重跑 `scripts/apple-simulator-test.sh`，`Executed 3 tests, with 0 failures`，`APPLE SIMULATOR XCTEST PASS`，构建日志中 headermap 警告 0 条。两个场景均为自有界面与合成数据，未安装或操作真实第三方 App、未使用真实账号。

## 2026-11-10 晚间补充 · Xcode 官方 MCP 探测

在 Xcode 27.2（27B52）的 Intelligence 设置中开启「允许外部 Agent 使用 Xcode 工具」后，对 `xcrun mcpbridge`（serverInfo `xcode-tools` 25464）做了 stdio 握手探测。

| 探测项 | 实际结果 |
| --- | --- |
| 工具枚举 | `tools/list` 返回 54 个工具：构建／测试（`BuildProject`、`RunAllTests` 等）、项目文件读写、预览渲染、诊断日志、文档搜索，以及设备交互五件套。 |
| 设备交互类 | `DeviceInteractionStartSession`（无 workspace，按 UDID 寻找并启动设备）、`DeviceInteractionInstallAndRun`、`DeviceInteractionSynthesize`、`DeviceInteractionEndSession`。`Synthesize` 描述为在物理设备或模拟器上合成 tap、swipe、type、硬件按键与方向变化，每次执行后返回截图与 UI 层级文件路径；参数 `activationBundleId` 说明为「在任何交互前激活的 App 的 bundle ID」，未见仅限开发者自有 App 的字样。 |
| 批准流程 | 首次工具调用要求先 `XcodeOpenWorkspace` 触发用户批准。从受限执行环境发起时 Xcode 报 "can't be put in front of the user for approval"，批准框无法弹出。最终通过真实终端执行 `sudo xcrun mcp-server enable --unsafe-always-allow-all-agents` 开启无头模式解决（仅本机开发验证用）；`xcrun mcp-server open <工程路径>` 可在无界面条件下打开 workspace。 |
| 非自有 App 实测 · 通过 | 在已启动的 iPhone 18 Pro Simulator 上：`DeviceInteractionStartSession` 成功建立会话；`DeviceInteractionSynthesize` 以 `activationBundleId=com.apple.Preferences` 激活**系统「设置」App**（非项目自有 App），合成点击 `t 38 84` 后截图 SHA-256 发生变化，UI 层级与截图均正确返回（`hierarchyPath`/`screenshotPath`）。证明官方通道可在 Simulator 上激活并操作非自有 App 并读回界面状态。 |
| 结论 | 通道 B（设备接管）在 Simulator 上有官方入口。可将 `mcpbridge` 包装为 PhoneBridge 的设备 Driver，沿用现有审批、验证与审计层；CI 回归仍以固定 XCUITest 为准。物理 iPhone 与真实第三方 App 未验证，`Synthesize` 描述虽含物理设备，真机能力仍按用户另行授权单独验收。 |

## 2026-11-10 追加 · 通用命名与官方 Xcode MCP 驱动

| 验证项 | 实际结果 |
| --- | --- |
| v0.4 通用命名 | 工具改为 `contacts_list` / `contacts_open` / `message_prepare`，Mock 场景为 `messaging`，合成联系人为 `contact-01` / `contact-02` / 两个同名 Alex。三个历史工具名与旧应用、联系人别名仍可调用，但不出现在 `tools/list`；v0.3 入口与 8 工具协议未改动。 |
| Python 测试 | 95 通过，0 失败。含 14 项新增 `XcodeMCPDriver` 单元测试、真实 stdio 子进程中通用名与旧别名混合调用。v0.2 归档 10 项通过。 |
| Simulator XCUITest | 三项全过（0 失败），`APPLE SIMULATOR XCTEST PASS`，联系人改名后回归通过。 |
| `XcodeMCPDriver` 本机实测 | 通过真实 `xcrun mcpbridge` 验证：`status` 正确披露边界；`observe` 返回本地截图 SHA-256 与 UI 层级路径；`open_app("fixture")` 激活后层级文件显示前台为 `org.example.phonebridge.fixture`；显式开启交互后 `tap` 转换 0–1 坐标为官方 `t 36 87` 命令并返回截图与层级。 |
| 安全闸门 | 仅允许 6 个设备交互／诊断工具；`tap` / `swipe` / `type_text` / `home` 默认返回 `independent_approval_required`，需操作者在启动服务的受控终端传入 `--allow-xcode-interactions`，不是 MCP 工具；发送消息始终禁用；不支持的方法不触达官方桥。`applicationState: NotRun` 是 Xcode 会话字段，前台 App 以 UI 层级为准。 |
| CLI stdio 端到端 | `python3 -m phonebridge.cli --driver xcode-mcp --sim-udid <UDID> serve` 真实子进程：`initialize` 成功、`tools/list` 12 个工具、`phone_status` 返回 `xcode-mcp`、`phone_tap` 默认拒绝 `independent_approval_required`、`message_prepare` 返回 `real_message_sending_disabled`、`phone_inspect` 成功返回截图 SHA-256 与 UI 层级路径，进程正常退出。 |

## 2026-11-10 追加 · 步骤 0a 驱动准入

| 验证项 | 实际结果 |
| --- | --- |
| Simulator 双重校验 | 默认检查器调用 `xcrun simctl list devices available -j` 确认 UDID 属于可用模拟器；`DeviceInteractionStartSession` 返回的 `deviceIsSimulator` 必须为 `true`、`deviceUUID` 必须与请求 UDID 一致，否则分别返回 `device_type_unverified` / `device_identity_mismatch`。 |
| 前台夹具校验 | 每次交互前重新捕获界面层级，解析首行 `Application bundle identifier`；只有等于 `org.example.phonebridge.fixture` 才允许点击、滑动、输入和 Home 键。`open_app` 激活后同样校验前台。 |
| 状态新鲜度 | `Synthesize` 必须返回本次的 `hierarchyPath` 与 `screenshotPath`，缺失任一返回 `fresh_observation_unavailable`；不再复用上一次观察的坐标或层级。 |
| 桥接超时与断线 | 握手和每次工具调用改用二进制 stdio + `select` 硬超时（默认 15 秒）；超时或连接关闭返回 `xcode_mcp_unavailable`，立即释放失效进程与会话，下次调用重新握手建会话。`--bridge-timeout` 可配置。 |
| 单元测试 | `tests/test_xcode_mcp.py` 17 项通过，覆盖非 Simulator、前台变化（系统设置 App）、会话设备类型与 UDID 不符、桥接无响应、旧状态缺失、超时释放进程、断线后重建会话四类反例。Python 全套 98 项通过。 |
| 真机 mcpbridge 实测 | `status` 初始 `preflight_passed: false`；`observe` 正确返回当前前台（SpringBoard / 系统设置）；`open_app("fixture")` 激活后前台为夹具，`tap` 生成 `t 38 84` 并通过；随后用另一会话把前台切到系统设置，再次 `tap` 返回 `foreground_app_not_allowlisted`。 |
| CLI stdio 端到端 | `python3 -m phonebridge.cli --driver xcode-mcp --sim-udid <UDID> serve`：`tools/list` 12 项、`phone_status` 返回 `preflight_passed: false` 与 `bridge_timeout: 15.0`、`phone_inspect` 返回本次截图哈希和前台 bundle、默认 `phone_tap` 返回 `independent_approval_required`、`message_prepare` 返回 `real_message_sending_disabled`、`phone_open_app` 激活夹具成功，进程正常退出。 |

以上仅为 Simulator 准入能力，真机与真实第三方 App 仍未验证。

## 2026-11-10 追加 · 步骤 0b 拟稿试跑与 G1

| 验证项 | 实际结果 |
| --- | --- |
| 提交路径禁用 | 自有 SwiftUI 夹具新增 `pb-disable-submit` 启动参数：私信 Send 与信息流 Post 按钮变为 Disabled，并显示可回读的禁用提示标识。新增第 4 项 XCUITest 覆盖该场景，4 项全部通过。 |
| 试跑入口 | `scripts/xcode-mcp-draft-spike.py --sim-udid <UDID>`：用 `simctl` 带禁用参数重启夹具，启动真实 stdio MCP 子进程，按最新 UI 层级的 `identifier` 与 `hitPoint` 定位元素并换算 0–1 坐标。 |
| 拟稿与回读 | 按稳定 ID `contact.contact-02` / `Casey` 定位合成联系人，聚焦 `chat.input`，`phone_type_text` 输入中文「测试消息」；层级中该输入框 `value` 精确等于「测试消息」。 |
| 记录增量为零 | 提交按钮标识为 `chat.send.disabled`、不存在 `chat.send`，点击禁用按钮后仍不存在 `chat.message.0`。 |
| 负面用例 | `message_prepare` 返回 `real_message_sending_disabled`；另起一个不带 `--allow-xcode-interactions` 的服务进程，`phone_tap` 返回 `independent_approval_required`。两处拒绝均生效。 |
| G1 判据（自动化脚本） | 等价 MCP 工具序列全部通过，`g1_passed: true`，本轮重跑仍通过。 |
| G1 判据（隔离会话） | 隔离 Codex 会话的自然语言拟稿复核 **仍待办**，不并入脚本结果；接手时按同一序列在 Codex 中复核。 |
| 会话 ID 修复 | 试跑发现固定会话标识会被 Xcode 判为「recently used」而拒绝；改为每进程默认生成唯一会话 ID，CLI 可用 `--session-id` 显式指定。新增单元测试覆盖。 |
| 回归 | Python 99 项通过、v0.2 归档 10 项通过、XCUITest 4 项通过。 |

以上仍只覆盖 Apple Simulator 与自有合成夹具；真机、真实第三方 App 与 Hark 联调未验证。

## 2026-11-10 追加 · 步骤 1 核心稳定化

| 验证项 | 实际结果 |
| --- | --- |
| 稳定内部 API | 新增 `phonebridge/api.py`：`CoreAPI.dispatch(action, args)` 作为 MCP 壳和未来独立 App 壳共用的唯一入口；12 个动作名与 MCP 工具名一致，启动时校验两者契约同步。审批的 `approve` / `deny` / `approval_list` 不在动作表里，Agent 无法通过 `dispatch` 触及。 |
| 任务时限 | 从第一个改变状态的动作开始计时，超时后拒绝后续状态改变，只读动作仍可用；`reset_task()` 只供可信侧调用，不是 MCP 工具。 |
| 动作预算 | 只累计改变状态的动作尝试（含失败），耗尽返回 `action_budget_exhausted`；只读动作不消耗预算。 |
| 有限重试 | 失败时返回 `retry` 建议并记录连续失败次数，超过 `--max-retries` 返回不允许重试；提交类动作永不建议自动重试，结果不明时禁止自动重放。成功后计数归零。 |
| 只读重试上限 | 连续相同只读动作超过 `--max-read-retries` 返回 `read_retry_limit_exceeded`；自动回读直接走核心观察，不占该配额。 |
| 结果不明回读 | 每个成功的状态改变自动调用核心观察；缺本次证据返回 `verification.outcome: unclear`、`pause: true`、`automatic_retry: false`，观察抛异常同样 fail closed。 |
| 单元测试 | `tests/test_core_api.py` 15 项通过，覆盖契约一致性、时限、预算耗尽、只读配额、重试上限、提交不重试、回读不明确与异常、审批通道仍可用。Python 全套 114 项通过。 |
| 真实 stdio 验证 | `--task-timeout 60 --action-budget 2 --max-read-retries 3 --max-retries 1`：`phone_status` 返回任务快照，前两个状态改变 `verification.outcome: clear`，第三个返回 `action_budget_exhausted`，进程正常退出。 |
| 回归 | 重跑 `scripts/xcode-mcp-draft-spike.py` 仍 `g1_passed: true`；XCUITest 4 项、v0.2 归档 10 项通过。 |

MCP 工具契约未变：仍 12 个工具、参数与别名行为一致。真机、真实第三方 App 与 Hark 联调仍未验证。

## 2026-11-10 追加 · 步骤 2 逐笔审批与恢复

| 验证项 | 实际结果 |
| --- | --- |
| 唯一提交入口 | `phonebridge/submissions.py` 的 `SubmissionGate` 是唯一调用 `driver.virtual_send` 的代码路径；源码扫描测试确认其他模块无直接调用。`CoreAPI.dispatch` 不暴露批准、拒绝、提案列表与恢复动作。 |
| 脱敏持久化台账 | `--submission-state` 使用 0700 目录内的 0600 JSONL，跨进程 `flock` 串行化，每条事件 `fsync` 后返回。只记录操作 ID、收件人 ID、正文 SHA-256／长度与状态迁移（`prepared → approved → denied / dispatching → completed / unresolved`）。测试读取原始文件断言明文正文与 `message`/`text`/`body`/`content` 字段均不存在。 |
| 提交前原子记录 | 派发状态在设备调用**之前**落盘，随后才消费审批并调用设备。提交前中断（注入崩溃钩子）后台账停在 `dispatching`，重启恢复为 `unresolved` 并暂停，记录数为零，不自动重发。 |
| 提交后回执丢失 | 设备调用已生效但回执丢失时，重启按目标与操作 ID 读回记录并确认，标记 `completed` 且不重发，记录数保持 1；若设备状态无法确认则标记 `unresolved` 并暂停。已批准但未派发的操作重启后同样暂停。恢复可重复执行且不产生新动作。 |
| 拒绝路径 | 未批准、切换收件人、追加正文导致草稿变化、TTL 过期、重放、8 线程并发执行均通过：批准后只增加一条匹配记录，第二次执行返回 `unknown_or_processed_operation`。 |
| 通用交互绕行 | 自定义驱动在 `tap` 中绕过审批直接提交时，`CoreAPI` 回读发现记录数增加，返回 `verification.submission_bypass_detected: true`、`outcome: unclear`、`pause: true`、`automatic_retry: false`；经 `phone_execute_operation` 的合法提交不触发该标记。 |
| 审批通道隔离 | `--approval-token-env` 要求每次请求携带操作者终端注入的令牌（不出现在进程命令行）；`--approval-uid` 用 OS 对端凭据（Linux `SO_PEERCRED` / macOS `LOCAL_PEERCRED`）限定审批者，配置 UID 与服务进程同账号时拒绝启动。`--require-approval-isolation` 在缺少隔离或持久化台账时拒绝启动。`phone_status` 的 `submissions.approval_isolated` 披露当前状态。 |
| 单元测试 | `tests/test_submissions.py` 24 项通过，覆盖上述全部路径；Python 全套 138 项通过、v0.2 归档 10 项通过。 |
| 真实 stdio 端到端 | 双子进程验证：`--require-approval-isolation` 启动后 `approval_isolated: true`；不带令牌的审批请求返回 `approval_token_required`，带令牌批准成功；`phone_execute_operation` 返回 `submission_state: completed`、记录数 1、重放被拒；台账文件不含明文。向台账注入一条 `dispatching` 后重启，服务启动时输出恢复报告，`prior_state: dispatching`、暂停且不重发。 |
| 回归 | 重跑 `scripts/xcode-mcp-draft-spike.py` 仍 `g1_passed: true`；XCUITest 4 项全过 `TEST SUCCEEDED`；MCP 工具契约仍为 12 个工具。 |

**边界声明**：未配置令牌或独立 UID 时审批通道仍是同账号演示模式，同 OS 账号下可自由执行 shell 的 Agent 仍可能自批；可写联调与真机阶段必须配置独立 UID 或外部可信审批端。真机、真实第三方 App 与 Hark 联调仍未验证。

## 2026-11-11 追加 · 711ab2a 评审修复

本轮修正[评审记录](reviews/711ab2a.md)全部 5 条发现，并修正上节关于「恢复按目标与操作 ID 核验」的表述：原实现只比正文摘要，未使用收件人也没有记录增量。逐条处理与 4 个决策点的自主决断见[评审回复](reviews/711ab2a-response.md)。

| 验证项 | 实际结果 |
| --- | --- |
| P1-1 提交前拦截 | `XcodeMCPDriver` 在每次交互前的新鲜层级中定位启用的提交控件（`chat.send` / `compose.post`）；`tap`、`swipe` 起终点命中即在派发交互命令前返回 `submission_requires_approval`，无法定位启用控件时 `submit_guard_unavailable` fail closed。7 项驱动反例覆盖命中、命中 post、禁用变体放行、远离控件放行、无法定位、滑动终点命中、层级记录计数。`CoreAPI` 的事后绕行检测改由驱动 `record_count()` 提供当次观察的记录数，真实驱动也生效。 |
| 真机拦截验收 | `scripts/xcode-mcp-submit-guard-spike.py --sim-udid <UDID>` 在**不带** `pb-disable-submit` 的常规启动下实测：`send_button_enabled: true`、`submit_tap_error: submission_requires_approval`、`submit_tap_retry.allowed: false`、`record_increment: false`、`guard_passed: true`；运行后把夹具恢复为禁用提交，不影响 G1 基线。 |
| P1-2 恢复证据归属 | 台账在 `prepared` / `dispatching` 事件记录该收件人的脱敏记录基线；`submission_evidence` 只接受「目标收件人记录数超过基线且新增记录摘要匹配」为完成证据，无按收件人归属能力的层级驱动一律 `unclear` 并暂停。新增反例证明：其他联系人的同文旧记录、目标联系人的历史同文记录均不再误判为完成；带一条历史记录时回执丢失仍能正确确认（`completed`，记录数 2）。 |
| P1-3 暂停状态机 | `CoreAPI` 增加持久暂停：回读不明或检测到绕行后，所有写动作返回 `paused_pending_readback`，只读放行；`reset_task()` 不解除；可信侧 `resume_after_readback()` 须绑定一次成功的当次回读。收紧 `_fresh_evidence`：需 `ok`、`revision` 与具体内容证据，仅含证据键名不再判 `clear`。安全类拒绝列入 `NO_RETRY_ERRORS`，不返回重试建议。6 项新增单元测试覆盖。 |
| P1-4 隔离表述 | 新增 `isolation_mode`（`token` / `external_uid_unverified` / `demo`）；`approval_isolated` 仅在令牌模式为真，`phone_status` 披露模式。`--require-approval-isolation` 只认实际存在的令牌，单独 `--approval-uid` 会被拒绝启动。CLI 与文档写明独立 UID 需外部托管端点加 OS ACL。3 项新增测试覆盖。**真实跨账号审批验收仍未做**。 |
| P2-5 台账唯一事实来源 | `approve` / `deny` 改为先落盘成功再改内存，写盘失败返回 `approval_not_durable` / `decision_not_durable`；提交门派发前 replay 台账，缺 `approved` 事件一律拒绝。新增反例：注入写盘失败后审批被拒、即使伪造内存批准也无法派发、记录数为零。该条按 P1 处理（与 P1-2 同根因）。 |
| 单元测试 | 新增 22 项反例（驱动 7、核心 API 6、提交与审批 9），Python 全套 **160 项**、v0.2 归档 10 项通过。 |
| 回归 | 0b 拟稿试跑仍 `g1_passed: true`；XCUITest 4 项、真实 stdio 端到端与 `git diff --check` / lints 均通过。 |
| 标注撤回 | 实现计划步骤 2 由「已完成」改为「Mock 与台账层已完成，完整可写验收未过」，真实驱动提交前拦截的完整可写验收列为 G2 阻塞项；G1 拆为脚本通过与隔离会话复核待办两项。 |

**剩余边界**：真实跨账号审批验收、真机、真实第三方 App、Hark 完整联调、隔离 Codex 会话的自然语言拟稿复核均未完成；同账号下令牌也不是强隔离。评审反例使用本地合成数据与替身。

## 2026-11-11 追加 · G2 真实驱动完整可写验收

G2 的阻塞项「真实驱动提交前拦截的完整可写验收」此前一直没过，根因是 `XcodeMCPDriver.virtual_send` 硬编码 `real_message_sending_disabled`，「批准后只新增一条匹配记录」这条判据从未在真实驱动上被验证。本轮补全驱动能力并用 `scripts/xcode-mcp-g2-writable-spike.py` 在 Apple Simulator 上跑完整序列，**36 项布尔判据全部通过，`g2_passed: true`**。

| 验证项 | 实际结果 |
| --- | --- |
| 运行范围 | 自有合成夹具（bundle `org.example.phonebridge.fixture`）、iOS 27.2 Simulator、iPhone 18 Pro。夹具以**提交按钮启用**的常规方式启动，脚本结束前重新以 `pb-disable-submit` 启动恢复 G1 基线。服务必须同时带 `--driver xcode-mcp`、`--allow-xcode-interactions`、`--allow-fixture-virtual-submit`、`--require-approval-isolation`、实际令牌与持久化台账，缺一项 CLI 拒绝启动。 |
| 隔离与开关披露 | `phone_status` 返回 `submissions.approval_isolated: true`、`approval_isolation_mode: token`、`virtual_submit_enabled: true`。 |
| 未审批通用点击 | 常规启动下点启用的 `chat.send`，在**派发交互命令之前**返回 `submission_requires_approval`、`retry.allowed: false`，回读层级无 `chat.message.0`。 |
| 未审批执行 / 无令牌自批 | 未批准就 `phone_execute_operation` 返回 `independent_approval_required`；审批 CLI 不带令牌返回 `approval_token_required`，随后执行仍被拒；记录数保持零。 |
| 目标／正文／上下文变更 | 换收件人返回 `recipient_not_verified_in_current_chat`，改正文返回 `draft_mismatch`；拟稿后再次点击输入框使 revision 变化，审批返回 `stale_or_expired_proposal`，执行被拒，记录数保持零。 |
| 批准后单次提交 | 操作者带令牌批准后 `phone_execute_operation` 返回 `ok: true`、`submission_state: completed`、`verification.source: attributed_new_record`、`real_message_sent: false`、`virtual_fixture_send: true`；回读层级**恰好一条** `chat.message.0` 且 `label` 与草稿完全一致。 |
| 重放 | 同一操作 ID 再次执行返回 `unknown_or_processed_operation`，记录数仍为 1。 |
| 重连／重启不重发 | 停掉 MCP 服务后向台账注入一条 `dispatching`，用同一台账重启服务。启动恢复报告 `pending_pause: 1`、`confirmed: false`、`resent: false`、`evidence: record_not_attributable`；重启后回读仍只有 1 条记录，不重复提交。 |
| 台账脱敏 | 原始 JSONL 中不存在草稿明文；事件序列含 `prepared / approved / dispatching / completed`。 |
| 单元测试 | `tests/test_xcode_mcp.py` 新增 9 项（开关关闭、上下文解析、通用点击仍被拦、批准单次提交、目标／正文不匹配、禁用控件、记录未确认暂停、歧义联系人、重启无新鲜观察暂停）；`tests/test_submissions.py` 新增 2 项 CLI 门禁；`tests/test_core_api.py` 新增 2 项失败路径暂停。Python 全套 **173 项**、v0.2 归档 10 项通过。 |
| 回归 | `scripts/xcode-mcp-draft-spike.py` 仍 `g1_passed: true`、`record_increment: false`；`scripts/xcode-mcp-submit-guard-spike.py` 仍 `guard_passed: true`；XCUITest 4 项 `TEST SUCCEEDED`；lints 与 `git diff --check` 干净。 |

**边界声明**：这里产生的只是合成夹具内的一条合成记录，不是真实消息发送；没有打开真实第三方 App、没有连接真实设备、没有接外部审批设备。TTL 超时由单元测试用替身时钟验证，真机脚本用「屏幕变化导致 revision 失效」这一等价路径覆盖；Hark 经 SSH 的链路断线重连属于步骤 3，本轮的「重连」仅指 MCP 服务进程重启与台账恢复。真实跨账号审批验收、隔离 Codex 会话的自然语言拟稿复核（G1 第二项）、真机与真实第三方 App 仍未验证。

## 2026-11-11 追加 · 步骤 3 主机侧 Hark/SSH/Codex 链路证据

步骤 3 拆成「主机侧链路」与「Hark 客户端端到端」两段。本轮完成前者的可复跑脚本与自动化回归，**不宣称 Hark 联调整体通过**。为避免真实模型调用带来的不确定性，自动化证据使用确定性客户端按 MCP JSON-RPC 直连私有套接字，输出与 `codex exec --json` 同构的事件；真实 Codex 模型的工具发现与自然语言编排仍是未通过项。

| 验证项 | 实际结果 |
| --- | --- |
| 可重连传输 | `phonebridge/relay.py` 的后端进程持有 `CoreAPI`、审批端点与提交台账，监听 0700 目录内的 0600 Unix 套接字；每次 stdio 连接由 `python3 -m phonebridge.relay stdio` 代理并重建 MCP 协议会话。CLI 新增 `backend --socket`，与 `serve` 共用同一套审批与台账参数。 |
| 客户端与审批隔离 | `phonebridge/hark.py` 构造 inline MCP 配置与 SSH 包装；`codex_env()` 从客户端环境移除 `PB_HARK_TOKEN`，令牌只存在于后端与可信侧审批 CLI。每个阶段都校验「令牌不在客户端环境」「令牌不在命令行」。 |
| 直连证据 | `scripts/hark-codex-spike.py` 在 Mock Driver 上跑拟稿 → 可信侧批准 → 执行 → 新连接回读 → 未批准执行 → 重放，**51 项布尔判据全过、`host_chain_passed: true`**，四类证据 `success` / `failure` / `interruption` / `reconnect` 全部 `verified: true`，共 9 次独立连接共享同一后端套接字。 |
| 中断证据 | `--interruption-codex-path` 指定的客户端在发出 `phone_execute_operation` 后不读回执即关闭连接并退出。后端仍只提交一次；新连接回读该联系人恰好一条匹配记录，重放该操作 ID 被拒，`interruption_no_duplicate_record` 与 `interrupted_operation_replay_rejected` 均通过。 |
| SSH 回环证据 | `scripts/hark-codex-ssh-loopback-spike.py` 在 127.0.0.1 起临时 key-only `sshd`（临时主机密钥、客户端密钥、`authorized_keys`、`known_hosts`，`BatchMode=yes`），以 `--transport ssh` 把每个阶段包装成独立 SSH 会话；`ssh_loopback_precheck: true`，完整链路同样 `host_chain_passed: true`。不修改系统 SSH 配置、不启用 Remote Login。 |
| 台账与脱敏 | JSONL 事件计数为 `prepared ×3 / approved ×2 / dispatching ×2 / completed ×2`（未批准与被中断的提案各留下一条 `prepared`），原始台账不含任何一条合成正文。 |
| 客户端边界 | 每个阶段只调用 `phonebridge` MCP 服务器，未出现 shell 执行或越权工具；`hark_client_verified: false`、`hark_ui_verified: false`。 |
| 单元测试 | `tests/test_relay.py` 7 项（重连状态保持、stdio 代理、套接字权限、MCP 初始化与 `tools/list`、命令构造、SSH 包装、令牌隔离与事件解析）；`tests/test_hark_spike.py` 2 项分别跑直连与 SSH 回环完整链路。Python 全套 **182 项**、v0.2 归档 **10 项**通过；lints 与 `git diff --check` 干净。 |

**边界声明**：证据中的客户端是确定性替身，不是真实模型调用；本机虽安装 Hark 客户端，但本轮未操作其 UI，也未验证真实 Hark SSH 会话。真实 Codex CLI 的 MCP 工具注入在本机多次尝试中不稳定（工具未注入或模型连接超时），因此**不列为通过项**，现场联调时须重新验收。G2 的 36 项真实驱动判据与本节证据互相独立：本节使用 Mock Driver，只验证传输、审批、中断与防重。真实跨账号审批验收、隔离 Codex 会话的自然语言拟稿复核（G1 第二项）、真机、真实第三方 App 与真实消息发送仍未验证。

## 2026-11-11 追加 · 步骤 4 通道 A 统一路由

实现计划里的「双通道路由，A 优先 B 兜底」此前只有设计原则，没有代码。本轮补齐 `phonebridge/intents.py` 的决策层并接入 `CoreAPI` 与 MCP 壳，**只登记自有合成夹具的无副作用只读能力**，不触碰真实 App Intents、快捷指令或第三方 App。`scripts/intent-channel-spike.py` 跑出 **13 项判据全过、`channel_a_passed: true`**。

| 验证项 | 实际结果 |
| --- | --- |
| 能力清单与策略披露 | `intents_list` 返回 `device.status` / `contacts.lookup` / `chat.readback` 三项只读能力和一项标记为必须审批的 `message.send`；`policy.strategy = A_first_B_fallback`，`real_app_intents_verified` 与 `real_shortcuts_invoked` 均为 `false`。`phone_status.routing` 披露同一策略。 |
| 只读意图执行 | `intent_run` 对已公开能力返回 `channel: A`、`verification.outcome: clear`、`source: intent_readback`；`contacts.lookup` 读到 4 个合成联系人，`chat.readback` 带回新鲜 `revision` 与 `messages`。已公开能力不依赖当前 UI 屏幕，避免把通道 A 变成变相的界面交互。 |
| 写动作阻断 | `message.send` 返回 `intent_requires_approval`、`routing.channel: blocked`，执行器调用次数为零，安全类拒绝不返回重试建议。 |
| 未公开能力 | 返回 `intent_not_published`，只给出 `routing.channel: B`、`fallback_allowed: true` 的**决策**，不自动执行任何设备动作；通道 B 原有审批与提交门不变。 |
| A 失败回退判据 | 注入替身执行器：确认无副作用时 `fallback_allowed: true` 且不暂停，后续设备动作可继续；无法确认无副作用时返回 `pause: true`，下一个写动作被 `paused_pending_readback` 拒绝。 |
| 结果不明暂停 | 回读 `verified: false` 时返回 `intent_readback_unclear`、`routing.outcome: unclear`、`fallback_allowed: false`、`automatic_retry: false` 并进入持久暂停；可信侧 `resume_after_readback()` 绑定一次成功回读后写动作恢复。 |
| 无副作用证明 | 整条链路前后 `phone_inspect` 的 `messages` 均为空列表，通道 A 没有产生任何合成记录。 |
| 契约与回归 | 新增 `intents_list` / `intent_run` 两个 MCP 工具，工具数由 12 增至 14；原 12 个工具、`wechat_*` 隐藏别名与 v0.3 的 8 工具协议全部保留。`tests/test_intents.py` 11 项通过；Python 全套 **193 项**、v0.2 归档 **10 项**通过；lints 与 `git diff --check` 干净。本轮不改动设备驱动，既有 Simulator spike 未重跑。 |

**边界声明**：历史章节中的「12 个工具」是本轮之前的契约快照，不作为当前数量。本轮只实现路由决策与合成只读能力，**没有验证真实 App Intents／快捷指令能否触发、参数与用户交互如何、结果如何回读**；系统或第三方 App 的公开动作在实测前一律不列入能力清单。通道 B 的 Simulator 与 G2 证据不受影响。真实跨账号审批验收、隔离 Codex 会话的自然语言拟稿复核（G1 第二项）、Hark 客户端端到端、真实 Codex 模型编排、真机与真实第三方 App 仍未验证。

## 重跑自动测试

从仓库根目录运行。

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p 'test_subprocess.py' -v
python3 -m unittest discover -s legacy/v0.2 -v
python3 legacy/v0.2/demo.py

python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/phonebridge --help
.venv/bin/python -m pip install -r legacy/requirements-v0.2.txt
.venv/bin/python legacy/smoke_mcp_v02.py
```

原始 ZIP 可按 [资产清单](artifact-inventory.md) 核对 SHA-256。分别在安全解压后的 v0.4 目录和干净的 `4ff8df5` 检出中运行 `python3 -m unittest discover -s tests -v` 可重跑两条历史基线。

v0.4 的进程测试通过真实 stdio 发现工具，从 Mock 的合成联系人聊天生成提案，确认审批前执行被拒绝。再经独立 CLI 批准、写入一次并拒绝重放，回读同一聊天只有一条匹配记录。另一项测试省略审批套接字并确认全程没有写入记录。相同工作流的 Codex 手动复验步骤见 [README](../README.md)。

`tests/test_simctl.py` 的九项使用注入的进程替身验证驱动契约，包括模拟器设备枚举、夹具启动、截图权限与失败清理。使用完整 Xcode 的独立 SwiftUI 和 XCUITest 验证见 [Apple Simulator 步骤](apple-simulator.md)。
