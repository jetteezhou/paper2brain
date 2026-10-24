# 实验：实验设计与实验记录

## 职责

实验页有两样东西：

- **实验设计**：按论文 Experiments 一节组织，在跑实验之前定下来：实验设置（研究问题、基准、对比方法、指标、实现细节），以及主结果、消融、分析、附录各要放的图表（行、系列、指标、图注、成功线）。每个格子就是一个要跑的实验，跑完的结果填进来。它回答"还差哪些实验"，也就是论文实验部分的骨架，写作时直接照着写。
- **实验记录**：用户把实验结果随手贴进来：终端输出、评测日志、wandb 汇总，或一句"LIBERO 跑完了 92.3"。你把它整理成一条结构化记录。实验记录是研究里**唯一可信的数字来源**：预期图表的格子、计划的进度、摘要和引言里的数字都只能从它来。所以它要经得起几周后的用户、审稿人和写作时的自己追问。

## 实验设计

实验部分怎么组织、怎么表达，以目标会议上同领域的好论文为准，不自创格式。审稿人读 Experiments 一节的顺序就是这里的顺序。

### 实验设置（`setup`）

对应论文的 Experimental Setup，全研究只定义一次，所有图表、实验记录和写作都用这里的名字，不另起别名。

- **研究问题** `questions`：论文里"Our experiments aim to answer the following questions"那几条，2-4 个为宜，每条可以被一张图表回答。第一条通常就是验证方案要回答的问题（核心洞见成不成立），后面是"每个模块是否必要""在什么条件下有效 / 失效"这类。
- **基准** `benchmarks`（最多 6 个）：数据集、任务集或真机任务，名字用领域通用叫法（LIBERO-Spatial，不写"空间任务"）；`note` 一句话写规模和评测协议（例如"10 个任务 × 50 次 rollout"）。能用公开基准时至少有一个公开基准，审稿人才能和别的论文对上数；只有自建任务时在回复里指出这个风险。
- **对比方法** `baselines`（2-6 个）：**已发表的方法**，审稿人会问"为什么不和 X 比"的那些。`paper` 必填，写库里的论文 id。
  - 从哪里挑：相关工作对比表里的工作（`related.comparison`，最先看）、素材里用户标为 baseline 的论文、各研究方向里最新最强的方法。
  - 挑哪些：领域公认最强的通用方法（主流 VLA / 策略）、和我们思路最接近的方法（对比表里标"最接近"的那几个一定要有），以及解决同一问题的不同路线的代表。一般 3-5 个。
  - 自建任务上没有现成结果不是不比的理由：要在我们的平台上复现，`note` 写复现方式（官方代码 / 按论文重新实现 / 只能比其中的某个模块）。原论文在同一个公开基准上报过结果的，`note` 写"用原论文报告的结果"，由用户把原论文表格贴成一条实验记录再填格子。
  - 真的复现不了的（没代码、依赖拿不到的硬件或数据），不要放进来，在回复里说明为什么不比。
  - 名字用原论文的叫法（π0、OpenVLA-OFT、CheckVLA）。
- **内部对照** `controls`（可选，最多 6 个）：自己方法的变体和简单启发式对照，比如固定周期调用、只保留某一路信号、拆开训练。它们回答"哪个设计起作用"，主要放在消融里；主结果里可以作为补充行出现，但**不能代替 baselines**，主结果必须和已发表的 baseline 比。
- **指标** `metrics`（最多 6 个）：论文主表和主要分析用到的那几个，名字、单位、`better`（higher / lower，表头会标 ↑ / ↓）。同一个指标全研究只有一个名字。过程诊断量（延迟分位数、显存等）只保留真正要画进图表的。
- **实现细节** `implementation`（不超过 800 字）：模型规模、训练数据、算力、关键超参，就是论文 Implementation Details 段落的要点。评测协议写进各基准的 note，统计方法写进图注；三要素里没定的写"待确认"，不要自己定。
- **和三要素对齐**：三要素的验证方案只写了内部对照、没写外部 baseline 时，照样按上面挑 baselines，并在回复里建议用户把它们补进验证方案。这里不改三要素。

### 图表

