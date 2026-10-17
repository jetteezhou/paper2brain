# paper2brain

给大模型 + 具身智能研究者用的论文库。在公众号、小红书刷到论文解读时，几秒钟随手收下；系统负责找到原论文、写快读卡、归类，需要时深读和讨论。

- **规则预处理**（不用模型）：识别链接，抓取解读快照，找到原论文（arXiv、DOI、OpenReview、任意 PDF 链接或直接拖入的 PDF；按标题检索 arXiv 和 Crossref），下载 PDF 并按页抽取全文。下载不到全文的论文先存元数据，可以在网页上补传 PDF。
- **Cursor agent**（在这台 Mac 上运行）：写快读卡、归入主题、写深读报告、讨论。工作规范在 `librarian/*.md`。
- **写入校验**：agent 只能通过 `./p2b` 写入论文库。快读卡和深读报告里的每条引用都会和原文逐字核对；用户改过的关联和主题，agent 不能再改。

- **插图与快捷跳转**（规则，不用模型）：从 PDF 按 "Figure N:" 图注裁出插图、读出章节目录。快读卡上左右翻看方法 / 架构图（agent 挑选，没挑时取总览图），点开可放大、跳到原文那一页。论文标题卡上直接跳到深读报告的某一节、原文的方法 / 实验 / 结论、架构图、你的划词和讨论；阅读器顶部有"原文目录"。
- **找论文**：在输入框说"帮我找……的论文"（或点"找论文"），agent 检索、挑 3–6 篇入库，并直接开启这组论文的讨论（`librarian/find.md`）。论文列表多选后也可以"一起讨论"。

- **研究**：每个主题下有"论文 / 研究"两个标签。多段讨论、想法、论文汇聚成一个研究，围绕**三要素**（研究问题、研究方法、验证方案）展开。想法、讨论消息、论文卡片上都能一键"加入研究"；素材双向关联，点一下能跳回当时的讨论（定位并高亮那条消息）、论文或想法。研究分"构思中"和"已确立"：agent 只能提交修改稿（`librarian/research.md`），由你采用、继续在讨论里调整或放弃；**只有你能确认确立**，且要求三要素齐全。

## 快速开始

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./p2b serve            # 打开 http://127.0.0.1:8765
```

数据默认存放在 `~/paper2brain-data`（SQLite、PDF、截图），不进仓库；可以用 `P2B_HOME` 指定其他目录。

没连接 agent 时，Web 界面照样可以收下内容、确认是哪篇论文、阅读 PDF、调整主题、记想法；快读卡、深读报告和讨论需要连接 agent。

## 连接 Cursor agent

1. 安装 Cursor CLI 并登录：`curl https://cursor.com/install -fsS | bash && agent login`
2. 在仓库目录启动本机 worker，保持运行：`ops/worker.sh`（worker 名默认为 `p2b-mac`）
3. 在 [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations) 创建 API key，写入 `~/paper2brain-data/config.json`：

   ```json
   {"cursor_api_key": "key_...", "machine": "p2b-mac"}
   ```

   可选字段：`"model"` 指定模型；`"auto_agent": false` 关闭收下后的自动整理。
4. 重启 `./p2b serve`。左下角显示"Agent 已连接"。

**调优 agent**：修改 `librarian/capture.md`、`discuss.md`、`deepread.md`，然后在捕获详情里点"重新整理"。每次运行的提示词、调用了哪些工具、最终回复，都能在"处理记录"里看到。

## 开机自启（可选）

```sh
sed "s#REPO#$PWD#g" ops/com.paper2brain.server.plist > ~/Library/LaunchAgents/com.paper2brain.server.plist
launchctl load ~/Library/LaunchAgents/com.paper2brain.server.plist
```

服务每 30 秒检查一次 iCloud 收件箱（iPhone 快捷指令写入的捕获），见 `shortcuts/README.md`。

## 目录

```
paper2brain/   代码：store（唯一碰 SQLite）、rules（写入校验）、sources（抓取）、pipeline（规则预处理）、
               structure（插图与目录）、cursor_api（agent）、server（Web API）、cli（./p2b）、inbox（iCloud 收件箱）
librarian/     agent 工作规范
web/           Web 界面（无构建步骤）
shortcuts/     iPhone 快捷指令搭建说明
ops/           worker 启动脚本、launchd 配置
tests/         写入规则与存储测试（临时数据库）
```

## 测试

```sh
.venv/bin/python -m unittest discover -s tests
```
