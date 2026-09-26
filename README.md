# Bandori PT Predict Benchmark

**v0.3.5 · 新增外部模型 JSONL runner 接口、泄漏受控训练导出，以及 development / selection / final 三阶段评测。**

针对 BanG Dream! GBP 活动排名档线：统一历史输入、预测时点、校准集、测试集和评分算法，输出可复现的 **0–100 分**，同时保留分档位、分提前量和分奖励制度的成绩。

> 当前状态：真实 JP 候选池已实测 80 场，其中 **54 场 T100/T1000/T2000 三档完整、237/240 条 tracker 序列通过单调性校验、168 条终值来自实际 post-end tracker observation**。v0.2.0 新增 `bandoribench-protocol-v2`：前若干完整活动只做 warm-up，后续活动逐场 expanding walk-forward；当前活动输入保留原始 tracker 采样，不再强制压成 6 小时。旧 `freeze` 仍保留用于复现实验性的 JP Pilot。

## 立即运行

Python 3.11+，运行本体仅用标准库，无须安装依赖；Windows、Linux、macOS 使用相同命令。

```bash
python -m unittest discover -s tests -v
python bandoribench.py demo --out runs/demo
```

`demo` 创建一个不可覆盖的合成 benchmark，执行全部基线并输出 `summary.json`。换一次参数，请换输出目录。

## 分数究竟是什么？

**PointScore** 评估每个固定时点对最终档线的点预测；**ProbScore** 评估预测分位数，使用 WIS（加权区间评分）。两个赛道分别排名，不能把数值直接横比；点预测不会自动变成“有置信度的概率预测”。

设单个案例的损失为 `loss`。先用**较早校准活动**中 `linear24` 基线的平均绝对误差建立每个“服务器 × 档位 × 提前量”的固定尺度 `s`，再计算 `loss / s`。分母不使用正在测试的活动最终分数。尺度下限为校准集该档位/提前量终值中位数的 1%，至少 1 PT，以防基线碰巧零误差。

按“奖励制度 × 档位 × 提前量”先在各格内对活动等权平均，再将各格等权平均，得到 `L`。最后**只对汇总损失**做显示变换：

```text
Score = 100 / (1 + L)
```

零损失是 100 分；`L=1` 是 50 分；`L=2` 是约 33.33 分。50 分表示平均损失等于冻结的校准尺度，**不是准确率 50%，也不是测试集基线必得 50 分**。测试基线必须实际计算。仍然输出原始 `macro_scaled_loss`；同一个冻结 benchmark 中，它与总分排序完全一致。

这个映射是本项目的设计选择，不是已经得到社区公认的标准。WIS 本身的统计性质不等于对最终总分任意非线性变换后仍具有同样的期望激励性质。我们保留并以原始损失作为统计分析依据，0–100 仅用于固定样本上的展示。

只比较同一 `benchmark_id`、同一赛道的成绩。修改数据、标签、校准期、取样粒度、尺度或测试时点，都会得到另一个 benchmark；分数不能跨版本当作统一“能力值”。

## 正式协议保留原始 tracker 采样

Protocol v2 仍在收官前 **72 / 48 / 24 / 12 / 6 小时**起报，但每道题向模型提供 `issued_at` 之前所有因果可见的原始 tracker observation；不插值、不制造 6 小时网格。模型可以自行重采样成 30 分钟、1h、3h、6h，或直接处理不规则时间序列。默认要求起报时最新观测不陈旧超过 3 小时；更早历史中的长缺口原样保留，不因此自动废掉整条序列。

Protocol v1 / Pilot 的 `freeze` 仍使用 6 小时 coarse-history，以便复现已经得到的 Pilot 分数。正式 JP v1 使用 `freeze-walkforward`，前 12 场完整活动作为 warm-up，后续每一场的 `history_event_ids` 只列出它之前已经结束的完整活动。工具按此列表拟合滚动校准；项目定位为可信离线回放，不做对抗性反作弊沙箱。

Protocol v2 的 target eligibility 以 **event × horizon 的完整请求档位面板**为最小单位：若某个起报时点任一请求档位因 stale / insufficient history 无法形成任务，该时点的全部请求档位一起排除，以保持 multi-tier analog / CARE 的 sibling 输入完整；同一活动其他正常 horizons 继续保留。冻结协议同时记录 `eligible_target_event_ids`、`fully_excluded_target_event_ids`、`excluded_case_count` 和 `excluded_cases_by_reason`。

当前主任务仍是**各时点对最终档线的预测**。多档联合轨迹 / Energy Score 留给后续协议。

## 真实数据工作流

第一版预设日服 T100 / T1000 / T2000，避免先把国服已知奖励制度变化混进主榜；这不代表已验证“日服永远稳定”。

