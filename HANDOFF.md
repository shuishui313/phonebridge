# PhoneBridge — 项目总方案与开发交接（HANDOFF）

## 当前接手点（2026-11-11）

v0.4 和 v0.2 原始 ZIP 已取回并校验。以 `4ff8df5` 为起点整合，v0.4 使用 `python3 -m phonebridge.cli` 或安装后的 `phonebridge`，v0.3 保留原命令和协议，v0.2 可独立重跑。Python、MCP、审批、SwiftUI、XCUITest 与 XcodeGen 源码均在仓库。原始归档和每个成员的 SHA-256 见 [资产清单](docs/artifact-inventory.md)。

本次重跑的结果与未验证项见 [测试报告](docs/test-report.md)。当前测试目标 App 为自有合成夹具中的私信与信息流场景（对标 WhatsApp 与 X 的交互形态），v0.4 工具名已通用化为 `contacts_list` / `contacts_open` / `message_prepare`，旧名保留为可调用别名；正文和附录中提及「微信」或 `wechat_*` 的地方均为历史快照描述。[产品需求](docs/product-requirements.md) · [安全边界](docs/security.md)

### 本日进展

- 昨日收工交接为 `4add3cd`，同事 review 记录为 `fd65db6`（`docs/reviews/711ab2a.md`）。今日远端无新提交、评审方未对 4 个决策点回复；按「减少过程性提问」的既有偏好，**按保守方向自主决断**并写入实现，评审方可复核或推翻。逐条映射见 [评审回复](docs/reviews/711ab2a-response.md)。
- **4 个自主决断（上午，当时状态）**：撤回步骤 2「已完成」改为「Mock 与台账层完成，完整可写验收未过」并列为 G2 阻塞项（该阻塞项已在同日下午解除，见下方 G2 条目）；真实驱动恢复证据无法归属到操作即一律暂停人工核验；暂停全局写动作、只读放行、可信侧 `resume_after_readback()` 绑定成功回读；`--approval-uid` 降级为外部托管模式、令牌为默认隔离。
- **5 条发现全部修复**：台账改为唯一事实来源（审批先落盘、派发前验 `approved` 事件、恢复按收件人与记录基线增量归属）；真实驱动在派发交互命令前拦截启用的提交控件、无法定位即 fail closed；`CoreAPI` 增加持久暂停、收紧回读 `clear` 判据、安全类拒绝不返回重试建议；隔离状态新增 `isolation_mode` 披露、门禁只认实际令牌。P2-5 按 P1 处理，与 P1-2 合并为同一根因修复。
- **验证**：Python 160 项、v0.2 归档 10 项、XCUITest 4 项通过；`scripts/xcode-mcp-draft-spike.py` 仍 `g1_passed: true`；新增 `scripts/xcode-mcp-submit-guard-spike.py` 在常规启动（提交按钮启用）下实测 `guard_passed: true`（事前拒绝、无重试建议、记录增量为零，运行后恢复禁用夹具）；lints 与 `git diff --check` 干净。
- **文档**：新增评审回复记录；实现计划撤回步骤 2 完成标注、写明 G2 阻塞项并更新现状表，G1 拆为「脚本通过 / 隔离会话复核待办」两项；测试报告补修复证据；安全边界、架构、README 与 Simulator 指南同步。所有改动已推送 `main`。
- **G2 完整可写验收（同日继续完成）**：此前真实驱动 `virtual_send` 硬编码禁用，「批准后只新增一条匹配记录」从未在真实驱动上验证过。本轮给 `XcodeMCPDriver` 补齐从新鲜层级解析的合成上下文（app / screen / selected_id / input_text / contacts / messages，只暴露当前联系人）与受控虚拟提交；新增操作者显式开关 `--allow-fixture-virtual-submit`，CLI 强制它必须同时具备真实驱动、交互授权、实际令牌与持久化台账；通用点击仍被事前拦截，只有唯一提交门能触发。`scripts/xcode-mcp-g2-writable-spike.py` 在 Apple Simulator 上实测 **36 项判据全过、`g2_passed: true`**，覆盖未审批拒绝、无令牌自批、目标／正文／上下文变更、批准后恰好一条可归属记录、重放拒绝、MCP 服务重启不重发、台账脱敏；运行后恢复 `pb-disable-submit` 基线。顺带修掉一个真实缺口：提交门返回 `pause: true` 的失败路径此前不进入持久暂停，证据 `unclear` 也会被任务级 `clear` 覆盖。详见 [测试报告](docs/test-report.md) 与 [安全边界](docs/security.md)。
- **验证更新**：Python 173 项、v0.2 归档 10 项、XCUITest 4 项通过；0b 拟稿试跑仍 `g1_passed: true`、常规启动拦截脚本仍 `guard_passed: true`；lints 与 `git diff --check` 干净。

