# 抖音本人账号鉴权与读取接口研究

核查日期：2026-09-15。研究范围：公开 GitHub 源码及 README；没有读取用户现有浏览器凭据，也没有操作主代理正在使用的浏览器 profile。

## 结论

项目采用独立 Chrome 持久会话，由用户在窗口中扫码登录；优先在抖音页面内使用同源 `window.fetch`，需要时让真实页面发出读取请求后采集响应。主代理已报告本人 self 接口成功；本文件中的收藏/点赞接口另有开源实现证据，但不冒充本研究者的账号实测。

已核实的参考仓库是 [mlbb229229-create/douyin-favorites-mcp](https://github.com/mlbb229229-create/douyin-favorites-mcp)，固定版本 `c064f7d002cdd9621e732f77db4e903e80593ce8`。它提供登录、本人账号、收藏、收藏夹及夹内视频读取；没有点赞列表工具，也没有创建/编辑收藏夹工具。当前代码规模很小，适合作为协议/流程参考，不建议整体引入成为运行依赖。

## 接口速查

所有路径均相对于 `https://www.douyin.com`。读取成功需要 HTTP 状态正常、可解析 JSON，以及 `status_code == 0`。固定业务参数之外，网页可能同时发送设备、版本和运行期令牌参数；应以当前已登录页面的请求为准，不用陈旧版本号覆盖真实会话。

| 目的 | 方法和路径 | 业务参数 | 返回结构 | 证据级别 |
| --- | --- | --- | --- | --- |
| 本人信息 | `GET /aweme/v1/web/user/profile/self/` | 项目使用 `aid=6383&device_platform=webapp` | `user.uid`、`user.sec_uid`、`user.nickname` | 参考源码；主代理本次会话已确认成功 |
| 全部收藏 | `POST /aweme/v1/web/aweme/listcollection/` | `cursor=0&count=10` 放 POST body；网页参考实现说明为表单编码 | `aweme_list[]`、`cursor`、`has_more` | 参考项目注释标记 2026-08-17 实测 |
| 收藏夹列表 | `GET /aweme/v1/web/collects/list/` | 查询参数 `cursor=0&count=20`；20 是本项目选择，F2 模型要求这两个字段 | `collects_list[]`、`cursor`、`has_more` | 收藏夹字段有 2026-08-18 实测注释；分页参数另由 F2 模型支持 |
| 指定收藏夹内容 | `GET /aweme/v1/web/collects/video/list/` | 查询参数 `collects_id=<数字字符串>&cursor=0&count=10` | `aweme_list[]`、`cursor`、`has_more` | README 和源码 |
| 本人喜欢/点赞 | `GET /aweme/v1/web/aweme/favorite/` | 查询参数 `sec_user_id=<本人 user.sec_uid>&max_cursor=0&count=20` | `aweme_list[]`、`max_cursor`、`has_more` | F2 模型、crawler、handler；favorites-mcp 本身没有这一工具 |

来源：[参考项目工具实现](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/c064f7d002cdd9621e732f77db4e903e80593ce8/src/douyin_favorites/tools.py)、[响应解析实现](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/c064f7d002cdd9621e732f77db4e903e80593ce8/src/douyin_favorites/interceptor.py)、[F2 请求模型](https://github.com/Johnserf-Seed/f2/blob/main/f2/apps/douyin/model.py)、[F2 接口路径](https://github.com/Johnserf-Seed/f2/blob/main/f2/apps/douyin/api.py)、[F2 crawler](https://github.com/Johnserf-Seed/f2/blob/main/f2/apps/douyin/crawler.py)。

### POST 格式存在的版本差异

F2 的 `fetch_user_collection` 使用 `_fetch_post_json(..., json=params.model_dump())`，并把模型字段也交给 URL 签名函数；favorites-mcp 的 2026-08 注释则明确指出游标在 POST 表单 body 中。因此不应照搬 F2 的序列化方法。当前项目的 `application/x-www-form-urlencoded` 方案与较新的网页观测一致；最终由主代理捕获到的真实请求确定。

## 响应字段与数据规范化

### 收藏夹

`collects_list` 每项包含：

- `collects_id`：收藏夹 ID；全程转成字符串，避免 JavaScript 大整数精度丢失。
- `collects_name`：名称。
- `total_number`：内容数量。
- `collects_cover.url_list`：封面 URL 数组。
- `create_time`：创建时间。

响应顶层 `cursor`、`has_more` 是列表分页状态，不应混成每个收藏夹本身的属性。

### 收藏/点赞/夹内视频

`aweme_list` 每项通常直接是 aweme 对象，包括 `aweme_id`、`desc`、`create_time`、`author`、`video`、`statistics` 等。尽量只对 MCP 输出任务需要的字段。

- 视频/图集标题通常用 `desc`。长文存在 `article_info.article_title`；若只读取 `desc`，部分内容会没有有用标题。
- 作者字段使用 `author.nickname`、`author.uid`、`author.sec_uid`。
- 普通封面在 `video.cover.url_list`；参考实现还有 `article_info.head_poster_list` 或 `article_info.cover` 后备。
- `video.duration` 按毫秒保留可避免错误换算；参考项目用大于 1000 才除以 1000 的启发式，不建议照搬。
- 互动数在 `statistics.digg_count`、`comment_count`、`collect_count`、`share_count` 等；不能把这些统计量当作本人是否点赞/收藏的依据。
- 本人喜欢列表的 `max_cursor` 与收藏类接口的 `cursor` 分开处理。应直接回传服务端游标，不尝试按时间生成下页游标。
- `has_more` 为假才视为正常结束。分页去重使用字符串 aweme ID；重复游标或连续无新项应报告未完整同步，而非默默宣称完成。

字段来源：[interceptor.py](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/c064f7d002cdd9621e732f77db4e903e80593ce8/src/douyin_favorites/interceptor.py)、[F2 handler.py](https://github.com/Johnserf-Seed/f2/blob/main/f2/apps/douyin/handler.py)。

## 浏览器会话与读取回退

参考项目的 `douyin_login_status` 仅检查 `sessionid`、`sessionid_ss`、`sid_guard`、`sid_tt` 是否存在；它也读取自己创建的 profile 内 Cookie 数据库。这个检查不能证明会话仍有效。本项目把“会话存在”与“self 接口确认账号”区分开，更适合 MCP。

独立 profile 只由一个浏览器进程持有。参考项目自己提醒：运行中的同一 profile 再启动第二个 Chrome 会产生锁冲突，造成伪登录失败。研究及诊断不应另开第二个客户端访问主代理正在使用的 profile。

若同源 HTTP 返回空内容或未触发可用请求，参考项目验证过的 UI 读取步骤是：

1. 打开 `/user/self`，等待 `domcontentloaded`；不要等 `networkidle`。
2. 真实点击“收藏”标签，采集 `listcollection` 响应；仅靠 `?showTab=favorite` 不足以保证触发请求。
3. 再点击“收藏夹”子标签，采集 `collects/list` 响应。参考项目候选选择器包含 `#semiTabfavorite_collection`，但现场应先观察实际 DOM。
4. 点击目标收藏夹卡片，按路径和 `collects_id` 匹配 `collects/video/list` 响应。
5. 页面分页可以用内容区滚轮触发；参考项目说明 `window.scrollTo` 对其页面滚动容器无效。

来源：[tools.py](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/c064f7d002cdd9621e732f77db4e903e80593ce8/src/douyin_favorites/tools.py)、[auth.py](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/c064f7d002cdd9621e732f77db4e903e80593ce8/src/douyin_favorites/auth.py)。

参考代码也存在不应复制的问题：`_scroll_harvest` 把所有页 `has_more` 做 OR，最后一页即使结束也可能一直报告有更多；读取分页 cursor 参数只是决定是否继续滚动，并不按该 cursor 发请求；每次创建 Interceptor 会添加 response listener 而未见解绑。这些设计适合早期验证，不应原样成为本项目契约。

## 签名与纯 HTTP 路径的现状

F2 有可运行结构的纯 HTTP 路径：登录 Cookie + 模型参数 + `ABogusManager.model_2_endpoint` 或 `XBogusManager.model_2_endpoint` + HTTP client。它的 A-Bogus 管理器调用 `AB(...).generate_abogus(params, body)`，X-Bogus 管理器调用 `XB(user_agent).getXBogus(...)`，最终分别添加 `a_bogus` / `X-Bogus`。这是源码层面的可实现路径，不代表该签名版本在当前用户会话和所有读取/写入接口都已通过验证。[签名管理器源码](https://github.com/Johnserf-Seed/f2/blob/main/f2/apps/douyin/utils.py)

本次实际开发已允许并选用了真实 Chrome。建议先采用该已成功建立登录的传输方式，不再引入独立签名库作为上线前提。页面内 `fetch` 成功也不能单凭结果断言浏览器自动给每个请求加签；应以真实请求及有效响应为准。不要在修改 cursor 后重用旧的签名值，也不要把带会话令牌的完整请求 URL 暴露给 MCP 日志。

二维码 API 协议本次未完成核查，未列出猜测端点。用户已经在独立 Chrome 登录，当前任务无需另造二维码登录协议。

## 许可证与可复用边界

- `mlbb229229-create/douyin-favorites-mcp` 是 MIT，版权行是 `Copyright (c) 2026 douyin-favorites-mcp contributors`。如果复制其代码或实质片段，需随附版权及许可文本。本研究只摘录协议字段并给出独立实现建议，未拷贝其模块。[固定版本 LICENSE](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/c064f7d002cdd9621e732f77db4e903e80593ce8/LICENSE)
- F2 当前仓库是 Apache-2.0。可用于接口模型及签名实现参考；若以后引入代码，应保留相应 LICENSE/NOTICE，并单独核查所导入文件和依赖许可证。本次没有把 F2 作为依赖，也没有复制其签名算法。[LICENSE](https://github.com/Johnserf-Seed/f2/blob/main/LICENSE)

## 给当前实现的具体建议

现 `browser.py` 的读取端点、POST 表单、本人 `sec_uid` 和点赞 `max_cursor` 与上述证据一致。优先继续账号实测。需要关注的改进只有：长文标题 fallback；按各接口真实游标选择下一页；登录之后如 HTTP 读取缺少模板，使用观察到的真实标签操作获取一次有效响应。读取结果缺字段时报告接口变化，不要合成空成功列表。
