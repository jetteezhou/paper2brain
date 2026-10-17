# iPhone 快捷指令

目标：在微信或小红书里点"复制链接"，**轻点手机背面两下**就收下，**1 秒内看到"已收下"**。三击背面可以再说一句想法；另有一个"只记想法"，不碰剪贴板。

每次收下都先写进 iCloud Drive 的 `paper2brain-inbox/` 文件夹，**写完立刻回执**，然后才调用 Cursor API 让 Mac 上的 agent 马上处理。API 慢、失败或 Mac 离线都不影响：Mac 上的 `./p2b serve` 会从 iCloud 收件箱补处理。两份用同一个 `id`，不会重复入库。

## 准备

- Mac 上已按仓库 README 启动本机 worker（名字下面记作 `p2b-mac`），并且有 Cursor API key。
- 在"文件"App 的 iCloud Drive 里新建文件夹 `paper2brain-inbox`。

## 快捷指令"收下"（轻点背面两下）

按顺序添加动作：

1. **获取剪贴板**。
2. **获取文件**：`iCloud Drive/paper2brain-inbox/last.txt`，关闭"找不到文件时报错"。用**设定变量**存成 `last`。
3. **如果** 剪贴板 **是** `last`（剪贴板还是上次收过的那条）：
   - 双击版：**显示通知**"这条已经收过了"，然后**停止此快捷指令**。
   - 三击版（"收下 + 想法"）：跳到第 5 步，但在第 7 步的词典里加 `local` = 真（布尔值），并且**不做**第 9 步。服务器会把这句想法补到上次那条上，不会再收一遍。
4. **存储文件**：把剪贴板存为 `iCloud Drive/paper2brain-inbox/last.txt`，打开"覆盖"、关闭"询问存储位置"。
5. **格式化日期**：当前日期，自定义格式 `yyyyMMdd-HHmmss`；**随机数** 1000 到 9999；**文本** `sc-[格式化的日期]-[随机数]`，用**设定变量**存成 `id`。
6. （只在"收下 + 想法"版本里加）**听写文本**，存成变量 `thought`。
7. **词典**：`id` = 变量 id，`text` = 剪贴板，`thought` = 变量 thought（没有就留空）。**设定名称**为 `[id].json`，然后**存储文件**到 `iCloud Drive/paper2brain-inbox/`，关闭"询问存储位置"。
8. **显示通知**"已收下"。到这里内容已经安全了。
9. **获取 URL 内容**：
   - URL：`https://api.cursor.com/v1/agents`，方法：`POST`
   - 头部：`Authorization` = `Bearer 你的 key`；`Content-Type` = `application/json`
   - 请求体选 JSON：
     - `prompt` → 词典，其中 `text` 填：
       ```
       你是 paper2brain 的论文管理员。先读 librarian/README.md 和 librarian/capture.md。
       这条捕获来自 iPhone，先按第 0 步入库：capture_id=[id]
       原文：[剪贴板]
       用户想法：[thought]
       然后处理它。
       ```
     - `env` → 词典：`type` = `machine`，`name` = `p2b-mac`
     - `repos` → 数组，其中一个词典：`url` = `https://github.com/jetteezhou/paper2brain`
10. 把上一步的结果**设定名称**为 `[id].agent.json`，**存储文件**到 `iCloud Drive/paper2brain-inbox/`。Mac 读到它就知道这条由哪个 agent 处理：网页上能看到"Agent 整理中"，在电脑上"讨论这篇"会接着这个 agent 的对话（你在 Cursor 手机端追问过的内容它都记得）。

第 9 步之后不再显示任何通知。网络不通时快捷指令可能弹一个错误，可以忽略：内容在第 7 步已经存进 iCloud，Mac 上线后会补处理（最多晚 10 分钟，给 API 启动的 agent 留出时间）。

最后在"设置 → 辅助功能 → 触控 → 轻点背面"里，把"轻点两下"设为"收下"，"轻点三下"设为"收下 + 想法"。

## 快捷指令"只记想法"

不读剪贴板，只记一句话。可以放在主屏幕、锁屏小组件，或者绑定操作按钮。

1. **听写文本**，存成变量 `thought`。
2. 同上第 5 步生成 `id`（前缀可以用 `th-`）。
3. **词典**：`id` = 变量 id，`text` 留空，`thought` = 变量 thought。**设定名称**为 `[id].json`，**存储文件**到 `iCloud Drive/paper2brain-inbox/`。
4. **显示通知**"已记下"。

不调用 API。Mac 下一轮检查收件箱（20 秒内）就会把它记成一条想法，出现在网页的"想法"里，agent 会补一句提炼。

## 截图版"收下截图"

把第 1 步换成**获取最新的截屏**，去掉第 2–4 步，然后：

- 第 7 步的 `text` 留空；另外把截图**存储**为 `[id]-1.png`。
- 第 9 步：先用 **Base64 编码**处理截图，再在 `prompt` 里加 `images` 数组，数组里放一个词典：`data` = Base64 结果，`mimeType` = `image/png`。

## 排查

- Mac 上运行 `./p2b serve` 后，网页收件箱里能看到这条记录；快捷指令走 API 时，agent 运行记录在 Cursor 的 Agents 列表里。
- 收件箱里的文件处理完会移到 `paper2brain-inbox/done/`。
- 同一段剪贴板只收一次：网页、快捷指令和 agent 的 `./p2b capture add` 都会把重复的内容并到以前那条上（有想法就补上想法）。
