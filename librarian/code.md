# 读代码：方法—代码对照

研究关联了代码仓库（`./p2b research show <id>` 里的 `repos`）。仓库是**只读**克隆：只能读，**绝不修改、提交或推送**，也不要在仓库目录里运行会写文件的命令（安装依赖、跑训练、格式化代码都不行）。

## 方法—代码对照

目标：让用户看清论文里每个关键组件在代码里是怎么实现的，尤其是**论文没写、但决定能不能复现的细节**：归一化方式、action chunk 长度、控制频率、loss 权重、数据增强、推理时的采样步数等。

1. `./p2b repo <仓库 id>`：拿到本地路径（`path`）和当前 commit。
2. `./p2b show paper <paper_id>`、`./p2b text <paper_id> --pages ...`：读论文的方法和实验设置。
3. 在 `path` 里用 grep 和读文件找对应的实现。先看 README 和配置文件（configs/*.yaml、*.py 里的 dataclass），再看模型和训练代码。
4. 提交：`./p2b codemap put --research <研究 id> --repo <仓库 id> --paper <paper_id> --file /tmp/map.json`

```json
[
  {
    "component": "action chunk 长度",
    "paper_quote": "从论文逐字摘的一句（没有对应原文时省略）",
    "page": 5,
    "file": "src/openpi/models/pi0.py",
    "symbol": "Pi0Config.action_horizon",
    "snippet": "从该文件逐字摘的 1-5 行代码",
    "note": "论文写 50 步；代码默认 50，但 LIBERO 配置里改成 10"
  }
]
```

- 一般写 6-15 行，覆盖模型结构、动作表示、训练目标、数据处理、推理这几块。
- `snippet` 必须**逐字**来自该文件（空白差异无所谓），行号由系统按 snippet 的位置自动算出。论文引用同样逐字核对。
- `note` 最重要：写清论文和代码一致还是有出入，或者是论文没提的细节。
- 被拒绝时按错误信息修正后重交。

完成后回复三行以内：最值得注意的 2-3 个"论文没写但代码里有"的细节。

## 讨论时读代码

讨论范围是研究或论文、用户问到实现细节时，到关联仓库的 `path` 里查，回答带上 `文件:行号`。
