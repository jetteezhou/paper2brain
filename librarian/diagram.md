# 画论文插图：动机图与方法框架图

## 职责

为研究画论文里的两类示意图（`--kind`）：

- **动机图**（`motivation`，研究概览页）：论文的 Figure 1 / teaser，放在标题下或引言旁。让读者在读正文之前就明白：现有方法在什么场景下失败、为什么失败，我们的洞见是什么、带来了什么不同。
- **方法框架图**（`method`，研究代码页）：方法一节的**整体框架图**（数据从输入流到输出、哪些模块、哪里是我们的新东西），有时是某个模块的**结构细节图**或"我们 vs baseline"的**结构对比图**。

结果图表（柱状图、曲线、表格）不归这里，在实验页的"实验设计"里。

标准是 π0、GR00T N1、Gemini Robotics、Qwen / DeepSeek 技术报告里的那种插图：审稿人扫一眼就知道方法在干什么、新在哪；放进论文不显得廉价。画得"能看"不算完成。

## 好的动机图长什么样

- **一个对比讲完一个问题**：最常见的是左右（或上下）两栏，左边"现有方法"、右边"我们"，同一个场景、同一个任务，只差在研究问题指出的那一点。其他形式（失败案例 → 原因 → 洞见；一条随时间展开的执行轨迹，标出 baseline 出错的时刻）也可以，但只讲**一个**问题。
- **问题要具体、可感**：画研究问题里那个真实的失败场景（具身方向常见：机械臂在哪一步抓空、长程任务第几步漂移、推理延迟让动作滞后），不画抽象的"性能差"。失败用红色叉号、虚线轨迹、"✗ collision"这类小标注点出来，成功用绿色对号。
- **洞见一句话写在图上**：我们这一栏的核心区别（核心洞见）用强调色标出，旁边一行短语点题（"trigger only when the scene changes"），读者不看图注也能抓住。
- **定量钩子（可选）**：实验记录里已经有的关键数字（成功率、调用次数、延迟）可以做成角落的小对比条或大号数字；**只用实验记录里的数字**，没有就不放，不写预期值。
- **少即是多**：不画网络结构细节（那是方法框架图的事），模块最多是"Policy""VLM"这样的黑盒。文字比方法图更少。
- **内容准确**：问题、洞见、对比对象都出自三要素和相关工作（`./p2b related show`）；对比的"现有方法"是相关工作里最接近的那类，名字和论文里一致，不歪曲别人的工作。
- 场景元素（机械臂、物体、桌面、相机视角）用简洁的扁平线条 SVG 画，风格统一；用户提供了真实的机器人照片或仿真截图时可以用（data URI 或本机绝对路径），裁成同样大小、圆角对齐。

## 好的方法图长什么样

**讲一个故事**
- 5 秒内看懂主数据流：一条主线，从左到右（或从上到下），不回折、不交叉。
- 主模块 ≤ 10 个左右；细节多就分成 2-4 个**阶段**，每个阶段一块浅底色面板，左上角写阶段名（"① Perception" 这类）。还多就拆成两张图，别硬塞。
- **新东西最显眼**：我们提出的模块用唯一的强调色、略大或加粗边框；沿用的部件（预训练 VLM、标准 Transformer）用中性灰。眼睛第一眼落的地方必须是贡献点。
- 冻结 / 训练、只在推理时 / 只在训练时这类区别要画出来（雪花和火焰的线条图标、虚线框、小标签），没有区别就不画。

**内容准确**
- 每个方块都对得上研究方法（`./p2b research show`）和代码对照；名字和论文里一字不差（关联了 Overleaf 时读方法一节，符号也照它的写）。三要素里没有的模块不画，拿不准的在回复里说。
- 关键的张量 / token 用图形表示，而不只是写字：token 序列画成一排小方块（不同来源不同颜色），图像观测用小缩略框，动作块画成一串小方格或一条短轨迹，噪声用浅灰的格子。箭头旁标出流过什么（\(o_t\)、\(a_{t:t+H}\)、"text tokens"），显而易见的不标。

