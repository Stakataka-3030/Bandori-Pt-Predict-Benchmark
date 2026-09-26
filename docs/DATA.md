# 数据协议与来源边界

## 公共入口

- Bestdori 活动索引：`https://bestdori.com/api/events/all.3.json`
- Bestdori 活动详情：`https://bestdori.com/api/events/{event_id}.json`
- Bestdori 档线：`https://bestdori.com/api/tracker/data?server={0|1|2|3}&event={event_id}&tier={tier}`
- Bestdori 活动归档最终档线：`https://bestdori.com/api/archives/all.5.json`，每场的 `cutoff[server][tier]`
- HHWX 档线：`https://hhwx.org/api/bandori/tracker/data?server={0|1|2|3}&event={event_id}&type=event&tier={tier}`

服务器依次为 JP/EN/TW/CN。以上接口取自公开实现，不是游戏官方 API 承诺。用户侧首次真实采集已确认 Bestdori 请求可达，并暴露、修复了 v0.1.1 的时间字段错误；仍不能据此宣称已实测全历史覆盖或数据频率。

已查阅的公开契约：

- https://github.com/byydzh/MYCX_1000/blob/main/config.py
- https://github.com/byydzh/MYCX_1000/blob/main/data_source.py
- https://github.com/BluewaterAlnilamII/hhwx/blob/main/src/lib/bandori/event-tracker/api-server.ts
- https://github.com/BluewaterAlnilamII/hhwx/blob/main/src/lib/bandori/event-tracker/cutoff-history-contract.ts
- https://github.com/BluewaterAlnilamII/hhwx/blob/main/src/lib/bandori/event-tracker/bestdori-prediction-server.ts

`main` 会变；正式数据版本需同时固定访问时间、源响应哈希和代码提交。HHWX 支持固定档位，不意味着每期每档都有档案。JSON 的毫秒时间戳不意味着毫秒观测，更不意味着历史上每分钟都有记录。

## 数据集结构

```json
{
  "schema": "bandoribench-dataset-v1",
  "synthetic": false,
  "knowledge_time": "observed_at_only",
  "events": [
    {
      "server": "jp",
      "event_id": 123,
      "start_at": 1600000000000,
      "end_at": 1600604800000,
      "event_type": "normal",
      "era": "unspecified",
      "tiers": {
        "1000": {
          "points": [
            {"time": 1600021600000, "ep": 12345},
            {"time": 1600043200000, "ep": 23456}
          ],
          "label": {
            "ep": 2345678,
            "time": 1600604800000,
            "quality": "verified",
            "evidence": "REPLACE_WITH_REAL_SETTLEMENT_SOURCE"
          }
        }
      }
    }
  ]
}
```

仅为结构例子；不能直接当真实数据或充分样本运行。所有时间均为 Unix 毫秒。`end_at` 专指**停止累计 PT 的时刻**，不指活动页面撤下或领奖截止。Bestdori 默认映射为 `endAt`。另外保存 `aggregate_end_at`，默认来自 `aggregateEndAt`（结果汇总结束时间）；它不是模型预测目标，只用于更严格地锚定结算后的终值观测。窗口覆盖文件示例：

```json
{"jp:123":{"start_at":1600000000000,"end_at":1600604800000,"aggregate_end_at":1600608400000}}
```

用 `collect --windows windows.json` 提供。历史档案若不能还原延期发生的时间，就不是无偏的实时回放；此类活动应在冻结前单独审查。

## 终值

可信终值标签只接受：

- `archive_final`：Bestdori 活动归档 `cutoff[server][tier]` 直接给出的最终档线。归档条目没有单独时间戳，因此标签时间使用该活动的 `aggregateEndAt` 作为结算锚点，并保留归档 URL、活动、区服、档位证据。
- `explicit_final`：源记录有 `isFinal=true`，且时间不早于停止累计 PT；它是提供方标记，不自动等于独立游戏结算核验。
- `post_aggregate_observation`：历史 tracker 没有 explicit final 标志，但存在时间达到或晚于 `aggregateEndAt` 的档线观测；采用最后一条这样的观测，并保留 URL 与时间证据。这是“结算后观测”标签，不冒充独立官方核验。
- `verified`：人工/其他可靠来源核验，并写明 evidence。
- `synthetic`：仅限显式标记 synthetic 的测试数据。

普通最后一条记录不能自动升级为 final；**仅仅晚于 `endAt` 也不够，自动标签要求达到 `aggregateEndAt`。**采集器得到原始序列但没有可信标签时仍保存原始响应与覆盖审计，只是不将该序列作为有真值的题目。可提供标签文件：

```json
{"jp:123:1000":{"ep":2345678,"time":1600604800000,"quality":"verified","evidence":"REPLACE_WITH_REAL_SETTLEMENT_SOURCE"}}
```

用 `collect --labels verified-labels.json`。验证器检查负数、NaN、重复时间戳冲突、积分倒退、终值低于已观测值、提供方 final 与标签冲突。遇到删榜/结算修正等特殊情况，应独立审计后发布另一种协议，而不是悄悄抹平。

## 数据缺失与采集礼仪

采集器顺序请求，默认请求间隔 0.5 秒、单次 20 秒超时，部分暂时性错误最多 3 次尝试；识别数值型 Retry-After，等待上限 120 秒。若提供方要求更低频或批量授权，应调整参数并遵守要求，不绕过限制。

原始成功响应按内容 SHA-256 存储，`acquisition.json` 保存 URL、取得时间、错误和各序列间隔统计。没有自动跨站兜底，以免混淆来源。因数据缺失的活动不能被预测得更容易的旧活动无痕替换；缺失及采集失败要随 release 审计公开。活动索引本身缺少时间的条目仍须在发布前另行盘点，此工具不声称能发现上游完全未列出的活动。

请求成功但空数组不是零分。六小时采样通过因果 as-of 实现，不插值、不补充开局 0 分。严格截止到已知数据；`available_at` 存在时用它过滤。没有时就保持 `observed_at_only` 标签，取得时间 `retrieved_at` 不伪装成历史可用时间。

## 冻结与发布

`private/benchmark.json` 给裁判使用，含测试真值；`public/tasks.json` 仅含任务前缀、早期校准活动、冻结尺度和 benchmark ID。`manifest.json` 列出参数及冻结前排除原因。bench ID 覆盖数据哈希、标签、任务、校准信息和软件版本；事后改包将校验失败。

公开聚合任务仍可被恶意使用者跨时点偷看，目录隔离不是系统安全边界。正式未知活动测试应由隔离执行器一次只投递一个 prefix，禁用未授权网络和其他文件访问，并封存 issued_at / available_at / 预测结果。当前版本没有实现这样的沙箱。

默认不提交原始数据、private 目录或运行产物。许可证与再分发权必须另行确认。合成数据可以用于检验代码，绝不能代替真实成绩。
