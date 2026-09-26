# 评分协议

软件版本 0.2.0。正式协议为 `bandoribench-protocol-v2`；旧 `protocol-v1` 仅用于复现 Pilot。

## 1. Walk-forward hindcast

活动按实际 `start_at` 排序，先筛选 `requested_tiers` 全部具备可信终值的完整活动。正式 JP v1 默认前 12 场完整活动作为 warm-up，之后每一场依次成为 hindcast 目标。

对目标活动 E_i，只允许模型用 `history_event_ids` 中列出的更早完整活动做训练、残差校准和参数估计。当前活动仅提供 `issued_at = end_at - horizon_hours` 以前实际可见的 tracker observations。默认 horizon 为 72/48/24/12/6 小时。

Protocol v2 在冻结时先做输入 eligibility：以 **event × horizon 的完整 requested-tier panel** 为最小单位。若该起报点任一请求档位因 stale / insufficient history 无法形成任务，则该起报点的整组请求档位从冻结案例中排除，但同一活动其他正常 horizons 保留。因此不同 era × tier × horizon cell 的活动数可以不同；这属于冻结前的数据可用性，不是允许提交者缺题。提交仍必须覆盖 `tasks.json` 中的全部冻结案例。

对于训练型外部模型，推荐使用 `model-eval` 的时间阶段视图。它仍使用同一个冻结 benchmark 和同一套 loss/scale/macro 规则，只是在 development / selection / final 阶段分别评分对应的冻结 case 子集；子集本身不会产生新的 benchmark ID。runner 的当前/未来真值通过流式协议隔离，训练事件只有在允许的时间点才会通过 `observe_event` 出现。详见 [MODEL_API.md](MODEL_API.md)。

v2 不强制重采样。输入是原始 tracker observation；模型可自行决定时间网格。起报时最后观测默认不得陈旧超过 3 小时。历史中间的长缺口作为缺测保留。

项目定位为可信离线回放，不实现沙箱或反作弊；`history_event_ids` 是科学协议中的因果边界。

## 2. 固定评分尺度

为了让不同 hindcast 的损失可比较，尺度只使用 warm-up 活动计算，之后冻结，不随测试结果更新。

每个 server × tier × horizon：

```text
s = max(mean(warmup linear24 absolute error),
        0.01 * median(warmup final PT),
        1 PT)
scaled_loss = loss / s
```

这套尺度是评分规范，不是模型训练限制。模型本身在后续 hindcast 可以使用当时已经结束的新活动继续学习。

## 3. PointScore

点预测损失为：

```text
abs(prediction - final)
```

在每个 era × tier × horizon cell 内先对活动等权平均 scaled loss，再对 cell 等权平均成 L：

```text
PointScore = 100 / (1 + L)
```

100 表示零损失；50 表示宏平均 scaled loss=1。它不是“准确率百分比”。

## 4. ProbScore

概率赛道要求 q05/q10/q25/q50/q75/q90/q95，prediction 必须等于 q50。使用 50/80/90% 中央区间的 Weighted Interval Score：

```text
IS_alpha = (u-l) + 2/alpha * max(l-y,0) + 2/alpha * max(y-u,0)
WIS = (0.5*abs(q50-y) + sum(alpha/2*IS_alpha)) / 3.5
```

主分同样先除冻结尺度，再宏平均，然后显示为 `100/(1+L)`。

概率报告同时强制输出：

- `coverage50`
- `coverage90`
- `mean_interval_width50`
- `mean_interval_width90`
- `median_bias`

Coverage 不硬塞进主分，以免破坏 WIS 的 proper-scoring 结构。Pilot 已经证明“高 WIS 分但区间过窄”是实际会发生的，因此这些诊断必须一起看。

## 5. 提交

```json
{
  "benchmark_id": "...",
  "model_id": "my-model",
  "model_version": "1",
  "provenance": "offline_walk_forward",
  "predictions": [
    {"case_id": "jp:342:1000:24", "prediction": 2200000}
  ]
}
```

概率赛道为每题另加完整 quantiles。提交必须覆盖全部冻结案例；缺题、错误、非法数值或分位数交叉都会得到 `eligible=false, score=null`，不按剩下的简单题给漂亮的部分分。

## 6. 不确定性

默认 200 次 whole-event bootstrap，按 era 分层，同一活动的全部 tier/horizon 一起重采样。区间描述有限历史活动下的成绩波动，不覆盖长期机制变化、上游数据偏差或反复调参造成的测试集过拟合。


## Competition-safe model evaluation

For multi-worker model competitions, use a development-only devkit rather than distributing protocol-v2 `public/tasks.json`. The latter contains shared labeled `reference_events` for trusted replay and therefore is not a physically isolated contestant artifact.

Report both case count and event count. Tier/horizon cases from one event are correlated; model-selection strength is governed much more by the number of held-out events than by the raw number of cases. Selection/final outputs are aggregate by default.
