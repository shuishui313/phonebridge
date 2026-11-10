# iPhone 与 iOS 27 可行性

PhoneBridge 第一阶段以 iPhone 和 iOS 27 为目标。测试目标分三层，每层覆盖不同的能力。

| 层次 | 可验证的行为 | 资产和验证范围 |
| --- | --- | --- |
| Python Mock | 模拟应用、联系人、草稿、独立审批、发送与回读 | 走 MCP 的本地端到端测试。消息留在虚拟手机进程内。 |
| Apple iPhone Simulator | 在 Apple 的 iOS 运行时验证自有 SwiftUI 测试 App 和受控 UI 流程 | 仓库提供独立 SwiftUI 启动检查及两项 XCUITest。MCP 另有受限 simctl Driver，支持状态、夹具启动和本地截图。 |
| 物理 iPhone | 在用户决定测试后，验证自有 App 和明确公开的系统或合作方能力 | 当前没有真机 Driver 或真机测试结果。 |

## 两种第三方 App 支持方式（双通道策略）

产品需求包含两种互补的第三方 App 操作方式，PhoneBridge 用同一驱动抽象承载，不互斥：

| 通道 | 方式 | 典型场景 | 依赖 | 边界 |
| --- | --- | --- | --- | --- |
| A · 意图通道 | App Intents / 快捷指令，调用系统或 App 显式公开的动作 | 「给妈妈发短信说我不回家吃饭」→ 系统「发送信息」动作 | 本机即可运行；Mac 上可用 `shortcuts run` 触发 | 只能做被公开的动作；多数第三方私信 App 不公开此类接口，未验证 |
| B · 设备接管通道 | 通用 UI 接管：观察屏幕 → 合成点击/滑动/输入 → 回读验证 | 操作任意 App 的任意界面 | iOS 27+ 可走 Xcode 官方 mcpbridge / Device Hub 协议；iOS 17.4–26 社区用 WebDriverAgent 自签 Helper | 需 USB 配对、开发者模式等设置；真机阶段另行授权 |

路由原则：能用 A 就不用 B（更稳、更快、不依赖 UI 变化）；A 不可用时才走 B。两条通道统一经过 PhoneBridge 的审批、验证、预算与审计层，Agent 入口不变。

### 社区公开方案调研结论（2026-11-10，仅阅读公开文档，未复制代码，文档不记录具体项目名称）

- Android 侧的通用接管方案依赖无障碍服务、无线调试与虚拟后台屏，其技术无法迁移到 iOS，但「后台操作不打扰用户」「高风险操作前询问」的交互范式可参考。
- 已有社区项目在公开文档中声明：真 iPhone 无越狱条件下，经 USB 配对、开发者模式和开发者服务通道可操作任意 App；自签 App（免费账号 7 天续签）配合本机回环代理可让控制服务在手机上运行、脱离电脑。这证明「真机任意 App 控制」与「本机运行」存在可行路径。
- 社区文档还给出通道 B 的工程约束：USB 信任、开发者镜像挂载、银行类 App 的环境检测、以及 Agent 可绕过网关直连设备的风险。
- 部分参考项目采用 AGPL 等强 copyleft 许可证，只阅读公开文档，不做代码级参考。

上述能力均来自社区项目公开文档的声明，PhoneBridge 未独立复现，不构成本仓库的验证证据。

### 对既有结论的修正

此前「普通 App 无法控制其他 App」的判断只覆盖「iPhone 上普通 App 作为控制器」的场景。多个社区项目的公开文档表明，经 Apple 开发者通道从外部驱动真机是另一条路径，不需要越狱，也不违反本项目的安全约定（不绕过系统安全保护，使用开发者模式与自签等 Apple 支持的机制）。代价是设备设置门槛、自签续期、第三方 App 服务条款风险和部分 App 的环境检测。Xcode 27 官方 mcpbridge 与社区方案使用的 Device Hub 协议同源。通道 B 的官方入口已在 Simulator 实测确认：可激活并操作系统「设置」App、合成点击、读回截图与 UI 层级（见[测试报告](test-report.md)）。真机与真实第三方 App 的验证仍按用户另行授权执行。