**视觉规范**
- **配色**：一个强调色 + 至多两个辅色 + 中性灰。填充用浅色（同色相、约 10-15% 浓度），描边用同色相的深色，文字深灰（`#1f2328`）而不是纯黑。颜色有语义时全图一致（例如视觉=蓝、语言=绿、动作=橙），并在角落放图例。参考调色板（色盲友好）：蓝 `#2f6fde` / `#e8f0fd`、橙 `#e8710a` / `#fdf0e3`、绿 `#1e8e3e` / `#e6f4ea`、紫 `#7c4dff` / `#f0ebff`、灰 `#5f6368` / `#f1f3f4`。
- **字体**：一个无衬线字体族 `"Helvetica Neue", Helvetica, Arial, sans-serif`，最多三档字号：阶段名、模块名（600 字重）、注释（400 字重，灰色）。
- **字号**（印出来不小于 7pt）：整栏宽的图（`figure*`，`\textwidth`）画布宽 **1200px**，单栏图（`\columnwidth`）画布宽 **600px**；两种都是正文 ≥ 18px、模块名 22-26px、阶段名 24-28px。
- **形状**：圆角矩形，圆角 8-12px，描边 1.5-2px；全图同一套圆角和线宽。对齐到 8px 网格：同一行的方块等高、中线对齐，间距一致。
- **箭头**：一种样式，正交折线或直线，实心小三角箭头；主流程用深色实线，次要 / 条件流程用浅色或虚线，反馈 / 梯度另用一种并在图例里说明。箭头端点贴在方块边上，不穿过其他方块和文字。
- **留白**：外边距 24-32px，模块之间 ≥ 24px；整体不留大片空白。宽高比：整栏图约 2.2:1 到 3:1，单栏图约 1.2:1 到 1.6:1。
- **不要**：阴影、渐变、模糊、emoji（PDF 里会变成位图）、剪贴画、3D 效果、彩虹配色、斜体堆砌、整句话塞进方块（方块里只放名字，最多再一行小字说明）。

## 技术约定

一个 HTML 文件就是一张图，`./p2b diagram render` 用无头 Chromium 把 `id="figure"` 的元素导出成矢量 PDF（放进论文）和 3 倍分辨率 PNG（给你看）。

- 根元素 `<div id="figure">` 写死 px 宽度（1200 或 600），`position: relative`，白底；高度随内容。
- 方块和文字用 HTML + CSS（flex / grid 排版最省事、对齐最准），图标和箭头用内联 SVG。图标自己画成简单的线条 SVG（描边 1.5-2px、和文字同色），风格统一。
- 数学符号写成 `\( ... \)`，渲染时自动用 KaTeX 排版。
- 不能联网：不引用外部字体、CDN 和图片。需要图片时用 data URI 或本机文件的绝对路径。
- 箭头的位置在 `window.layout()` 里按方块的实际位置算：渲染器等公式和字体排好后调用它，方块挪了箭头自动跟着走，不要手算坐标。

骨架（照着改，不必照抄配色和结构）：

```html
<!doctype html><meta charset="utf-8">
<style>
  #figure { width: 1200px; position: relative; padding: 28px; box-sizing: border-box; background: #fff;
            font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1f2328; font-size: 18px; }
  .row { display: flex; align-items: center; gap: 56px; }
  .stage { background: #f6f8fa; border-radius: 14px; padding: 20px; }
  .stage > h4 { margin: 0 0 14px; font-size: 24px; font-weight: 600; }
  .box { border: 2px solid #5f6368; background: #f1f3f4; border-radius: 10px; padding: 14px 18px; text-align: center; }
  .box b { display: block; font-size: 22px; font-weight: 600; }
  .box small { color: #5f6368; font-size: 18px; }
  .ours { border-color: #e8710a; background: #fdf0e3; }
  #wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
  .wire { fill: none; stroke: #3c4043; stroke-width: 2; stroke-linejoin: round; }
</style>
<div id="figure">
  <div class="row">
    <div class="box" id="obs"><b>Observation</b><small>\(o_t\), instruction</small></div>
    <div class="box" id="vlm"><b>VLM backbone</b><small>frozen</small></div>
    <div class="box ours" id="head"><b>Action expert</b><small>flow matching</small></div>
  </div>
  <svg id="wires"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
    <path d="M0,1 L10,5 L0,9 z" fill="#3c4043"/></marker></defs></svg>
</div>
<script>
window.layout = () => {
  const F = document.getElementById('figure').getBoundingClientRect(), svg = document.getElementById('wires');
  const at = (id, side) => {  // side: l r t b
    const r = document.getElementById(id).getBoundingClientRect();
    return [(side === 'l' ? r.left : side === 'r' ? r.right : (r.left + r.right) / 2) - F.left,
            (side === 't' ? r.top : side === 'b' ? r.bottom : (r.top + r.bottom) / 2) - F.top];
  };
  const wire = (a, sa, b, sb, cls = '') => {
    const [x1, y1] = at(a, sa), [x2, y2] = at(b, sb);
    const d = 'lr'.includes(sa) ? `M${x1},${y1} H${(x1 + x2) / 2} V${y2} H${x2}` : `M${x1},${y1} V${(y1 + y2) / 2} H${x2} V${y2}`;
    svg.insertAdjacentHTML('beforeend', `<path class="wire ${cls}" d="${d}" marker-end="url(#arrow)"/>`);
  };
  wire('obs', 'r', 'vlm', 'l');
  wire('vlm', 'r', 'head', 'l');
};
</script>
```

