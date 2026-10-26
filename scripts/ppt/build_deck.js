// Build the competition deck with pptxgenjs (official pptx skill workflow).
//
// Design: forest-green / cream / amber, dark cover and closing, numbered
// amber badges as the repeated motif. Chinese body uses Microsoft YaHei.
// Run: node scripts/ppt/build_deck.js
const path = require("path");
const pptxgen = require("pptxgenjs");

const ROOT = path.resolve(__dirname, "..", "..");
const SHOTS = path.join(ROOT, "docs", "submission", "screenshots");
const OUT =
  process.env.DECK_OUT ||
  path.join(
    ROOT,
    "docs",
    "submission",
    "【教育-重庆邮电大学-赵乙亦-证据工作台】.pptx"
  );

const C = {
  forest: "163A2C",
  green: "2C6E49",
  sage: "9DBFA5",
  cream: "F6F4EC",
  white: "FFFFFF",
  ink: "16211B",
  muted: "5F6B63",
  amber: "D98E32",
  pale: "E8EDE4",
  darkMuted: "C9D6CA",
};
const FONT = "Microsoft YaHei";

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "证据工作台";
pptx.subject = "第二届全国人工智能应用创新大赛";
pptx.title = "证据工作台";

function text(slide, value, opts = {}) {
  slide.addText(value, {
    fontFace: FONT,
    color: C.ink,
    margin: 0,
    valign: "top",
    breakLine: false,
    ...opts,
  });
}

function badge(slide, number, x, y, size = 0.42, fill = C.amber) {
  slide.addShape(pptx.ShapeType.ellipse, {
    x,
    y,
    w: size,
    h: size,
    fill: { color: fill },
    line: { color: fill, width: 0 },
  });
  text(slide, String(number), {
    x,
    y: y + 0.02,
    w: size,
    h: size - 0.04,
    align: "center",
    valign: "middle",
    fontSize: 14,
    bold: true,
    color: C.white,
  });
}

function titleBlock(slide, kicker, title, dark = false) {
  text(slide, kicker, {
    x: 0.75,
    y: 0.45,
    w: 8,
    h: 0.3,
    fontSize: 11,
    bold: true,
    color: dark ? C.amber : C.green,
    charSpacing: 2,
  });
  text(slide, title, {
    x: 0.75,
    y: 0.75,
    w: 11.8,
    h: 0.7,
    fontSize: 30,
    bold: true,
    color: dark ? C.white : C.ink,
  });
}

function footer(slide, number) {
  text(slide, `证据工作台 · ${number}`, {
    x: 0.75,
    y: 7.08,
    w: 6,
    h: 0.25,
    fontSize: 9,
    color: C.muted,
  });
  text(slide, "第二届全国人工智能应用创新大赛 · 通用赛道", {
    x: 8.0,
    y: 7.08,
    w: 4.6,
    h: 0.25,
    fontSize: 9,
    color: C.muted,
    align: "right",
  });
}

function card(slide, x, y, w, h, fill = C.cream) {
  slide.addShape(pptx.ShapeType.roundRect, {
    x,
    y,
    w,
    h,
    fill: { color: fill },
    line: { color: fill, width: 0 },
    rectRadius: 0.08,
  });
}

function picture(slide, file, x, y, w, h) {
  slide.addImage({
    path: path.join(SHOTS, file),
    x,
    y,
    w,
    h,
    sizing: { type: "contain", w, h },
  });
}