### 下一阶段

1. **步骤 3 · Hark 联调（G2 剩余阻塞项）**：真实驱动的完整可写验收已通过，Hark 经 SSH 的连通、成功、失败、中断记录与重连防重现在是 G2 最后的阻塞项；通道 A 可并行验证自有 App 的无副作用意图。步骤 3 通过且核心 API 稳定后，再排期主机托管的 Swift 原型和远程 MCP。 [Hark 接入步骤](docs/hark-codex.md) · [实现计划](docs/implementation-plan.md)
2. **真实跨账号审批验收**：`--approval-uid` 在当前 0700 目录 / 0600 套接字模型下默认不可连接，需先决定并实现外部托管端点加 OS ACL 或外部可信审批设备，再用真实不同 UID 进程验收；现有测试只校验了凭据判定逻辑。 [评审回复](docs/reviews/711ab2a-response.md)
3. **G1 剩余项**：在隔离 Codex 会话中按 `scripts/xcode-mcp-draft-spike.py` 的等价工具序列复核自然语言拟稿，单独记录结果；该项仍为待办，不与自动化脚本结果合并。 [测试报告](docs/test-report.md)
4. **边界不变**：真实消息发送始终禁用；`--allow-fixture-virtual-submit` 只是自有合成夹具内的验收开关，默认关闭，必须与实际令牌和持久化台账同时启用，不打开真实第三方 App、不接真机。可写联调与真机阶段必须用 `--require-approval-isolation` 启动并配置实际令牌、指定 `--submission-state`；同账号下令牌也不是强隔离。提交结果不明确时先回读，仍无法判定就持久暂停写动作、只读放行，禁止自动重发。G3a 经用户另行授权后开始真机只读取证；G3b 在 G2 通过、只读证据齐备、审批已外置并取得单独写入授权后，才在自有 App 上验证受控写入；目标第三方 App 另行逐能力验证，端侧独立运行由 G4 单独验收，金融与支付默认禁用。真机、真实第三方 App、跨账号审批与 Hark 完整联调均未验证。 [安全边界](docs/security.md) · [iOS 可行性](docs/ios-feasibility.md)


**项目名称：** PhoneBridge  
**GitHub：** https://github.com/shuishui313/phonebridge  
**文档日期：** 2026-11-10  
**目标平台：** iPhone，优先验证 iOS 27  
**主要 Agent 入口：** Hark  
**开发主力：** Codex  
**可选模型：** DeepSeek API、其他支持工具调用的 AI 模型  
**当前状态：** MCP 软件原型已实现并完成模拟测试，尚未完成真实 iOS 控制闭环。

---

## 一、用户最终想实现什么

开发一个原创、独立、可扩展的 **iPhone Phone Agent 控制工具**。

最终用户可以在 Hark 中用自然语言下达指令，例如：

> 打开微信，找到我的测试联系人，发送“我今天晚上八点到”。

PhoneBridge 应使 AI Agent 能够：

