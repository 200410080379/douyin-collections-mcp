# 抖音原生章节要点 / AI 总结读取协议

核查日期：2026-09-16。研究仅下载官网公开静态 JavaScript；没有访问浏览器 profile，没有生成总结、笔记、评论或消息。主代理另行验证真实详情响应，本文件不保存真实账号、视频 ID、标题或总结内容。

## 结论

现有 `GET /aweme/v1/web/aweme/detail/` 的 `aweme_detail` 可以携带原生章节摘要和分段要点，不必调用生成服务。网页版将其显示为“章节要点”。需要同时验证平台的 AI 标记：`chapter_list` 本身不足以证明内容由 AI 生成。

推荐优先接入已确定的数据来源：

1. `chapter_list` 和 `chapter_abstract`，且 `recommend_chapter_apply_status === 1`。
2. 没有生效的 `chapter_list` 时，`recommend_chapter_info.chapter_recommend_type === 1`，使用其中的 `recommend_chapter_list` 和 `chapter_abstract`。

对于没有明确 AI 标记的普通章节，不返回“AI 总结成功”。没有内容时返回不可用；不要把视频描述、字幕或作者章节拼成替代总结。

## 官方证据

| 来源 | 证据 |
| --- | --- |
| [官网详情 API 和详情读取映射](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~2baf5df4.1f7e9381.js) | GET `aweme/detail/`，参数含 `aweme_id`、`request_source`、`origin_type`；结果读取 `aweme_detail` |
| [官网作品规范化模型](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/client-entry~1bdd1f87.be75b51a.js) | 原始章节字段转换为 `chapterInfo`，确定章节优先级和 `showAiTag` |
| [官网章节侧栏](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/ChapterSideCard.e1edb013.js) | “章节要点”标题、摘要/详情渲染、AI 标签、时间换算和知识点字段 |
| [官网 AI 卡片](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/AiCard.855a5eeb.js) | AI 笔记包含用户草稿、生成/重新生成和保存交互；与已有章节要点读取不同 |
| [官网个人主页](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/routes-User-id-route.2681347f.js) | 个人“AI 笔记”标签受功能开关控制，存在关联视频的笔记管理功能 |

侧栏脚本地址来自 [官网 runtime 的异步 chunk 映射](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/runtime~client-entry.c0ada728.js)。哈希固定的 URL 代表本次核查版本，不应成为生产代码下载依赖。

## 读取接口

```text
GET https://www.douyin.com/aweme/v1/web/aweme/detail/
query: aweme_id=<视频字符串ID>
```

官网调用还附加公共 query（`aid=6383`、`device_platform=webapp`、`channel=channel_pc_web`）和 `request_source`、`origin_type`，并使用当前会话的运行时环境。已有 BrowserClient 的详情读取 transport 可复用。

响应必须首先验证 `status_code=0`、`aweme_detail` 是对象、返回 `aweme_id` 等于请求目标。ID 应继续使用字符串，响应先保持 text，再在 Python 解析，以免 JavaScript 对 64 位数字舍入。

本次章节侧栏接收 `awemeInfo.chapterInfo` 直接渲染，没有找到为了显示这些已有章节而单独调用总结生成接口的需要。不能由此推断所有抖音 AI 产品都只读这一接口。

## 字段和来源判定

### 直接章节

| 原始字段 | 含义 |
| --- | --- |
| `aweme_detail.chapter_list` | 直接章节列表；可能包含非 AI 来源的章节 |
| `aweme_detail.chapter_abstract` | 视频级摘要文本 |
| `aweme_detail.recommend_chapter_apply_status` | 等于数值 `1` 时，官网为直接章节开启 `showAiTag` |

没有上述 AI 标记时，只能说这些是“未确认 AI 来源的章节”。不能武断认定一定由作者手写，也不能作为原生 AI 总结输出。

### 推荐章节

| 原始字段 | 含义 |
| --- | --- |
| `aweme_detail.recommend_chapter_info.chapter_recommend_type` | 等于数值 `1` 时，官网可以选用推荐章节 |
| `.recommend_chapter_list` | 推荐章节列表 |
| `.chapter_abstract` | 推荐章节对应的视频摘要 |
| `.chapter_recommend_source` | 真实响应可能出现；本次渲染逻辑没有用它决定 AI 标签，未确认数值枚举 |
| `.push_scene` | 真实响应可能出现；本次未确认其枚举，不用它判定摘要来源 |

