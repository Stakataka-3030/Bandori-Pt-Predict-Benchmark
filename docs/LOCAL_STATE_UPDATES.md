# Tsukushi 本地状态与自动更新

Tsukushi v0.3.12 将随程序提供的拟合文件当作只读种子。首次启动时，程序把种子复制到用户目录，随后始终从用户目录读取当前状态。Windows 默认路径为 `%LOCALAPPDATA%\Tsukushi\model\`：

- `tsukushi-seed-state.json`：不可变的初始拟合状态，供重建和恢复使用。
- `tsukushi-training.json`：后续已完成活动的衍生训练贡献、公开终值与来源摘要；不保存原始 tracker 归档。
- `tsukushi-state.json`：从种子及训练贡献重建的当前拟合状态。损坏时可从前两份文件重建。
- `training.log`：自动更新失败时的本机诊断日志。

同版本 GitHub Release 也单独提供 `Tsukushi-Seed-State-vX.Y.Z.json`，供无 Windows exe 的定时环境初始化；它是由可信历史导出的拟合参数，不是原始历史数据包。

程序启动后立即异步检查一次，保持打开时每小时再检查一次；更新不阻塞已有模型预测。只选择按国服实际开场时间排序、严格晚于当前训练截止时间的活动。活动须已结束，`aggregateEndAt` 须已过至少 5 分钟。四个档位都必须有赛中轨迹，以及 `endAt` 之后、`aggregateEndAt` 之前一致且不倒退的 Bestdori 收官观测。缺失、冲突、来源错误或网络失败时不猜终值、不跳过这场去训练更晚活动，旧状态照常可用。

每场完成活动先对**旧状态**计算 Kaori 12 小时校准样本和 Aoi 轨迹模板，再把该活动的 tail、倍率、校准与轨迹贡献写入训练文件。当前拟合从种子和新增贡献重建、校验哈希后原子替换。训练文件先提交；如果中途断电，下一次启动可用它重建状态。这个过程是当前经验模型的增量再拟合，不涉及神经网络搜索，也不更改冻结的 benchmark 或已发布的历史预报。

可手动运行一次同步，或由未来的 GitHub Actions 调用：

```powershell
python examples/local_app/app.py --sync-state --seed <种子状态.json> --state <可写目录\tsukushi-state.json>
```

命令以 JSON 返回 `updated`、`up_to_date` 或 `pending_final`，并报告训练截止时间与拟合哈希。桌面版会在界面底部显示更新状态。预测选择 HHWX 档线来源时，训练样本仍统一来自 Bestdori 的可核验收官 tracker，不混用来源。

初始种子状态已经训练至国服活动 323。回放审计中，用截至 322 的种子和公开 323 收官 tracker 自动更新后，得到的 `fit_sha256` 与从完整可信历史重新导出的 323 状态完全一致。该审计的原始资料只留在本地，不提交到仓库。