// ---------------------------------------------------------------- 1 cover
{
  const s = pptx.addSlide();
  s.background = { color: C.forest };
  s.addShape(pptx.ShapeType.ellipse, {
    x: 10.6,
    y: -1.2,
    w: 4.2,
    h: 4.2,
    fill: { color: C.green },
    line: { color: C.green, width: 0 },
  });
  text(s, "JIUWENSWARM · EVIDENCE WORKBENCH", {
    x: 0.9,
    y: 1.5,
    w: 9,
    h: 0.35,
    fontSize: 12,
    bold: true,
    color: C.amber,
    charSpacing: 2,
  });
  text(s, "证据工作台", {
    x: 0.9,
    y: 1.95,
    w: 9,
    h: 1.1,
    fontSize: 54,
    bold: true,
    color: C.white,
  });
  text(s, "可审计的多 Agent 资料研读与交付", {
    x: 0.92,
    y: 3.15,
    w: 9.5,
    h: 0.6,
    fontSize: 24,
    color: C.sage,
  });
  text(s, "每条结论都能回到原文，冲突不被消解", {
    x: 0.92,
    y: 3.95,
    w: 9.5,
    h: 0.45,
    fontSize: 16,
    color: C.darkMuted,
  });
  text(s, "https://workbench-335317-9-1515867239.sh.run.tcloudbase.com", {
    x: 0.92,
    y: 6.3,
    w: 11,
    h: 0.3,
    fontSize: 11,
    color: C.sage,
  });
  s.addNotes("开场：一句话说明作品是什么、为谁解决什么问题。不要念标题。");
}

// ------------------------------------------------------- 2 background
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "01 · 作品背景与说明", "政策研读缺的不是答案，是出处");
  const items = [
    ["引用挂在答案末尾", "读者看不出哪句话由哪条原文支撑"],
    ["冲突被悄悄抹平", "两份文件要求不一致时，结论被折中成一个说法"],
    ["完成没有验证", "模型说“已完成”，但结果没有通过任何结构化检查"],
  ];
  items.forEach(([head, body], i) => {
    const y = 1.75 + i * 1.45;
    badge(s, i + 1, 0.85, y + 0.05);
    text(s, head, { x: 1.45, y, w: 7.4, h: 0.4, fontSize: 18, bold: true });
    text(s, body, { x: 1.45, y: y + 0.42, w: 7.4, h: 0.55, fontSize: 13, color: C.muted });
  });
  card(s, 9.1, 1.75, 3.45, 3.95, C.cream);
  text(s, "我们服务的人", { x: 9.4, y: 2.0, w: 2.9, h: 0.35, fontSize: 13, bold: true, color: C.green });
  text(s, "学校管理者 · 教研人员 · 合规负责人", {
    x: 9.4,
    y: 2.45,
    w: 2.9,
    h: 1.2,
    fontSize: 15,
    color: C.ink,
    lineSpacingMultiple: 1.3,
  });
  text(s, "他们承担的是合规责任，所以需要能回查的结论，而不是一段流畅的生成文本。", {
    x: 9.4,
    y: 3.35,
    w: 2.85,
    h: 2.0,
    fontSize: 12,
    color: C.muted,
    lineSpacingMultiple: 1.3,
  });
  footer(s, 2);
  s.addNotes("三个问题各一句，不要展开；最后落到“谁承担合规责任”。");
}

// ---------------------------------------------------- 3 data sources
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "02 · 数据来源", "25 份官方文件，全部可回查");
  text(s, "两类语料", { x: 0.85, y: 1.65, w: 5.3, h: 0.35, fontSize: 15, bold: true });
  const rows = [
    ["AI 治理", "6 份", "92 块", "网信办 · 全国人大官网"],
    ["教育政策", "19 份", "288 块", "教育部 · 湖南省教育厅 · 中国政府网"],
  ];
  rows.forEach((r, i) => {
    const y = 2.1 + i * 1.15;
    card(s, 0.85, y, 5.5, 0.95, C.cream);
    text(s, r[0], { x: 1.05, y: y + 0.15, w: 1.5, h: 0.3, fontSize: 14, bold: true, color: C.green });
    text(s, `${r[1]} / ${r[2]}`, { x: 1.05, y: y + 0.5, w: 2.3, h: 0.3, fontSize: 12, color: C.muted });
    text(s, r[3], { x: 3.3, y: y + 0.3, w: 2.9, h: 0.5, fontSize: 11, color: C.muted });
  });
  text(s, "权威性：只取官方站点原文，来源清单随仓库提交。", {
    x: 0.85,
    y: 4.5,
    w: 5.5,
    h: 0.5,
    fontSize: 12,
    color: C.muted,
  });
  text(s, "时效性：优先现行有效版本；无法核对纯文本的文件不纳入。", {
    x: 0.85,
    y: 4.95,
    w: 5.5,
    h: 0.5,
    fontSize: 12,
    color: C.muted,
  });
  s.addChart(
    pptx.ChartType.bar,
    [{ name: "切片数", labels: ["AI 治理", "教育政策"], values: [92, 288] }],
    {
      x: 6.7,
      y: 1.75,
      w: 5.8,
      h: 4.3,
      barDir: "bar",
      showTitle: true,
      title: "切片数（块）",
      titleColor: C.ink,
      titleFontSize: 13,
      showValue: true,
      dataLabelPosition: "outEnd",
      dataLabelColor: C.ink,
      dataLabelFontSize: 12,
      chartColors: [C.green],
      catAxisLabelColor: C.muted,
      valAxisLabelColor: C.muted,
      valGridLine: { color: C.pale, size: 1 },
      catGridLine: { style: "none" },
      showLegend: false,
      valAxisMaxVal: 320,
      valAxisMinVal: 0,
    }
  );
  footer(s, 3);
  s.addNotes("强调来源可回查；不吹数量，直接说 25 份 380 块。");
}

