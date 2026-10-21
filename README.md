# paper2brain

给大模型 + 具身智能研究者用的论文库。在公众号、小红书刷到论文解读时，几秒钟随手收下；系统负责找到原论文、写快读卡、归类，需要时深读和讨论。

- **规则预处理**（不用模型）：识别链接，抓取解读快照，找到原论文（arXiv、DOI、OpenReview、任意 PDF 链接或直接拖入的 PDF；按标题检索 arXiv 和 Crossref），下载 PDF 并按页抽取全文。下载不到全文的论文先存元数据，可以在网页上补传 PDF。
- **Cursor agent**（在这台 Mac 上运行）：写快读卡、归入主题、写深读报告、讨论。工作规范在 `librarian/*.md`。
- **写入校验**：agent 只能通过 `./p2b` 写入论文库。快读卡和深读报告里的每条引用都会和原文逐字核对；用户改过的关联和主题，agent 不能再改。

- **插图与快捷跳转**（规则，不用模型）：从 PDF 按 "Figure N:" 图注裁出插图、读出章节目录。快读卡上左右翻看方法 / 架构图（agent 挑选，没挑时取总览图），点开可放大、跳到原文那一页。论文标题卡上直接跳到深读报告的某一节、原文的方法 / 实验 / 结论、架构图、你的划词和讨论；阅读器顶部有"原文目录"。
- **万能栏（⌘K 或 `/`）**：收下、搜索、找论文、记想法、开始研究、对当前论文的操作、跳到任何页面和主题，都在这一个栏里。在页面任何地方粘贴链接、截图或 PDF，或者把文件拖进来，它会带着内容弹出；输入文字时边打边出库里的论文、讨论和研究。关掉时内容会留着。
- **找论文**：在万能栏说"帮我找……的论文"（或输入描述后选"找论文"），agent 检索、挑 3–6 篇入库，并直接开启这组论文的讨论（`librarian/find.md`）。论文列表多选后也可以"一起讨论"。

- **讨论**：以你和 agent 的对话为核心组成一棵树，论文则以论文为核心归在主题树里。随手记下的想法（收下时写的那句、论文页上记的、手机上说的）就是一段讨论的开头；在任意一条消息上"从这里分叉"，新分支挂在下面，带着从根到分叉点的上下文（agent 也拿到这条路径）。讨论页左边是大纲（顺序稳定；折叠、放大、过滤、拖拽；`j k h l` 导航，`Tab` / `⇧Tab` 调整层级，`e` 改名，`b` 分叉），右边是**河流**：从根到当前这段讨论读作一条主流，上游消息直接铺开，哪条消息分过叉、分出了哪几支，就在那条消息下面列成卡片，点一张河流就改道过去；也可以切到**地图**——整棵树的可缩放缓拖画布，拖一个节点到另一个上面就改了挂载，虚线边表示从某条消息分叉。讨论里引用的论文自动成为反向链接，论文的"记录"页能看到哪些讨论引用了它。agent 会给讨论起标题、把明显接着上一段的讨论挂过去；你改过的标题和位置，agent 不再动（`librarian/discuss.md`）。
- **研究**：每个主题下有"论文 / 研究"两个标签。多段讨论、论文汇聚成一个研究，围绕**三要素**（研究问题、研究方法、验证方案）展开。讨论、讨论消息、论文卡片上都能一键"加入研究"；素材双向关联，点一下能跳回当时的讨论（定位并高亮那条消息）或论文。研究分"构思中"和"已确立"：agent 只能提交修改稿（`librarian/research.md`），由你采用、继续在讨论里调整或放弃；**只有你能确认确立**，且要求三要素齐全。

## 快速开始

一键安装（可重复运行）：装 Python 依赖、建数据目录，在 macOS 上把服务装成开机自启的后台服务；带参数时顺便装好并切到对应的 agent 后端：

```sh
ops/setup.sh tcodex     # 或 ops/setup.sh cursor；不带参数则保持 config.json 里现有的后端
```

