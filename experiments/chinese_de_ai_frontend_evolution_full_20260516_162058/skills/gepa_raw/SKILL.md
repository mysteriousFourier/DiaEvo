---
name: "c02-evolved-工单-队列"
description: "Evolved trace-grounded workflow for tasks similar to: 中文非专业用户要求客服物流工单网页去 AI 味：我想做一个网页，给我们客服主管每天早上看物流工单情况用。她不懂技术，只想一眼知道今天哪些客户比较急、谁负责、有没有快超时的单子、整体忙不忙。页面要看起来专业一点，不要像那种 AI 生成的炫酷模"
tags: ["-", "工单", "队列", "sla", "时间", "chat_completion", "write_html", "edge_headless_screenshot", "evolved", "candidate"]
source_cluster: "C02"
risk_score: 0.61
status: candidate
---

# c02-evolved-工单-队列

## When To Use

Use this skill when a task is similar to `中文非专业用户要求客服物流工单网页去 AI 味：我想做一个网页，给我们客服主管每天早上看物流工单情况用。她不懂技术，只想一眼知道今天哪些客户比较急、谁负责、有没有快超时的单子、整体忙不忙。页面要看起来专业一点，不要像那种 AI 生成的炫酷模板，别一堆紫色渐变、大圆角卡片和空话。最好电脑上能看，手机上临时打开也别乱掉。你帮我做一个可以展示的版本，里面可以用模拟数据，但别太假。 反馈：这个太像 AI 模板了，紫色渐变太重。

参考原则：# 参考设计原则提炼

## 一、实时队列监控原则

1. **按渠道拆分队列视图**
   - 将工单队列按邮件、消息、语音等渠道独立展示，每个渠道的实时积压数量必须单独呈现，便于识别不同入口的负载差异。
   - 语音工单仅在座席接听前显示于队列中，转派后应立即从当前队列移除，避免重复计数。

2. **SLA 状态三级分层可视化**
   - 对队列中的工单按 SLA 状态强制分为三类：已违约、即将违约、在 SLA 内。使用颜色编码（如红、黄、绿）进行区分，确保风险等级一目了然。
   - 支持按渠道维度进一步拆分 SLA 状态，使管理者能定位特定渠道的 SLA 压力点。

3. **队列活动历史趋势对比**
   - 提供“进入队列”、“离开队列”、“滞留队列”三个维度的历史数据，按时间桶聚合，支持钻取查看具体工单明细。
   - 历史趋势图应支持按渠道独立切换，帮助发现周期性积压规律。

4. **座席容量与队列等待时间关联展示**
   - 在队列视图中同时显示座席可用数量、座席容量上限、队列中最长等待时间和平均等待时间，形成“供需”对照。
   - 等待时间指标需实时更新，避免使用缓存数据造成决策延迟。

## 二、积压健康与工作负载原则

5. **创建与解决趋势对比**
   - 使用折线图展示近几小时内工单创建量与解决量的动态对比。创建量持续高于解决量时，应触发视觉警告（如区域高亮或阈值线），提示“正在累积债务”。

6. **积压工单按状态拆解**
   - 将积压工单按“待处理”、“处理中”、“暂停”、“等待请求者”等状态分类展示，揭示积压的真实原因：是团队阻塞、外部依赖还是流程停滞。
   - 状态分类应使用堆叠条形图或分组指标卡，避免单一总数掩盖结构性问题。

7. **座席负载与风险一体化视图**
   - 以表格形式列出每位座席的“未解决工单数”、“未回复未解决数”、“活跃 SLA 工单数”三列，将负载量与响应风险并排呈现。
   - 表格支持按任意列排序，使团队负责人能快速定位负载不均或 SLA 风险集中的座席。

8. **未分配工单作为独立预警指标**
   - 将“未分配工单”数量作为独立 KPI 卡片展示，并置于显眼位置。该指标通常代表可最快解决的积压，数值异常时应立即引起注意。

## 三、需求分析与上游改进原则

9. **请求类别排名展示**
   - 使用条形图展示当前工单的需求类别分布（如权限申请、硬件故障、账号问题等），按数量降序排列。
   - 类别排名需支持时间段切换，帮助识别可通过自助服务或自动化削减的重复需求。

10. **首次响应时间作为体验核心指标**
    - 将“首次响应时间”作为独立 KPI 展示，与 SLA 违约指标并列。该指标直接关联员工或客户的实际感知，不应隐藏在二级页面。

## 四、操作节奏与时间锚点原则

11. **按日间节奏设计检查锚点**
    - 仪表盘信息架构应支持三个关键时间节点的快速判断：早间启动（是否已落后）、午间漂移检查（积压是否蔓延）、晚间交接（哪些工单不可等待）。
    - 对应地，关键指标（如 SLA 风险、未分配数、积压趋势）应固定在首屏，无需滚动即可完成三轮检查。

12. **筛选条件持久化与上下文保持**
    - 仪表盘的筛选条件（品牌、组别、渠道类型、标签等）应支持保存为预设视图，避免每次访问重复配置。
    - 钻取进入工单明细后，返回时应保持之前的筛选状态和时间范围。

## 五、信息密度与布局原则

13. **首屏完成核心判断**
    - 将队列健康、SLA 风险、座席负载三类信息压缩在单屏内，避免纵向滚动才能完成全局状态评估。
    - 使用指标卡承载绝对值，使用迷你图表承载趋势，两者组合形成“数值+方向”的完整信息单元。

14. **表格横向滚动时固定关键列**
    - 当工单列表或座席表格列数较多需要横向滚动时，工单 ID 或座席姓名等标识列应固定于左侧，确保上下文不丢失。

15. **实时数据与历史数据分区放置**
    - 实时指标（当前队列数、SLA 状态）置于页面上方或左侧，历史趋势图表置于下方或右侧，形成“现状→趋势”的自然阅读流。`.
