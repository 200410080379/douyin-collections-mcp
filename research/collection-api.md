# 抖音收藏与收藏夹接口证据

核查日期：2026-09-15。数据来源为抖音官网当前发布的公开 JavaScript，以及少量开源项目用于交叉核对。研究过程只下载公开静态资源，没有读取或使用账号 Cookie，也没有执行账号写入。

## 结论

官网确实有创建、编辑收藏夹和将视频加入/移出收藏夹的 HTTP 接口。业务字段、序列化方式和公开/私密取值均能从官网代码追溯，下面不是猜测接口。接口运行在用户已登录的浏览器会话中；独立 HTTP 客户端是否无需网页运行时亦可使用，仍需另行验证。

## 官方来源

这些为版本带哈希的官网公开脚本，未来官网更新后地址可能失效。模块编号可用于在压缩文件中定位相关函数。

| 来源 | 证据内容 |
| --- | --- |
| [S1: 收藏夹 API 模块](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~43479360.7bc82896.js) | 模块 284055：收藏夹读取、maintain、video/move；模块 681373：作品类型枚举 |
| [S2: HTTP 包装器](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~c01d2505.6f729cf6.js) | 模块 599090：GET/POST 参数位置、header、版本参数 |
| [S3: 个人主页](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/routes-User-id-route.2681347f.js) | 批量添加、移出、移动收藏视频的调用字段与 UI 提示 |
| [S4: 收藏夹弹窗](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/68331.fd02a988.js) | 模块 322596：创建/编辑表单和 secret 值；模块 959532：弹窗；收藏选择器的入夹调用 |
| [S5: 收藏作品 API](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~2baf5df4.1f7e9381.js) | 模块 303476：收藏/取消收藏参数，收藏项目类型枚举 |
| [S6: 喜欢/收藏列表](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~6.8504b11e.js) | 读取喜欢和收藏列表；条件性附加 X-App-Id/X-Device-Id |
| [S7: 作品规范化](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~1bdd1f87.be75b51a.js) | collect_stat 与 user_digged 的布尔映射 |
| [S8: 点赞 API](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~3.d4fb475e.js) | 点赞/取消点赞 body 和 is_digg 成功条件 |

异步脚本路径从 [当前 runtime](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/runtime~client-entry.c0ada728.js) 的 chunk 名称/hash 映射获得。个人主页依赖列表来自 [路由注册脚本](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/32900.b7422fa2.js)。本次下载缓存位于 `/tmp/douyin-js/`，不会纳入产品依赖。

## 1. POST 的参数位置

S2 模块 599090 的 `v_` 导出是 POST 包装器。调用参数含义依次是：路径、query 参数、body 数据、Axios 配置。默认 body 是空对象。query 传 Axios 的 `params`；body 对象使用 query-string 序列化，Content-Type 是 `application/x-www-form-urlencoded; charset=UTF-8`。请求使用 `withCredentials: true`。

因此不能把所有写接口的业务参数一律放 body：收藏夹的两个接口业务字段在 **query**，收藏与点赞的业务字段在 **body**。

公共 query 来自 `COMMON_SEARCH_PARAMS` 和运行时环境。包装器还设置 `pc_client_type`、`pc_libra_divert`、`update_version_code`、`support_h265`、`support_dash`，请求拦截器附加 webid/uifid 和版本值。版本值：

- `/aweme/v1/web/collects/maintain/` 使用 `version_code=230000`、`version_name=23.0.0`。
- 未命中特殊覆盖表的接口默认 `version_code=170400`、`version_name=17.4.0`，包括 video/move。

这些是官网发送的字段，不表示每一个都是服务端的最小必需项。最小字段集合需要实测后再缩减。

## 2. 创建/编辑收藏夹

来源：S1 模块 284055、S4 模块 322596。

`POST https://www.douyin.com/aweme/v1/web/collects/maintain/`

| 操作 | Query 业务字段 | Body |
| --- | --- | --- |
| 新建 | `action=1`、`collects_name=名称`、`secret=1或0` | 空 |
| 改名/改公开范围 | `action=0`、`collects_id=字符串ID`、`collects_name=新名称`、`secret=1或0` | 空 |
| 删除收藏夹 | `action=2`、`collects_id=字符串ID` | 空 |

`secret=1` 是私密；`secret=0` 是公开。证据是 UI 的“设置为公开”Switch 为真时调用 `secret=0`，为假时调用 `secret=1`。官网创建表单默认公开；本工具可以明确选择私密作为默认值。

编辑但名称未变时，官网把 `collects_name` 设为 undefined，即不发送该字段。不要将空字符串当作省略。

官网表单名称 `maxLength=15`，空白名称不能提交。响应检查 `status_code=0`；新建/编辑的结果位于 `collects_info`。收藏夹 ID 使用 `collects_id_str`；名称 `collects_name`；数量 `total_number`；公开状态 `status`。UI 把 `status=0` 标识为私密。

