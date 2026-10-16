# iPhone 快捷指令

目标：在微信或小红书里点"复制链接"，**轻点手机背面两下**就收下。三击背面可以再说一句想法。

每次捕获发两份：

1. 先写进 iCloud Drive 的 `paper2brain-inbox/` 文件夹。Mac 离线时也不会丢，Mac 上线后由 `./p2b serve` 补处理。
2. 再调用 Cursor API，在 Mac 上启动 agent 立即处理。

两份用同一个 `id`，不会重复入库。

## 准备

- Mac 上已按仓库 README 启动本机 worker（名字下面记作 `p2b-mac`），并且有 Cursor API key。
- 在"文件"App 的 iCloud Drive 里新建文件夹 `paper2brain-inbox`。

## 快捷指令"收下"（绑定到轻点背面两下）

按顺序添加动作：

1. **获取剪贴板**。
2. **格式化日期**：当前日期，自定义格式 `yyyyMMdd-HHmmss`。
3. **随机数**：1000 到 9999。
4. **文本**：`sc-[格式化的日期]-[随机数]`，然后用**设定变量**存成 `id`。
5. （只在"收下 + 想法"版本里加）**听写文本**，存成变量 `thought`。
6. **词典**：`id` = 变量 id，`text` = 剪贴板，`thought` = 变量 thought（没有就留空）。
7. **设定名称**：把词典命名为 `[id].json`，然后**存储文件**到 `iCloud Drive/paper2brain-inbox/`，关闭"询问存储位置"。
8. **获取 URL 内容**：
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
9. **如果**上一步结果包含 `agent`：**显示通知**"已收下"；**否则**显示"已存入收件箱，Mac 上线后整理"。

最后在"设置 → 辅助功能 → 触控 → 轻点背面"里，把"轻点两下"设为"收下"，"轻点三下"设为"收下 + 想法"。

## 截图版"收下截图"

把第 1 步换成**获取最新的截屏**，然后：

- 第 6 步的 `text` 留空。
- 第 7 步另外把截图**存储**为 `[id]-1.png`。
- 第 8 步：先用 **Base64 编码**处理截图，再在 `prompt` 里加 `images` 数组，数组里放一个词典：`data` = Base64 结果，`mimeType` = `image/png`。

## 排查

- Mac 上运行 `./p2b serve` 后，网页里的捕获详情能看到这条记录。快捷指令走 API 时，agent 运行记录在 Cursor 的 Agents 列表里。
- 收件箱里的文件处理完会移到 `paper2brain-inbox/done/`。
