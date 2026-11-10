# Apple iPhone Simulator 测试

仓库有两个独立的原生测试入口。两者都使用自有 SwiftUI App 和合成数据。

| 入口 | 验证范围 |
| --- | --- |
| `scripts/simulator-smoke.sh` | 保留自 GitHub 基线。编译、安装并启动精简夹具，检查 SwiftUI 首屏的 `onAppear` 写出本次运行专属的标记。 |
| `scripts/apple-simulator-test.sh` | 源自 v0.4。用 XcodeGen 生成工程，在原生 iPhone Simulator 上运行两项 XCUITest。检查同名联系人有不同标识，并从“小明”聊天输入和发送测试文本，读取聊天记录。 |

## 前置条件与运行方式

在 macOS 安装并选中完整 Xcode，完成初次启动设置，安装 iOS 27 Simulator runtime 并创建一台 iPhone Simulator。安装 [XcodeGen](https://github.com/yonaskolb/XcodeGen) 2.46.0 及以上版本。`simulator/project.yml` 已显式设置 `PRODUCT_NAME: $(TARGET_NAME)`，这是 Xcode 27 环境必需的，否则生成的工程会因空产物名而构建失败。从仓库根目录运行以下命令。

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
- 点击、滑动、输入、打开真实微信或发送真实消息超出 simctl Driver 的能力，工具会返回对应错误。

XCUITest 直接操作自有 App，模拟发送发生在 App 的内存里。当前 MCP 到 simctl 的路径限于状态、启动和截图。两条路径的结果分别记录在 [测试报告](test-report.md)。系统权限边界见 [iPhone 与 iOS 27 可行性](ios-feasibility.md)。
