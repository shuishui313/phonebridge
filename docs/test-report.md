# PhoneBridge 0.3.0 验证报告

验证日期为 2026-11-10（香港时间）。环境为 macOS、Python 3.10.10 和 Codex CLI 0.167.0-alpha.3.2。

| 验证范围 | 结果 | 实际验证内容 |
| --- | --- | --- |
| Python 虚拟手机 | 通过 10 项 | 主屏幕、应用导航、页面状态校验、联系人精确匹配和重名消歧。验证审批、拒绝、状态变化后提案失效、重复执行、并发执行和聊天记录隔离。 |
| MCP 和本机审批通道 | 通过 8 项 | 实际子进程通过 stdio 初始化和发现工具，覆盖 4 个握手协议版本。验证参数、工具范围、套接字权限、状态变更和审批后的模拟发送。 |
| 本机 Codex 连接 | 通过 | 使用隔离的 Codex app-server 会话发现 8 个工具，从主屏幕进入模拟微信，通过独立审批命令批准提案，再经 MCP 写入并读回一条模拟消息。 |
| Apple Simulator | 未运行 | 本机选中的是 Command Line Tools，`xcrun --find simctl` 无法找到 `simctl`。已检查脚本语法、Swift 源码解析和缺少工具时的错误提示。 |
| 实体 iPhone | 未运行 | 当前项目仅包含 Python 模拟后端和独立的 Apple Simulator 测试应用。 |
| Hark | 未运行 | 已记录通过同一主机上的 Codex 会话接入的方式，尚无 Hark 客户端实测结果。 |

在仓库根目录重跑 18 项自动化测试：

```sh
python3 -m unittest discover -s tests -v
```

在安装完整 Xcode 和 iOS 运行时的 Mac 上，可按 [Apple Simulator 验证步骤](apple-simulator.md) 单独构建、安装和启动 SwiftUI 测试应用。该流程的结果仅覆盖测试应用启动和首屏生命周期。
