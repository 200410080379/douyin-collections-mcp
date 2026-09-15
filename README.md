# 抖音收藏整理 MCP

在本地运行的 MCP 服务，用于读取自己的抖音收藏、点赞和收藏夹，并把分类方案保存为可分批执行的计划。分类结果的目标是抖音账号里的真实收藏夹。

AI 分类由连接此 MCP 的客户端模型完成，例如根据视频标题和作者提出「做饭」「旅行」「学习」等分类；本项目无需额外配置 AI API Key。目前提供视频元数据，不包含自动观看视频或转写音频的能力。

## 当前验证进度

截至 2026-09-15：

| 功能 | 验证情况 |
| --- | --- |
| 当前登录账号、收藏视频、点赞视频、收藏夹列表、收藏夹内容、单条视频详情读取 | 已在真实登录会话中验证 |
| MCP 分类方案生成、识别视频已在目标夹、回读与完成状态 | 已通过真实 stdio MCP 调用验证，无重复修改 |
| 新建收藏夹 | 已通过真实 MCP 创建私密中文名称收藏夹、回读隐私状态，并清理测试夹 |
| 把已有收藏加入目标收藏夹及写后回读 | 已通过真实 MCP 调用验证，包含完成计划重跑不重复操作 |
| 把仅点赞的视频先收藏再入夹 | 已通过明确授权的真实 MCP 测试；测试后原收藏/点赞状态已恢复 |
| 本地缓存、账号隔离、计划持久化与 MCP 接口 | 88 项自动化测试通过；Ruff 检查通过 |

上述真实写入验证独立于模拟测试，范围为少量测试视频。验证详情见 [research/verification.md](research/verification.md)。MCP 服务名称为 `douyin-collections`，按下面步骤安装并连接客户端。

## 如何连接抖音

工具通过 Playwright 启动**独立的 Chrome 浏览器会话**，由你在窗口中扫码登录。读取使用页面环境里的同源 `fetch`；收藏和入夹使用网页自己的 Axios 请求客户端，复用当前运行时请求头。新建收藏夹使用网页原生表单，并明确关闭公开开关。

浏览器操作限定在独立会话内；不会控制操作系统鼠标、键盘，也不需要接管日常使用的浏览器配置。密码、验证码和 Cookie 不作为 MCP 参数传入或返回。

接口来自抖音网页端，属于非官方集成。网页接口或响应字段变化时需要更新适配器；遇到无法确认的结果，工具会停止并保留执行状态。

## 安装

建议在 macOS 上使用 Python 3.13、`uv` 和已安装的 Google Chrome。项目声明支持 Python 3.11 及以上版本。

在项目目录运行：

```bash
uv sync --python 3.13
uv run douyin-collections-mcp doctor
```

`doctor` 只检查本地配置，并显示数据目录和浏览器配置，不代表账号已经登录。

### 使用 Chromium

默认使用电脑上已安装的 Chrome。如果希望使用 Playwright 管理的 Chromium：

```bash
uv run playwright install chromium
DOUYIN_MCP_BROWSER=chromium uv run douyin-collections-mcp login
```

同时将 MCP 配置中的 `DOUYIN_MCP_BROWSER` 设为 `chromium`，使登录和 MCP 使用相同的浏览器选择与数据目录。

## 登录

### 从命令行登录

```bash
uv run douyin-collections-mcp login
```

在弹出的独立浏览器窗口中自行扫码。命令默认等待 300 秒，成功读取到当前账号后退出并关闭窗口；登录会话保留在本地，下次运行 MCP 会复用。

可调整等待时间，范围为 1–1800 秒：

```bash
uv run douyin-collections-mcp login --timeout 600
```

### 通过 MCP 登录

连接客户端后，也可以依次调用：

1. `douyin_login_start`：打开独立浏览器的登录页面。
2. 在窗口中扫码。
3. `douyin_login_status`：验证当前登录账号。

使用同一数据目录时，应让一个服务实例使用该浏览器会话。运行命令行登录前，先关闭仍占用该会话的 MCP 浏览器。

## MCP 客户端配置

可以安装命令后注册到 Codex：

```bash
uv tool install --editable . --python 3.13
codex mcp add douyin-collections -- /ABSOLUTE/PATH/TO/douyin-collections-mcp serve
```

用 `command -v douyin-collections-mcp` 查询实际安装路径，替换上面的占位符。注册后可运行 `codex mcp get douyin-collections --json` 查看配置。

运行以下命令，生成带有当前虚拟环境 Python 绝对路径的通用 JSON 配置：