删除只删除收藏夹；官网确认文案明确表示视频仍保留在所有收藏中。但工具是否提供删除功能，应独立按产品范围决定，不能借测试任意删除现有收藏夹。

## 3. 将已收藏视频加入收藏夹

来源：S1 模块 284055 导出 RX；S3 批量添加调用、S4 收藏选择器。

`POST https://www.douyin.com/aweme/v1/web/collects/video/move/`

所有业务字段为 query，body 为空。

| 字段 | 含义 |
| --- | --- |
| `item_ids` | 视频 ID 字符串数组，用重复键传递 |
| `item_type=2` | 收藏项目类型 AWEME。与作品的 aweme_type 不同 |
| `to_collects_id` | 目标收藏夹字符串 ID |
| `update_collects_sort=true` | 官网添加调用设置此值 |
| `collects_name` | 目标收藏夹名称，部分页面调用会带；简单批量添加调用省略 |
| `move_collects_list` | 选中的目标收藏夹 ID 数组，部分调用会带；简单批量添加调用省略 |

S1 明确使用 query-string 的 `arrayFormat: none`。数组编码示意：

```text
item_ids=VIDEO_ID_1&item_ids=VIDEO_ID_2&item_type=2&to_collects_id=FOLDER_ID&update_collects_sort=true
```

不能编码为 JSON 字符串，也不能使用 `item_ids[]`，否则不等同于官网调用。S3 的简单批量添加只需要前四项；响应以 `status_code=0` 判定成功。应随后读取目标收藏夹确认实际成员关系。

**加入不代表替换其他分类。** 简单添加调用没有 `from_collects_id`。视频可以加入所选多个收藏夹，官网逐个目标发送请求。`move_collects_list` 在已观察代码中是目标列表，不是应被清空的源列表。

## 4. 从某收藏夹移出，保留视频收藏

来源：S3 的 removeVideosFromCollection。

同一个 `POST /aweme/v1/web/collects/video/move/`，query：

- `item_ids`：重复键视频 ID 数组。
- `item_type=2`。
- `from_collects_id`：源收藏夹 ID。
- `collects_name`：源收藏夹名称。
- 不提供 `to_collects_id`。

官网“移动到其他收藏夹”由先移出、后加入两次操作完成，不是事务。工具实现若要避免中途失败丢失分类，宜先确认加入目标成功，再移出来源；失败时返回明确的部分完成状态。不要把取消收藏代替移出收藏夹。

## 5. 读取接口

| 用途 | 方法/路径 | 业务参数与结果 |
| --- | --- | --- |
| 所有收藏夹 | GET `/aweme/v1/web/collects/list/` | query `cursor=0,count=12`；响应 `collects_list,cursor,has_more,total_number` |
| 某收藏夹内容 | GET `/aweme/v1/web/collects/video/list/` | query `collects_id,cursor,count`；响应 `aweme_list,cursor,has_more` |
| 全部收藏视频 | POST `/aweme/v1/web/aweme/listcollection/` | form body `cursor=0,count=10`；公共 query |
| 喜欢的视频 | GET `/aweme/v1/web/aweme/favorite/` | query `sec_user_id,max_cursor=0,min_cursor=0,count=10,cut_version=1`；官网还发送空/default `whale_cut_token` |

