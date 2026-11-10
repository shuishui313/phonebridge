# iPhone 与 iOS 27 可行性

PhoneBridge 第一阶段以 iPhone 和 iOS 27 为目标。测试目标分三层，每层覆盖不同的能力。

| 层次 | 可验证的行为 | 资产和验证范围 |
| --- | --- | --- |
| Python Mock | 模拟应用、联系人、草稿、独立审批、发送与回读 | 走 MCP 的本地端到端测试。消息留在虚拟手机进程内。 |
| Apple iPhone Simulator | 在 Apple 的 iOS 运行时验证自有 SwiftUI 测试 App 和受控 UI 流程 | 仓库提供独立 SwiftUI 启动检查及两项 XCUITest。MCP 另有受限 simctl Driver，支持状态、夹具启动和本地截图。 |
| 物理 iPhone | 在用户决定测试后，验证自有 App 和明确公开的系统或合作方能力 | 当前没有真机 Driver 或真机测试结果。 |

## 第三方 App 的权限边界

iOS 将第三方 App 隔离在各自的沙盒内。通过普通公开 API 安装的 PhoneBridge App 无法任意读取其他 App 的数据，也无法任意注入触控或读取它们的界面。当前模拟微信里的联系人、草稿和消息均为项目夹具。自有 App 或 Apple Simulator 中通过的测试无法证明真实微信具备相同能力。[Apple 平台安全指南](https://support.apple.com/en-sg/guide/security/sec15bfe098e/web)

App Intents 和快捷指令只提供 App 或系统明确公开的操作。若考虑接入第三方 App，应先确认它公开了哪些操作，再逐项验证可用参数、用户交互及结果回读。对微信的搜索联系人、填草稿、发送和校验均未做此类验证。[Apple App Intents 文档](https://developer.apple.com/documentation/appintents)

Xcode 的 XCTest 与 XCUIAutomation 可用于验证自有 App 的 UI。先用 iOS 27 的 iPhone Simulator 验证自有测试 App，记录 Xcode 版本、运行时、设备型号和测试结果。仓库提供首屏生命周期检查和测试 App 的联系人与聊天流程，执行方法见 [Apple Simulator 验证步骤](apple-simulator.md)。[Apple Xcode 测试文档](https://developer.apple.com/documentation/xcode/testing)

## 接入真实 Driver 的验证条件

在 Simulator 上接通 MCP 到自有测试 App 后，分别验证联系人识别、草稿绑定、审批后单次提交、失败后状态回读和重复执行。若发送结果无法确定，应先查询状态，再决定是否继续，避免重复发送。跨 App 能力只有在 iOS 或第三方提供明确接口并现场验证后，才进入 Driver 的可用能力清单。真机试验由用户另行决定，初期使用测试帐号与合成数据，敏感金融操作保持禁用。