// --------------------------------------------------- 4 knowledge base
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "03 · 知识库内容", "每个工作区一个独立索引");
  const stats = [
    ["25", "份文档", "官方公开文本"],
    ["380", "个切片", "400 字 / 80 重叠"],
    ["2", "个工作区", "互不检索对方"],
    ["0", "跨域泄漏", "离线断言钉住"],
  ];
  stats.forEach(([num, label, sub], i) => {
    const x = 0.85 + (i % 2) * 3.0;
    const y = 1.8 + Math.floor(i / 2) * 1.75;
    card(s, x, y, 2.7, 1.45, C.cream);
    text(s, num, { x: x + 0.2, y: y + 0.1, w: 2.3, h: 0.6, fontSize: 34, bold: true, color: C.green });
    text(s, label, { x: x + 0.22, y: y + 0.72, w: 2.2, h: 0.3, fontSize: 13, bold: true });
    text(s, sub, { x: x + 0.22, y: y + 1.05, w: 2.3, h: 0.3, fontSize: 10, color: C.muted });
  });
  text(s, "切片保留：来源 URI · 内容修订 · chars 定位 · 所属工作区", {
    x: 7.0,
    y: 1.9,
    w: 5.6,
    h: 0.5,
    fontSize: 14,
    bold: true,
  });
  text(
    s,
    "授权边界是“打开哪个数据库”，不是查询参数过滤。\n检索 SQL 不跨语料；两个工作区互相检索不到对方内容。\n无向量时标记 keyword_only，不伪造相似度。",
    {
      x: 7.0,
      y: 2.45,
      w: 5.5,
      h: 2.4,
      fontSize: 12,
      color: C.muted,
      lineSpacingMultiple: 1.45,
    }
  );
  footer(s, 4);
  s.addNotes("四个数字是事实卡；右下三条是设计取舍，不展开术语。");
}

// ------------------------------------------------------ 5 preprocessing
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "03 · 数据预处理", "从官方页面到可检索证据");
  const steps = [
    ["抓取", "curl 获取官方页面\n按页面编码解码"],
    ["解析", "正文段落抽取\n去导航 / 页脚"],
    ["切片", "400 字 / 80 重叠\n记录 chars 定位"],
    ["索引", "SQLite FTS5 trigram\n新版本先删旧块"],
    ["取证", "成员调用 retrieve\n证据带完整来源"],
  ];
  steps.forEach(([head, body], i) => {
    const x = 0.65 + i * 2.5;
    badge(s, i + 1, x + 0.05, 2.0, 0.5);
    text(s, head, { x, y: 2.65, w: 2.1, h: 0.4, fontSize: 17, bold: true });
    text(s, body, {
      x,
      y: 3.1,
      w: 2.1,
      h: 1.2,
      fontSize: 11,
      color: C.muted,
      lineSpacingMultiple: 1.35,
    });
    if (i < steps.length - 1) {
      slideArrow(s, x + 1.95, 2.5);
    }
  });
  card(s, 0.85, 4.75, 11.6, 1.35, C.cream);
  text(s, "在 Agent 中的用途", { x: 1.1, y: 4.95, w: 3, h: 0.3, fontSize: 13, bold: true, color: C.green });
  text(
    s,
    "成员检索到的每条证据都带着来源、修订与定位，直接进入结论引用校验；没有检索到的证据不能作为事实依据。",
    { x: 1.1, y: 5.3, w: 11.0, h: 0.6, fontSize: 12, color: C.muted }
  );
  footer(s, 5);
  s.addNotes("流程只讲输入输出；最后一句是差异点：证据必须真的被检索到。");
}