1. 理解用户的自然语言任务。
2. 获取当前手机界面及设备状态。
3. 识别目标 App、联系人和界面元素。
4. 规划点击、滑动、输入等操作。
5. 执行操作并获取新的屏幕状态。
6. 判断操作是否成功，失败时有限重试。
7. 对发送消息等敏感操作进行独立用户审批。
8. 返回最终执行结果、失败原因和必要的操作记录。

**产品定位不是重新开发一个聊天 App，而是提供统一的手机操作能力。产品分两种形态：供 Hark、ChatGPT 等 Agent App 直接接入的 MCP 服务，以及自带对话入口的独立 App。两形态共享同一核心，详见 [产品需求](docs/product-requirements.md)。**

首期只开发 iPhone，不开发 Android。

### 用户对产品的明确要求

- Hark 是主要交互入口，不希望日常使用时频繁切换软件。
- 支持 Codex、ChatGPT 可用的集成方式以及其他兼容 Agent。
- 尽量减少对长期在线电脑和云服务器的依赖。
- 最终希望只在 iPhone 本机运行，但允许先通过电脑或测试环境验证能力。
- 开发过程尽可能自主完成，用户只负责最终验收。
- 优先复用已经写好的原创 PhoneBridge 代码，不复制其他同类项目的代码。
- 第一版聚焦一个真正跑通的操作闭环，而不是堆积功能。
- 尽量一天交付可验证的阶段性 MVP，但不能用模拟结果冒充真机能力。

---

## 二、最终产品架构

建议采用可替换执行器的结构：

```text
用户
 │
 ▼
Hark / Codex / 其他 Agent
 │
 ▼
MCP 客户端
 │
 ▼
PhoneBridge MCP Server
 │
 ├── 会话管理
 ├── 工具调用与参数校验
 ├── 操作审批与权限控制
 ├── 执行日志
 └── 设备适配层
       │
       ├── Mock Driver
       ├── Apple Simulator Driver
       └── Future iPhone Driver
              │
              ▼
          iPhone / 测试 App
```

### 必须分离的模块

**Agent 层：** 由 Codex 或其他模型负责理解任务、制定计划和选择工具。

**MCP 服务层：** 负责暴露统一手机操作接口，不直接依赖特定模型。

**设备执行层：** 负责屏幕观察、点击、滑动和输入等具体动作。

**安全层：** 独立于 Agent 执行权限控制，确保模型不能自行绕过用户审批。

**验证层：** 比较操作前后状态，判断动作是否成功。

### Hark 集成原则

第一条可行路径是：

`Hark → SSH → Codex → MCP → PhoneBridge`

这条路线适合初期验证，但 Hark 到 Codex 的完整实际联调仍待完成。

长期研究目标是：

`Hark → 本机 PhoneBridge → iPhone 操作能力`

这条路线需要确认 Hark 的扩展能力，以及 iOS 是否允许目标控制方式。

**不能假设 Hark 能通过 SSH 自动获得对同一台 iPhone 上其他 App 的控制权限。**

---

## 三、iOS 技术可行性与关键约束

这是整个项目最重要的技术风险。

普通第三方 iOS App 不能直接获得控制任意其他 App 的通用系统权限。

Apple 官方 XCUITest 可以在受支持的开发和测试环境中执行界面自动化，但不能据此推断普通 App Store 应用也能控制微信。

因此必须分别验证：

1. **软件 Mock 环境**：验证 MCP、状态机、Agent 调用、权限和测试逻辑。
2. **Apple 官方 Simulator**：使用 macOS、Xcode 和测试 App 验证实际界面操作。
3. **iPhone 真机**：研究授权测试环境下可行的设备执行能力。
4. **独立本机运行**：单独研究脱离电脑、服务器的技术可行性及可分发性。

模拟器成功不等于真机成功；真机研究成功也不等于能够作为普通 App 向所有用户分发。

如果完全本机跨 App 控制不可行，应明确技术边界，并评估系统支持的 App Intents、快捷指令、用户辅助操作或外部设备执行等替代路线。

---

## 四、已有成果与真实进度

