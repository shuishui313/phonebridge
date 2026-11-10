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