The task should share at least two mined signals from the trigger list before this skill is applied.
Keep the skill as a generated draft until verification, validation, and human promotion approval all pass.

## Trigger Signals

Task terms:
- `-`
- `工单`
- `队列`
- `sla`
- `时间`
- `座席`
- `指标`

Files or extensions:
- `.html`
- `.md`
- `.png`

Tools and failures:
- `chat_completion`
- `write_html`
- `edge_headless_screenshot`
- `rubric_evaluation`
- `glm_vision_evaluation`
- `紫色渐变过度`
- `大圆角卡片堆叠`
- `空泛营销文案`
- `营销页气质`
- `参考吸收不足`
- `紫色渐变过度`

## Mined Evidence

- Source cluster: `C02`
- Trace ids: `TR-DEAI-001, TR-DEAI-002, TR-DEAI-004, TR-DEAI-006, TR-DEAI-008`
- Cluster size: `5`
- Failure rate: `1.00`
- Coverage gap: `0.72`
- Tool success rate: `1.00`
- `coverage_gap` score `0.72`: High failure, retry, or no-skill rate indicates work that is not well covered by existing skills.
- `failure_hotspot` score `1.00`: Repeated failures or tool errors suggest a workflow that needs explicit recovery guidance.
- `high_reuse_path` score `1.00`: A recurring tool sequence can be captured as a reusable operational path.

## Operating Steps

1. Inspect only the files and commands that match the mined trigger signals.
2. Reproduce the smallest failing or repeated workflow before making changes.
3. Prefer the mined tool path before adding new tools: `chat_completion` -> `write_html` -> `edge_headless_screenshot` -> `rubric_evaluation` -> `glm_vision_evaluation`.
4. If the task shows `紫色渐变过度`, `大圆角卡片堆叠`, `空泛营销文案`, `营销页气质`, `参考吸收不足`, capture that failure before editing.
5. Make the narrowest workspace-scoped change or recommendation that addresses the evidence.
6. Run the closest safe validation and record the result for the next feedback cycle.

## Failure Fallbacks

- If validation fails, capture the command, exit code, and failure category before retrying.
- If the same validation fails twice, stop broadening the change and summarize the unresolved failure.
- If a tool or command requires approval, stop at preview and request explicit human approval.
- If the task drifts outside the mined evidence, fall back to the closest installed skill or ask for review.

## Verification Suggestions

- Run `DiaEvo verify --skill <candidate-dir>` before queueing promotion.
- Run `DiaEvo validate --skill <candidate-dir> --approve` only after reviewing `validation.json`.
- Compare the candidate against existing registry skills and merge or specialize if it is near-duplicate.
- After use, run `DiaEvo feedback` so tool events become future mining evidence.

## Safety Constraints

- Keep all file reads and writes inside the active workspace.
- Do not install dependencies, use network access, or run shell commands without explicit approval.
- Do not include real credentials, tokens, passwords, or private project secrets.
- Do not auto-promote or auto-install this generated candidate.