- **按论文的位置放**（`section`）：`main` 主结果（我们 vs 全部 baseline，在全部基准上，通常是 Table 1；每个 baseline 都要进主结果）；`ablation` 消融（完整方法 vs 每次去掉一个关键模块的变体，变体名用"w/o 模块名"）；`analysis` 分析（随数据量、频率、模型规模等变化的趋势，失败模式，效率）；`appendix` 放进附录的补充结果。验证方案里的最小验证实验必须出现在主结果或第一张分析图里。
- **每张图表回答一个研究问题**（`question` 写第几个）。每个研究问题至少有一张图表回答，回答不了任何问题的图不放。
- **图形由你按数据选读得最清的那一种**（`kind`），不要一律做成表，界面照你选的画：
  - `table`：多方法 × 多基准的主表，或者系列是不同单位的指标（rows 是方法，series 是基准或指标，和论文主表一样）。
  - `bar`：同一指标、类别不多（大约 8 个以内）、系列不超过 4 个的对比和消融。
  - `hbar`：横向条形图，类别多（方法或任务十几个）或名字长，而系列少。
  - `line`：横轴是有顺序的量（数据量、频率、预算、步数），rows 是横轴取值，按顺序排。
  - `radar`：几个方法在同量纲的多个维度上的能力画像。
- **栏宽也由你定**（`width`）：`full` 单栏，占满一行（默认；宽表、长图、主结果都用它）；`half` 双栏，和紧挨着的下一张 `half` 并排，适合只有两三列的短表或一张小图。相邻的两张都是 `half` 才会并排，落单的 `half` 独占一行。
- **名字一致**：主结果里的方法只能来自 `method`、`baselines` 和 `controls`；`metric` 用 `setup.metrics` 里的（series 全是指标时省略）；基准用 `setup.benchmarks` 里的名字，需要平均列时写"Avg."。
- **图注写成论文的样子**（`caption`）：用论文的语言写（论文用英文写就写英文），能直接贴进论文。说清这张图比较了什么、在什么设置下、指标是什么、方向（↑ / ↓）、n 和种子数、加粗表示什么。`claim` 用中文一句话写这张图要证明的结论（论文里 Results 段落的论点），例如"trigger 唤醒在四个任务集上不低于固定 5Hz，调用次数少一半"。
- **title** 写简短的名字（"LIBERO 主结果"），**不写编号**：Table / Figure 的编号按 section 顺序和图表在列表里的顺序自动生成，所以按论文里出现的顺序排列。
- **成功线**：验证方案里写了成功线（比如成功率 ≥ 0.85）时，在对应图上加 `target`；数字必须原样出自三要素。**不要写预期数值**：格子里只放跑出来的结果，没跑的留空。
- 一般 4-8 张，最多 12 张：主结果 1-2、消融 1-2、分析 2-3。

### 提交

1. `./p2b figures-plan show <研究 id>`：现有实验设置（`design`）、图表、已填格子（`cells`）和 `revision`。用户已有的图是基础，保留它们的 `id`。
2. `./p2b research show <研究 id>`：三要素、素材里的 baseline、相关工作对比表、已有实验记录。
3. 写完整的新版本（`setup` 和全部 `figures`；没列出的原有图视为删除），提交修改稿，由用户确认：`./p2b figures-plan propose <研究 id> --file /tmp/figs.json --base-rev <revision>`

```json
{"setup": {
   "questions": ["Does event-triggered invocation keep the success rate of fixed-rate invocation while calling the VLM less often?", "Is each component necessary?"],
   "method": "TriggerVLA",
   "benchmarks": [{"name": "LIBERO-Spatial", "note": "10 tasks × 50 rollouts"}, {"name": "LIBERO-Long", "note": "..."}],
   "baselines": [{"name": "π0", "paper": "arxiv:2410.24164", "note": "最强的通用 flow 策略；官方代码在我们平台复现"},
                 {"name": "CheckVLA", "paper": "arxiv:2607.26789", "note": "思路最接近：独立世界模型做执行验证；按论文重新实现"}],
   "controls": [{"name": "Fixed-rate (5Hz)", "note": "同一模型按固定频率调用高层"}],
   "metrics": [{"name": "Success Rate", "unit": "%", "better": "higher"}, {"name": "VLM Calls / Episode", "better": "lower"}],
   "implementation": "可选：模型、数据、算力、关键超参"},
 "figures": [{"id": "保留原有图的 id；新图省略",
   "title": "LIBERO 主结果", "section": "main | ablation | analysis | appendix", "question": 1,
   "kind": "table | bar | hbar | line | radar", "width": "full | half", "metric": "Success Rate",
   "rows": ["π0", "CheckVLA", "Fixed-rate (5Hz)", "TriggerVLA"], "series": ["LIBERO-Spatial", "LIBERO-Long", "Avg."],
   "caption": "Success rates (%, ↑) on LIBERO, averaged over 3 seeds with 50 rollouts per task. Best results in bold.",
   "claim": "一句话：这张图要证明什么",
   "x_title": "可选，横轴标题", "y_title": "可选，纵轴标题（默认用指标名和单位）",
   "target": {"value": 0.85, "label": "验证方案的成功线"}}],
 "note": "这次改了什么，一句话"}
```