function slideArrow(s, x, y) {
  s.addShape(pptx.ShapeType.rightArrow, {
    x,
    y,
    w: 0.4,
    h: 0.28,
    fill: { color: C.sage },
    line: { color: C.sage, width: 0 },
  });
}

// --------------------------------------------------------- 6 method
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "04 · 分析方法", "为什么是“多 Agent + 确定性门禁”");
  card(s, 0.85, 1.75, 5.4, 4.5, C.cream);
  text(s, "单 Agent", { x: 1.1, y: 1.95, w: 4.8, h: 0.4, fontSize: 18, bold: true, color: C.muted });
  text(
    s,
    "分工、依赖和完成判定都在一段上下文里。\n出了错，无法定位是谁、哪一步。\n模型说完成，就是完成。",
    { x: 1.1, y: 2.5, w: 4.85, h: 2.8, fontSize: 13, color: C.muted, lineSpacingMultiple: 1.5 }
  );
  card(s, 6.95, 1.75, 5.5, 4.5, C.pale);
  text(s, "多 Agent + 门禁", { x: 7.2, y: 1.95, w: 4.9, h: 0.4, fontSize: 18, bold: true, color: C.green });
  const pts = [
    "Leader 出计划，应用侧校验：无环、依赖、验收",
    "原生任务板派发，成员结构化回传",
    "引用、完成、执行有效性由程序判定",
  ];
  pts.forEach((p, i) => {
    badge(s, i + 1, 7.2, 2.55 + i * 0.95, 0.36, C.green);
    text(s, p, { x: 7.75, y: 2.55 + i * 0.95, w: 4.4, h: 0.8, fontSize: 13, lineSpacingMultiple: 1.25 });
  });
  text(s, "模型自评不能替代程序校验。", {
    x: 7.2,
    y: 5.55,
    w: 4.8,
    h: 0.35,
    fontSize: 12,
    italic: true,
    color: C.muted,
  });
  footer(s, 6);
  s.addNotes("左右对比，最后一句是立场：不把完成声明当验收。");
}

// ------------------------------------------------------ 7 task graph
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "05 · 问题理解与逻辑论证", "任务板是框架自己的，不是文本里的“假装分工”");
  card(s, 0.75, 1.55, 11.85, 3.6, C.cream);
  picture(s, "task-graph-crop.png", 0.95, 1.68, 11.45, 3.35);
  const pts = [
    ["原生调度", "scheduled 调度器按 assignee 拉起成员"],
    ["结构化回传", "成员通过受校验的 submit 工具提交 claims / evidence"],
    ["可审计", "每个 result 绑定 execution，过期执行不能覆盖"],
  ];
  pts.forEach(([head, body], i) => {
    const x = 0.85 + i * 3.95;
    badge(s, i + 1, x, 5.45);
    text(s, head, { x: x + 0.55, y: 5.43, w: 3.1, h: 0.35, fontSize: 16, bold: true });
    text(s, body, { x: x + 0.55, y: 5.82, w: 3.2, h: 1.0, fontSize: 11, color: C.muted, lineSpacingMultiple: 1.3 });
  });
  footer(s, 7);
  s.addNotes("指着截图讲：两个任务、两名成员、状态在动。强调不是 Leader 在文字里分工。");
}

