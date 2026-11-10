# PhoneBridge 验证报告

2026 年 11 月 10 日（香港时间）。本机为 macOS 27.0.1（26A434）、arm64、Python 3.10.10、Codex CLI 0.167.0-alpha.3.2，已安装 Xcode Command Line Tools。Python 套接字测试使用允许本机 Unix 域套接字的执行环境。

| 验证范围 | 实际结果 |
| --- | --- |
| 原 GitHub 基线 `4ff8df5` | 18 通过，0 失败，0 跳过。 |
| v0.4 原始 ZIP 独立解压 | 52 通过，0 失败，0 跳过。包含独立进程审批与模拟发送回读。 |
| 整合后的 Python 套件 | 79 通过，0 失败，0 跳过。其中 18 项为 v0.3 兼容回归，61 项为 v0.4。涵盖过期、拒绝、并发防重、MCP 错误恢复、审计、假 `simctl` 和实际 stdio 子进程。 |
| v0.2 原始源码 | 10 通过，0 失败，0 跳过。离线演示四步完成，回报 `simulated: true`、`message_sent: false`。 |
| v0.2 FastMCP 进程 | MCP Python SDK 1.30.0 发现 8 个工具。完成导航、手势、草稿与确认，原型返回 `blocked`，重放被拒绝。 |
| 打包和 CLI | `python3 -m pip install .` 构建并安装 0.4.0 wheel。在仓库外执行 `phonebridge --help` 成功。 |
| 本机 Codex MCP | 用隔离的 `codex app-server` 会话直接调用。v0.3 发现 8 项、v0.4 发现 12 项。两版均经独立审批终端写入并回读一条模拟消息。这次验证覆盖客户端和工具调用，Hark 和自然语言模型回合另行验收。 |
| 本机 Swift／脚本检查 | 两代 SwiftUI 与 v0.4 XCTest 的 3 份 Swift 文件通过解析，3 份 Shell 脚本通过 Bash 语法检查。 |
| GitHub Actions | 配置 Linux Python 与 Xcode 27 Simulator 测试工作流。本地通过 YAML 解析，远端运行记录见仓库 Actions。 |
| Hark、真机与真实微信 | 未运行。本次验证未连接实体 iPhone 或真实微信。 |

## 2026-11-10 晚间补充：Apple Simulator 首次实测

本机安装完整 Xcode 27.2（27B52）与 iOS 27.2 Simulator runtime（含 24B91016s 变体），`xcode-select` 指向完整 Xcode，许可已接受。测试设备为 iPhone 18 Pro Simulator（UDID `9117E5FD-038E-4A8D-B262-C427F6F5B489`），macOS 27.0.1、arm64。

| 验证项 | 实际结果 |
| --- | --- |
| `scripts/simctl-preflight.sh` | 通过。xcodebuild 27.2、simctl 可用、设备列表正常。 |
| `scripts/apple-simulator-test.sh` | `APPLE SIMULATOR XCTEST PASS`。两项 XCUITest 通过（0 失败）：同名联系人标识区分、从"小明"聊天输入并发送并读回。结果包在 `build/ios-ui.*/PhoneBridgeUITests.xcresult`。 |
| `scripts/simulator-smoke.sh` | 通过。夹具 App 在真实 Simulator 上启动并写出 `onAppear` 标记。 |
| simctl MCP Driver 实测 | `AppleSimulatorDriver.status()` 正确返回 Booted 的 iPhone 18 Pro；`open_app("fixture")` 启动夹具成功；`observe()` 截图成功并返回本地路径与 SHA-256。 |
| 修复记录 | 首次运行因 XcodeGen 2.46.0 + Xcode 27 生成的工程缺少 `PRODUCT_NAME` 而构建失败（"Multiple commands produce '.app'"）。在 `simulator/project.yml` 的 `settings.base` 显式加 `PRODUCT_NAME: $(TARGET_NAME)` 后通过。 |

Xcode 27 环境下 XcodeGen 需要显式 `PRODUCT_NAME` 的结论已沉淀，后续 GitHub Actions 的 macOS runner 同样适用。Hark、真机与真实微信仍未验证，本节结论只覆盖 Simulator 与 simctl Driver 能力。

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

v0.4 的进程测试通过真实 stdio 发现工具，从 Mock 的小明聊天生成提案，确认审批前执行被拒绝。再经独立 CLI 批准、写入一次并拒绝重放，回读同一聊天只有一条匹配记录。另一项测试省略审批套接字并确认全程没有写入记录。相同工作流的 Codex 手动复验步骤见 [README](../README.md)。

`tests/test_simctl.py` 的九项使用注入的进程替身验证驱动契约，包括模拟器设备枚举、夹具启动、截图权限与失败清理。使用完整 Xcode 的独立 SwiftUI 和 XCUITest 验证见 [Apple Simulator 步骤](apple-simulator.md)。