```bash
uv run douyin-collections-mcp config
```

把输出加入支持 `mcpServers` 格式的客户端配置。其他客户端可使用相同的命令和参数，按其 MCP 配置格式填写。

配置模板见 [examples/mcp-config.json](examples/mcp-config.json)。其中的 `/ABSOLUTE/PATH/...` 是占位符，需要替换为本机绝对路径：

```json
{
  "mcpServers": {
    "douyin-collections": {
      "command": "/ABSOLUTE/PATH/TO/douyin-collections-mcp/.venv/bin/python",
      "args": ["-m", "douyin_collections_mcp", "serve"],
      "env": {
        "DOUYIN_MCP_BROWSER": "chrome",
        "DOUYIN_MCP_DATA_DIR": "/ABSOLUTE/PATH/TO/PRIVATE/douyin-collections-mcp"
      }
    }
  }
}
```

如果在配置中自定义数据目录，命令行登录时也要设置相同的 `DOUYIN_MCP_DATA_DIR`。否则两个进程会使用不同的登录会话。

服务使用 **stdio** 传输，由客户端启动和管理。直接运行下面的命令时，终端等待 MCP 消息是正常行为：

```bash
uv run douyin-collections-mcp serve
```

省略子命令也会运行 stdio 服务。

## 推荐使用流程

### 1. 确认账号并读取资料

调用 `douyin_login_status`，确认显示的是要整理的账号；再用 `douyin_list_folders` 读取已有收藏夹。

分别同步收藏和点赞，例如：

```json
{"source": "favorites", "max_pages": 3}
```

```json
{"source": "likes", "max_pages": 3}
```

以上参数用于 `douyin_sync`。工具每次读取有限页数：

- `data.complete=false`：还有后续页面，用返回的 `data.next_cursor` 作为下一次 `cursor` 继续同步。
- `data.complete=true`：本次已经读到该来源的末页。从 `cursor="0"` 开始沿游标连续读取到末页，才算完成这一轮遍历。
- `processed` 是本次处理的条目数，不是账号总量，也不是新增视频数。

收藏夹内容使用 `source="folder"`，并提供字符串 `folder_id`。单页工具 `douyin_list_videos` 和 `douyin_list_folders` 通过 `has_more` 与 `next_cursor` 表示后续页。

### 2. 让 AI 生成分类预览

可以这样要求客户端：

> 读取我的收藏和点赞，按做饭、旅行、学习分类。优先复用已有收藏夹。先展示每条视频的标题和目标收藏夹，保存方案，等我确认后执行。

模型可使用 `douyin_search_cache` 筛选已同步的视频，然后调用 `douyin_prepare_classification` 保存计划。下面的 ID 仅作格式示例，实际必须来自当前账号的读取结果：

```json
{
  "assignments": [
    {"video_id": "1234567890123456789", "folder_name": "做饭"},
    {"video_id": "2234567890123456789", "folder_name": "学习"}
  ],
  "allow_favorite_liked": false
}
```

预览返回 `plan_id`、每条操作和 `executed=false`，此时只保存本地计划。

**只有点赞、尚未收藏的视频，进入收藏夹前需要先收藏。** 若明确希望这样处理，在生成方案时设置 `allow_favorite_liked=true`；默认值为 `false`，不会隐含授权先收藏。预览中的 `will_favorite` 标出预计需要先收藏的条目，执行前也会读取实时收藏状态。

每个计划最多 200 条操作，同一视频可分配到不同收藏夹，但不能重复提交相同的视频与收藏夹组合。收藏夹名称限制为 **15 个 UTF-16 单元**：通常一个汉字计 1，常见表情符号计 2。

### 3. 执行已经确认的计划

用户要求执行具体预览后，客户端调用 `douyin_apply_plan`：

```json
{"plan_id": "替换成预览返回的plan_id", "max_operations": 5}
```

实现的执行规则是：

- 复用唯一同名收藏夹；不存在时新建，新建收藏夹默认仅自己可见。复用已有收藏夹时沿用其当前可见性。
- 将视频加入目标收藏夹，保留原有点赞及收藏夹归属。当前提供添加模式，不提供移出旧收藏夹、删除视频或取消点赞操作。
- 每条操作先检查当前状态，写入后回读目标结果；已完成条目会跳过。
- 每次最多执行 `max_operations` 条，默认 5，允许 1–20；遇到错误就停止后续写入。
- 同一个 `plan_id` 可继续执行剩余条目，进度在服务重启后仍然保留。