// ------------------------------------------------------ 8 citation funnel
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "05 · 逻辑论证", "从 380 个切片到可支撑的引用");
  const bars = [
    ["380", "全库切片", 10.4],
    ["101", "单任务检索证据", 8.2],
    ["12", "fact 结论", 5.8],
    ["12", "通过校验的引用", 4.2],
  ];
  bars.forEach(([num, label, w], i) => {
    const y = 1.85 + i * 1.05;
    const x = 0.85 + (10.4 - w) / 2;
    s.addShape(pptx.ShapeType.roundRect, {
      x,
      y,
      w,
      h: 0.78,
      fill: { color: i === 3 ? C.green : C.pale },
      line: { color: i === 3 ? C.green : C.pale, width: 0 },
      rectRadius: 0.06,
    });
    text(s, `${num}  ${label}`, {
      x,
      y: y + 0.16,
      w,
      h: 0.45,
      align: "center",
      fontSize: 15,
      bold: true,
      color: i === 3 ? C.white : C.ink,
    });
  });
  card(s, 11.35, 1.85, 1.35, 2.55, C.cream);
  text(s, "规则", { x: 11.5, y: 2.0, w: 1.1, h: 0.3, fontSize: 12, bold: true, color: C.green });
  text(s, "事实必须引用\n本任务实际\n收到的证据", {
    x: 11.45,
    y: 2.45,
    w: 1.15,
    h: 2.8,
    fontSize: 10,
    color: C.muted,
    lineSpacingMultiple: 1.4,
  });
  footer(s, 8);
  s.addNotes("数字来自示例运行：101 条证据、12 条 fact 全部有引用。说明被裁掉的证据不算支撑。");
}

// ------------------------------------------------------ 9 run data
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "05 · 真实运行数据", "不预置答案，全部来自真实回合");
  const kpis = [
    ["40", "离线检查脚本", "645 项断言 · 0 失败"],
    ["55 KB", "教育评估报告", "returned / team_completed"],
    ["2", "公网工作区", "AI 治理 59 KB · 教育 55 KB"],
  ];
  kpis.forEach(([num, label, sub], i) => {
    const x = 0.85 + i * 4.0;
    card(s, x, 1.8, 3.65, 2.2, C.cream);
    text(s, num, { x: x + 0.25, y: 2.0, w: 3.1, h: 0.7, fontSize: 38, bold: true, color: C.green });
    text(s, label, { x: x + 0.27, y: 2.85, w: 3.1, h: 0.35, fontSize: 14, bold: true });
    text(s, sub, { x: x + 0.27, y: 3.25, w: 3.1, h: 0.5, fontSize: 11, color: C.muted });
  });
  text(s, "真实回合：Leader 规划 2 个任务，两名成员检索并逐条引用；以原生 team.completed 结束。", {
    x: 0.85,
    y: 4.5,
    w: 11.6,
    h: 0.5,
    fontSize: 13,
    color: C.muted,
  });
  text(s, "失败也可见：超时、空闲、崩溃都写明确原因，不伪装成完成。", {
    x: 0.85,
    y: 5.05,
    w: 11.6,
    h: 0.5,
    fontSize: 13,
    color: C.muted,
  });
  footer(s, 9);
  s.addNotes("先给三个数字；不展开检查脚本细节。");
}

// ------------------------------------------------------ 10 innovation
{
  const s = pptx.addSlide();
  s.background = { color: C.forest };
  titleBlock(s, "06 · 创新点展示", "三个可验证的差异", true);
  const items = [
    ["结论级引用", "引用是否有效，按任务实际检索集判定，而不是按语料里有没有。"],
    ["冲突保留", "跨来源、同句式、不同数值并列展示；系统不裁决，也不取平均。"],
    ["真实委派", "原生调度器拉起成员；只有 team.completed 且退出码 0 才算成功。"],
  ];
  items.forEach(([head, body], i) => {
    const y = 1.85 + i * 1.55;
    badge(s, i + 1, 0.9, y + 0.05);
    text(s, head, { x: 1.55, y, w: 9.8, h: 0.4, fontSize: 20, bold: true, color: C.white });
    text(s, body, { x: 1.55, y: y + 0.45, w: 10.3, h: 0.8, fontSize: 13, color: C.darkMuted, lineSpacingMultiple: 1.3 });
  });
  footer(s, 10);
  s.addNotes("每个创新点一句话 + 一条证据；不要念三遍“创新”。");
}

