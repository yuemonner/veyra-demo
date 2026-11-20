// Chinese wording of the interface strings, for a case written in Chinese. Technical terms stay in English.
// Exact strings first, then patterns for strings with numbers in them. Data (log lines, quotes) is never passed here.

export const DICT: Record<string, string> = {
  // Top bar and views
  "Upload data": "上传数据", "Upload logs": "上传日志", "Debug with Veyra": "和 Veyra 一起排查", "Download report": "下载报告",
  "Investigate again": "重新分析", "Investigating…": "分析中…", "Share": "分享", "Engine report": "引擎报告",
  "Investigation": "排查", "Log": "日志", "Record": "记录",
  "Loading the field case…": "正在加载案例…", "After a quiet period the first visit can take up to a minute.": "服务空闲后首次打开可能需要一分钟。",
  "Loading…": "加载中…",
  // Mode line
  "Replay of a recorded investigation": "录制排查的回放", "Rules-only analysis, no AI model": "仅规则分析，未使用 AI 模型", "Live AI investigation": "实时 AI 排查",
  "What is this?": "这是什么？", "Interactive replay": "交互式回放", "Rules-only analysis": "仅规则分析",
  "An investigation Veyra already ran, recorded and played back step by step. Nothing is re-run.": "Veyra 之前完成并录制的一次排查，逐步回放，不会重新运行。",
  "Fixed checks with no AI model: on a text log, the first error, the warnings before it, restarts; on a recording or table, Veyra's signal rules.":
    "不使用 AI 模型的固定检查：文本日志看第一条 error、它之前的 warning 和重启；录制数据或表格用 Veyra 的信号规则。",
  "Veyra's AI investigator decides and runs checks on the original text log, and every quoted line is verified. Text logs today.":
    "Veyra 的 AI 排查员自己决定并在原始文本日志上执行检查，每一条引用都经过核对。目前支持文本日志。",
  "Not on this public demo. Offered in a scheduled technical demo.": "公开演示不提供，可预约技术演示。",
  // Investigation view
  "Read the evidence sheet": "读取证据表", "Listed the events": "列出事件", "Listed the anomaly candidates": "列出异常候选",
  "Each line is quoted from Veyra's evidence sheet for this recording: lines computed from the recorded signals, reproducible from them, and verified. The note beside it says what it shows.":
    "每一条都引用 Veyra 为这份录制数据生成的证据表：由记录的信号计算得出，可以从原始数据重现，并经过核对。旁边的标签说明它表示什么。",
  "Probable cause": "可能原因", "Cause not established": "原因未确定", "No failure found in the data": "数据里没有发现故障",
  "Why Veyra thinks so": "Veyra 的依据", "Already checked": "已经查过的",
  "Each line is quoted from the data and verified against the file. The note beside it says whether it is observed in that line or inferred from it.":
    "每一条都引用原始数据，并和文件逐行核对过。旁边的标签说明它是数据里直接看到的，还是推断出来的。",
  "What you found": "你的排查结果", "Not established by the data": "数据还不能确定的", "Earlier cases:": "历史案例：",
  "Where the fault can be": "故障可能在哪里", "Your checks changed the picture": "你的检查改变了判断", "Your result changed the picture": "你的结果改变了判断",
  "Leading": "最可能", "Open": "待查", "Weakened": "可能性下降", "Ruled out": "已排除",
  "Investigating": "分析中", "Veyra is investigating the data": "Veyra 正在分析数据", "Starting": "开始",
  "Deciding the next check": "正在决定下一步检查",
  "You don't need to stay on this page. Each check below runs on the original data.": "不用一直停在这个页面。下面每一步都是在原始数据上执行的。",
  "Reading the log and running the checks it can do itself.": "读取日志，先做它自己能做的检查。",
  "Continuing with what the engineer found.": "根据工程师的检查结果继续分析。",
  "Copy steps": "复制步骤", "Copied": "已复制", "Run it. What did you find?": "做完这个检查后，你看到了什么？",
  "Rules out": "排除", "Weakens": "削弱", "Leaves": "保留", "Then": "然后",
  "I can't do this — find another way": "这个做不了，换个方法",
  "Continue with this result": "用这个结果继续", "Go to review": "去复核", "Cancel": "取消",
  "Save and continue the investigation": "保存并继续排查", "Saving…": "保存中…",
  "Why not? (optional)": "为什么做不了？（选填）", "What exactly did you see? (optional, helps the next step)": "具体看到了什么？（选填，有助于下一步）",
  "Replay from round 1": "从第 1 轮重播", "Continue now": "现在继续", "Pause here": "在这里暂停", "Skip to the current state": "直接看当前结论",
  "Replay from the start": "从头重播", "Review & close": "复核并关闭",
  "Two questions. Veyra writes the record and the lesson.": "回答两个问题，Veyra 会写好记录和经验。",
  "Is this conclusion right?": "这个结论对吗？", "After the fix, is the symptom gone?": "修复后，故障现象还在吗？",
  "Is the machine back in service?": "车辆恢复运营了吗？", "Gone": "消失了", "Still there": "还在", "Not checked yet": "还没确认",
  "No fix applied": "还没修", "Yes": "是", "No": "否", "Don't know": "不知道", "Confirm and close": "确认并关闭", "Not yet": "先不关",
  "Closing…": "关闭中…",
  "Your answers are recorded as what you accepted and observed. They do not verify the root cause: that needs an inspection, a reproduction or a fix confirmed by evidence.":
    "你的回答会被记录为你认可和观察到的内容。它们不等于根因已验证：根因需要拆检、复现，或有数据证明修复有效。",
  "Case closed · candidate lesson saved": "案例已关闭 · 经验已保存（待验证）",
  "The log, as Veyra read it": "Veyra 读到的日志", "Case record": "案例记录",
  "investigation rounds": "轮排查", "checks run by Veyra": "项 Veyra 执行的检查", "actions by engineers": "项工程师操作",
  "Full ledger and integrity details": "完整记录和完整性校验",
  "Possible cause": "可能原因", "Anomaly": "异常", "Effect of the failure": "故障造成的结果", "Routine": "常规",
  "Not the cause here": "这次不是原因", "Dismiss in this case": "本案忽略", "Not routine": "不是常规", "Undo": "撤销",
  "Veyra has not investigated this case yet": "Veyra 还没有分析这个案例",
  "Investigate now": "开始分析", "Read the log now": "读取日志",
  "Veyra reads the original data, runs the checks it can do itself, and gives you one next action.": "Veyra 读取原始数据，先做它自己能做的检查，然后给你一个下一步动作。",
  "This check was already done in the recorded case.": "录制的案例里这项检查已经做过。",
  " Continuing with the engineer's result in a few seconds.": " 几秒后用工程师的结果继续。",
  " Choose the recorded result to continue.": " 选择录制的结果继续。",
  "Recorded with the live AI investigator.": "由实时 AI 调查员录制。",
  "Show the steps": "显示步骤", "Hide the steps": "隐藏步骤",
  // Tags
  "Observed": "观测", "Inferred": "推断", "Engineer report": "工程师报告", "Hypothesis": "假设",
  "Lead, not established": "线索，未确定", "Would settle it": "能下结论的检查", "Veyra's current view": "Veyra 当前判断",
  "headline": "标题",
  // Discussion drawer
  "Bring a guess or a bench result. The AI investigator tests it against the original data, says which parts hold, and proposes the next check.":
    "说出你的判断或台架测试结果。AI 调查员会用原始数据验证，告诉你哪些部分成立，并给出下一步检查。",
  "Investigate together": "一起排查", "Quick checks": "快速检查", "AI investigator · open-ended": "AI 调查员 · 开放式讨论",
  "fixed · in your browser · no AI": "固定检查 · 在浏览器里运行 · 不用 AI",
  "The recorded session ends here: the next step would be the engineer running the check above and telling Veyra what they found. Live rounds need the AI investigator, which is off on the public demo. The quick checks (fixed, in your browser) still work.":
    "录制的会话到这里结束：下一步应该是工程师去做上面的检查，再把结果告诉 Veyra。实时讨论需要 AI 调查员，公开演示里没有开启。快速检查（固定检查，在浏览器里运行）仍然可以用。",
  "A record linked to data: the fix removed the fault in data recorded after it, or a reproduction recorded with its data. An engineer's word alone (an inspection note, 'the fix held') is shown as reported, not verified.":
    "需要一条带数据的记录：修复后记录的数据证明故障消失，或复现时同时记录了数据。只有工程师的说法（拆检记录、“修好了”）只算报告，不算已验证。", "Investigation replay": "排查复盘", "the engineers' steps · against the data": "工程师的每一步 · 放回数据里看",
  "How the engineers investigated this case, replayed step by step against the data: what the data showed before each step, and what it shows after.": "工程师是怎么排查这个案例的，一步一步放回数据里复盘：每一步之前数据显示了什么，之后又显示了什么。",
  "Your guess or result": "你的判断或结果", "Veyra queries the data": "Veyra 查询数据", "What holds": "哪些成立", "Next check": "下一步检查",
  "Your result": "你的结果",
  "Recorded session.": "录制的会话。",
  "Play the recorded session": "播放录制的会话", "Show all rounds": "显示全部轮次", "Pause after this round": "这一轮之后暂停",
  "Your message, part by part": "逐条验证你的说法", "Writing the round": "正在写结论", "Querying the data": "正在查询数据",
  "Holds": "成立", "Doesn't hold": "不成立", "Partly": "部分成立", "Data can't tell": "数据无法判断",
  "My guess": "我的判断", "What I found": "我的检查结果", "Send to Veyra": "发送给 Veyra",
  "Leading now:": "当前最可能：", "Still open:": "仍待确认：", "Open the discussion": "打开讨论",
  "Loading the discussion…": "正在加载讨论…",
  "No discussion recorded for this case. Live rounds need the AI investigator, which is off here.": "这个案例没有录制讨论。实时讨论需要 AI 调查员，这里没有开启。",
  // Investigator steps (from the engine, shown as they ran)
  " · where the case stands now.": " · 案例当前的结论。", " · the last investigation round; the case continued in the discussion (current state at the top).": " · 这是最后一轮排查；之后在讨论里继续（当前状态见页面顶部）。",
  "Hazardous check: needs review by a qualified engineer before anyone runs it.": "有危险的检查：任何人动手之前，必须由有资质的工程师审核。", "· where the case stands now.": "· 案例当前的结论。",
  "supports": "支持", "weakens": "削弱", "against": "反对", "Looking for the earlier workspace?": "找旧版工作区？", "Open it": "打开",
  "(manual analysis, older engine).": "（手动分析，旧版引擎）。", "now leading": "变为最可能", "reopened": "重新待查",
  "How sure we are": "结论有多可靠", "Download quality record (CSV)": "下载质量记录（CSV）", "Reproduced": "已复现", "Verified": "已验证",
  "An investigation that points to one explanation, with the main alternatives weakened or ruled out by the data.": "调查指向一个解释，主要的其他可能已被数据削弱或排除。",
  "The symptom reproduced on purpose under stated conditions (bench, field or simulation), recorded by an engineer.": "在写明的工况下（台架、现场或仿真）有意复现了故障现象，并由工程师记录。",
  "Confirmed by inspection, by reproduction, or by data showing the fix removed the fault and it held.": "通过拆检、复现，或有数据证明修复后故障消失且没有复发。",
  "Record a reproduction": "记录一次复现", "An engineer's record moves the case up the ladder; Veyra cannot do it alone.": "只有工程师的记录能把案例往上推一级，Veyra 自己做不到。",
  "Where": "地点", "Field": "现场", "Bench": "台架", "Simulation": "仿真", "Log replay": "日志回放", "Conditions and setup": "工况和设置", "Result": "结果",
  "Partly reproduced": "部分复现", "Not reproduced": "未复现", "Inconclusive": "无法判断", "What you saw": "看到了什么", "Save to the case": "保存到案例",
  "Checks Veyra ran": "Veyra 执行的检查", "Answered from the data": "从数据里回答的问题", "Questions for people": "需要问人的问题", "Data requests": "需要补的数据",
  "Discussion rounds": "讨论轮次", "Investigation time": "AI 调查用时",
  "Data to pull from your systems (nobody in the field needs to answer)": "需要从系统里调取的数据（不用问现场）",
  "Before asking the field": "问现场之前，先查数据", "Why it matters": "为什么要问", "Not in the data": "数据里为什么没有",
  "No signal in this data can show": "这份数据里没有信号能看到的", "Conditions": "工况", "Compare with": "对照对象", "Approval": "审批",
  "Needs an engineer's approval before it is run": "执行前需要工程师批准",
  "Read the whole log": "读取整个日志", "Compared with earlier cases": "对比历史案例", "Read the report and earlier results": "读取报告和之前的结果",
  "Decoded every packet in the log": "解码日志里的每个数据包",
};

