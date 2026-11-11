# 711ab2a 评审回复与修复记录

回复日期为 2026 年 11 月 11 日。对应 [评审记录](711ab2a.md)，范围为本轮修复提交。原评审的 5 条发现逐条对照源码核实后**全部成立，无误报，不需要反驳**，已全部处理。未等到评审方对 4 个决策点的回复，按保守方向自主决断并在下文列明理由，评审方可复核或推翻。

## 逐条处理

| 编号 | 确认的根因 | 修复 | 证据 |
| --- | --- | --- | --- |
| P1-1 通用交互缺少提交前拦截 | `CoreAPI._record_count` 依赖 `messages` 字段，而 Xcode 驱动只返回 `hierarchy_path` / `sha256` / `revision`，事后绕行检测在真实驱动下恒不执行；原逻辑也只做事后标记，不是事前拒绝 | `XcodeMCPDriver` 在每次交互前的新鲜层级里定位启用的提交控件（`chat.send` / `compose.post`），`tap` 与 `swipe` 的起终点命中即在**派发交互命令之前**返回 `submission_requires_approval`；无法定位启用控件时 `submit_guard_unavailable` fail closed。驱动新增 `record_count()` 从当次层级读取记录数，使 `CoreAPI` 的事后检测对真实驱动也生效，形成纵深防御。禁用变体 `chat.send.disabled` / `compose.post.disabled` 不拦，保持 0b 试跑可用 | 7 项驱动单元测试；`scripts/xcode-mcp-submit-guard-spike.py` 在**不带** `pb-disable-submit` 的常规启动下实测 `guard_passed: true`，`submit_tap_error: submission_requires_approval`、`retry.allowed: false`、记录增量为零 |
| P1-2 恢复误认历史同文记录 | `submission_evidence` 的 `recipient_id` 传参后未使用，只遍历当前界面的消息列表比对正文摘要；`MockIPhone.observe()` 返回的是当前选中联系人的记录，且没有增量或序号归属 | 台账在 `prepared` / `dispatching` 事件记录该**目标联系人**的派发前记录基线（`baseline_count`，脱敏计数）；恢复只接受「该收件人记录数超过基线、且新增记录摘要匹配」为完成证据。层级驱动没有按收件人归属的能力，统一返回 `unclear` 并暂停 | 新增反例：其他联系人的同文旧记录、目标联系人的历史同文记录均不再被误判；带历史记录时回执丢失仍能被正确确认（`completed`，记录数 2）；不可归属记录保持 `unclear` |
| P1-3 结果不明后仍可继续写入 | `TaskGovernor` 无暂停状态，`_verify()` 只把 `pause` 放进响应；`note_failure` 对普通写失败仍给出 `allowed: true` | `CoreAPI` 增加持久暂停状态：回读不明或检测到绕行后，所有 `mutating` / `critical` 动作返回 `paused_pending_readback`，只读放行；`reset_task()` 不解除暂停；可信侧 `resume_after_readback()` 必须绑定一次成功的当次回读。收紧 `_fresh_evidence`：需 `ok`、`revision` 与具体内容证据，仅含证据键名不再判 `clear`。安全类拒绝加入 `NO_RETRY_ERRORS` 不返回重试建议 | 6 项新增单元测试，覆盖暂停、只读放行、重置不解除、恢复需新鲜回读、弱证据不 clear、安全拒绝不重试 |
| P1-4 独立账号默认无法连接 | `required_uid != os.getuid()` 与「0700 目录 + 0600 套接字均属服务账号」互斥，另一个 UID 在对端凭据校验前就进不了目录，属配置上的逻辑死结；原测试替换了 `peer_uid_fn` 掩盖了这一点 | 保留 token 作为**本地可用**的隔离手段，把 `required_uid` 明确降级为「外部托管模式」：新增 `isolation_mode`（`token` / `external_uid_unverified` / `demo`），`approval_isolated` 仅在 token 模式为真，`phone_status` 披露模式；`--require-approval-isolation` 现在只认实际存在的令牌，单独的 `--approval-uid` 会被拒绝启动；CLI 与类注释写明需外部托管端点加 OS ACL | 3 项新增测试：UID-only 模式不被报告为已隔离、门禁拒绝无令牌启动、令牌模式仍正常；反例用合成数据与替身，**未做真实跨账号验收**，见剩余边界 |
| P2-5 审批写盘失败仍可派发 | `core.approve` 先 `p.approved = True` 再落盘；`SubmissionGate.execute` 不检查台账里是否存在 `approved` 事件，`prepared` 也不在 `NON_REPLAYABLE` 内 | `approve` / `deny` 改为**先落盘成功再改内存**，写盘失败返回 `approval_not_durable` / `decision_not_durable`；提交门在派发前 replay 台账，缺 `approved` 事件一律拒绝。与 P1-2 合并为同一个原则：**台账是唯一事实来源，内存状态只是派生** | 新增反例：注入写盘失败后审批被拒、即使伪造内存批准也无法派发、记录数为零；另有「缺落盘批准事件」直接拒绝测试 |

