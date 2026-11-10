# 历史交付与本次整合

v0.4 开发包与 GitHub 上的 v0.3 独立开发。原开发包完成了 Linux 上的 Python 测试并提出逐项合并的建议。此次从原对话下载 v0.4、从本机下载目录找回 v0.2，并以 GitHub `main` 的 `4ff8df5` 为整合起点。文件大小、哈希与逐代差异见 [源码清单](artifact-inventory.md)。

| 来源 | 仓库中的位置 |
| --- | --- |
| GitHub v0.3 | `run_phonebridge.py`、`phonebridge/{mcp,model,operator}.py`、原两组测试、SwiftUI 启动夹具和冒烟脚本继续提供原入口与工具签名。 |
| v0.4 Python | `python3 -m phonebridge.cli` 和安装后的 `phonebridge` 是新入口。新的核心、驱动和审批模块位于 `phonebridge/`，MCP 模块改名为 `mcp_v04.py`，同名测试改名为 `test_mcp_v04.py`。 |
| v0.4 Apple Simulator | SwiftUI 聊天夹具、XCUITest、XcodeGen 工程定义及两份脚本位于 `simulator/` 和 `scripts/`，与 v0.3 的首屏冒烟入口并存。 |
| v0.2 | 归档内九个原始文件统一保留在 `legacy/v0.2/`。v0.4 ZIP 中的 `legacy_v02/` 内容逐字节相同。 |
| 项目配置 | v0.4 的打包元数据和 Python CI 与历史基线共同验证。执行命令、环境与结果见 [测试报告](test-report.md)。 |

调用者按现有工具签名选择入口。迁至 v0.4 的发送流程需要输入焦点、正文与收件人 ID，并使用 v0.4 返回的 `approval_id`。两代会话分别维护内存状态，应在各自服务的套接字上审批。[README](../README.md)