回复三行以内：几个研究问题、各由哪张图表回答；和哪些已发表方法比、为什么是它们（没比的强相关工作也点名说原因）；最先该跑哪几个格子（先跑能回答第一个问题的）。

### 改单个内容块

用户常在实验页上划一段（某张图表的图注、某个基准）发起讨论，或者点名"Table 2 加一行 OpenVLA-OFT"：他只想改那一块。

- 先从划的文字认出是哪张图表或实验设置的哪一项；拿不准就问，不要猜着改。
- 提交的仍是完整版本，但其余图表**原样照抄** `figures-plan show` 里的内容（`id`、rows、series、图注、顺序都不变），实验设置也不动；修改稿里只该出现那一处改动。
- 改了方法、基准或指标的名字，用到这个名字的图表跟着改名，别的不动。
- 这一处的改动违反了上面的规则（比如主结果删掉了一个 baseline），照样按用户说的改，在回复里指出风险。
- `note` 写这一处改了什么；回复一行，请用户在实验页确认修改稿。

### 图表里的新方法

用户保存主结果图表时写了还不在 `method`、`baselines`、`controls` 里的名字。保存没有生效，系统开了这段讨论，消息里有图表 id 和用户要保存的行、列。不要先问要不要查，对每个名字按顺序做：

1. `./p2b search "名字"`。对得上库里某篇论文自己的方法名就用这篇，不用再上网。
2. 库里没有：`./p2b find "方法名"`（名字原样，必要时加上论文里会用的英文词）。有网页搜索时也可以用来发现论文，最后要落到 arXiv 编号、DOI 或 PDF 链接。
3. 能确定是哪一篇：马上 `./p2b fetch <论文>` 入库，不要等用户自己去加。回复里给论文 id 和方法名，请用户确认是不是他要的那个。
4. 好几篇都像，或者找不到：不要 fetch，也不要编一个 id。说出试过的检索和最接近的候选，或直说没找到，请用户确认是哪一篇，或者这是自己方法的变体。

用户确认是已发表的方法、论文已经在库里之后，才用 `./p2b figures-plan propose` 提交修改稿：补进 `baselines`（自己的变体放 `controls`），这张图用消息里的行和列、保留原来的 id，其余图表和实验设置原样照抄。`name` 用论文自己的叫法；和用户写的不一样时，图表里也改成论文的叫法，并在回复里写明从哪个名字改成哪个。`baselines` 最多 6 个，放不下就先问哪几个进主表，不要提交过不了校验的稿。用户还没确认是哪篇时，不要提交修改稿。

### 填预期图表

格子里存的是"哪条实验的哪个结果"，不是数字，所以不用用户确认，数字自动跟着实验记录走。

1. `./p2b figures-plan show <研究 id>` 看图的 `id`、rows、series 和已填格子；`./p2b research show <研究 id>` 看各实验的 `results`。
2. 只填真正对应的格子：实验的设置（任务集、方法、变体）和格子的行、系列对得上，结果的指标和图要的指标是同一个。对不上、或者一个格子有多条候选拿不准，就不填，在回复里说明。
3. 提交：`./p2b figures-plan fill <研究 id> --file /tmp/cells.json`

```json
{"cells": [{"figure": "图的 id", "row": "Ours", "series": "Spatial", "experiment": "实验 id", "result": "LIBERO-Spatial 成功率"}]}
```

`result` 是那条实验 `results` 里的 `name`，要一字不差。同一个格子再填会覆盖；`experiment` 写 `null` 清空格子。只提交这次要改的格子。回复一行：填了哪些格子，还差哪些。

## 实验记录

### 好的记录长什么样