## 步骤

1. `./p2b research show <研究 id>`：动机图看研究问题、核心洞见、验证方案和实验记录（可用的数字），再 `./p2b related show <研究 id>` 看最接近的工作差在哪；方法图看研究方法（画什么）、代码对照（模块名、输入输出的形状、哪些冻结）、关联仓库。自己的仓库（`role` 为 own）里有实现时，到 `./p2b repo <仓库 id>` 给的路径里读模型代码确认结构。关联了 Overleaf 时 `./p2b overleaf show <研究 id>`，读方法一节，名字和符号照它的写。
2. `./p2b diagram list <研究 id>`：已有的图。改已有的图时在它的源文件（`html`）上改，用同一个 `--name` 覆盖；新图起新名字（`teaser`、`framework`、`action-head`、`vs-baseline`），渲染时写上 `--kind`。
3. **先想清楚再写**：动机图先用一句话写出"现有方法在 X 时失败，因为 Y；我们 Z"，图只画这句话。方法图列出模块和它们之间的流（谁的输出进谁）；标出哪个是贡献点；分几个阶段。两种都要选整栏还是单栏（动机图通常整栏）。
4. 写 `/tmp/<name>.html`，渲染：

   ```
   ./p2b diagram render <研究 id> --kind method --name framework --file /tmp/framework.html --title "整体框架图" --caption "Overview of ... (a) ... (b) ..."
   ```

5. **打开返回的 `png` 看图**，以审稿人的眼光过一遍下面的清单；`warnings`（文字溢出、文字互相重叠、连线穿过文字、超出画布、字号太小）必须清零；箭头的标注放在线的一侧，不要压在线上。改完重新 render。**至少看、改两轮**，第一版几乎总有问题。
6. 图注（`--caption`）用论文的语言（通常英文），第一句概括整张图，后面按阶段或 (a)(b) 依次说明，强调色代表什么要说出来。

## 检查清单

- 动机图：遮住图注，只看图，能不能说出"现有方法哪里不行、我们好在哪"？两栏是不是只差在那一点？
- 第一眼落在哪？是不是我们的贡献点？
- 主数据流能不能一笔顺下来？有没有交叉、回折、穿过方块的箭头？
- 名字、符号和研究方法 / 论文一致吗？有没有方法里没有的模块？
- 对齐：同一行等高、中线齐；间距一致；有没有挤在一起或大片空白？
- 配色：强调色只用在贡献点上吗？颜色语义全图一致、有图例吗？
- 文字：都在方块里、没有被裁掉；缩到论文里还读得清（字号规则）？
- 是不是像大厂技术报告里的图？哪里看起来廉价（默认样式、粗黑边框、乱用颜色、文字太多），就改哪里。

## 放进论文

用户要把图放进 Overleaf 论文时（按 `librarian/writing.md` 的"改 Overleaf 论文"），把 `figure.pdf` 复制到 Overleaf 本地目录的图片文件夹（沿用项目已有的，通常是 `figures/`），文件名用图的名字；动机图放在引言开头（通常是第一张图，可用 `\twocolumn[...]` 或 `teaser` 宏放在标题下，照项目模板已有的写法），方法图放在方法一节，插入 `figure*`（整栏）或 `figure`（单栏），`\includegraphics[width=\textwidth]`，图注和 `\label{fig:<name>}` 照项目已有的格式写。

## 回复

两行以内：这张图画了什么、贡献点怎么突出的；拿不准的结构（方法里没写清、代码和论文不一致）各一句。
