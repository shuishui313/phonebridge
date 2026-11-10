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

仅允许 6 个设备交互／诊断工具。驱动准入先通过 `simctl` 清单和会话返回字段双重确认目标是 Simulator；每次交互前重新捕获层级并确认前台是自有夹具、截图与层级均为本次结果；握手和调用有硬超时（默认 15 秒，`--bridge-timeout` 可调），断线会释放会话。点击、滑动、输入和 Home 键默认拒绝，只有操作者在可信终端启动服务时追加 `--allow-xcode-interactions` 才启用，发送消息始终禁用。会话在服务结束时关闭。`--fixture-bundle` 可指定自有夹具的 bundle ID。边界说明见 [安全边界](security.md)，驱动用法见 [README](../README.md)。