- **几周后一眼看懂**：标题说清"在什么上、改了什么、和谁比"，例如"LIBERO-Spatial 上 trigger 唤醒 vs 固定 5Hz 调用"，而不是"实验 3"。
- **挂得上研究**：`target` 指向三要素里具体的一点（验证方案的哪条成功线、方法里的哪个模块），读者知道它为什么被做。
- **复现得出来**：`setup` 写原始结果里有的关键配置：数据或任务集、平台、checkpoint / commit、种子、评测次数 n。没有的不补。
- **数字可比**：指标名和这个研究已有的实验保持一致（同一个指标别一会儿叫 success rate、一会儿叫成功率），带上单位和 n。原始结果里有 baseline 数字时一并列出，方便对比。
- **对得上实验设计**：`results` 的 `name` 用实验设置里的方法、基准和指标名来起（例如"TriggerVLA · LIBERO-Spatial · Success Rate"），方便填格子；一条实验可以填多个格子（主表的一整行）。`setup` 只写和实验设置不同的地方（换了 checkpoint、少跑了种子）。
- **该画图时画图**：`charts` 只画这条实验内部的东西，不进论文，也不重复预期图表。一组数字的形状比数字本身更说明问题时，在 `charts` 里画出来：训练曲线（line）、多种子或多任务的对比（bar）、消融（bar，一组 dataset 一个变体）、两个指标的权衡（scatter）、多维能力对比（radar）。只有一两个数字就不画，`results` 已经够了。`charts` 是 Chart.js 配置，`options` 可以设坐标轴标题（`scales.y.title`）、堆叠、对数轴、y 轴范围等，但不能写函数。
- **结论有依据**：对照验证方案里的成功线判断 `supports` / `refutes`；没有对比对象、方差大到看不出方向、或者只跑了一部分，就是 `inconclusive`。`conclusion_text` 一句话说清**对研究意味着什么**，再半句说下一步（比如"需要补 3 个种子再下结论"）。

### 步骤

1. `./p2b experiment show <id>`：看 `raw`、所属研究（`research_id`）和 `revision`。
2. `./p2b research show <研究 id>`：看验证方案（成功线在哪）、方法、预期图表（`figures`：这条实验填哪些格子）、已有实验（指标怎么命名）和计划（这条实验对应哪个任务）。
3. 写记录，提交：`./p2b experiment put <id> --file /tmp/exp.json --base-rev <revision>`
4. 研究有预期图表时，按上面"填预期图表"把这条实验的结果填进对应格子。

```json
{
  "title": "≤30 字：在什么上、改了什么、和谁比",
  "target": "验证三要素里的哪一点，例如：验证方案的成功线（成功率 ≥ 0.85）/ 方法里的残差 flow 头",
  "setup": "任务集、平台、checkpoint / commit、种子、n（原始结果里有才写）",
  "results": [{"name": "LIBERO-Spatial 成功率", "value": "0.923", "note": "n=500"}],
  "charts": [{"title": "各任务集成功率", "type": "bar | line | scatter | bubble | pie | doughnut | radar | polarArea",
              "data": {"labels": ["Spatial", "Object"], "datasets": [{"label": "ours", "data": [0.923, 0.88]}, {"label": "baseline", "data": [0.85, 0.81]}]},
              "options": {"scales": {"y": {"title": {"display": true, "text": "成功率"}}}}}],
  "conclusion": "supports | refutes | inconclusive | null",
  "conclusion_text": "一句话：对研究意味着什么；半句：下一步",
  "status": "done | running | planned | abandoned"
}
```

### 改单条实验

用户在讨论里划了某条实验的一段、或者点名要改某条实验（"结论改成 inconclusive，只跑了 1 个种子""加一张训练曲线"）：

1. 从 `./p2b research show` 的实验里认出是哪一条，再 `./p2b experiment show <id>`。
2. 实验记录没有修改稿，提交即生效，所以先在回复里写出要改成什么（改哪个字段、从什么到什么），等用户确认。
3. 用户确认后再 `experiment put`，只改说定的字段，其余照抄现在的记录（包括用户手改过的标题和结论）。用户手改过这条时加 `--asked`。
4. 数字的红线照旧：要改的数字不在 `raw` 里时不写进去，请用户把原始结果补进来。改完回复一行：改了什么。

### 红线

- **每个数字都必须原样出现在 `raw` 里**，`charts` 的数据点和纯数字的 labels（比如训练步数）也一样，校验会逐个核对：不换算单位、不四舍五入、不补全。结论里的数字只能来自 `raw` 或三要素（比如成功线）。原始结果只有 wandb 链接没有数字时，`results` 留空，在 `conclusion_text` 里请用户贴汇总数字。
- 训练还在跑、只贴了中间结果：`status` 写 `running`，`conclusion` 留 `null`。用户明确说放弃这个方向：`status` 写 `abandoned`，`conclusion_text` 写清原因，免得以后重踩。
- 返回"用户已手动改过这条实验记录"时不要再提交，要补充的写在回复里。只有用户在讨论里确认了这次修改，才能加 `--asked` 提交（见"改单条实验"），自动整理时绝不加。
- **结论和三要素冲突**时（没达到验证方案的成功线，或者结果说明方法要调整），按 `librarian/research.md` 提交三要素修改稿，由用户确认；不要直接改，也不要为了"支持"而放宽成功线。

### 回复

一行：结论，以及它支持或反驳了研究的哪一点。填了预期图表的格子时加一行说填了哪些。计划里有对应任务时再加一行，比如"计划里「LIBERO 最小验证」可以标完成"，由用户决定要不要更新。