以下是此前开发交接记录，接手者应以实际仓库代码和重新执行的测试为准。

### v0.2：早期原创原型

已有文件：

- `bridge.py`
- `server.py`
- `simulator.py`
- `test_bridge.py`
- `test_workflow.py`
- `demo.py`
- `README.md`
- `TEST_REPORT.md`

实现了基本 MCP 接口、模拟设备执行器、消息审批和基础测试。

### GitHub 仓库版本

此前交接显示：

- 仓库 `main` 曾有提交 `07bf738`。
- 已实现 8 个 MCP 工具。
- 已报告 18 项自动化测试通过。
- 已报告本机 Codex 的 MCP 工具发现及模拟任务调用得到验证。

上述提交及状态需要重新检查，不应默认仍是仓库最新版本。

### v0.4：尚待合并进 GitHub 的开发包

此前交付的文件名：

`PhoneBridge_v0.4_source.zip`

此前报告的功能：

- 12 个 MCP 工具。
- 52 项 Python 自动化测试通过。
- 模拟微信联系人查找、消息草稿和发送审批。
- MCP 跨进程测试。
- 模拟操作结果验证。
- Apple Simulator 测试适配代码。
- SwiftUI 测试 App 与 XCUITest 相关源码。
- README、开发说明和测试报告。

**注意：v0.4 不等于已完成 GitHub 合并。测试数字来自此前的开发记录，接手者必须在自己的环境中重新执行测试。**

### 当前验收矩阵

| 能力 | 记录状态 |
|---|---|
| Python Mock Driver | 已实现，曾报告测试通过 |
| MCP 基础接口 | 已实现 |
| 模拟微信流程 | 曾报告通过 |
| 独立消息审批 | 已有实现，需继续验证 |
| Apple Simulator 相关代码 | 已编写，未在官方环境实测 |
| Apple 官方 Simulator 运行 | 未完成 |
| Hark 完整联调 | 未完成 |
| 真实 iPhone 控制 | 未完成 |
| 真实微信消息操作 | 未完成 |
| 完全 iPhone 本机运行 | 未完成 |
| v0.4 同步 GitHub | 未完成 |

任何开发者都不能将 Mock 测试通过描述为真实 iPhone 控制已经成功。

---

## 五、接下来必须完成的开发工作

### P0：整理并统一代码基线

目标：形成唯一可信的 GitHub 项目主分支。

任务：

- 检查当前 GitHub 仓库全部文件、分支和提交历史。
- 解压并检查 v0.4 开发包。
- 对比现有仓库版本与 v0.4。
- 合并有效实现，避免覆盖已完成的功能。
- 清除重复模块和无效测试。
- 固定 Python、MCP SDK 等依赖版本范围。
- 配置自动测试和 CI。
- 完善 README 和发布说明。
- 确保密钥、Token、设备识别信息不进入 Git。

验收：从干净环境获取仓库后，可以安装依赖并运行全部软件自动化测试。

### P1：完成真实的 Simulator 自动化

环境：macOS + Xcode + Apple 官方 iOS Simulator。

任务：

- 构建并启动 SwiftUI 测试 App。
- 使用官方测试能力读取界面状态。
- 验证点击、滑动、输入和界面切换。
- 将结果适配至 PhoneBridge Driver 接口。
- 验证截图或其他受支持的观察方式。
- 为操作失败、坐标失效、界面变化和超时添加测试。
- 记录设备型号、系统运行时版本、Xcode 版本和实际测试结果。

必须区分 Simulator 实际执行与 Mock Driver。

验收：测试 App 中完成可重复的真实 UI 自动化，并有可核验的测试日志。

### P2：完成 Agent 与 MCP 闭环

任务：

- 验证 Codex 能发现全部 MCP 工具。
- 验证真实 MCP 工具调用，而不只是直接调用 Python 方法。
- 实现 `observe → plan → act → verify` 循环。
- 设置单任务执行时限、动作次数上限和重试次数。
- 每个动作都返回明确的状态和错误信息。
- 对用户可见的任务结果与实际设备状态进行交叉验证。
- 任务中断时保存必要状态，并避免重复执行危险动作。