**评级建议**：P2-5 实际破坏的是审批可审计性（台账显示未批准但消息已发出），且是 P1-2 的同一根因，本轮按 P1 处理。

## 四个决策点的自主决断

1. **步骤 2 完成标注**：撤回「已完成」，改为「Mock 与台账层已完成 / 真实驱动提交前拦截已实现但未纳入 G2 前的完整可写验收」。实现计划把该验收明确列为 G2 阻塞项，Hark 完整联调继续排在 G2 之后，Mock 连通性试跑可并行。
2. **恢复证据归属**：Mock 用「收件人 + 记录增量 + 摘要」强证据；真实驱动（层级文件）无法把记录归属到操作 ID，一律暂停交可信侧人工核验，不做自动确认，也不为「自动确认」伪造证据强度。
3. **暂停范围与解除**：全局暂停写动作、只读放行；解除入口 `resume_after_readback()` 只在可信侧可用，且必须绑定一次成功的当次回读核验，避免死锁或变成可绕过的开关。
4. **`--approval-uid` 架构**：选「token 作默认隔离 + UID 降级为外部托管模式」，不实现独立 UID 托管端点的协议反转。理由是后者工作量大且仍需 ACL，而当前阶段本就不进入真实发送；文档如实标注 demo 模式的局限。

## 验收记录补充

- G1 拆为两项：**自动化脚本等价工具序列通过**（`g1_passed: true`，本轮重跑仍通过）；**隔离 Codex 会话的自然语言拟稿复核待办**（仍未做）。
- 评审反例全部使用本地合成数据与替身。当时真实跨账号审批验收、真机、真实第三方 App、Hark 完整联调仍未验证。
- **同日后续更新（P1-4 闭环）**：`scripts/cross-uid-approval-spike.py --authenticate` 已在 macOS 本机以真实的第二 UID 进程完成现场验收，`outcome: "passed"`、`cross_uid_process_verified: true`、`approver_process_real: true`，15 项判据全过，P1-4 所缺的真实跨账号验收补齐；证据与边界见 [测试报告](../test-report.md)。这不改变本回复上表的降级结论（token 仍是默认隔离、单独 `--approval-uid` 仍不被记为已隔离），也不等于真机、独立审批设备、真实第三方 App 或 Hark 完整联调已验证。
- 本轮新增 `scripts/xcode-mcp-submit-guard-spike.py`，运行后会把夹具恢复为 `pb-disable-submit`，不影响 G1 基线。
- **同日后续更新**：决策 2 保持不变（真实驱动恢复仍一律暂停人工核验），但决策 1 中的「G2 阻塞项」随后已于 2026-11-11 当天解除——真实驱动的完整可写验收在 Apple Simulator 上以 36 项判据通过，见 [测试报告](../test-report.md)。方式为给真实驱动补齐可归属的合成上下文与受控虚拟提交（仅在自有夹具内、需操作者显式开关 + 完整审批隔离、产生的是合成记录而非真实消息），并非放宽决策 2 的归属标准。Hark 完整联调仍是 G2 剩余阻塞项。
