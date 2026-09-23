### 还在自己研究readme吗！！什么年代了，把仓库clone下去，用你的agent打开，直接描述你的提取需求，agent自己会去读readme并帮助你提取!!

# wxtext：导出自己的微信私聊文字

本地命令行工具：输入某位联系人的稳定 ID、微信号、唯一昵称或备注，导出与该联系人的本机私聊文字记录。不联网、无 UI、无遥测，只处理本机已有的数据。

## 功能特性

- 按联系人导出私聊**普通文字**消息（含 Unicode 表情、换行和 Zstandard 压缩的长文字）。
- 图片、语音、文件、引用/链接卡片及系统通知不展开，跳过数量记录在 `manifest.json`。
- 仅限本机已有记录；手机独有的历史需先通过微信自身的迁移功能同步到本机。
- 输出 `messages.txt`（可读）、`messages.jsonl`（结构化）和 `manifest.json`（清单与校验哈希）。

## 环境要求

- Windows，当前登录用户运行；采集和导出不在 WSL 内运行。
- Python 3.12 或更新的 64 位版本。
- 微信 4.x，且为本机当前登录的账号。

## 安装

在项目目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\wxtext.cmd doctor
```

安装依赖时 pip 需要联网；安装后工具运行不联网。无需激活虚拟环境或更改 PowerShell 执行策略，也可以用 `.\.venv\Scripts\python.exe -m wxtext` 代替 `.\wxtext.cmd`。

## 快速上手

四步完成一次导出。

**第 1 步：登录微信**，在微信设置里确认数据保存位置，找到该账号的 `db_storage` 目录。

**第 2 步：缓存密钥**

```powershell
.\wxtext.cmd prepare --data-dir "D:\微信文件\xwechat_files\你的账号目录\db_storage"
```

看到“已验证并加密缓存”即成功。数据目录也能从微信配置和 Documents 的常见位置自动发现；出现多个账号时必须用 `--data-dir` 明确选择。路径会记住，以后直接运行 `prepare` 即可。

**第 3 步：从微信托盘菜单正常退出微信**。只关闭聊天窗口不算退出。

**第 4 步：查找联系人并导出**

```powershell
.\wxtext.cmd contacts --query "张三"
.\wxtext.cmd export --target "wxid_目标" --out "D:\我的聊天导出"
```

`export --target` 也接受唯一昵称、备注或微信号：

```powershell
.\wxtext.cmd export --target "老朋友" --out "D:\我的聊天导出"
```

昵称重名时返回候选列表，不会自动挑选，需用具体 `user_id` 重试。若不能确定自己的 ID，运行 `contacts` 确认后用 `--self-id "wxid_自己"` 明确指定；`prepare --self-id` 可保存此设置。

## 命令参考

| 命令 | 作用 |
| --- | --- |
| `doctor` | 检查环境、数据目录、数据库清单和密钥缓存状态 |
| `prepare` | 扫描微信进程内存，验证并加密缓存密钥；`--data-dir` 选择账号，`--self-id` 保存自己的 ID，`--scan-timeout` 调整扫描时限（默认 60 秒） |
| `contacts --query "关键词"` | 按备注、昵称、微信号搜索联系人，返回稳定 `user_id` |
| `export --target <ID或名称> --out <目录>` | 导出目标私聊文字；`--self-id` 指定自己的 ID |

所有子命令支持 `--json`：stdout 输出单个 JSON 结果，进度写 stderr。返回码 `0` 成功、`2` 需要配合或不支持、`3` 系统/数据库错误、`130` 用户中断。

## 输出说明

每次成功导出生成一个 `private-<内容指纹>` 目录：

| 文件 | 内容 |
| --- | --- |
| `messages.txt` | 可读文字，显示双方 ID、发送者和 Windows 本地时区时间 |
| `messages.jsonl` | 一行一条消息，UTF-8；包含 UTC 时间、原始 Unix 秒、消息 ID 和来源 |
| `manifest.json` | 目标、数量、覆盖时间、分片摘要、重复数量、跳过的消息类型和输出文件哈希 |

相同快照、目标和显示时区重复导出会复用相同结果；微信有新消息后重新导出生成新目录，旧导出不受影响。已有文件被手工修改时不覆盖。

## 更新与重新导出

微信产生新消息后，重复以下流程即可：

```powershell
# 1. 登录微信后重新缓存（缓存仍有效时可跳过）
.\wxtext.cmd prepare
# 2. 从托盘正常退出微信
.\wxtext.cmd export --target "wxid_目标" --out "D:\我的聊天导出"
```

重新导出时微信仍须处于退出状态；有效缓存可以复用，不必每次扫描内存。

## 状态目录

状态默认保存于 `%LOCALAPPDATA%\wxtext`：

- `settings.json`：选定的数据目录和可选的自己 ID。
- `keys/*.dpapi`：当前 Windows 用户 DPAPI 加密的已验证密钥。
- `work/`：临时快照和排序数据库，正常完成、异常和 Ctrl+C 时自动清理。

缓存绑定 Windows 用户，不能直接给 WSL 或其他 Windows 用户使用。进程被强制结束可能留下 `work` 残留，确认没有工具正在运行后可手动删除其子目录；不要删除微信自己的数据文件。

## 故障排查

| 状态 | 处理 |
| --- | --- |
| `WINDOWS_REQUIRED` | 改到 Windows PowerShell 运行 |
| `NEED_LOGIN` | 自己打开并登录微信 |
| `NEED_EXIT` | 从托盘正常退出微信后重跑当前命令 |
| `NEED_CLEAN_EXIT` | 存在非空 rollback journal；正常打开再退出微信，仍有日志则停止排查，不要删日志 |
| `INVALID_WAL` / `PAGE_AUTH_FAILED` | 保留源文件和日志停止核查，不要强行忽略 WAL |
| `SOURCE_CHANGED` | 快照期间数据库发生变化；确认微信已退出后重跑 |
| `ACCOUNT_REQUIRED` | 用 `--data-dir` 明确选择账号 |
| `ACCESS_DENIED` | 使用相同 Windows 用户；微信以管理员运行时改用管理员 PowerShell |
| `KEY_NOT_FOUND` | 确认账号目录，打开目标聊天/历史记录后再 prepare；仍失败按版本信息适配 |
| `UNSUPPORTED_VERSION` | 检查 doctor 输出的完整文件版本，不盲目重试 |
| `SELF_ID_REQUIRED` / `SENDER_UNRESOLVED` | 核实自己的稳定 ID 和发送者映射 |
| `AMBIGUOUS_TARGET` | 使用返回的具体 `user_id` |
| `UNSUPPORTED_SCHEMA` / `TEXT_DECODE_FAILED` | 按报错的分库、表、列或 local_id 适配 |
| `CACHE_UNAVAILABLE` / `CACHE_INVALID` | 使用原 Windows 用户，必要时 `prepare --refresh` 重建缓存 |

`--state-dir` 可在每个子命令后指定自定义状态目录；后续调用必须使用同一目录。工具不要求关闭系统安全功能，也不会修改微信文件或强制结束进程。

## 限制

- 只导出普通文字；无法解压或解码的文字会报错，不截断或猜测正文。
- 源数据库和 WAL 只按二进制读取，不做写入、checkpoint 或迁移。
- 对微信版本的兼容依赖本机的内存布局与数据库格式，版本更新后如失败，按 `doctor --json` 的版本信息适配，详见 `docs/ARCHITECTURE.md`。

## 更多文档

- [架构与适配说明](docs/ARCHITECTURE.md)
- [Windows 实机验收步骤](docs/WINDOWS_ACCEPTANCE.md)

开发和测试说明见 `pyproject.toml` 与 `tests/`；测试使用独立 SQLCipher 库生成的合成数据库，不涉及真实聊天数据。
