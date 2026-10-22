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

## 多台 Mac

几台 Mac 可以同时用同一个论文库，经 iCloud 云盘的 `paper2brain-handoff` 文件夹互通（活的数据库不放 iCloud）。

**日常：同时开着，在哪台上做什么都行。** `config.json` 里 `"sync": true` 时（实验功能，接入新 Mac 时会随配置带过去），各台每 20 秒交换一次改动：论文、快读卡、主题、研究页、讨论（含已结束的 agent 回复）、笔记都同步，PDF、插图、方法图和代码仓库的文件也双向同步。`config.json` 里的设置和密钥（Cursor API key、快速搜索 / Gemini 接口、访问口令、各模块的模型）也跟着最后改它的那台走，各台的 worker 名（`machine`）除外；读配置的地方每次都重新读，不用重启，Cursor key 变了会自动重启本机 worker。几十秒内改了同一处时，以后改的为准。删掉的文件不同步（另一台上的留着不用）；Overleaf 的本地副本不同步，它经 Overleaf 自己同步。在一台上开的讨论到另一台接着聊时，会换一个本机的新 agent，之前说过的话照样带上。

**持有者**：iCloud 收件箱（手机上收下的内容）和自动整理、深读、起标题这些后台 agent，只在“持有论文库”的那台上跑，免得几台各做一遍；它还每半小时往 iCloud 放一份完整快照，不停服务，作兜底。最后一次接手的那台就是持有者。

**什么时候点“接手”**：网页左下角“Agent”设置 →“两台 Mac”一栏，显示谁是持有者、快照是什么时候的；“从 iCloud 接手到这台”用快照替换本机论文库（本机旧库先备份到 `backups/`，已同步过的改动再补回来），服务重启约一分钟后页面自动刷新。只在这几种时候需要：新 Mac 第一次用；想让这台负责收件箱和后台 agent（比如持有者那台要关机一阵子）；同步出了问题，想从快照重来。

### 接入一台新 Mac

前提：新 Mac 登录了同一个 Apple ID 并打开了 iCloud 云盘；能用 SSH 访问 GitHub。

1. 在已经在用的那台 Mac 上确认 iCloud 云盘里有 `paper2brain-handoff`（持有者每半小时自动放一份快照；还没有的话在仓库目录运行一次 `.venv/bin/python ops/handoff.py push`，它会停掉那台的服务，新 Mac 接手完再在那台上运行 `ops/install.sh` 启动回来）。在访达里看着它上传完（云朵图标不再转）。
2. 在新 Mac 上装代码和环境（仓库别放进 iCloud 云盘，macOS 不让后台服务读那里的文件）：

   ```sh
   git clone git@github.com:jetteezhou/paper2brain.git ~/paper2brain
   cd ~/paper2brain && git checkout cursor/web-mvp-f5a8
   ops/setup.sh tcodex      # Python 依赖、TCodex、开机自启的服务和 worker
   tcodex login
   curl https://cursor.com/install -fsS | bash && agent login
   ```

3. 接手论文库，两种方式任选：打开 http://127.0.0.1:8765 →左下角“Agent”设置 →“两台 Mac”→“从 iCloud 接手到这台”；或者在终端运行 `.venv/bin/python ops/handoff.py pull`。会先等 iCloud 把快照下载完（1 GB 左右，下载太慢会提示稍后再试），然后替换本机的空库、把文件路径改成这台的，重启服务。
4. 刷新页面，输入访问口令（随配置带过来，用户名随便填）。检查论文数、打开一篇看 PDF 和插图、左下角显示“Agent 已连接”、在一段讨论里发条消息看 agent 能回。

开了同步时，新 Mac 会自动起一个自己的 Cursor worker 名（`p2b-<主机名>`），因为两台同时在线时不能共用一个名字；想用别的名字，接手前在新 Mac 的 `~/paper2brain-data/config.json` 里写好 `"machine"`。接手后新 Mac 成了持有者；想让原来那台继续收手机收件箱，在原来那台上再点一次“接手”。

**只想一次用一台（不开同步）**：离开时在旧 Mac 上 `ops/handoff.py push`（停服务、放快照），到新 Mac 上 pull。两台共用一个 worker 名，旧讨论换了机器也能接着聊。忘了 push 的话，自动快照最多差半小时。

**从别的设备访问**：默认只接受本机。要对外开放，必须设访问口令：`config.json` 的 `password`，或环境变量 `P2B_PASSWORD`（浏览器会弹登录框，用户名随便填；设了之后本机访问也要输）。经代理或域名访问时，再把那个域名写进 `config.json` 的 `hosts`（或环境变量 `P2B_HOSTS`，逗号隔开）。

设了口令后，服务默认也对内网开放：同一网络里的手机和电脑打开 `http://<这台 Mac 的 IP>:8765`（启动日志 `/tmp/paper2brain.log` 里打印了这个地址）。只想开本机就用 `./p2b serve --host 127.0.0.1`。

**升级与备份**：数据库带版本号（`PRAGMA user_version`）。新版本第一次打开旧数据库时（`./p2b serve` 或任何一条 `./p2b` 命令），先把原库完整复制到 `~/paper2brain-data/backups/p2b-before-v<N>-<时间>.sqlite`，再按顺序补上缺的表、列和索引，原有数据不删不改。例外是两次结构调整：只写了一句想法、没有任何内容的旧捕获，变成一条独立想法（v4）；每条想法变成一段讨论，原话是第一条消息、提炼成为标题，它原来那段讨论和研究素材并进来，之后删掉想法表（v7）。服务运行时每天另存一份 `backups/p2b-YYYYMMDD.sqlite`，保留最近 7 份。要回退，停掉服务后把备份复制回 `p2b.sqlite` 即可。

