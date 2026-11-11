# Apple iPhone Simulator 测试

仓库有两个独立的原生测试入口。两者都使用自有 SwiftUI App 和合成数据。

| 入口 | 验证范围 |
| --- | --- |
| `scripts/simulator-smoke.sh` | 保留自 GitHub 基线。编译、安装并启动精简夹具，检查 SwiftUI 首屏的 `onAppear` 写出本次运行专属的标记。 |
| `scripts/apple-simulator-test.sh` | 源自 v0.4。用 XcodeGen 生成工程，在原生 iPhone Simulator 上运行三项 XCUITest。合成夹具包含两个测试 App 场景：私信（对标 WhatsApp，检查同名联系人标识、输入发送并读回）与信息流（对标 X，撰写、发布并读回）。均为自有合成界面与合成数据，不使用真实第三方 App 或账号。 |

## 前置条件与运行方式

在 macOS 安装并选中完整 Xcode，完成初次启动设置，安装 iOS 27 Simulator runtime 并创建一台 iPhone Simulator。安装 [XcodeGen](https://github.com/yonaskolb/XcodeGen) 2.46.0 及以上版本。`simulator/project.yml` 已显式设置 `PRODUCT_NAME: $(TARGET_NAME)`（Xcode 27 必需，否则生成的工程会因空产物名而构建失败）和 `ALWAYS_SEARCH_USER_PATHS: NO`（关闭不再支持的传统 headermap，消除构建警告）。从仓库根目录运行以下命令。

```sh
./scripts/simctl-preflight.sh
SIM_UDID='替换为可用的 iOS 27 iPhone Simulator UDID'
xcrun simctl bootstatus "$SIM_UDID" -b
./scripts/simulator-smoke.sh "$SIM_UDID"
./scripts/apple-simulator-test.sh "$SIM_UDID"
```

本地运行 UI 测试会在 `build/ios-ui.*` 下生成工程、DerivedData 和 `PhoneBridgeUITests.xcresult`。每次运行使用独立目录，可在 Xcode 打开结果包检查实际的测试数量和失败详情。记录 Xcode 版本、模拟器型号、iOS runtime 和通过／失败／跳过数量。可从 GitHub Actions 的 **Apple Simulator** 工作流手动重跑，并在日志里查看原生文件变更后自动运行的结果。

## 受限 simctl MCP Driver

先将 `PhoneBridgeFixture.app` 安装到选定的 Simulator。可在上面的 `build/ios-ui.*/DerivedData/Build/Products/Debug-iphonesimulator/` 目录中取得 App。使用 v0.4 的 MCP 入口启动服务。

```sh
python3 -m phonebridge.cli --driver simctl --sim-udid "$SIM_UDID" serve
```

- `phone_status` 查看选定 Simulator 的状态。
- `phone_open_app(app="fixture")` 启动已安装的夹具 `org.example.phonebridge.fixture`。
- `phone_inspect` 将截图保存在宿主机的本地临时文件中，返回路径和 SHA-256。
- 点击、滑动、输入、打开真实第三方 App 或发送真实消息超出 simctl Driver 的能力，工具会返回对应错误。

XCUITest 直接操作自有 App，模拟发送发生在 App 的内存里。当前 MCP 到 simctl 的路径限于状态、启动和截图。两条路径的结果分别记录在 [测试报告](test-report.md)。系统权限边界见 [iPhone 与 iOS 27 可行性](ios-feasibility.md)。

## Xcode MCP Driver

`python3 -m phonebridge.cli --driver xcode-mcp --sim-udid "$SIM_UDID" serve` 通过 Apple 官方 `xcrun mcpbridge` 观察设备状态、激活已安装夹具并读回本地截图与 UI 层级。交互命令语法来自 Xcode 导出的 `device-interaction` skill（`t x y`、`t x1 y1 f x2 y2`、`sender keyboard kbd <text>`、空命令仅捕获状态），0–1 坐标按层级文件中的 Window 尺寸换算。

步骤 0b 拟稿试跑脚本为 `scripts/xcode-mcp-draft-spike.py --sim-udid <UDID>`：它用带 `pb-disable-submit` 参数的夹具重启设备会话，按最新 UI 层级的稳定标识定位合成联系人和输入框，录入并回读完整草稿，核验记录增量为零与两处拒绝生效，最后输出 `g1_passed`。运行脚本即代表操作者显式开启交互能力；隔离的 Codex 会话可复用同一序列复核。证据见 [测试报告](test-report.md)。

提交前拦截复验脚本为 `scripts/xcode-mcp-submit-guard-spike.py --sim-udid <UDID>`：它**不带** `pb-disable-submit` 启动夹具，使 Send 按钮处于启用状态，按层级定位并点击该控件，验证在派发交互命令前返回 `submission_requires_approval`、无重试建议、记录增量为零，最后输出 `guard_passed` 并把夹具恢复为禁用提交。常规启动的提交按钮不再能被通用点击触发；[评审回复](reviews/711ab2a-response.md) 记录了该修复。

G2 完整可写验收脚本为 `scripts/xcode-mcp-g2-writable-spike.py --sim-udid <UDID>`：服务同时带 `--allow-xcode-interactions`、`--allow-fixture-virtual-submit`、`--require-approval-isolation`、实际令牌与持久化台账；脚本验证未审批通用点击被事前拒绝、无令牌自批被拒、目标／正文／上下文变更失效、批准后只新增一条可归属的合成记录、重放被拒、MCP 服务重启后 in-flight 操作暂停且不重发，共 36 项判据，输出 `g2_passed`。结果见 [测试报告](test-report.md)。

仅允许 6 个设备交互／诊断工具。驱动准入先通过 `simctl` 清单和会话返回字段双重确认目标是 Simulator；每次交互前重新捕获层级并确认前台是自有夹具、截图与层级均为本次结果；握手和调用有硬超时（默认 15 秒，`--bridge-timeout` 可调），断线会释放会话。点击、滑动、输入和 Home 键默认拒绝，只有操作者在可信终端启动服务时追加 `--allow-xcode-interactions` 才启用。`--allow-fixture-virtual-submit` 是第二个显式开关，只允许唯一提交门在**自有合成夹具**内生成一条合成记录（`simulated: true` / `real_message_sent: false`）；它要求完整审批隔离，默认关闭，夹具以 `pb-disable-submit` 启动时不生效。**真实消息发送始终禁用**。两个验收脚本都会在结束时把夹具恢复为 `pb-disable-submit`。会话在服务结束时关闭。`--fixture-bundle` 可指定自有夹具的 bundle ID。边界说明见 [安全边界](security.md)，驱动用法见 [README](../README.md)。