## 第三方 App 的权限边界

iOS 将第三方 App 隔离在各自的沙盒内。通过普通公开 API 安装的 PhoneBridge App 无法任意读取其他 App 的数据，也无法任意注入触控或读取它们的界面。当前合成私信场景里的联系人、草稿和消息均为项目夹具。自有 App 或 Apple Simulator 中通过的测试无法证明真实第三方 App 具备相同能力。[Apple 平台安全指南](https://support.apple.com/en-sg/guide/security/sec15bfe098e/web)

App Intents 和快捷指令只提供 App 或系统明确公开的操作。若考虑接入第三方 App，应先确认它公开了哪些操作，再逐项验证可用参数、用户交互及结果回读。对目标私信与信息流 App 的搜索联系人、填草稿、发布和校验均未做此类验证。[Apple App Intents 文档](https://developer.apple.com/documentation/appintents)

Xcode 的 XCTest 与 XCUIAutomation 可用于验证自有 App 的 UI。先用 iOS 27 的 iPhone Simulator 验证自有测试 App，记录 Xcode 版本、运行时、设备型号和测试结果。仓库提供首屏生命周期检查和测试 App 的联系人与聊天流程，执行方法见 [Apple Simulator 验证步骤](apple-simulator.md)。[Apple Xcode 测试文档](https://developer.apple.com/documentation/xcode/testing)

## 接入真实 Driver 的验证条件

在 Simulator 上接通 MCP 到自有测试 App 后，分别验证联系人识别、草稿绑定、审批后单次提交、失败后状态回读和重复执行。若发送结果无法确定，应先查询状态，再决定是否继续，避免重复发送。跨 App 能力只有在 iOS 或第三方提供明确接口并现场验证后，才进入 Driver 的可用能力清单。真机试验由用户另行决定，初期使用测试帐号与合成数据，敏感金融操作保持禁用。

## Xcode MCP 的开发用途

Apple 为外部 Codex 提供 `xcrun mcpbridge`。安装完整 Xcode 后，可在 Xcode 的 Intelligence 设置中允许外部 Agent 使用工具，打开生成的 PhoneBridgeFixture 工程，再在开发用 Codex 会话中注册 `codex mcp add xcode -- xcrun mcpbridge`。Claude Code 市场的 Xcode 插件由 Apple Inc. 提供，入口链接本机 Xcode 自带的插件。两者可用于构建、SwiftUI 预览和诊断，Xcode 27 还演示了在自有 App 的 Simulator 上观察无障碍树和截图，以及点击、滑动和输入。[Apple 外部 Agent 指南](https://developer.apple.com/documentation/xcode/giving-external-agents-access-to-xcode) · [Apple Simulator 演示](https://developer.apple.com/videos/play/wwdc2026/8121/?time=1956) · [Claude Code 市场条目](https://github.com/anthropics/claude-plugins-official/pull/6338/files)

先在独立开发会话试跑一次 `观察 → 按稳定 ID 找合成联系人 → 输入 → 回读草稿`。确认外部 Codex 能发现并调用所需工具后，再评估是否用它实现自有测试 App 的实时 Simulator 适配，或继续使用专用 XCTest 桥。CI 以固定的 XCUITest 保留回归证据。将开发工具留在独立会话，Hark 执行侧通过 PhoneBridge 保持审批约束。`xcrun mcpbridge` 是 Mac 侧开发工具，依赖本机完整 Xcode 与其后台服务，**不代表「只在 iPhone 本机运行」目标取得进展**；该目标仍按 [产品需求](product-requirements.md) 单独规划。PhoneBridge 已将官方能力包装为 `XcodeMCPDriver`，仅使用设备交互与诊断工具白名单，改变设备状态的操作默认拒绝，需操作者在启动服务的受控终端显式开启，详见 [安全边界](security.md)。Mac 开发环境中连接自有测试 App 的能力，与 iPhone 上第三方 App 的权限分别验收。[测试报告](test-report.md) · [iOS 运行时安全](https://support.apple.com/en-ie/guide/security-pdf/sec15bfe098e/1/web/1)