没连接 agent 时，Web 界面照样可以收下内容、确认是哪篇论文、阅读 PDF、调整主题、记想法（开一段讨论，等连上 agent 再接着聊）；快读卡、深读报告和讨论里的回答需要连接 agent。

## 多人共用一台服务器（团队版）

数据和网页服务放在一台云服务器上，每人只用浏览器；agent 仍在每个人自己的 Mac 上跑，用自己的 Cursor key，只能通过服务器的接口按本人权限读写。每人一个独立的论文库（服务器上各自一个目录，互相看不到）；想和别人一起做的研究，复制进一个**共享空间**再邀请成员。

- **角色**：所有者（邀请、改角色、确立研究、删研究、改模型和 Overleaf）、编辑、只读。
- **共享空间的 agent**：用空间里指定的一位成员（默认是创建者）的 key 和 Mac 跑；所有者可以在设置里换人。
- **TCodex**：也在成员自己的 Mac 上跑。装了 `tcodex` 的 Mac 接入时会顺便装上本机 TCodex，它向服务器领取这个人（和由 ta 负责 agent 的共享空间）的 TCodex 任务、在本机运行并把回答传回；这台 Mac 在线时模型列表里才有 TCodex 的模型。搜索框的 AI 推荐、起标题这些一次性的快速调用不走 TCodex，要用就配"快速搜索接口"。
- **服务器上不跑的**：iCloud 收件箱、多台 Mac 同步与交接；Overleaf 只能在网页上推拉，agent 改不了服务器上的本地副本。

**搭服务器**（Ubuntu 24.04，域名的 A 记录已指向它，只开 22/80/443 端口）：

```sh
git clone <仓库地址> ~/paper2brain && cd ~/paper2brain
sudo ops/server.sh p2b.example.com    # 依赖、Chromium、systemd 服务 paper2brain、Caddy（自动 HTTPS）；最后打印第一个账号的邀请链接
```

数据在 `/var/lib/paper2brain`（`hub.sqlite` 是账号和成员，`libs/<库 id>/` 是各个论文库，每个库每天自己备份到 `backups/`）。再要邀请新账号：`sudo -u <运行用户> env P2B_ROOT=/var/lib/paper2brain ./p2b admin invite --url https://p2b.example.com`；已有账号的人也可以在共享空间里直接生成邀请链接，对方用它就会顺便建好账号。

**每个人的 Mac**：打开邀请链接、写名字；在网页左下角"Agent"设置里点"生成本机 agent 的口令"，照着显示的命令在终端里运行。成员的 Mac 用的是单独的仓库 [paper2brain-client](https://github.com/jetteezhou/paper2brain-client)（`setup.sh <服务器地址> <口令>`：问一次 Cursor API key 和快速搜索接口，登记到服务器上这个人的论文库，把 worker 和 TCodex 装成开机自启）。它只有转发 `./p2b` 和运行 TCodex 的少量代码，基本不变；agent 的工作规范 `librarian/` 每个任务开始时用 `./p2b guide` 从服务器取，所以服务代码和规范怎么改，成员都不用更新客户端。换设备用登录名和密码登录。

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

## 启动与停止

`ops/setup.sh` 已经把网页服务（`com.paper2brain.server`）和 Cursor worker（`com.paper2brain.worker`）装成开机自启、退出后自动重启的后台服务，平时不用管。手动操作（在仓库目录）：

```sh
ops/install.sh                                                     # 装上并启动（也用于 push 或 bootout 之后启动回来）
launchctl kickstart -k gui/$(id -u)/com.paper2brain.server         # 重启网页服务（改了代码或 config.json 之后）；worker 同理
launchctl bootout gui/$(id -u)/com.paper2brain.server              # 停掉，开机也不再自启，直到再运行 ops/install.sh
launchctl print gui/$(id -u)/com.paper2brain.server | grep state   # 看是否在运行
tail -f /tmp/paper2brain.log /tmp/paper2brain-worker.log           # 日志；启动行里有本机和内网地址
```

不想装后台服务时，在终端前台运行 `ops/run.sh`（网页服务和 worker 一起，Ctrl-C 停），或只开网页服务 `./p2b serve`。

服务每 30 秒检查一次 iCloud 收件箱（iPhone 快捷指令写入的捕获），见 `shortcuts/README.md`。

## 目录

```
paper2brain/   代码：store（唯一碰 SQLite）、rules（写入校验）、sources（抓取）、pipeline（规则预处理）、
               structure（插图与目录）、cursor_api（agent）、server（Web API）、cli（./p2b）、inbox（iCloud 收件箱）、
               accounts（团队版的账号、库与成员）
librarian/     agent 工作规范
web/           Web 界面：Preact + htm + signals（web/vendor），ES 模块直接加载，无构建步骤；
               app/main.js 外壳与路由，discuss.js 讨论工作台，papers.js 收件箱与论文，reader.js 阅读器，research.js 研究
shortcuts/     iPhone 快捷指令搭建说明
ops/           worker 启动脚本、launchd 配置；团队版的 server.sh（Linux 服务器）、systemd 和 Caddy 配置
tests/         写入规则与存储测试（临时数据库）
```

## 测试

```sh
.venv/bin/python -m unittest discover -s tests
```
