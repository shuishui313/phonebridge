# PhoneBridge 源码来源与差异

核对日期为 2026 年 11 月 10 日。这里登记实际取得的文件、历史记录中的缺件，以及各版本的行为差异。当前运行入口和本次实测结果见 [README](../README.md) 和 [测试报告](test-report.md)。

## 已取得的源码

| 来源 | 核对结果 |
| --- | --- |
| GitHub `main` | 本次起点为 `4ff8df56b87a2192e1707111b6eff787851f9936`。该提交增加 `HANDOFF.md`，上一个提交 `087298c01db12f22806f1c980238b72da06c3f59` 改动 README，源代码和测试继承自 `07bf738611fb55d5c2949e0f46b24726de555a17`。 |
| 旧 Codex 工作区 | 两个干净检出分别指向 `07bf738` 和 `4ff8df5`。另找到早期的 `staging-model`、`staging-tests` 和 `staging-simulator` 目录，其中六份源文件、测试和说明逐字节匹配 `07bf738`，因此归入同一来源。 |
| v0.2 下载 | 找到 `iphone_phone_bridge_v0_2 (1).zip`、`iphone_phone_bridge_v0_2 (2).zip` 和 `iphone_phone_bridge_v0_2.zip`。另从原对话下载了上传附件 `iphone_phone_bridge_v0_2(1).zip`。四个文件逐字节一致，每个 8,374 字节，SHA-256 为 `a50cb816af61a3a15746290ba44565536d8c3fbcff09aca574ada1fdb50e1aca`。 |
| v0.4 原始交付 | 本机起初没有 `/mnt/data` 或 v0.4 文件。从原 ChatGPT 对话「AI协作复刻游戏」下载了 `PhoneBridge_v0.4_source.zip`。该文件 40,229 字节，SHA-256 为 `65c9c4cb62d314df2b9ad5527e8178f6bda2bfb540c26bca3aadde9b8367373e`。 |
| 散件下载 | 独立 `TEST_REPORT.md` 与 v0.2 ZIP 内同名文件逐字节一致。原对话的 `README(1).md` 与本机 2,285 字节的 `README.md` 逐字节相同，保存在 `legacy/README-v0.2-original.md`，SHA-256 为 `f1ac49f4c2981cbe89d4229e66d9f95859282eac6907c77d7a16ede1841144e8`。ZIP 内 README 保留其全部内容，另有一个 v0.2 测试小节。散件的内容已随 ZIP 版保留，从原对话取回的 `README (1).md`（6,435 字节）和 `test-report.md`（1,912 字节）与 v0.4 ZIP 内对应文件逐字节一致。 |

