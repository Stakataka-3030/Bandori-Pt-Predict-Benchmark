# 评分协议

软件版本 0.1.1；协议 `bandoribench-protocol-v1`。

## 1. 一道题是什么

题目键是 `server:event_id:tier:horizon_hours`。目标是 `issued_at = end_at - horizon_hours` 时，对 `end_at` 的最终档线作出预测。图形为了显示而延伸到收官时刻的线段，不是额外发布的一次预测。

固定一个服务器。所有活动按实际开始时间排序，前 N 场作为校准集，后面的整场活动作为测试集。校准活动必须在最早测试活动开始前全部结束。不能按 ID 大小拆分，也不能随机拆同一活动的采样点。

每个校准尺度至少有 3 场有效活动；默认建议 10 场校准、20 场测试。数字只是首版运行配置，不代表统计充分性已经得到证明。跨国服奖励制度的尺度可沿用较早时期，这必须在 manifest 的时期信息和分项成绩中披露，不能用测试时期结果重新归一化。

## 2. 损失函数

点预测使用 `abs(prediction - final)`，对应预测中位数的目标，而不是把预测均值与中位数混为一谈。

概率预测使用 q05/q10/q25/q50/q75/q90/q95。令 alpha 为 0.5、0.2、0.1，分别得到 50%、80%、90% 中央区间 [l,u]：

```text
IS_alpha = (u-l) + 2/alpha * max(l-y,0) + 2/alpha * max(y-u,0)
WIS = (0.5*abs(q50-y) + sum(alpha/2*IS_alpha)) / 3.5
```

区间无限扩大也会付出宽度损失。分位数相互交叉、缺分位数、重复数值键别名、非有限数值均无效。`prediction` 必须等于 q50。不能把点预测的标准误差当成未来档线的不确定性，不能把固定加 10% 直接称为 90% 分位数。

定义与依据：Bracher et al., *Evaluating epidemic forecasts in an interval format*, 2021，https://doi.org/10.1371/journal.pcbi.1008618 。该论文论证 WIS 的性质；本项目的分数映射、取样方案与分组权重是另行作出的工程设计。

## 3. 校准、聚合、显示

针对每个服务器/档位/提前量，从校准活动计算参考基线 `linear24` 的绝对误差。冻结尺度：

```text
s = max(mean(calibration_reference_abs_error),
        0.01 * median(calibration_final_PT),
        1 PT)
scaled_loss = loss / s
```

仅校准数据进入分母；不使用测试活动的最终 PT，也不按每个测试案例动态选择最有利的尺度。

在每个 `era × tier × horizon` 格内，对活动等权平均。再对 manifest 中有效格等权平均成 L。所有格权重在读入参赛预测前已确定，采样条数不能改变分数权重。显示 `100/(1+L)`，同时输出 L、逐案例损失、各提前量/档位/时期分数以及异常诊断。

映射单调但非线性：只在最终汇总后施加，不把每题变成分数后平均。原始 WIS 保留为统计评价指标；不声称显示变换在随机测试集的期望意义下仍是一条 proper scoring rule。

不能通过手工增加“预测线越平滑越好”的惩罚奖励僵化模型，合理吸收新信息可能导致终值预测改变。严重低估与低于当前 PT 作为诊断，不通过任意不对称主损失诱导所有模型系统性报高。单独的风险决策赛道可另行预注册。

## 4. 提交 JSON

```json
{
  "benchmark_id": "复制冻结任务中的完整 SHA-256",
  "model_id": "my-model",
  "model_version": "your-version",
  "provenance": "offline_recompute",
  "predictions": [
    {
      "case_id": "jp:123:1000:24",
      "prediction": 2200000,
      "quantiles": {
        "0.05": 1800000,
        "0.1": 1900000,
        "0.25": 2050000,
        "0.5": 2200000,
        "0.75": 2400000,
        "0.9": 2650000,
        "0.95": 2800000
      }
    }
  ]
}
```

这是格式例子，不是真实事件预测；实际提交必须包含该 benchmark 的全部案例。点预测赛道可省略 quantiles。算法运行失败可提交 `{"case_id":"...","error":"原因"}`，结果是无正式分数，而不是悄悄跳题。

```bash
python bandoribench.py score runs/jp-v1/private/benchmark.json submission.json --track probabilistic --out report.json
```

任何有效性失败都会使 `eligible=false, score=null`；进程退出码为 2。结构性错误（例如错 benchmark、重复题）直接拒绝。成功退出为 0。未知模型不会被悄悄当成 linear24。

## 5. 不确定性与重复评测

默认进行 200 次固定随机种子的 bootstrap：按 era 分层，对整场活动抽样，同场所有档位和提前量一起进入样本。任何时代少于两场有效测试活动，则不输出该区间。它只反映固定校准条件下、假定活动层面可交换时的有限样本波动；不覆盖校准尺度的不确定性、长期时间依赖或规则变化。不能据此轻易宣称两个模型的差异显著。

反复看同一测试集后调参会把它变成验证集。正式项目应维护固定公开验证版本和封存测试版本；最终还需要未来活动的前瞻测试。当前离线工具没有加密隔离或沙箱，不提供反作弊安全保证。
