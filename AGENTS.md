# PhoneBridge 开发约定

- 在 `origin/main` 的真实提交上开展工作。提交前检查 `git diff --check` 和实际变更清单，保留可运行的旧入口及测试。
- 从仓库根目录执行 `python3 -m unittest discover -s tests -v`。历史 v0.2 使用 `python3 -m unittest discover -s legacy/v0.2 -v` 单独验证。修改 MCP 工具或审批时，验证真实 stdio 子进程与本地审批通道。
- 历史归档放在独立目录并登记原始校验值、迁移位置和修改。`legacy/v0.2/` 的九个原始文件保留原样，独立于当前服务。查阅 [资产清单](docs/artifact-inventory.md) 和 [测试报告](docs/test-report.md)。
- 保持模拟发送所需的人工审批与 MCP 工具分离。测试只使用合成联系人和消息。提交中不得包含令牌、设备标识、真实消息、截图或本地操作日志。银行、券商和支付应用默认禁用。
- 分别记录软件 Mock、Apple Simulator、Codex、Hark 和实体 iPhone 的运行证据。真机和真实第三方 App 仅在用户另行授权后验证。