```bash
# 当前正式候选池：最近 80 场
python bandoribench.py collect --server jp --source bestdori --recent 80 --tiers 100 1000 2000 --out data/jp-80

# 正式 v2：完整活动中前 12 场 warm-up，后续逐场 walk-forward，输入保留原始采样
python bandoribench.py freeze-walkforward data/jp-80/dataset.json --warmup-events 12 --out runs/jp-v1

# 从仓库内置的国务院办公厅公告规则可重复生成 CN 2019-2026 冻结日历
python bandoribench.py calendar-fetch --server cn --years 2019 2020 2021 2022 2023 2024 2025 2026 --out calendars/cn-2019-2026.json

# 显式评测节假日/调休日：把固定日历一起冻结进新的 benchmark
python bandoribench.py freeze-walkforward data/cn-80/dataset.json --warmup-events 12 --tiers 500 1000 2000 --calendar calendars/cn-2019-2026.json --out runs/cn-core-v1

# 基线
python bandoribench.py predict runs/jp-v1/public/tasks.json --model calibrated-linear24 --out runs/jp-calibrated-linear24.json
python bandoribench.py score runs/jp-v1/private/benchmark.json runs/jp-calibrated-linear24.json --out runs/jp-calibrated-linear24-report.json
```

**终值优先使用 Bestdori `api/archives/all.5.json` 的 `cutoff[server][tier]`（`archive_final`）。** 对归档尚未覆盖的新活动，若 tracker 在 `endAt` 到 `aggregateEndAt` 之间存在收官观测，且这些观测的 PT 完全一致，则自动标记为 `post_end_final`。若多条收官观测互相矛盾，则不猜终值；再尝试明确的 `isFinal` / 稳定的 `aggregateEndAt` 后观测，最后才需要 `--labels verified-labels.json`。

`--source hhwx` 可切换档线来源；元数据仍显式来自 Bestdori，并记录来源，不做无痕自动回退。**预测目标时刻使用 Bestdori `endAt`（活动终止 / PT 停止），`aggregateEndAt` 只用于判断结算后的档线观测能否作为自动终值标签。** 两者分别保存，不能混为一个字段。可用 `--windows` 提供核验后的 `start_at` / `end_at`，以及可选的 `aggregate_end_at` 覆盖。

## 国服：顺序和奖励制度

根据仓库发起者 2026-09-26 提供的纠正：**311 → 310 → 314；312、313 提前举办**。程序用 `start_at` 排序，并为这些关系加入回归测试和矛盾检查。

**以国服 310 实际开场时刻为制度切换边界**：此前为 T1000 语音表情，之后为 T500 / T1500 两档语音表情。对 310 / 311 / 312 / 313 / 314 设置显式规则，其他活动看实际时间；没有边界信息就标记 unknown，绝不使用 `event_id >= 310`。

这些是用户提供的领域纠正，不伪装成已独立核验的官方奖励表。通用算法不被强迫使用奖励特征；评分报告按 `era` 分层，避免把制度前后混成不透明的总体数值。跨制度测试可以做，但须显式标明它是分布变化挑战集。最终主模型计划在 JP 上完成架构验证后，对 CN 历史重新拟合/校准，而不是把 JP 参数原样搬过去。国服采集例如：

```bash
python bandoribench.py collect --server cn --source hhwx --recent 30 --tiers 500 1000 1500 2000 --out data/cn
```

## 基线与外部算法

| 模型名 | 含义 |
|---|---|
| `persistence` | 认为当前分数就是最终分数的弱基线 |
| `linear24` | 按最近约 24 小时实际涨速外推 |
| `calibrated-linear24` | linear24 + 预测当时所有既往活动残差中位数；正式点预测强基线 |
| `linear24-quantiles` | 用预测当时所有既往活动残差构造分位数；概率基线 |
| `bestdori-hierarchical` | Bestdori 公开公式家族；活动类型 rate 向同档全局历史 rate 收缩，v2 可全覆盖 |
| `multitier-analog-ensemble` | 同时读取当前 T100/T1000/T2000 的 6/12/24h 相对涨速和跨档位比值，在当时可用历史中找近邻活动；历史活动的“当前进度→最终倍率”形成 point + quantile ensemble |
| `hhwx-instant` | 精确重放 HHWX UI 的约 9m45s 短窗速度线性投影 |
| `hhwx-24h` | 精确重放 HHWX UI 的约 23h55m 日速度线性投影 |
| `rinko-dpra-replay` | 重放 2022 Hoshino `bandori-predict` 保留下来的 Rinko/DPRA rolling-regression + slope/gamma `FIN` 算法 |
| `care-s` | CARE-S v0.3.0：以多档 analog ensemble 为先验，完整叠加条件修正和 OOS 残差；保留用于消融与复现 |
| `care-s2` | CARE-S2：继续使用相同特征，但只用更早 OOS 预测自动选择条件修正强度 λ 与残差扩散 τ；保留作为条件修正消融 |
| `causal-stack` | 用当前任务之前的历史 hindcast 学习 analog / Bestdori 的 L1 最优融合权重，并只用更早 OOS WIS 在 0.5–2.0 间选择 analog ensemble 的 spread；不读取当前活动终值 |
| `bestdori-recalibrated` | 旧 Pilot 的固定校准期公式家族，仅 protocol-v1 |

