"""Plain numeric and readable bulletins from one shared two-mode forecast."""

from datetime import datetime, timedelta, timezone
import math


TIERS = (500, 1000, 1500, 2000)
HOUR = 3600000
CN = timezone(timedelta(hours=8))


def _hour(stamp):
    # Round half up. The inclusive game end 22:59:59 appears as 23:00.
    return ((int(stamp) + HOUR // 2) // HOUR) * HOUR


def _points(values, tier):
    value = float(values[tier] if tier in values else values[str(tier)])
    if not math.isfinite(value) or value < 0:
        raise ValueError("报文含有非法预测值")
    return str(int(math.floor(value + .5)))


def format_bulletins(panel, snapshots):
    if set(snapshots) != {"mashiro", "rui"}:
        raise ValueError("报文必须包含 Mashiro 和 Rui")
    issued = int(snapshots["mashiro"]["issued_at"])
    end = int(snapshots["mashiro"]["end_at"])
    for snapshot in snapshots.values():
        if int(snapshot["issued_at"]) != issued or int(snapshot["end_at"]) != end:
            raise ValueError("两种逻辑的报文起报时刻不一致")
    issue_hour, end_hour = _hour(issued), _hour(end)
    horizon = max(0, (end_hour - issue_hour) // HOUR)
    event_id = int(panel["event_id"])
    server = panel.get("server", "cn").upper()
    if not server.isascii() or not server.isalnum():
        raise ValueError("服务器代码只能含 ASCII 字母和数字")
    clamped = any(snapshot.get("rank_order_adjustment", {}).get("t1500_clamped", False)
                  for snapshot in snapshots.values())
    utc = lambda stamp: datetime.fromtimestamp(stamp / 1000, timezone.utc).strftime("%Y%m%d%H")
    def beijing(stamp):
        date = datetime.fromtimestamp(stamp / 1000, CN)
        # Windows strftime can reject non-ASCII literals under an English locale.
        return f"{date.year:04d}年{date.month:02d}月{date.day:02d}日{date.hour:02d}时"
    numeric = ["STSTSTST", f"{event_id}{server}", utc(issue_hour), utc(end_hour), str(horizon)]
    readable = [f"{event_id}-{panel.get('event_name') or f'活动{event_id}'}-{panel.get('server_name', '国服')}",
                f"起报时间：{beijing(issue_hour)}", f"截活时间：{beijing(end_hour)}",
                f"时效：{horizon}小时", "====报文===="]
    for mode, code, heading in (("mashiro", "MAS", "MASHIRO（重建初始场模型）："),
                                ("rui", "RUI", "RUI（剪枝模型）：")):
        snapshot = snapshots[mode]
        numeric.append(code + "KAORI")
        numeric.extend(_points(snapshot["control"], tier) for tier in TIERS)
        numeric.append(code + "AOI")
        numeric.extend(_points(snapshot["member_p10"], tier) + "|" +
                       _points(snapshot["member_p90"], tier) for tier in TIERS)
        readable.extend(["", heading, "===控制==="])
        readable.extend(f"T{tier}：{_points(snapshot['control'], tier)}" for tier in TIERS)
        readable.append("===集系===")
        for tier in TIERS:
            readable.extend([f"T{tier}：", f"10%：{_points(snapshot['member_p10'], tier)}",
                             f"90%：{_points(snapshot['member_p90'], tier)}"])
    if clamped:
        numeric.append("CLAMPED")
        readable.extend(["", "模型数值已Clamp"])
    numeric.append("EDEDEDED")
    return {"numeric": "\n".join(numeric) + "\n",
            "readable": "\n".join(readable) + "\n",
            "clamped": clamped, "horizon_hours": horizon}