GitHub 历史基线包含 `phonebridge/{mcp,model,operator}.py`、`run_phonebridge.py`、`tests/{test_mcp,test_model}.py`、`simulator/Fixture.swift`、`scripts/simulator-smoke.sh`、`docs/{apple-simulator,test-report}.md`、`README.md`、`HANDOFF.md`、`.gitignore` 和包初始化文件。提交历史可从 [GitHub 基线](https://github.com/shuishui313/phonebridge/tree/4ff8df56b87a2192e1707111b6eff787851f9936) 核对。

仓库内的 [v0.2 原始 ZIP](../artifacts/iphone_phone_bridge_v0_2.zip) 和 [v0.4 原始 ZIP](../artifacts/PhoneBridge_v0.4_source.zip) 保留上述原始字节。

### v0.2 归档清单

ZIP 包含一个 `iphone_phone_bridge/` 目录与九个普通文件，解压后总计 15,554 字节。归档成员均位于该目录下，压缩数据 CRC 校验通过。下列大小和哈希按解压后的原始字节计算。

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `bridge.py` | 3,856 | `50d1ff2a75f628e9c8b48e1000c5039416d8d41503495a8faba9b4331ad49afe` |
| `server.py` | 1,671 | `57f5db04b567ef9dc51cfdca43613f3ba1df1a67854b9242b5bcaf2dfbc39b53` |
| `simulator.py` | 1,700 | `80fb22175ad7e2c715ccde6d5ee6168e17edd472d8c81172a66e8aab7cdb2ba6` |
| `test_bridge.py` | 1,178 | `0ae6a34499a48357bc536334adf8a817bc8194179daf2366af43732ed734cf2d` |
| `test_workflow.py` | 1,372 | `0b5d6b32ed9a0f458d0769e053e3684e353f12d65afe56259b45c38cee1b37cb` |
| `demo.py` | 474 | `7ce623331311caaae1eda34d407ea946f9ff25cd8cf9732d95b8a3b58d2144d7` |
| `demo_result.json` | 901 | `2b24a0ef6720dba2bd6940a48ab85fca8afcf738885ab62e5f5279a0b946e9d0` |
| `README.md` | 2,714 | `e0aa16fceb4a23ae4c55939d0e506313fa5cdc9360fe277b2811cca95957ad7a` |
| `TEST_REPORT.md` | 1,688 | `6103c0c67fa84e4d949da177784e8127c8ccb53b09477e6225282a9dc4dbff4e` |

### v0.4 归档清单

该 ZIP 有 37 个普通文件，解压后总计 88,671 字节。校验了成员路径、文件类型和 CRC，再写入隔离目录。`legacy_v02/` 下的九个文件逐字节匹配独立 v0.2 归档，统一保存在 `legacy/v0.2/`。其余 28 个原始成员如下，哈希用于校验归档内容。

| 原始路径 | 字节 | SHA-256 |
| --- | ---: | --- |
| `.github/workflows/python-tests.yml` | 359 | `effb144eabba4b0fa660363cd16c8567c6a24fa37151f860ab302713c5a6febe` |
| `.gitignore` | 110 | `532a3086e279f3ebea3f6c9472f3827c07f5461a2ab6bb32a671a10bda1fb392` |
| `AGENTS.md` | 1,085 | `42a41f525fc1c19e09ec876d1f667f51ed04c1a2ca8cb6e76846e4c81f506e44` |
| `README.md` | 6,435 | `e5c6598d3e6a605bb64794d0b78ea391232a6b179cd6c017e6435730cd2f19cc` |
| `docs/acceptance.md` | 1,674 | `78f26930f03b37d6d77945a43716999aee838b1240df7dba8326dd1092493323` |
| `docs/apple-simulator.md` | 1,746 | `6d363d5b20165ad31e89cbad107b9dbf1f15c22eb3e7f23be1f9f24dceefb33d` |
| `docs/hark-codex.md` | 1,919 | `259c6709e8dde31353f202c08e3febdffccf2534bb951f7585cc4be528f954b6` |
| `docs/merge-existing-github.md` | 1,341 | `e0e006b296810b3f3f6b6b8ade58a3aa5b29a780db0703a20d66c8299b3da322` |
| `docs/security.md` | 1,986 | `f75abcce2c7b44bf84d10279bad9c016c08c444644c6f9b74620b04918fbe4eb` |
| `docs/test-report.md` | 1,912 | `5758b515c2d8c85b84184ed12fe7b53eed3aaf9b5b68bb50830e0d8e4a2facf0` |
| `phonebridge/__init__.py` | 252 | `f69812b6cb6ca68e560278d0d291aa90dcf3605103a9e26c49b49ec5345d6564` |
| `phonebridge/approvals.py` | 3,578 | `d3c12446831f339cd4dbc8927827e1a2cc70bcaff6c0ba1743a002ba0aab8b80` |
| `phonebridge/cli.py` | 2,180 | `af26cc268327169144c1b02c3658e5d3586a24dde39ff73f20bb3c13f4f16de0` |
| `phonebridge/core.py` | 7,430 | `6741e631a84527c8746bb550a7db8b3bff7d6cbc3f5966575990a9163d6d8f70` |
| `phonebridge/drivers.py` | 10,021 | `d135048eb1c253cd02c8c7eb9ce88a21484333e1d14807b5a6e092ea8f458137` |
| `phonebridge/mcp.py` | 6,711 | `1cc6eebe431d1cf4ded8d09384391579231f8f4e51ab3a98f08337d3c9650c3c` |
| `pyproject.toml` | 470 | `32331ab49b93c522f7dca6a7f2bd2c707d1af8a436d25050129cfcf9fea2c36a` |
| `run_phonebridge.py` | 112 | `97a951d9d3a8c2ee6d6ab2d64cf9d5d6e24e19393381978833cf6878fa223db6` |
| `scripts/apple-simulator-test.sh` | 1,133 | `2c77ad32ef0bc726638e646ae7f287b72a2861b3e647ea59fc56bbf160d35b09` |
| `scripts/simctl-preflight.sh` | 217 | `7dd4366839fee6c8876083c8e162a3bb246ce6fb96d73c94f2959b6008beaa51` |
| `simulator/PhoneBridgeFixture/PhoneBridgeFixtureApp.swift` | 2,191 | `cfc9141b179e06cc49dd0d0086789ce7555f828c19528f39d8810285779fa9dd` |
| `simulator/PhoneBridgeFixtureUITests/PhoneBridgeFixtureUITests.swift` | 1,209 | `bb71141e881e7dd83427aa49b61857ef2f866072a0474308745e8ee3eef9b156` |
| `simulator/project.yml` | 1,023 | `2c0a27075ce05b5d32be0c5ee89666893dfeb4e2f4a425de1506f7e13f694a31` |
| `tests/test_approvals.py` | 2,521 | `ddb5005b6650ead45d9d7b03f267bbf2b5a401d318ab844a89f66498217049f6` |
| `tests/test_core.py` | 6,261 | `b3c06211723dfbaf1bd17be24b527f9534de785ecf8bcc4d433d9f628c356e2a` |
| `tests/test_mcp.py` | 3,311 | `168814e32bd063fdf8eb8fdaa8b117e93338932f08760df6efd82aa1b0b91d26` |
| `tests/test_simctl.py` | 2,850 | `2a0e9190cf1837f092f3cc8862672d62cbb93ba063644406ee3a2e707b7cd50a` |
| `tests/test_subprocess.py` | 3,080 | `a5efeadb105b438a52206fc4511bcc713774c92ce2e5ea8c9cc85eeb00bbeb8e` |

## 三个来源的行为对比

| 项目 | v0.2 原型 | GitHub 历史基线 `4ff8df5` | v0.4 原包 |
| --- | --- | --- | --- |
| MCP | FastMCP stdio，8 个工具。`phone_open_app` 接收 `app_name`。 | 标准库 stdio，8 个工具。`phone_open_app(app_id)`、`wechat_prepare_message(text)`、`phone_execute_operation(operation_id)`。 | 标准库 stdio，12 个工具。增加 `phone_status`、`phone_tap`、`phone_swipe` 和 `phone_type_text`。对应签名为 `phone_open_app(app)`、`wechat_prepare_message(recipient_id, message)` 和 `phone_execute_operation(approval_id)`（后续通用化版本中 `wechat_prepare_message` 更名为 `message_prepare`，历史名称保留为可调用别名）。 |
| 模拟状态 | 可注入 Mock Driver、校验坐标、限制文本、检查应用白名单，提供从观察到草稿的离线演示。 | 联系人精确匹配、重名消歧、聊天记录隔离、屏幕修订和并发防重。 | 新增输入焦点、文本草稿、显式校验草稿及收件人、审批有效期和元数据日志。 |
| 审批与发送 | MCP 可提交确认标志，执行始终返回 `blocked`。 | `approvals` 通过本机 Unix 套接字批准或拒绝，模拟写入一次。 | `approvals` 通过本机 Unix 套接字批准或拒绝，先匹配草稿与会话，再单次写入。两版套接字均以操作系统账号为信任边界。 |
| Python 测试 | 2 个模块，10 个 Mock 测试。 | 2 个模块，18 个模型或 stdio 子进程测试。 | 5 个模块，覆盖 52 个审批、Mock、MCP、假 `simctl` 和跨进程工作流用例。 |
| Apple 测试资产 | `simulator.py` 执行 Python 合成流程。 | `simulator/Fixture.swift` 与脚本检查 SwiftUI 首屏启动。 | 自有 SwiftUI 聊天夹具、XcodeGen 工程配置、两项 XCUITest 和 `simctl` 适配器。适配器支持状态、截图、启动夹具；通用点击及输入由夹具的 XCUITest 单独测试。 |

v0.2 的工具、Mock Driver、演示与测试以 [独立历史原型](../legacy/v0.2/) 保留。v0.3 保留 `run_phonebridge.py` 和原 MCP 实现。v0.4 的 `phonebridge/mcp.py` 与 `tests/test_mcp.py` 分别迁入 `phonebridge/mcp_v04.py` 与 `tests/test_mcp_v04.py`，可用 `python3 -m phonebridge.cli` 或安装后的 `phonebridge` 启动。路径与整合细节见 [合并记录](merge-existing-github.md)。
