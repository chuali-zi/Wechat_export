# wxtext：定向导出自己的微信私聊文字

本地命令行工具。在 WSL 开发，采集和实际导出在 **Windows 本地、当前登录用户**下运行。输入某位联系人的稳定 ID、微信号、唯一昵称或备注，输出该私聊本机已有的普通文字。没有 UI、网络服务、遥测、GitHub 上传或其他账号采集功能。

**当前验证状态：**跨平台核心使用独立 SQLCipher 4 引擎生成的数据库测试；Windows API、DPAPI 和你所安装微信的内存布局需要 Windows 实机验收。代码中的内存特征只是候选定位，只有数据库页 HMAC 校验通过才接受密钥，不能据此宣称兼容全部“最新微信”。

## Windows 快速使用

安装 Python 3.12 或更新的 **64 位**版本，在项目目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\wxtext.cmd doctor
```

安装依赖时 pip 会联网；安装后工具运行不联网。无需激活虚拟环境或更改 PowerShell 执行策略。也可以用 `.\.venv\Scripts\python.exe -m wxtext` 代替 `.\wxtext.cmd`。

1. 自己打开微信，登录要提取的账号。
2. 在微信设置里确认数据保存位置，找到该账号的 `db_storage`。首次运行：

```powershell
.\wxtext.cmd prepare --data-dir "D:\微信文件\xwechat_files\你的账号目录\db_storage"
```

数据目录也会从微信配置和 Documents 的常见位置发现；出现多个账号时必须用 `--data-dir` 明确选择。工具不做全盘搜索。路径会记住，以后直接 `prepare` 即可。

3. 看到“已验证并加密缓存”后，**从微信托盘菜单正常退出**。只关闭聊天窗口不算退出。
4. 搜索联系人，找到稳定的 `user_id`：

```powershell
.\wxtext.cmd contacts --query "张三"
.\wxtext.cmd export --target "wxid_目标" --out "D:\我的聊天导出"
```

唯一昵称、备注或微信号也可直接导出：

```powershell
.\wxtext.cmd export --target "老朋友" --out "D:\我的聊天导出"
```

昵称重名时返回候选人，不自动挑选。重新导出时微信仍须退出；有效缓存可以复用，不必每次扫描内存。

若不能确定自己的 ID，运行 `contacts` 查看联系人数据，确认自己的稳定 `user_id` 后明确指定：

```powershell
.\wxtext.cmd export --target "wxid_目标" --self-id "wxid_自己" --out "D:\我的聊天导出"
```

`prepare --self-id "wxid_自己"` 可以保存此设置。这里不是自己的昵称；若与消息发送者映射不符，将停止导出。

## 输出与数据处理

每次成功生成一个 `private-<内容指纹>` 目录，包含：

| 文件 | 内容 |
| --- | --- |
| `messages.txt` | 可读文字，显示双方 ID、发送者和 Windows 本地时区时间 |
| `messages.jsonl` | 一行一条消息，UTF-8；包含 UTC 时间、原始 Unix 秒、消息 ID 和来源 |
| `manifest.json` | 目标、数量、覆盖时间、分片摘要、重复数量、跳过的消息类型和输出文件哈希 |

相同快照、目标和显示时区重复导出会复用相同结果；不同快照生成新的目录。已有文件被手工修改时，不覆盖。

仅输出普通文字类型（包含 Unicode 表情、换行及 Zstandard 压缩长文字）。图片、语音、文件、引用/链接卡片及系统通知不展开，跳过数量写入说明。未知压缩格式或无法解码的文字会报错，不截断或猜测正文。每条解压文字限制为 16 MiB。

目标私聊可能跨多个 `message_N.db`。工具必须验证所有消息分库的密钥和结构，逐库解密、查询目标后清理明文；其他人的消息不进入最终归档。联系人存在但所有分库均无该会话时返回 `NO_LOCAL_CONVERSATION`；会话仅有非文字消息时成功输出零条文字并提供类型统计。

源数据库和 WAL 只按二进制读取，不执行源文件写入、checkpoint 或迁移。微信正常退出后，工具复制数据库及存在的 `-wal`，复核文件清单、大小、mtime 和 SHA-256；来源清单在对应数据库的 `wal` 字段中记录 WAL 文件名、大小和哈希。加密 WAL 仅在私有副本上验证并重放已提交事务，随后解密；源文件保持不变。非空 `-journal` 仍阻止导出，不要手动删除任何日志。首次快照会顺序读取并复核哈希，大账号耗时随数据量增长。

WAL 重放检查头部、滚动校验和及加密页 HMAC；只应用最后一次有效提交之前的事务。同一代未提交帧也必须通过验证，但不重放；完整帧头的盐与 WAL 头部盐不匹配时，其后的旧代或预分配尾部不再解析、不重放，允许非整帧尾部。活动代的残缺头部/帧和损坏会拒绝导出，不会静默退回旧数据库。此能力限于已实现的加密格式和已退出客户端，不代表支持在线读取或所有微信版本，仍需 Windows 实机验收。

状态默认保存于 `%LOCALAPPDATA%\wxtext`：

- `settings.json`：选定的数据目录和可选的自己 ID。
- `keys/*.dpapi`：当前 Windows 用户 DPAPI 加密的已验证密钥。
- `work/`：临时快照和目标消息排序数据库，正常完成、异常和 Ctrl+C 时清理。

运行状态、导出目录和原微信账号目录必须分开。进程被强制结束或断电时可能留下 `work`，确认没有工具正在运行后可手动删除其残留子目录；不要删除微信的数据文件。缓存不能直接给 WSL 或另一 Windows 用户使用；Python 不能保证所有内存副本安全擦除。

## 机器调用与故障处理

所有子命令支持 `--json`：stdout 为单个 JSON 结果，进度写 stderr。返回码 `0` 成功、`2` 需要配合或不支持、`3` 系统/数据库错误、`130` 用户中断。

```powershell
.\wxtext.cmd doctor --json
.\wxtext.cmd prepare --scan-timeout 120 --json
.\wxtext.cmd export --target "wxid_目标" --out "D:\我的聊天导出" --json
```

主要扫描默认最多 60 秒，另有最多 10 秒的主密钥布局回退；不自动无限重试。

| 状态 | 需要怎样配合 |
| --- | --- |
| `WINDOWS_REQUIRED` | 改到 Windows PowerShell 运行；WSL 只负责开发/离线测试 |
| `NEED_LOGIN` | 自己打开并登录微信 |
| `NEED_EXIT` | 从托盘正常退出微信后重跑当前命令 |
| `NEED_CLEAN_EXIT` | 存在非空 rollback journal；正常打开再退出微信，仍有日志时停止并在本机适配，不能删日志 |
| `INVALID_WAL` / `PAGE_AUTH_FAILED` | WAL 格式、事务边界或认证失败；保留源文件和日志，停止并在本机核查，不强行忽略 WAL |
| `SOURCE_CHANGED` | 快照期间数据库或 WAL 发生变化；确认微信已退出后重跑 |
| `ACCOUNT_REQUIRED` | 用 `--data-dir` 明确选择自己的 `db_storage` |
| `ACCESS_DENIED` | 使用相同 Windows 用户；如果微信以管理员运行，改用管理员 PowerShell |
| `KEY_NOT_FOUND` | 确认账号目录，打开目标聊天/历史记录后再 prepare；仍失败则依据完整版本适配 |
| `UNSUPPORTED_VERSION` | 检查 doctor 的完整文件版本，不盲目重试旧版偏移 |
| `SELF_ID_REQUIRED` / `SENDER_UNRESOLVED` | 核实自己的稳定 ID 和当前分库发送者映射 |
| `AMBIGUOUS_TARGET` | 使用返回的具体 `user_id` |
| `UNSUPPORTED_SCHEMA` / `TEXT_DECODE_FAILED` | 根据报错的分库、表、列或 local_id 在本机适配 |
| `CACHE_UNAVAILABLE` / `CACHE_INVALID` | 使用原 Windows 用户，必要时 `prepare --refresh` 重建该账号缓存 |

`--state-dir` 可在每个子命令后指定自定义状态目录；后续调用必须使用同一目录。工具不要求关闭系统安全功能，也不会自动修改微信或强制结束进程。

## 开发和验证

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest -q
```

密码格式和端到端测试使用独立 native SQLCipher 4 库生成的合成数据库，不使用真实聊天。测试通过 `ctypes.util.find_library('sqlcipher')` 寻找密码库，或用 `WXTEXT_TEST_SQLCIPHER` 指定库文件。**这只是测试依赖，Windows 使用工具时不需要安装 SQLCipher。** 缺少该库会明确跳过对应测试，不能把跳过算成解密验证通过。

本环境已检测到 SQLCipher `4.14.0 community` / SQLite `3.51.3`。Windows DPAPI 测试在 WSL 会跳过。

详见 [架构与适配说明](docs/ARCHITECTURE.md) 和 [Windows 实机验收步骤](docs/WINDOWS_ACCEPTANCE.md)。只有本机已存的记录能被导出；手机独有的历史需先通过微信自身功能迁移。
