# 整理一条实验

用户把实验结果随手贴进来：终端输出、评测日志、wandb 的汇总、一句话"LIBERO 跑完了 92.3"。你把它整理成结构化记录，让几周后的用户一眼看懂：这条实验验证了研究的哪一点，结论是什么。

## 步骤

1. `./p2b experiment show <id>`：看 `raw`（原始结果），以及它属于哪个研究（`research_id`）。
2. `./p2b research show <研究 id>`：看三要素，尤其是"验证方案"，弄清这条实验在验证哪一点；也看看已有的实验，保持指标命名一致。
3. 提交：`./p2b experiment put <id> --file /tmp/exp.json --base-rev <revision>`

```json
{
  "title": "≤30 字：做了什么",
  "target": "验证三要素里的哪一点，例如：验证方案第 2 条 / 方法里的残差 flow 头",
  "setup": "配置要点、数据、平台、代码 commit（原始结果里有才写）",
  "results": [{"name": "LIBERO-Spatial 成功率", "value": "0.923", "note": "n=500"}],
  "conclusion": "supports | refutes | inconclusive | null",
  "conclusion_text": "一句话：对研究意味着什么",
  "status": "done | running | planned | abandoned"
}
```

## 规则

- **每个数字都必须原样出现在 `raw` 里**，校验会逐个核对。不要换算单位、不要四舍五入、不要补全原始结果里没有的数字。原始结果里只有 wandb 链接、没有数字时，`results` 留空，在 `conclusion_text` 里请用户贴一下汇总数字。
- 没有对比基线、看不出方向时，`conclusion` 写 `inconclusive`；用户明确说"放弃这个方向"时，`status` 写 `abandoned`，并在 `conclusion_text` 里写清原因。
- 返回"内容已被修改"说明用户刚手动改过，重新 `show` 后在用户的版本上补充，不要覆盖。
- **实验结论和已确立的三要素冲突**时（比如验证方案里写的成功线没达到，或者结果说明方法需要调整），按 `librarian/research.md` 提交一份三要素修改稿，由用户确认。不要直接改。

完成后回复一行：这条实验的结论，以及它支持或反驳了研究的哪一点。