// ------------------------------------------------------ 11 perspective
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "06 · 独特视角", "把 RAG 的评价对象换掉");
  card(s, 0.85, 1.75, 11.6, 1.9, C.pale);
  text(s, "不是“答案像不像”，而是“结论有没有出处、冲突有没有保留、完成有没有验证”。", {
    x: 1.2,
    y: 2.2,
    w: 10.9,
    h: 1.0,
    fontSize: 21,
    bold: true,
    color: C.forest,
    lineSpacingMultiple: 1.3,
  });
  text(s, "教育 × 合规审核", { x: 0.9, y: 4.0, w: 5.5, h: 0.4, fontSize: 17, bold: true, color: C.green });
  text(
    s,
    "不是“会聊天的法律助手”，而是“能交差的可审计工作底稿”。\n先解决“谁依据哪条、哪里不一致”，再谈教学创新。",
    { x: 0.9, y: 4.45, w: 5.6, h: 1.5, fontSize: 13, color: C.muted, lineSpacingMultiple: 1.45 }
  );
  text(s, "对 AI 进校园的意义", { x: 6.9, y: 4.0, w: 5.5, h: 0.4, fontSize: 17, bold: true, color: C.green });
  text(
    s,
    "AI 作业批改能不能进校，取决于准入、数据和人审，而不是功能演示。\n把政策要求落成可验收的清单，才是学校能用的助手。",
    { x: 6.9, y: 4.45, w: 5.5, h: 1.5, fontSize: 13, color: C.muted, lineSpacingMultiple: 1.45 }
  );
  footer(s, 11);
  s.addNotes("跨学科视角：教育场景 + 合规审核方法，不是通用问答。");
}

// ------------------------------------------------------ 12 results
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "07 · 结果解读", "把“读文件”变成“可核对的清单”");
  const cols = [
    ["以前", ["通读多份文件", "结论靠印象", "冲突看不到", "无法复核"], C.muted, C.cream],
    ["现在", ["结论带 [fact] 与证据", "来源 / 章节可回查", "冲突候选并列", "报告可下载"], C.green, C.pale],
  ];
  cols.forEach(([head, items, color, fill], i) => {
    const x = 0.85 + i * 6.0;
    card(s, x, 1.75, 5.55, 3.7, fill);
    text(s, head, { x: x + 0.3, y: 1.95, w: 4.9, h: 0.4, fontSize: 18, bold: true, color });
    items.forEach((item, j) => {
      text(s, `${i === 1 ? "✓" : "—"}  ${item}`, {
        x: x + 0.3,
        y: 2.55 + j * 0.65,
        w: 4.9,
        h: 0.4,
        fontSize: 13,
        color: i === 1 ? C.ink : C.muted,
      });
    });
  });
  text(s, "不是万能问答：未收录的资料不会凭空出现，证据不足会显式说明。", {
    x: 0.85,
    y: 5.75,
    w: 11.6,
    h: 0.5,
    fontSize: 12,
    italic: true,
    color: C.muted,
  });
  footer(s, 12);
  s.addNotes("左右各四条，讲使用前后的差别；最后补一句边界。");
}

// ------------------------------------------------------ 13 problems 1
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "07 · 问题与建议（1/2）", "两个真实缺口，不藏着");
  const rows = [
    ["公网不开放上传与 shell/文件工具", "赛后增加本地/管理员模式 + 沙箱工具包，公网保持只读。"],
    ["未挂持久卷，重启后运行历史丢失", "挂 CFS 或对象存储归档；索引可自动重建，历史需持久化。"],
  ];
  rows.forEach(([q, a], i) => {
    const y = 1.75 + i * 2.2;
    card(s, 0.85, y, 11.6, 1.85, i === 0 ? C.cream : C.pale);
    badge(s, i + 1, 1.1, y + 0.2, 0.4, C.green);
    text(s, q, { x: 1.7, y: y + 0.18, w: 10.4, h: 0.4, fontSize: 16, bold: true });
    text(s, `建议：${a}`, { x: 1.7, y: y + 0.75, w: 10.4, h: 0.8, fontSize: 13, color: C.muted, lineSpacingMultiple: 1.3 });
  });
  footer(s, 13);
  s.addNotes("主动说缺口，评委会更信前面的数据。");
}