验收：用户提供自然语言任务，Codex 能自动调用 PhoneBridge，在测试 App 中完成流程并报告结果。

### P3：完成 Hark 集成

任务：

- 验证 Hark 可以连接运行 Codex 的受控主机。
- 验证从 Hark 发出的自然语言指令能触发 MCP 工具。
- 确认 Agent 执行状态可查询。
- 验证任务成功、失败和中断后的反馈。
- 确认 SSH 断连和恢复不会导致任务意外重复执行。
- 单独调查 Hark 是否具有可信的原生扩展接口。

验收：在 Hark 中输入指令，测试 App 实际执行，并能在 Hark 中查询结果。

### P4：进行 iPhone 真机可行性研究

**只有用户决定进入真机测试后才进行。**

研究任务：

- 确认 iOS 27 对应设备及测试环境。
- 验证可用的授权设备连接方式。
- 研究目标控制方式是否依赖 Mac、开发者权限或特殊安装方式。
- 检验是否能够观察及操作目标第三方 App。
- 评估是否具备普通用户安装和分发的条件。
- 编写技术可行性报告，明确成功条件及不可实现的功能。

不能通过绕过系统安全保护来假装满足 App Store 普通应用的权限要求。

---

## 六、第一版端到端验收场景

以自行开发的合成测试 App 为目标，交互形态对标 WhatsApp（私信）与 X（信息流），Simulator 阶段不使用真实第三方 App 或真实账号。

任务：

> 打开测试 App，找到“测试联系人 A”，输入“你好，这是一条 PhoneBridge 测试消息”，等待我确认后发送。

完整验收步骤：

1. Agent 接收任务。
2. 调用 PhoneBridge，读取当前状态。
3. 打开测试 App。
4. 找到准确的测试联系人。
5. 检查收件人是否匹配。
6. 填入指定消息。
7. 返回草稿和收件人供用户确认。
8. 没有审批时绝不发送。
9. 获得有效审批后执行一次发送。
10. 读取结果，确认测试 App 中出现对应消息记录。
11. 返回完成状态及必要日志。

还要测试：

- 找不到联系人。
- 有多个同名联系人。
- 界面加载失败。
- 操作超时。
- 用户取消。
- 重复审批。
- 重复发送。
- 网络或 Agent 中断。
- 不受允许的 App。
- 未授权的敏感操作。

发送操作必须受独立安全模块控制，不能只依赖模型的提示词。

---

## 七、安全要求

- 采用最小权限。
- 默认不允许访问银行、券商、支付等敏感 App。
- 发送消息、执行外部交易、删除数据等动作须使用明确权限规则。
- 使用短时有效的操作审批，并绑定具体操作、目标和内容。
- 凭据放在环境变量或安全密钥管理系统，不写入代码库。
- 日志对个人信息、消息正文和 Token 进行脱敏。
- 不将完整手机截图默认上传到不可信服务。
- 保留用户停止任务的能力。
- 防止模型生成的指令绕过工具边界或审批流程。
- 尊重第三方 App 的规则及用户授权。

---

## 八、开源与原创性要求

可研究同类社区项目的公开技术文档与问题讨论（仓库文档不记录具体项目名称），但不得复制其源代码后简单修改名称、格式或变量来掩盖来源。

PhoneBridge 应保持独立实现、清晰记录第三方依赖，并遵守实际使用的开源许可证。

---

## 九、建议的 GitHub 文档结构

```text
phonebridge/
├── README.md
├── AGENTS.md
├── HANDOFF.md
├── docs/
│   ├── architecture.md
│   ├── product-requirements.md
│   ├── test-report.md
│   ├── ios-feasibility.md
│   ├── hark-integration.md
│   └── security.md
├── [existing source modules]
├── [existing tests]
└── [simulator / ios test assets]
```

