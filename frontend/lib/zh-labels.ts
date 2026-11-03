// Chinese wording of the interface strings, for a case written in Chinese. Technical terms stay in English.
// Exact strings first, then patterns for strings with numbers in them. Data (log lines, quotes) is never passed here.

export const DICT: Record<string, string> = {
  // Top bar and views
  "Upload data": "上传数据", "Upload logs": "上传日志", "Debug with Veyra": "和 Veyra 一起排查", "Download report": "下载报告",
  "Investigate again": "重新分析", "Investigating…": "分析中…", "Share": "分享", "Engine report": "引擎报告",
  "Investigation": "排查", "Log": "日志", "Record": "记录",
  "Loading the field case…": "正在加载案例…", "After a quiet period the first visit can take up to a minute.": "服务空闲后首次打开可能需要一分钟。",
  "Loading…": "加载中…",
  // Investigation view
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
  "Recorded with the live AI investigator.": "由实时 AI investigator 录制。",
  "Show the steps": "显示步骤", "Hide the steps": "隐藏步骤",
  // Tags
  "Observed": "观测", "Inferred": "推断", "Engineer report": "工程师报告", "Hypothesis": "假设",
  "Lead, not established": "线索，未确定", "Would settle it": "能下结论的检查", "Veyra's current view": "Veyra 当前判断",
  "headline": "标题",
  // Discussion drawer
  "Bring a guess or a bench result. The AI investigator tests it against the original data, says which parts hold, and proposes the next check.":
    "说出你的判断或台架测试结果。AI investigator 会用原始数据验证，告诉你哪些部分成立，并给出下一步检查。",
  "Investigate together": "一起排查", "Quick checks": "快速检查",
  "Your guess or result": "你的判断或结果", "Veyra queries the data": "Veyra 查询数据", "What holds": "哪些成立", "Next check": "下一步检查",
  "Your result": "你的结果",
  "Recorded session.": "录制的会话。",
  "Play the recorded session": "播放录制的会话", "Show all rounds": "显示全部轮次",
  "Your message, part by part": "逐条验证你的说法", "Writing the round": "正在写结论", "Querying the data": "正在查询数据",
  "Holds": "成立", "Doesn't hold": "不成立", "Partly": "部分成立", "Data can't tell": "数据无法判断",
  "My guess": "我的判断", "What I found": "我的检查结果", "Send to Veyra": "发送给 Veyra",
  "Leading now:": "当前最可能：", "Still open:": "仍待确认：", "Open the discussion": "打开讨论",
  "Loading the discussion…": "正在加载讨论…",
  "No discussion recorded for this case. Live rounds need the AI investigator, which is off here.": "这个案例没有录制讨论。实时讨论需要 AI investigator，这里没有开启。",
  // Investigator steps (from the engine, shown as they ran)
  " · where the case stands now.": " · 案例当前的结论。", "· where the case stands now.": "· 案例当前的结论。",
  "supports": "支持", "weakens": "削弱", "against": "反对", "Looking for the earlier workspace?": "找旧版工作区？", "Open it": "打开",
  "(manual analysis, older engine).": "（手动分析，旧版引擎）。", "now leading": "变为最可能", "reopened": "重新待查",
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
  [/^Your next action(?: · (.+))?$/, (_, w) => `你的下一步动作${w ? ` · ${WHERE[w] || w}` : ""}`],
  [/^(\d+) of (\d+) still possible$/, (_, a, b) => `${b} 个里还有 ${a} 个可能`],
  [/^· seen at: (.+)$/, (_, w) => `· 观测位置：${w}`],
  [/^Leading: (.+) · (\d+) of (\d+) still possible$/, (_, l, a, b) => `最可能：${l === "none" ? "无" : l} · ${b} 个里还有 ${a} 个可能`],
  [/^Still unknown: (.+)$/s, (_, u) => `仍未确定：${u}`],
  [/^Since this investigation · (\d+) rounds? of discussion with the AI investigator( \(recorded\))?$/, (_, n, r) => `这次排查之后 · 和 AI investigator 讨论了 ${n} 轮${r ? "（录制）" : ""}`],
  [/^Latest next check \(round (\d+)\):$/, (_, n) => `最新的下一步检查（第 ${n} 轮）：`],
  [/^Where the fault can be: (.+)$/s, (_, x) => `故障可能在哪里：${x.replace(/ leading\b/g, " 最可能").replace(/ weakened\b/g, " 可能性下降").replace(/ ruled out\b/g, " 已排除").replace(/ open\b/g, " 待查")}`],
  [/^(\d+) earlier case\(s\) compared$/, (_, n) => `对比了 ${n} 个历史案例`],
  [/^(\d+) occurrences?; followed by (“.*”) after ([\d.]+)–([\d.]+) s$/, (_, n, q, a, b) => `${n} 次；之后 ${a === b ? a : `${a}–${b}`} s 出现 ${q}`],
  [/^Recorded (\S+ \S+) with the live AI investigator \(([^)]+)\) on a local instance\..*SYNTHETIC case.*$/s,
    (_, at, m) => `录制于 ${at}，使用实时 AI investigator（${m}），在本地实例上运行。工程师一方的消息是为演示编写的脚本，每条按所标角色编写；Veyra 的回复是模型的真实输出，未经修改。合成案例：数据为本演示生成。`],
  [/^Round (\d+) of (\d+)$/, (_, a, b) => `第 ${a} 轮，共 ${b} 轮`],
  [/^Recorded investigation · round (\d+) of (\d+)\.$/, (_, a, b) => `录制的排查 · 第 ${a} 轮，共 ${b} 轮。`],
  [/^Round (\d+): the engineer replies$/, (_, a) => `第 ${a} 轮：工程师回复`],
  [/^Read lines (\d+)\s*[–-]\s*(\d+)$/, (_, a, b) => `读取第 ${a}–${b} 行`],
  [/^Searched for (“.*”)$/, (_, q) => `搜索 ${q}`],
  [/^Measured (“.*”)$/, (_, q) => `统计 ${q}`],
  [/^(\d[\d,]*) matching lines?$/, (_, n) => `${n} 行匹配`],
  [/^(\d[\d,]*) lines?; first failure at line (\d+)$/, (_, n, f) => `${n} 行；第一个故障在第 ${f} 行`],
  [/^(\d+) occurrences?, every ([\d.]+) s \(median\)$/, (_, n, s) => `${n} 次，间隔约 ${s} s（中位数）`],
  [/^(\d+) lines?$/, (_, n) => `${n} 行`],
  [/^Round (\d+) · (.+) · (guess|result of a check) · scripted for this demo$/, (_, n, who, k) => `第 ${n} 轮 · ${who} · ${k === "guess" ? "判断" : "检查结果"} · 为演示编写`],
  [/^AI investigator · (.+) · recorded (\S+)(?: · (\d+) s)?$/, (_, m, d, s) => `AI investigator · ${m} · 录制于 ${d}${s ? ` · ${s} s` : ""}`],
  [/^Next check · (.+)$/, (_, w) => `下一步检查 · ${w}`],
];

export function zh(text: string): string | null {
  const k = text.trim();
  if (!k || !/[A-Za-z]/.test(k)) return null;
  const hit = DICT[k];
  if (hit) return text.replace(k, hit);
  for (const [re, f] of RULES) { const m = k.match(re); if (m) return text.replace(k, f(...m)); }
  return null;
}