手动安装：

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # 画方法图用的无头浏览器
./p2b serve            # 打开 http://127.0.0.1:8765
```

数据默认存放在 `~/paper2brain-data`（SQLite、PDF、截图），不进仓库；可以用 `P2B_HOME` 指定其他目录。

**换一台 Mac 用**：一次只在一台 Mac 上用，通过 iCloud 云盘交接（活的数据库不放 iCloud，iCloud 里只放快照）：

```sh
.venv/bin/python ops/handoff.py push   # 要离开的 Mac：停掉本机服务，把数据库快照、PDF、仓库和 config.json 放进 iCloud 云盘/paper2brain-handoff
.venv/bin/python ops/handoff.py pull   # 要接手的 Mac：等 iCloud 同步完再运行；会先备份本机旧库，再接手并重启服务
```

新 Mac 第一次用：克隆代码、`ops/setup.sh`（顺带装好开机自启），装好并登录 Cursor CLI 和 `tcodex`，再 pull。两台 Mac 共用 `config.json` 里同一个 worker 名（`machine`），已有的讨论换了机器也能接着聊；本机 `config.json` 里另设了 `machine` 时 pull 会保留它。

**从别的设备访问**：默认只接受本机。要对外开放，必须设访问口令：`config.json` 的 `password`，或环境变量 `P2B_PASSWORD`（浏览器会弹登录框，用户名随便填；设了之后本机访问也要输）。经代理或域名访问时，再把那个域名写进 `config.json` 的 `hosts`（或环境变量 `P2B_HOSTS`，逗号隔开）。

设了口令后，服务默认也对内网开放：同一网络里的手机和电脑打开 `http://<这台 Mac 的 IP>:8765`（启动日志 `/tmp/paper2brain.log` 里打印了这个地址）。只想开本机就用 `./p2b serve --host 127.0.0.1`。

**升级与备份**：数据库带版本号（`PRAGMA user_version`）。新版本第一次打开旧数据库时（`./p2b serve` 或任何一条 `./p2b` 命令），先把原库完整复制到 `~/paper2brain-data/backups/p2b-before-v<N>-<时间>.sqlite`，再按顺序补上缺的表、列和索引，原有数据不删不改。例外是两次结构调整：只写了一句想法、没有任何内容的旧捕获，变成一条独立想法（v4）；每条想法变成一段讨论，原话是第一条消息、提炼成为标题，它原来那段讨论和研究素材并进来，之后删掉想法表（v7）。服务运行时每天另存一份 `backups/p2b-YYYYMMDD.sqlite`，保留最近 7 份。要回退，停掉服务后把备份复制回 `p2b.sqlite` 即可。

没连接 agent 时，Web 界面照样可以收下内容、确认是哪篇论文、阅读 PDF、调整主题、记想法（开一段讨论，等连上 agent 再接着聊）；快读卡、深读报告和讨论里的回答需要连接 agent。

## 连接 Cursor agent

1. 安装 Cursor CLI 并登录：`curl https://cursor.com/install -fsS | bash && agent login`
2. 在仓库目录启动本机 worker，保持运行：`ops/worker.sh`（worker 名默认为 `p2b-mac`）
3. 在 [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations) 创建 API key，写入 `~/paper2brain-data/config.json`：

   ```json
   {"cursor_api_key": "key_...", "machine": "p2b-mac"}
   ```

   可选字段：`"model"` 指定默认模型（各模块的模型在网页左下角"Agent 已连接"里分别选，存在 `"models"` 里，可带上下文长度、推理强度等参数）；`"auto_agent": false` 关闭收下后的自动整理；`"max_agents"`（默认 5）同时运行的 agent 上限，多出的排队（iCloud 收件箱一次补处理多条时也一样）；`"run_timeout_min"`（默认 60）超过这么久还没结束的运行会被停止，后面的队列照常往下走；`"overleaf_token"` Overleaf 的 Git token（账户设置 → Git Integration，需要 Overleaf 的 Git 集成），研究关联 Overleaf 项目后 agent 就能在讨论里改论文，改动在"写作"页确认后才推送。
4. 重启 `./p2b serve`。左下角显示"Agent 已连接"。

**改用 TCodex（腾讯内网）**：装好 `tcodex` 并登录一次（`tcodex login`，或设置 `TCODEX_AUTH_TOKEN`），两个后端可以同时用：在网页左下角"Agent"设置里给每个模块单独选模型，Cursor 和 TCodex 的模型（标"TCodex ·"）都在同一个列表里，例如研究用 TCodex 的 GPT、其他用 Cursor 的模型；选"默认"的模块用 `config.json` 的 `"backend"`（默认 `cursor`）。已经开始的讨论一直留在它开始时的后端上。只用 TCodex 时不需要上面的 Cursor worker 和 API key。agent 以 `tcodex exec` 在本仓库目录里运行（不开沙箱，和 Cursor worker 一样能联网、写数据目录）。服务重启会中断正在运行的 TCodex 任务。

**调优 agent**：修改 `librarian/capture.md`、`discuss.md`（回答、整理讨论树、起标题）、`deepread.md`，然后在捕获详情里点"重新整理"。每次运行的提示词、调用了哪些工具、最终回复，都能在"处理记录"里看到。

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
web/           Web 界面：Preact + htm + signals（web/vendor），ES 模块直接加载，无构建步骤；
               app/main.js 外壳与路由，discuss.js 讨论工作台，papers.js 收件箱与论文，reader.js 阅读器，research.js 研究
shortcuts/     iPhone 快捷指令搭建说明
ops/           worker 启动脚本、launchd 配置
tests/         写入规则与存储测试（临时数据库）
```

## 测试

```sh
.venv/bin/python -m unittest discover -s tests
```