接手者应先查看实际仓库结构，不要机械创建重复目录。

`AGENTS.md` 应保留对后续 Codex Agent 的执行说明。

---

## 十、给下一位 Codex Agent 的指令

你现在是 PhoneBridge 项目的主要开发 Agent。

请以 https://github.com/shuishui313/phonebridge 为唯一目标仓库继续开发。

首先检查真实仓库状态、分支、提交和现有测试，不要假设以前交接中的提交仍是最新状态。

获取并比对 v0.4 代码包，合并尚未进入仓库的有效功能。

优先完成：

1. 统一代码和测试基线。
2. MCP 工具及安全机制完善。
3. 官方 Apple Simulator 测试。
4. Codex MCP 实际调用。
5. Hark 端到端集成。
6. 项目文档与 CI。

不要重新发明已经实现的功能。

不要将 Mock 测试解释成真实 iPhone 控制成功。

不要在未授权时操作真实第三方 App、发送真实消息或处理敏感数据。

遇到 macOS、Xcode、GitHub 权限或设备访问等不可消除的环境限制时，完成所有不依赖该条件的工程工作，并清楚记录阻碍。

每次交付必须包含：

- Git commit SHA。
- 实际修改的文件。
- 测试命令和测试结果。
- 已验证能力。
- 未验证能力。
- 已知问题。
- 下一阶段的具体任务。

不要为了满足工期而伪造测试结果。

**用户的工作偏好：自主完成开发，尽可能减少过程性提问，用户只参与最终验收及必要的高风险授权。**

---

## 十一、项目完成的判定标准

PhoneBridge 软件 MVP 完成，需要：

- MCP 服务可可靠启动。
- Agent 可以完成真实工具调用。
- 独立审批与安全边界有效。
- Apple Simulator 中完成测试 App 的真实 UI 自动化。
- Hark 能发出自然语言任务并收到执行结果。
- 所有结果具有可重复验证的测试证据。
- 源代码和文档同步 GitHub。

真实 iPhone 以及真实微信操作属于后续单独验收阶段，不应提前标记为完成。

**最终产品愿景：一个用户只需发出自然语言指令，就能让 AI 在授权范围内观察、操作和验证 iPhone 任务的通用 Phone Agent Bridge。**

---

## 附录：2026-11-10 仓库核验与接手起点

交接正文保留了上一轮的产品方案和历史记录。开始开发时，以本节所列的仓库基线与实际测试为准。

### 仓库基线与测试证据

