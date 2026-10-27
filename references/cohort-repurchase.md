# 首购同期群N日复购：固定计算契约 v2

用于首购客户在N个经过日内是否再次有效购买；不是复购次数、滚动期间老客占比、留存或客户未来价值预测。当前只支持全体同期群汇总，不支持渠道归因、分群、同日合单和日历日窗口。客户身份、独立订单和有效购买定义先由实际业务口径确定。

## 数据准备与能力边界

从宿主实际授权结果或用户文件构造JSON，不自行下载明细或绕过权限。需要客户和订单明细；仅有聚合客户数/销售额不能复原本方法输入。先用已有结果，缺什么才补什么。

必填字段：

| 字段 | 语义 |
| --- | --- |
| schema_version | 字符串 `2` |
| source_ref / definition_ref | 实际输入来源/版本及有效购买定义引用；脚本保留引用，真实性由适配环节核验 |
| timezone | IANA业务时区；用于明确业务条件，边界时间须已换为带偏移的实际时刻 |
| cutoff / validity_as_of | 包含该时刻；有效/取消/退款状态按该截止规范化，两个时刻须相同 |
| history_complete | 布尔；核实输入覆盖全部相关客户的有效购买历史才为true。有限时间导出的首笔不等于首购；false返回unsupported |
| window_days | 正整数；每一天为经过的24小时，不支持按当地日历日计算 |
| cohort_start / cohort_end | 首购范围，左闭右开；end不得晚于cutoff；均为带时区偏移ISO-8601时间 |
| bounds_policy | `unverified` 或 `fixed_cohort_irreversible_success`；默认选择需来自业务证据，不能为了得到区间而声明稳定 |
| stability_ref | 仅稳定策略时必填，引用证明同期群身份/首购历史固定、已观测成功不会被未来退款/修订撤销的政策及数据依据；脚本保留引用，不自动认证 |
| orders | 数组，每行仅order_id、customer_id、purchased_at、valid；ID为非空字符串，valid为布尔 |

适配时先统一跨渠道身份和订单粒度，再按确认的退款/取消规则生成valid；脚本不猜这些规则。截止之后的订单不参与结果。相同订单ID的相同规范化记录去重；冲突即报错，不把订单行直接当独立订单。

首购取截止前有效订单最早时刻；有效订单数至少2且第二笔不晚于首购+N×24小时才算复购。同一时刻两个独立有效订单按两笔计算；若业务按同日/支付单合并，应先规范化订单，未确认时不套此定义。

## 执行与消费

固定脚本：[cohort_repurchase.py](../scripts/cohort_repurchase.py)。

在允许执行且具备Python 3.9+及IANA时区数据的环境，从Skill目录运行：

```bash
python3 scripts/cohort_repurchase.py /absolute/path/normalized-orders.json
```

输出到stdout；成功退出0，不支持退出2，输入或文件错误退出1。不要把非零退出时的JSON当成功结果。输入输出JSON不得含NaN或重复字段。脚本不联网、不写源文件，不返回客户级明细。

- counts给出同期群、成熟/未成熟、成熟复购和未成熟已复购人数。
- mature_repeat_rate仅使用成熟客户作分母；分母为0返回null，不说成0%。成熟客户可能偏向更早首购人群，不能假定代表完整同期群。
- mature_repeat_rate和counts均为cutoff有效状态快照下的观察值；观察成熟不等于退款/历史状态最终确认。
- 只有稳定策略及stability_ref齐备才输出full_cohort_rate_bounds：在同期群不变、已观测成功不可撤销的条件下，为已观测复购/总人数到(已观测复购+未成熟未复购)/总人数，bounds_type为conditional_identification_bounds，不是置信区间。
- 未核验稳定性时bounds_policy=unverified，不填stability_ref；full_cohort_rate_bounds=null、bounds_type=not_established，保留当前快照人数和成熟复购率。禁止把这时的已观测成功比例称最终下界。退款、历史补录或首次订单撤销可能改变成功和同期群成员。
- 空同期群下，稳定策略返回上下界均为null；不保证稳定时整个边界字段为null。
- status=unsupported/error时没有可依赖的数值结果。历史是否完整依赖提供方真实确认，不是脚本自动检测能力。
- 输出保留范围、口径引用、输入内容SHA256、去重/排除计数和方法版本；哈希是复现标识，不是隐私匿名化保证。

交付解释需要实际分子分母、窗口和覆盖，应同时说明比率及其计算依据。复购较低本身不能证明发券等行动有效。schema v2输入不能与旧v1混用。脚本执行成功只证明给定输入下完成计算，不证明来源真实性或行动收益。