// ------------------------------------------------------ 14 problems 2
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "07 · 问题与建议（2/2）", "优化优先级");
  const steps = [
    ["持久化", "历史与配额不随重启丢失"],
    ["通用工具包", "文件 / 命令 / 网络，沙箱内启用"],
    ["更多工作区", "换语料即可复制到新领域"],
    ["自适应路由", "按任务复杂度选模型"],
  ];
  steps.forEach(([head, body], i) => {
    const x = 0.8 + i * 3.05;
    badge(s, i + 1, x, 2.0, 0.5);
    text(s, head, { x, y: 2.7, w: 2.7, h: 0.4, fontSize: 16, bold: true });
    text(s, body, { x, y: 3.15, w: 2.6, h: 0.9, fontSize: 12, color: C.muted, lineSpacingMultiple: 1.3 });
  });
  card(s, 0.85, 4.6, 11.6, 1.35, C.cream);
  text(s, "先做持久化，再做工具包：前者影响评委体验，后者影响产品边界。", {
    x: 1.1,
    y: 4.95,
    w: 11.0,
    h: 0.6,
    fontSize: 14,
    color: C.ink,
  });
  footer(s, 14);
  s.addNotes("顺序有理由：先保体验，再扩能力。");
}

// ------------------------------------------------------ 15 risks
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "07 · 风险识别与应对", "把风险写在明处");
  const risks = [
    ["数据安全 / 隐私", "公网只读预置语料、无上传；会话隔离；密钥只在服务端。"],
    ["幻觉 / 误引", "结论级引用 + 结构化验收；未验证结论改标 [unverified]。"],
    ["成本 / 滥用", "并发与每日配额、回合截止时间、单实例锁。"],
    ["平台依赖", "单容器镜像；换平台只换部署配置。"],
  ];
  risks.forEach(([head, body], i) => {
    const x = 0.85 + (i % 2) * 6.0;
    const y = 1.75 + Math.floor(i / 2) * 2.25;
    card(s, x, y, 5.55, 1.95, i % 2 === 0 ? C.cream : C.pale);
    text(s, head, { x: x + 0.3, y: y + 0.2, w: 4.9, h: 0.4, fontSize: 16, bold: true, color: C.green });
    text(s, body, { x: x + 0.3, y: y + 0.7, w: 4.95, h: 1.1, fontSize: 12, color: C.muted, lineSpacingMultiple: 1.35 });
  });
  footer(s, 15);
  s.addNotes("四类风险各一句应对；数据安全是评委最关心的。");
}

// ------------------------------------------------------ 16 UI
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "08 · 应用结果呈现", "工作台：登录、选工作区、给目标");
  picture(s, "login.png", 0.8, 1.7, 4.2, 4.5);
  picture(s, "chat-start.png", 5.3, 1.7, 5.5, 4.5);
  card(s, 11.0, 1.8, 1.6, 4.2, C.cream);
  text(s, "默认工作区\n已切到教育", { x: 11.15, y: 2.0, w: 1.35, h: 1.0, fontSize: 11, bold: true, color: C.green, lineSpacingMultiple: 1.3 });
  text(s, "目标只用自然语言，不预置任务拆法。", { x: 11.15, y: 3.2, w: 1.35, h: 2.2, fontSize: 10, color: C.muted, lineSpacingMultiple: 1.35 });
  footer(s, 16);
  s.addNotes("左边登录，右边选工作区和目标；强调默认就是教育。");
}