S1/S6 为官方直接证据；[douyin-favorites-mcp 源代码](https://github.com/mlbb229229-create/douyin-favorites-mcp/blob/master/src/douyin_favorites/tools.py) 是前三项的开源交叉证据。

**ID 必须保留完整精度。** 官网收藏夹模型直接使用 `collects_id_str`。不要在 JavaScript `response.json()` 中把 64 位 JSON 数值解析为 Number 再转字符串。浏览器 fetch 可把响应 `.text()` 返回 Python 后解析，或直接使用明确的 `*_id_str` 字段。视频 ID 与 folder ID 传入 MCP 均使用 string。

## 6. 点赞/收藏状态与单视频收藏

来源：S5、S7、S8。

- `collect_stat === 1` 表示当前账号已收藏。
- 官网把 `user_digged !== 0` 转为已点赞；实现应检查字段存在，不能把缺失值误当已点赞。
- `/aweme/v1/web/aweme/collect/` 是 POST，公共 query；form body 为 `aweme_id`、`action`（1 收藏，0 取消）、`aweme_type`。
- `aweme_type` 默认 Normal=0；应该使用原始作品类型。官网枚举含 NewNormal=4、Note=68、Article=163；此处类型不能填收藏项目类型 2。
- `/aweme/v1/web/commit/item/digg/` 是 POST，公共 query；form body 为 `aweme_id`、`type`（默认 1，取消用 0）、`item_type`（默认 0）。官网在响应 status_code=0 时还要求 `is_digg` 存在，缺失会改判失败。

把仅点赞的视频加入收藏夹时，S4 的官网选择器会先执行收藏，再执行入夹。若已经收藏则直接入夹。因此工具可以先读取 `collect_stat`，仅在未收藏时新增收藏，完成入夹后分别报告两步结果。

## 7. 登录、签名、CSRF 与失败处理证据

本次确认的是浏览器会话内的官方业务协议，不是免登录或破解签名协议。

- S2 的 POST helper 使用凭据请求与表单 Content-Type，且附加环境参数和 uifid header；在该 helper 中没有看到显式的 CSRF token 赋值。
- 页面加载独立安全 SDK，签名/防护可能在 fetch/XHR 层附加。不能仅凭业务 helper 没有签名字段，声称接口永远无需 `a_bogus` 或 CSRF。
- S1 中的响应处理器覆盖 maintain/collect/digg 等路径，处理 `x-vc-bdturing-parameters`、`x-whale-throughput-abort-data` 与账号验证提示。
- 若返回验证码、重新登录或账号验证，工具应停止该操作并返回可解释状态，不绕过这些步骤。写入失败不要盲目重复新建，否则可能创建重复收藏夹。

## 8. UI 流程证据（备查）

无需模拟屏幕坐标即可理解操作语义。S3/S4 的 JSX 给出：

- 个人主页 → 收藏 → 收藏夹；列表页有“新建收藏夹”。
- 新建弹窗：名称输入（15 个字以内）→“设置为公开”开关→“确认”。
- 编辑弹窗：“编辑收藏夹”→名称/公开开关→“确认修改”。
- 收藏视频列表批量管理 → 选择视频 → 收藏夹选择器 → 确定或添加。
- 收藏夹内容页可“移出”或“移动”视频；移出有确认提示，移动由移出与加入两个调用组成。

这些名称来自官网静态代码；并非本研究通过 UI 写入实测。产品优先使用会话内 HTTP，不需依赖这些可变化的界面选择器。

## 9. 2026-09-15 联调补充：原生 SDK 初始化和实测结果

### SecureSDK 初始化的位置

S2 的 GET/POST 包装器在调用 Axios **之前**，会对受保护的接口等待安全 SDK 初始化。该步骤位于 S1 模块 826601；导出 `Ek` 判断路径是否受保护，`Fm` 等待 `web_protect` 场景的 SecureSDK 初始化。初始化使用官网配置、调用 SDK 的正常启动流程，并等待初始化事件或超时。收藏夹维护、入夹、收藏和喜欢列表接口均在受保护名单中。

因此，直接调用页面的 `window.axiosInstance.request` 能使用 Axios 请求拦截器，却不等于调用完整业务包装器；它不会自行补上包装器在调用 Axios 前的初始化等待。原生“喜欢”页签触发的 `GET /aweme/v1/web/aweme/favorite/` 会经过完整包装器。登录后的原生 token beat 初始化也会启动同一安全 SDK。打开原生喜欢列表属于只读操作，可以在必要时让网站正常完成初始化；不需要伪造任何验证数据。

这是静态调用链的确定差异，**尚未证明它导致过当前测试的写入错误**。已有入夹实测在当前 transport 下成功，因此没有据此加入额外初始化动作。

### 已验证的入夹与移出能力

主代理在用户授权下，对用户手动创建的一个私密测试收藏夹和一条已有收藏执行了有限测试。研究代理未操作浏览器或账号。主代理报告以下项目通过，记录保存在本地 `output/existing-folder-smoke.json`；该记录可能含账号内容标识，应保留在本地并排除版本控制。

- `POST /aweme/v1/web/collects/video/move/` 使用本文的 `to_collects_id` 参数成功返回 `status_code=0`。
- 回读目标收藏夹确认新增成员关系。
- 重跑同一已完成分类方案，没有重复写入。
- 使用本文的 `from_collects_id` 参数成功移出测试条目。
- 测试后原有收藏状态和点赞状态均保持不变。

这验证了当前实现的重复键 `item_ids` 编码、`item_type=2`、query/body 分配，以及当前浏览器会话下的 Axios transport。创建能力随后也单独验证，见下节。

### 后续创建与完整 MCP 验收已通过

早期创建测试收到 `status_code=3002184`，未创建收藏夹。公开脚本没有找到该错误码的解释，也不能把 SDK 初始化差异直接当作错误原因。

后续主代理使用普通中文名称，通过原生表单及已安装的 stdio MCP 分别创建了私密收藏夹，回读确认后清理成功。完整 MCP 验收还覆盖已有收藏入夹，以及明确允许后将仅点赞视频先收藏再入夹。结束后测试成员关系、临时收藏状态和自动创建的测试夹均已清理，原有点赞状态保持。结果见本地 `output/mcp-live-smoke.json`，说明见 [verification.md](verification.md)。这些独立的实际结果构成创建和写入成功的依据；早期错误的具体原因仍未确认。