const CONF: Record<string, string> = { high: "高", medium: "中等", low: "低" };
const WHERE: Record<string, string> = { remote: "远程", "on site": "现场", "in the lab": "实验室" };

export const RULES: [RegExp, (...m: string[]) => string][] = [
  [/^(Probable cause|Cause not established|No failure found in the data) · (high|medium|low) confidence$/, (_, a, c) => `${DICT[a] || a} · ${CONF[c]}置信度`],
  [/^Veyra ran (\d+) checks? on the original data in ([\d.]+) s · every quoted line verified against the file(?: · (\d+) unverifiable statements? removed)?$/,
    (_, n, s, d) => `Veyra 在原始数据上执行了 ${n} 项检查，用时 ${s} s · 每条引用都已和文件逐行核对${d ? ` · 删除了 ${d} 条无法核实的说法` : ""}`],
  [/^Effects, not causes \((\d+)\)$/, (_, n) => `是结果，不是原因（${n}）`],
  [/^How Veyra investigated \((\d+) steps\)$/, (_, n) => `Veyra 的排查过程（${n} 步）`],
  [/^Playing round (\d+) of (\d+)$/, (_, a, b) => `正在播放第 ${a} 轮，共 ${b} 轮`],
  [/^Round (\d+) of (\d+) starts in a few seconds$/, (_, a, b) => `第 ${a} 轮（共 ${b} 轮）几秒后开始`],
  [/^Continue: round (\d+) of (\d+)$/, (_, a, b) => `继续：第 ${a} 轮，共 ${b} 轮`],
  [/^Your next action(?: · (.+))?$/, (_, w) => `你的下一步动作${w ? ` · ${WHERE[w] || w}` : ""}`],
  [/^Next action proposed by this investigation round \(superseded: the current next check is at the top\)(?: · (.+))?$/, (_, w) => `这一轮排查提出的下一步（已被取代：当前的下一步见页面顶部）${w ? ` · ${WHERE[w] || w}` : ""}`],
  [/^(\d+) of (\d+) still possible$/, (_, a, b) => `${b} 个里还有 ${a} 个可能`],
  [/^· seen at: (.+)$/, (_, w) => `· 观测位置：${w}`],
  [/^Leading: (.+) · (\d+) of (\d+) still possible$/, (_, l, a, b) => `最可能：${l === "none" ? "无" : l} · ${b} 个里还有 ${a} 个可能`],
  [/^Still unknown: (.+)$/s, (_, u) => `仍未确定：${u}`],
  [/^Since this investigation · (\d+) rounds? of discussion with the AI investigator( \(recorded\))?$/, (_, n, r) => `这次排查之后 · 和 AI 调查员讨论了 ${n} 轮${r ? "（录制）" : ""}`],
  [/^Latest next check \(round (\d+)\):$/, (_, n) => `最新的下一步检查（第 ${n} 轮）：`],
  [/^Where the fault can be: (.+)$/s, (_, x) => `故障可能在哪里：${x.replace(/ leading\b/g, " 最可能").replace(/ weakened\b/g, " 可能性下降").replace(/ ruled out\b/g, " 已排除").replace(/ open\b/g, " 待查")}`],
  [/^(\d+) earlier case\(s\) compared$/, (_, n) => `对比了 ${n} 个历史案例`],
  [/^(\d+) occurrences?; followed by (“.*”) after ([\d.]+)–([\d.]+) s$/, (_, n, q, a, b) => `${n} 次；之后 ${a === b ? a : `${a}–${b}`} s 出现 ${q}`],
  [/^Recorded (\S+ \S+) with the live AI investigator \(([^)]+)\) on a local instance\..*SYNTHETIC case.*$/s,
    (_, at, m) => `录制于 ${at}，使用实时 AI 调查员（${m}），在本地实例上运行。工程师一方的消息是为演示编写的脚本，每条按所标角色编写；Veyra 的回复是模型的真实输出，未经修改。合成案例：数据为本演示生成。`],
  [/^Round (\d+) of (\d+)$/, (_, a, b) => `第 ${a} 轮，共 ${b} 轮`],
  [/^Recorded investigation · round (\d+) of (\d+)\.$/, (_, a, b) => `录制的排查 · 第 ${a} 轮，共 ${b} 轮。`],
  [/^Round (\d+): the engineer replies$/, (_, a) => `第 ${a} 轮：工程师回复`],
  [/^Read lines (\d+)\s*[–-]\s*(\d+)$/, (_, a, b) => `读取第 ${a}–${b} 行`],
  // Recording investigations (evidence sheet and its tools)
  [/^Read sheet lines (\d*)\s*[–-]\s*(\d*)$/, (_, a, b) => `读取证据表第 ${a}–${b} 行`],
  [/^Searched the sheet for (“.*”)$/, (_, q) => `在证据表中搜索 ${q}`],
  [/^Looked at (.+) from ([\d.]+) to ([\d.]+) s$/, (_, x, a, b) => `查看 ${x}，${a}–${b} 秒`],
  [/^Found the changes in (.+)$/, (_, x) => `查找 ${x} 的变化`],
  [/^Compared before and after ([\d.]+) s$/, (_, a) => `对比 ${a} 秒前后`],
  [/^Related (.+) and (.+)$/, (_, a, b) => `分析 ${a} 与 ${b} 的关系`],
  [/^Ordered what moved first, ([\d.]+)[–-]([\d.]+) s$/, (_, a, b) => `排出 ${a}–${b} 秒内最先变化的信号`],
  [/^Searched for (“.*”)$/, (_, q) => `搜索 ${q}`],
  [/^Measured (“.*”)$/, (_, q) => `统计 ${q}`],
  [/^(\d[\d,]*) matching lines?$/, (_, n) => `${n} 行匹配`],
  [/^(\d[\d,]*) lines?; first failure at line (\d+)$/, (_, n, f) => `${n} 行；第一个故障在第 ${f} 行`],
  [/^(\d+) occurrences?, every ([\d.]+) s \(median\)$/, (_, n, s) => `${n} 次，间隔约 ${s} s（中位数）`],
  [/^(\d+) lines?$/, (_, n) => `${n} 行`],
  [/^Round (\d+) · (.+) · (guess|result of a check) · scripted for this demo$/, (_, n, who, k) => `第 ${n} 轮 · ${who} · ${k === "guess" ? "判断" : "检查结果"} · 为演示编写`],
  [/^AI investigator · (.+) · recorded (\S+)(?: · ([\d.]+) s)?$/, (_, m, d, s) => `AI 调查员 · ${m} · 录制于 ${d}${s ? ` · ${s} 秒` : ""}`],
  [/^AI investigator · (.+) · live(?: · ([\d.]+) s)?$/, (_, m, s) => `AI 调查员 · ${m} · 实时${s ? ` · ${s} 秒` : ""}`],
  [/^Real data · (.+)$/s, (_, x) => `真实数据 · ${x}`],
  [/^Next check · (.+)$/, (_, w) => `下一步检查 · ${WHERE[w] || w}`],
  [/^Veyra answered (\d+) questions? from the data, so nobody has to ask them\. It asks (\d+) that only a person can answer\.$/,
    (_, a, q) => `Veyra 已经从数据里回答了 ${a} 个问题，不用再问现场；只有 ${q} 个问题需要人来回答。`],
  [/^Veyra answered (\d+) questions? from the data, so nobody has to ask them\. It needs nothing from the field this round\.$/,
    (_, a) => `Veyra 已经从数据里回答了 ${a} 个问题，这一轮不需要再问现场。`],
  [/^Ask: (Field engineer|Customer|Supplier)$/, (_, w) => `问：${({ "Field engineer": "现场工程师", Customer: "客户", Supplier: "供应商" } as Record<string, string>)[w]}`],
  [/^Leaves: (.+)$/s, (_, x) => `保留：${x}`],
  [/^Weakens: (.+)$/s, (_, x) => `削弱：${x}`],
  [/^How to see it: (.+)$/s, (_, x) => `怎么看到：${x}`],
  [/^Window: (.+)$/s, (_, x) => `时间范围：${x}`],
  [/^Must match: (.+)$/s, (_, x) => `工况要求：${x}`],
  [/^Why: (.+)$/s, (_, x) => `原因：${x}`],
];

export function zh(text: string): string | null {
  const k = text.trim();
  if (!k || !/[A-Za-z]/.test(k)) return null;
  const hit = DICT[k];
  if (hit) return text.replace(k, hit);
  for (const [re, f] of RULES) { const m = k.match(re); if (m) return text.replace(k, f(...m)); }
  return null;
}