推荐分支启用时，官网同时设置 `useRecommendChapter=true`、`showAiTag=true`。普通观看者可见 AI 生成提示；作者查看自动推荐章节时可见平台智能生成提示。作者身份只影响 UI 展示，不改变模型中的 AI 标记。

### 优先级与空数组

官网先用 JavaScript `!!chapter_list` 决定直接章节是否生效。**空数组 `[]` 在 JavaScript 中为 true。** 所以：

- `chapter_list=null` 或缺失：可以继续考虑推荐分支。
- `chapter_list=[]`：占据直接章节分支，不能因 Python 空列表是假值而回退到推荐章节。
- 有效的非空 `chapter_list`：直接章节优先，即使同时存在推荐章节。

生产解析应先验证字段类型，再明确处理 null/缺失和列表，避免把错误类型的真值对象也当章节列表。可用性还需要有非空摘要、章节描述或知识点等实际内容；只有 AI 标记而没有内容，不应返回成功总结。

## 章节和知识点结构

| 字段 | 类型 / 单位 | 官方渲染用途 |
| --- | --- | --- |
| `timestamp` | 数值，毫秒 | 除以 1000 后显示时间并跳转播放器 |
| `desc` | 字符串 | 章节标题 |
| `detail` | 字符串 | 章节正文 / 段落概述 |
| `points` | 可选列表 | 知识点 |
| `points[].type` | 数字或数字字符串 | 官网仅显示字符串化后等于 `"1"` 的知识点 |
| `points[].desc` | 字符串 | 知识点标题 |
| `points[].detail` | 字符串 | 知识点正文；UI 按换行分段 |

侧栏将 `timestamp / 1000` 传给播放器 seek，确定时间单位为毫秒。不要依据数值大小猜测秒/毫秒。时间可直接同时输出 `start_ms` 和 `start_seconds`，不要自行推测末章结束时刻。

官网若任何一章存在 `type=1` 的 points，会切换到知识点展示模式；该模式显示知识点正文而不是每章普通 detail。MCP 可以同时保留原始章节 detail 和有效知识点，但应说明知识点是平台字段，不是工具再次总结。

本次没有在这条章节模型或渲染路径中发现通用 `is_ai` 字段。不要发明或要求接口提供该字段；AI 来源应按上述两个明确条件判断。

## 完全合成的响应例子

以下文字、时间与结构仅用于说明，未复制任何真实视频总结：

```json
{
  "status_code": 0,
  "aweme_detail": {
    "aweme_id": "1000000000000000000",
    "chapter_list": null,
    "recommend_chapter_info": {
      "chapter_recommend_type": 1,
      "chapter_abstract": "合成例子：介绍一个示例问题及其解决步骤。",
      "recommend_chapter_list": [
        {"timestamp": 0, "desc": "示例背景", "detail": "合成的背景介绍。"},
        {
          "timestamp": 30000,
          "desc": "示例步骤",
          "detail": "合成的步骤说明。",
          "points": [{"type": 1, "desc": "示例要点", "detail": "合成的要点说明。"}]
        }
      ]
    }
  }
}
```

## 与其他功能的边界

- **AI 文稿 / 字幕**：偏向音频转录。`caption`、字幕或口播文字不是这里确认的章节摘要来源，不能作为总结的静默替代。
- **视频描述 `desc`**：全局视频介绍文案与章节内部 `desc` 不同，不能用全局描述冒充摘要。
- **AI 搜索**：针对问题进行搜索或问答，与读取已有视频章节不同。
- **AI 笔记**：官网卡片有用户草稿、生成、重新生成、保存等行为；不是读取既有章节需要执行的动作。
- **`douyin_p_c_video_extra.ai_summary`**：官网规范化代码确实把它直接映射为 `aiSummary`，但本次未找到其完整渲染结构，也没有确认它是字符串、对象还是功能开关。不能只凭字段名把它加入文本提取。

## 实测状态

主代理报告：已在真实 DETAIL 响应中观察到 `chapter_list=null` 和 `chapter_recommend_type=1` 的推荐章节，包含文本摘要与 `desc/detail/timestamp`；网页分段时间与毫秒换算一致。此处仅记录结构验证结果，不记录真实内容或标识。更多视频是否有该能力，应逐条读取并返回明确的可用/不可用状态，不能保证每个账号或视频都有原生摘要。