同步本文前，GitHub 默认分支为 `main`，最新提交为 [`087298c01db12f22806f1c980238b72da06c3f59`](https://github.com/shuishui313/phonebridge/commit/087298c01db12f22806f1c980238b72da06c3f59)。该提交在历史提交 `07bf738611fb55d5c2949e0f46b24726de555a17` 之后，仅改动 README。当前代码基线为 v0.3.0。

| 验证项 | 截至本次交接的证据 |
| --- | --- |
| Python 模拟及 MCP | 在最新基线上用 macOS、Python 3.10.10 执行 `python3 -m unittest discover -s tests -v`，18 项通过。10 项覆盖模拟状态和并发防重，8 项覆盖真实 stdio 子进程的工具发现、调用和本地审批通道。 |
| 本机 Codex | [仓库测试报告](docs/test-report.md) 记录隔离 Codex 会话发现 8 个工具、完成模拟审批、发送和回读。本次重跑范围为自动化测试。 |
| Apple Simulator | 仓库含独立的 SwiftUI 启动夹具 `simulator/Fixture.swift` 和 [启动验证步骤](docs/apple-simulator.md)。其启动脚本只验证 App 能构建、安装、启动及记录首屏事件。MCP 当前仍连接 Python 模拟后端。本机 `xcrun --find simctl` 未找到工具，官方 Simulator 和 XCUITest 流程尚无实测。 |
| Hark、iPhone 与微信 | Hark → SSH → Codex 的完整联调、iOS 27 真机、真实微信和 Hark 同机本地运行均待验证。验收时分别记录路径和证据。 |
| CI 与持久化 | 当前基线未包含 GitHub Actions 工作流。模拟状态、聊天记录和审批均保存在服务进程内，重启后清空。 |

[README 的工具清单](README.md#mcp-工具)列有 `phone_inspect`、`phone_open_app`、`phone_home`、`phone_back`、`wechat_list_contacts`、`wechat_open_chat`、`wechat_prepare_message` 和 `phone_execute_operation`。按界面元素观察、通用点击／滑动／输入和跨进程会话恢复是后续设备适配工作。Codex 可作为 Agent，DeepSeek API 是可选模型入口。ChatGPT Plus 订阅不提供通用 API 密钥，直接调用 API 时须单独配置相应平台的凭据和计费。[OpenAI 订阅说明](https://help.openai.com/en/articles/6950777)

### v0.2、仓库基线与 v0.4 的对比

- **v0.2 已定位。** 本次检查了 `iphone_phone_bridge_v0_2.zip` 的源码和文件清单。包内有 FastMCP stdio 封装、文本观察、模拟手势及 10 项模拟测试。MCP 内的 `phone_confirm_message` 接收确认参数，模拟实现即使确认也会阻止发送。测试结果沿用包内历史记录。
- **GitHub 基线是独立实现。** 采用 Python 标准库实现 stdio MCP，支持联系人精确匹配、同名消歧、按会话隔离消息记录，以及经本机 Unix 套接字审批后单次写入模拟记录。审批套接字信任同一操作系统账号，适用于受控演示。Hark 远程接入前须为用户审批建立与 Agent 权限隔离的通道。
- **v0.4 等待取回并复验。** 本次未取得 `PhoneBridge_v0.4_source.zip`，也未在当前 GitHub 基线发现该包所报告的 12 工具、52 测试或 XCUITest 实现。取得归档后先登记来源和校验值，再比对工具输入输出、测试断言、状态机、审批边界和设备适配接口。先在独立目录重跑包内测试，再逐项迁移已核验的增量并保留现有回归测试。

### P0 开工顺序与首次验收判据

1. 从最新 `origin/main` 建立工作分支。记录完整基线 SHA、Python 版本和测试结果。先让同一条 unittest 命令在干净检出和 CI 上通过，保存工具清单及使用说明。v0.4 归档未取得时，仍可推进 CI、接口文档和测试基线，并把归档对比单独列为待办。
2. 在 P1 用自有 SwiftUI 测试 App 建立可复位的联系人和聊天记录夹具。为联系人、输入框和发送键设置稳定的可访问性标识。用 XCUITest 验证打开、唯一定位、填稿、点击及读回，并保留运行时版本与测试结果。P2 再以相同的 UI 场景通过 MCP 完成端到端验证。
3. 首次发送验收以固定测试联系人 ID 和一条合成消息为输入。审批前读回草稿及收件人，确认聊天记录增量为 0。独立审批应绑定会话、操作 ID、联系人 ID、正文及有效期。获准后执行一次，回读同一联系人会话，确认匹配消息增量为 1。再次提交同一操作 ID，增量仍为 1。记录环境、执行结果和脱敏的核验依据。
4. 未匹配、多个匹配、拒绝、过期、状态变化或超额时停止发送并报告原因。为单任务设置时限、动作预算、重试次数和发送额度。读操作及确认未产生副作用的动作可在预算内重试。发送结果不明时先回读，仍无法判定就暂停并等待用户处理。
5. 为断线和重启保存最小任务状态、操作 ID、审批有效性及核验结论。恢复后重新观察设备，确认目标、草稿和消息记录。以持久化的操作 ID 去重，审批失效时重新请求用户确认。日志只保留必要的状态和脱敏证据，银行、券商和支付 App 默认拒绝。真机验证依照用户另行授权与 Apple 支持的测试能力开展。[Apple Xcode 设备测试文档](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices)