`bestdori-hierarchical` 与 `bestdori-recalibrated` 都**不是 Bestdori 当年的实际预测档案，也不等同于当前 Bestdori 线上模型**。前者只复用公开公式思想，并在每个 hindcast 时点从当时已有历史重新估 rate；后者保留用于复现 Pilot。

`multitier-analog-ensemble` 是后续 WNC-style 联合模型之前的统计探针：它不训练神经网络，不使用当前活动真值；只测试“多档联合状态 + 历史轨迹形状 + ensemble”本身是否能超过现有单档基线。

HHWX 的两条投影按其公开源码窗口和线性外推公式重放，因此在相同 raw prefix 下可复现；HHWX 页面另有的 “Bestdori prediction” 属于 Bestdori 公式家族，不重复列为独立模型。`rinko-dpra-replay` 来自公开保留下来的旧 Rinko 算法代码。茨菇第一预测线依赖 Bestdori `rates.json` 的历史状态，而旧 rate 快照未被可靠归档，因此暂不把“今天用当前 rate 重算过去”冒充历史平台预测；MYCX 的 JP/CN 适配留到后续模型阶段。

CARE-S / CARE-S2 与 causal-stack 的实现与训练边界见 [docs/CARE.md](docs/CARE.md)。`freeze-walkforward --tiers ...` 可以从一个采集了更多档位的数据集冻结公共子面板；这对 CN 很重要，因为 T1500 历史覆盖远少于 T500/T1000/T2000。显式日历使用 `bandoribench-calendar-v1`。CN 2019–2026 快照由仓库内置的国务院办公厅公告规则离线生成；`known_at` 控制公告可见时间，修订项再用 `previous_type` + `previous_known_at` 表示修订前已知安排，避免任何一层公告回灌到更早 hindcast。实现、来源和当前进度见 [Calendar provider](docs/CALENDAR_PROVIDER.md) 与 [Calendar progress](docs/CALENDAR_PROGRESS.md)。

外部算法只需读取 `public/tasks.json` 并按 [提交协议](docs/SCORING.md) 写出 JSON，然后使用同一个 `score` 命令。不限定 Python、神经网络或统计模型。

## 外部训练模型接口

v0.3.5 推荐训练型模型走 [Model API](docs/MODEL_API.md)，而不是直接读取整个 `reference_events`。评测器会启动一个长期驻留 runner，只在活动预测全部完成后才通过 `observe_event` 交付该活动真值；`forecast_panel` 只包含当前可见输入。

标准工作流按时间切为 development / selection / final，默认约 70% / 15% / 15%。development 提供多个连续时间块与累计 checkpoint 供调参；selection 默认隐藏 case-level loss；final 进一步只保留粗粒度结果，作为一次性尾部 holdout。由于 benchmark 在本地，这些是防止意外泄漏和规范实验流程的机制，不是对抗性沙箱。

常用入口：

```bash
python bandoribench.py model-plan runs/cn-core-v1
python bandoribench.py model-export-training runs/cn-core-v1 --phase final --out data/model-final-train.json
python bandoribench.py model-eval runs/cn-core-v1 --phase development --track point --out runs/model-dev.json -- python my_model.py checkpoint.bin
```

Python runner 可直接使用 `bandoribench_model.serve()`；仓库的 `examples/persistence_model.py` 是最小可运行示例。

## 防止漂亮但无效的成绩

- 不接受错误 benchmark ID、重复/陌生 case、非有限或负预测；概率赛道要求完整、单调的七个分位数。
- 一个必测 case 缺交或无效，就输出 `score: null` 和失败清单，不按剩余简单题算正式分数。
- 各活动等权、提前量分层，不让高频采样占据额外权重；置信区间按整场活动重采样，不把采样点当独立活动。
- 软件测试包含未来数据扰动、终值隔离、冻结校验、国服时间乱序、奖励边界、Bestdori 实际时间字段、`endAt` 收官终值锚定、收官观测冲突拒绝、缺失记录、WIS 及端到端运行。

**边界**：当前就是可信算法的离线 hindcast benchmark，不设计对抗性反作弊。v2 的公开 bundle 为了避免重复巨大历史数据，会保存一份 `reference_events`，每题用 `history_event_ids` 明确规定算法在该 hindcast 中允许使用哪些既往活动；内置基线严格按此列表计算。历史记录没有 `available_at` 时仍标为 `observed_at_only`，不宣称重现当时接口延迟。

## 文件

`bandoribench.py` 是采集、冻结、基线及评分入口；`tests/` 为离线测试；`docs/SCORING.md` 定义评分和提交协议；`docs/DATA.md` 定义数据与来源；`.github/workflows/ci.yml` 仅做离线测试和合成 smoke，不定时采集、不生成真实成绩。

实现和数据协议尚处于早期版本。原始第三方数据、临时运行文件和裁判真值默认不提交 Git。公开接口可读取不等于获得任意再分发许可；发布真实数据版本前需确认来源条款。项目许可证尚未指定。