// ------------------------------------------------------ 17 prompt
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "08 · 提示词示例 1", "一次“评估”，不是一次“问答”");
  card(s, 0.85, 1.7, 11.6, 1.55, C.pale);
  text(s, "学校拟引入一款 AI 作业批改工具，请依据教育政策与 AI 教育指南，评估其合规要求并给出依据条文。", {
    x: 1.15,
    y: 1.95,
    w: 11.0,
    h: 1.1,
    fontSize: 17,
    color: C.forest,
    lineSpacingMultiple: 1.35,
  });
  const points = [
    ["准入", "进校审查 + 白名单"],
    ["数据", "最小授权 + 非必要不强制同意"],
    ["人审", "AI 批改只作建议，教师确认"],
  ];
  points.forEach(([head, body], i) => {
    const x = 0.85 + i * 4.0;
    badge(s, i + 1, x, 3.7, 0.4, C.green);
    text(s, head, { x: x + 0.55, y: 3.68, w: 3.0, h: 0.35, fontSize: 15, bold: true });
    text(s, body, { x: x + 0.55, y: 4.05, w: 3.2, h: 0.7, fontSize: 11, color: C.muted, lineSpacingMultiple: 1.3 });
  });
  text(s, "报告在“结论”下逐条列出 [fact] 与依据；点结论可只看它引用的原文。", {
    x: 0.85,
    y: 5.3,
    w: 11.6,
    h: 0.5,
    fontSize: 12,
    color: C.muted,
    italic: true,
  });
  footer(s, 17);
  s.addNotes("念一遍提示词；强调输出是合规清单，不是一段泛泛的回答。");
}

// ------------------------------------------------------ 18 report + conflicts
{
  const s = pptx.addSlide();
  s.background = { color: C.white };
  titleBlock(s, "08 · 报告与冲突面板", "结论、依据、冲突三件事同屏");
  picture(s, "report-conflicts.png", 0.75, 1.65, 8.2, 4.9);
  card(s, 9.2, 1.75, 3.35, 4.7, C.cream);
  text(s, "看点", { x: 9.45, y: 1.95, w: 2.9, h: 0.3, fontSize: 13, bold: true, color: C.green });
  const notes = [
    "事实逐条标 [fact]",
    "每条带依据与定位",
    "冲突面板并列，不裁决",
    "空状态写明检测规则",
  ];
  notes.forEach((n, i) => {
    text(s, `· ${n}`, { x: 9.45, y: 2.45 + i * 0.8, w: 2.85, h: 0.65, fontSize: 11, color: C.muted, lineSpacingMultiple: 1.25 });
  });
  footer(s, 18);
  s.addNotes("截图就是评委会看到的报告；冲突为空时解释规则，不暗示一致。");
}

// ------------------------------------------------------ 19 closing
{
  const s = pptx.addSlide();
  s.background = { color: C.forest };
  s.addShape(pptx.ShapeType.ellipse, {
    x: -1.6,
    y: 4.6,
    w: 4.6,
    h: 4.6,
    fill: { color: C.green },
    line: { color: C.green, width: 0 },
  });
  text(s, "现在可以打开链接试一次", {
    x: 0.9,
    y: 1.7,
    w: 10.5,
    h: 0.7,
    fontSize: 34,
    bold: true,
    color: C.white,
  });
  text(s, "两个工作区 · 真实模型 · 每条结论可回查", {
    x: 0.92,
    y: 2.55,
    w: 10.5,
    h: 0.5,
    fontSize: 17,
    color: C.sage,
  });
  text(s, "https://workbench-335317-9-1515867239.sh.run.tcloudbase.com", {
    x: 0.92,
    y: 4.0,
    w: 11,
    h: 0.35,
    fontSize: 13,
    color: C.white,
  });
  text(s, "账号与密码随作品在报名系统提交。", {
    x: 0.92,
    y: 4.45,
    w: 9,
    h: 0.35,
    fontSize: 11,
    color: C.darkMuted,
  });
  s.addNotes("收尾只做邀请，不重复内容。");
}

pptx.writeFile({ fileName: OUT });
console.log("saved", OUT, "slides", pptx.slides.length);