上述核心流程已用真实账号验证；大规模整理仍按分批、回读和失败即停止的方式执行。

用 `douyin_get_plan` 查看进度。`pending` 表示待执行，`running` 表示执行中，`completed` 表示回读确认完成，`failed` 表示失败，`uncertain` 表示远端结果尚未确认。中断后不要把 `running` 或 `uncertain` 当作已失败且从未发生写入；续跑会先检查远端状态。

## 13 个 MCP 工具

| 工具 | 用途 |
| --- | --- |
| `douyin_status` | 查看本地安装与浏览器状态 |
| `douyin_login_start` | 打开独立浏览器供用户登录 |
| `douyin_login_status` | 验证会话并读取当前账号 |
| `douyin_list_videos` | 读取一页收藏、点赞或收藏夹视频并缓存 |
| `douyin_list_folders` | 读取一页个人收藏夹 |
| `douyin_sync` | 分页同步视频到本地缓存 |
| `douyin_search_cache` | 搜索当前账号已缓存的标题、作者或视频 ID |
| `douyin_prepare_classification` | 保存分类预览，返回 `plan_id` |
| `douyin_get_plan` | 查看当前账号的计划及操作状态 |
| `douyin_apply_plan` | 分批执行已确认的分类计划 |
| `douyin_create_folder` | 新建仅自己可见的收藏夹，或复用唯一同名收藏夹 |
| `douyin_close_browser` | 关闭浏览器并保留本地会话 |
| `douyin_diagnostics` | 查看接口路径、HTTP 状态和适配器观察情况 |

成功响应通常为 `{"ok": true, "data": {...}}`。失败通过 MCP 工具错误返回结构化错误信息，不包含 Cookie、签名或请求正文。

## 本地缓存与数据目录

默认数据目录为 `~/.local/share/douyin-collections-mcp`，可用 `DOUYIN_MCP_DATA_DIR` 覆盖：

```text
douyin-collections-mcp/
├── browser-profile/       独立浏览器的本地登录会话
└── collections.sqlite3    账号隔离的视频缓存、收藏夹与分类计划
```

数据目录和浏览器会话目录权限为 `700`，SQLite 数据库文件权限为 `600`。缓存和计划按账号隔离，切换账号后不能执行另一账号的计划。登录信息由浏览器配置目录保留，不写入分类计划。

`douyin_search_cache` 返回的 `scope="local_cache"` **不等于实时、全量的抖音列表**。缓存只包含实际读取过的视频，未读完的分页不会凭空补齐；在抖音中取消收藏或移出收藏夹后，旧缓存也不会因此自动删除。执行前会重新检查实时状态；需要更新列表时，重新同步对应来源。缓存搜索仍需当前登录账号验证，不提供脱离账号身份的离线浏览。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOUYIN_MCP_DATA_DIR` | `~/.local/share/douyin-collections-mcp` | 会话、缓存和计划目录 |
| `DOUYIN_MCP_BROWSER` | `chrome` | 支持 `chrome`、`chromium`、`msedge` |
| `DOUYIN_MCP_HEADLESS` | 未设置 | 设为 `1` 时不显示浏览器窗口；首次扫码登录时保持未设置 |

## 排查与开发

```bash
uv run douyin-collections-mcp doctor
uv run pytest -q
uv run ruff check src tests
```

- 浏览器无法启动：确认所选浏览器已安装，且同一会话目录没有被另一进程占用。
- 登录或验证提示：在独立浏览器中完成平台要求，再调用 `douyin_login_status`。
- `complete=false`：按返回游标继续读取，不要把当前缓存数量当作账号全量。
- `account_mismatch`：查看当前登录账号，回到计划所属账号后再继续。
- `ambiguous_folder`：存在多个同名收藏夹，先在抖音中区分名称。
- `write_unverified` / `write_uncertain`：用计划状态和实际收藏夹内容确认结果，保留原计划以便续跑。
- `3002184`：早期创建测试返回“操作失败”，网页原生表单也出现过；后续普通中文名称的私密收藏夹创建已成功。具体原因尚未确认，不应把该错误固定解释为账号限制或自动反复重试。可先复用已有收藏夹。
- 接口返回格式变化：使用 `douyin_diagnostics` 获取路径与状态，更新 [browser.py](src/douyin_collections_mcp/browser.py) 和 [normalize.py](src/douyin_collections_mcp/normalize.py) 的适配及测试。

自动化测试覆盖本地存储、分页和响应归一化、服务行为与 MCP 工具协议；真实账号的读写验证进度单独记录在文档开头。
