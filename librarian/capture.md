# 收下一条捕获

目标：把这条捕获对应到原论文，写好快读卡，归入主题。用户此刻多半在手机上，只看你最后一条回复。

## 0. 入库（只在提示词里带着原始内容时做）

如果提示词里直接给了原始内容（来自 iPhone 快捷指令），先入库再处理：

```
./p2b capture add --id <提示词里的 capture_id> --text "<原文>" [--thought "<用户想法>"] --source shortcut
./p2b ingest <capture_id>
```

`capture add` 按 id 幂等，重复执行没有副作用。

## 1. 看清这条捕获

`./p2b show capture <id>`，重点看：

- `status`：`mapped` 已关联论文 / `needs_confirm` 有候选待确认 / `unmapped` 没找到 / `needs_agent` 只有截图 / `failed` 预处理失败（看 `note`）。
- `snapshot_text`：解读正文。`candidates`：规则找到的候选论文，`why` 说明来源。
- `thoughts`：用户随手记下的想法。它就是这次的阅读意图，写快读卡时要回应它。
- 如果附带截图，你能直接看到图片：找出论文标题、作者或 arXiv 号。

## 2. 找到原论文

- 已经 `mapped`：跳过。
- 有候选：对照解读内容判断是哪篇（也可能是合集，涉及多篇）。确定后用 `./p2b link` 关联，`--evidence` 写清依据，例如"解读第二段给出的标题与候选完全一致"。
- 没有候选：从正文或截图里找英文标题、arXiv 号、DOI 或论文 PDF 链接，用 `./p2b find` 检索，再关联。
- 论文不在 arXiv 上（会议论文、期刊、只有项目页）：优先找 DOI 或 PDF 直链（CVF、项目页、出版社开放获取页），用 `./p2b link <capture_id> <PDF 链接>` 关联，系统会下载并抽取全文。
- **拿不准就不要关联**。在回复里列出两三个候选，直接问用户"是哪一篇？"。错配比没配更糟。
- 解读不是讲具体论文（行业新闻、观点、综述盘点）：不关联，回复里说明。
- 关联后 `has_fulltext` 为 false（PDF 下载不到）：不要写快读卡，回复里请用户在网页上点"上传 PDF"。

## 3. 写快读卡（每篇关联的论文一张）

先读原文：`./p2b text <id> --pages 1-4`，再看实验部分对应的页。卡片写给研究者：要具体、带数字、带对比对象，不说空话。

```json
{
  "tldr": "一句话：做了什么、关键结果（≤60 字）",
  "problem": "要解决的问题，为什么现有方法不行",
  "method": "方法核心，2-4 句，保留关键名词",
  "results": [
    {"text": "在 X 任务上成功率 85%，比 OpenVLA 高 20 个点", "quote": "从原文逐字摘的一句话", "page": 7}
  ],
  "resources": {"code": "https://github.com/... 或 null", "project": null, "data": null, "real_robot": true, "platform": "Franka / ALOHA / 未知"},
  "relevance": "和用户的关系：回应用户的想法；它在主题树里的位置；和库里哪几篇接近（用 ./p2b search 查）",
  "takes": [{"capture_id": "<捕获 id>", "take": "这篇解读的核心观点一句话", "consistent": true}]
}
```

- `results` 至少 1 条，最好 2-4 条。`quote` 必须**逐字**来自原文（空白差异无所谓），`page` 写页码。校验会逐条核对，找不到就拒绝。
- `takes`：每条关联到这篇论文的解读，用一句话总结它的观点；解读说法和原文明显不符时，`consistent` 写 `false`。
- 提交：`./p2b card put <arxiv:id> --file /tmp/card.json`。如果已有卡片（`show paper` 里的 `card_rev` > 0），只有新解读带来新观点时才更新，提交时带上 `--base-rev`。

## 4. 归入主题

`./p2b topics` 查看主题树，选最贴切的叶子节点，可以选多个：

```
./p2b topic set <arxiv:id> "VLA/推理加速" "VLA/实时推理"
```

只能判断大类时选父节点（如 "WAM"）；完全不属于任何主题就不设，论文会留在"未归类"。

## 5. 回复用户

三四行以内，手机上一眼能看完：

```
已收下：π0（arXiv 2410.24164）→ VLA/tokenizer
一句话：……
你的想法：……（如果用户记了想法，回应一句）
```

需要用户确认时，只问那一个问题。
