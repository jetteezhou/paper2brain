# paper2brain

给大模型 + 具身智能研究者用的论文库。在公众号、小红书刷到论文解读时，几秒钟随手收下；系统负责找到原论文、写快读卡、归类，需要时深读和讨论。

- **规则预处理**（不用模型）：识别链接，抓取解读快照，找到原论文（arXiv、DOI、OpenReview、任意 PDF 链接或直接拖入的 PDF；按标题检索 arXiv 和 Crossref），下载 PDF 并按页抽取全文。下载不到全文的论文先存元数据，可以在网页上补传 PDF。
- **Cursor agent**（在这台 Mac 上运行）：写快读卡、归入主题、写深读报告、讨论。工作规范在 `librarian/*.md`。
- **写入校验**：agent 只能通过 `./p2b` 写入论文库。快读卡和深读报告里的每条引用都会和原文逐字核对；用户改过的关联和主题，agent 不能再改。

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
               cursor_api（agent）、server（Web API）、cli（./p2b）、inbox（iCloud 收件箱）
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
