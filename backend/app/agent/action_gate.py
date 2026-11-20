"""Action safety gate: every physical action Veyra proposes passes deterministic checks, and the safety findings of the
physical checks cannot be left out of a report. Blocking, not warning.

It sits after the model (the Safety & Experiment Design discipline of the investigator) and uses only what already exists:
  * the planner's templates (hv_state, safety.qualification, hv_exposure) and safety holds (blocks_hv_state, release_by),
  * the check findings (severity violation / review),
  * experiment_safety.classify (hazard class of a free-text action: offline ... live_hv),
and returns a verdict with the rule that decided it and the rule's provenance.

Rules (each one deterministic; a proposed action is BLOCKED when any applies):
  G1 hold          a safety hold is active and the action would need HV closed (template hv_state closed, a live-HV /
                   machine-operation / fault-reproduction class, or words that close / energise / charge HV), unless it is
                   the hold's own release check without such words.
  G2 unknown       the action names a template the plan does not offer.
  G3 unvalidated   a hazardous action with no template: no validated procedure exists for it here.
  G4 approval      a hazardous action not marked needs_approval.
Coverage (the missing-finding failure): every active hold and every check finding of severity violation or review must be
cited by ref in the report's safety_findings. The model is asked first; what it still leaves out is inserted by the gate
and labelled as inserted.

When an action is blocked, the gate replaces it with the deterministic alternative (the hold's release check, else the
planner's first recommended check that passes the gate) and keeps the blocked action, with its reasons, in the record.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

GATE_VERSION = "action-gate-v6.3"
ENERGISE = re.compile(r"\bHV (is )?(closed|on|energi[sz]ed)\b|\bwith (the )?(HV|high voltage|contactors?) (closed|on)\b|\bclos(e|es|ed|ing) (the |both |all )?(HV |main |high[- ]voltage |main\+ |main- |positive |negative )*(contactors?|relays?)\b|"
                      r"\bclose HV\b|\bre-?energi[sz]|(?<!de-)(?<!de)(?<!non-)\benergi[sz](e|es|ed|ing)\b(?! (state|status|time|interval|period|data|log))|\\bw/ ?(HV|high voltage) (live|on|closed)\\b|\bpre-?charge (the |a )?(HV |DC )?(bus|link|dc link|capacitors?|inverter|system|pack)\b|"
                      r"\b(run|start|do|perform|trigger|initiate|command|force|allow|let) (a |the )?pre-?charge\b|(执行|进行|触发|开始|启动|做|强制)预充|预充(一下|一次)|\bpower (it |the vehicle |the pack )?(up|on)\b|\bin READY\b|\bREADY state\b|"
                      r"\b(re)?charg(e|ing) (the )?(pack|battery|vehicle)\b|\bunder (a |an |the )?([\w-]+ ){0,2}load\b|\bHV on\b|\bdrive (the )?vehicle\b|"
                      r"\b(on|to|onto|into) (the |a |an )?(AC |DC |fast |slow |wall |mobile |public )?(charger|charging (station|point|post|pile|cable)|EVSE|wallbox)\b|"
                      r"\b(raise|bring|restore|get|take) (the )?(SOC|SoC|state of charge) (back )?(up )?to\b|\bcharge (it |them )?(up|to)\b|"
                      r"\bhot[- ]?(probe|probing|work|swap|plug|test)|\blive[- ](work|working|probe|probing|line|measurement|test)|"
                      r"\b(bring|brings|bringing|make|makes|making|keep|get|go|goes|is|are|while|with)\b[\w -]{0,30}\blive\b(?! (data|telemetry|view|stream|feed|plots?|logs?|signals?|values|readings|dashboard|session|call))|"
                      r"上高压|上电|闭合.{0,4}(接触器|继电器)|(闭合|合上|接通|恢复)(主正|主负|主回路)?高压|高压(闭合|接通|上电)(?!.{0,3}(次数|记录|时间|过程|事件))|"
                      r"装回.{0,4}(维修开关|MSD).{0,6}(闭合|上电|上高压)|充电|补电|带电|充到|充至|充满|慢充|快充|充电桩|放到|放电到", re.I)
# A clause that forbids or postpones energising ("do not close HV", "before any charge", "with HV open") does not ask for it.
NEGATED = re.compile(r"\b(do not|don't|never|no|not|without|before|until|unless|avoid|prohibit\w*|forbid\w*|must not|nobody|no one|neither|nor|none|refrain from|ban|banned)\b|不要|禁止|之前|不得|严禁|切勿|无需|无须|"
                     r"不(?!同|断|足|久|少|仅|但|是|一|明|稳|正常|超过|低于|高于)|未(?!知|来)|勿|别(?=[\u4e00-\u9fff])", re.I)
OFFLINE_KINDS = ("offline", "document", "data")
# Physical contact with the machine. A remote action whose words contain none of these is data / document work, whatever
# hazards its text mentions as conditions ("HV stays open", "no charging"): the keyword hazard classifier is not used for it.
CONTACT = re.compile(r"\b(clamp|probe|connect (to |a |the )?(meter|probe|charger|tool|harness|pack|cable)|disconnect|unplug|plug in|touch|remove|install|replace|"
                     r"tighten|re-?seat|arm (the )?(drone|vehicle|motors?)|power (it |the \w+ )?(up|on)|switch on|turn on|start (the )?(vehicle|machine|motor|drone|engine)|"
                     r"drive|fly|take[- ]?off|close (the )?(contactors?|relays?|HV)|open (the )?(pack|cover|housing|enclosure|contactors?|battery box)|"
                     r"measure (it )?(on|at|across) (the )?(pack|cell|terminal|bus|cable)|on the (vehicle|machine|pack)|at the (vehicle|machine|pack))\b|"
                     r"\bplug(s|ged|ging)? (the |a |an )?[\w -]{0,30}\b(into|in to|onto) (the )?[\w -]{0,20}(connector|port|socket|pack|harness|vehicle|car|battery|bus)\b|"
                     r"拆|装|接入|断开|连接|上电|试车|试飞|测量.{0,6}(电池|电芯|母线|端子)|打开.{0,4}(电池|包|盖|外壳|箱)|开盖|插上|拔下|拔掉|短接|跨接|解锁", re.I)


def is_offline_action(na: Dict[str, Any]) -> bool:
    """Remote, and no step touches or operates the machine (data export, log analysis, document review)."""
    return (na.get("where") or "").lower() in ("remote", "desk", "office") and not asks_for(CONTACT, _text(na))


NOUN_AFTER = re.compile(r"(?i)\s*(?:(?P<link>and|&|/)\s*(?P<w1>[\w-]+)\s+|(?P<w2>[\w+-]+)\s+)?(history|histories|logs?|records?|data|curves?|events?|sessions? (log|data|history)|counts?|cycles? (count|data|log)|"
                        r"reports?|profiles?|strategy|rules?|thresholds?|settings?|parameters?|limits?|statistics|stats|timestamps?|summary|plots?|"
                        r"bands?|bins?|channels?|inputs?|commands?|positions?|percentages?|traces?|telemetry|review|server|controllers?|modes?|stack|"
                        r"software|computer|app|failures?|faults?|codes?|DTCs?|errors?|timeouts?|resistors?|circuits?|durations?|time|times)\b")
NOUN_AFTER_ZH = re.compile(r"^(?:[与和及、/](?:标定|放电|充电|静置|校准))?(历史|数据|日志|曲线|次数|报告|策略|说明|参数|阈值|事件|失败|故障|报错|超时|异常|电阻|回路|继电器|码)")   # 记录 can be a verb
NOUN_PHRASES = re.compile(r"(?i)\b(manual |HV |high[- ]voltage )?service disconnect\b|\bMSD\b|\bdisconnect switch\b|\bkey[- ]?fob\b|维修开关")


def _negated_lists(text: str) -> str:
    """'do not arm, fly or charge it' / '严禁上电、充电或驾驶': the negation covers the whole list, so list commas become 'or'."""
    out = []
    for sent in re.split(r"(?<=[.;:!?\n。；])", text):
        m = NEGATED.search(sent)
        if m:
            head, tail = sent[: m.end()], sent[m.end():]
            tail = re.sub(r"\(([^()]*)\)", lambda p: "(" + re.sub(r",|，|、", " or ", p.group(1)) + ")", tail)
            last_or = max([x.start() for x in re.finditer(r"(?i)\b(or|nor)\b|或", tail)] or [-1])
            if last_or >= 0:
                tail = re.sub(r",|，|、", " or ", tail[:last_or]) + tail[last_or:]
            elif re.search(r"[\u4e00-\u9fff]", m.group(0)):
                tail = re.sub(r"、(?=不|未|勿|别)?", "或", re.split(r"[，,]", tail)[0]) + tail[len(re.split(r"[，,]", tail)[0]):]
            sent = head + tail
        out.append(sent)
    return "".join(out)


def _noun_compound(pattern_match: "re.Match[str]", clause: str) -> bool:
    rest, before = clause[pattern_match.end():], clause[: pattern_match.start()]
    axis = re.search(r"(?i)\b(by|per|vs\.?|versus|against|across)\s+(the\s+|each\s+|every\s+)?$", before)
    m = NOUN_AFTER.match(rest)
    if m:                                        # a modifier word between them must not be a verb ("charge the pack and plot data")
        w1, w2 = (m.group("w1") or "").lower(), (m.group("w2") or "").lower()
        if w1 and (w1 in DATA_VERBS or w1 in OBSERVE_VERBS or w1 in HAZARD_IMPERATIVES or w1 in ("the", "a", "an", "to", "then", "it", "them")):
            m = None                             # "charge the pack and plot data": a second verb, not a compound
        elif w2 and (w2 in HAZARD_IMPERATIVES or w2 in ("the", "a", "an", "to", "then", "it", "them", "and", "or")):
            m = None                             # a direct modifier ("flight review server") may be a noun that is also a verb
    return bool(m or NOUN_AFTER_ZH.match(rest) or axis)


def asks_for(pattern: "re.Pattern[str]", text: str) -> bool:
    """True when some clause asks for what `pattern` names, ignoring clauses that forbid or postpone it, and words used as
    nouns ("charging history", "flight logs", "the HV service disconnect")."""
    text = _negated_lists(NOUN_PHRASES.sub(" component ", re.sub(r"(?i)\b(is|are|be|gets?|will be)\s+then\b", r"\1", text)))
    for clause in re.split(r"(?i)[.;:,!?\n]|，|。|；|\bthen\b|\band then\b|然后|之后再|再(?=上|接|充|启|点|通)", text):
        for m in pattern.finditer(clause):
            if not NEGATED.search(clause[: m.start()]) and not re.match(r"\s*(之前|以前|前)", clause[m.end():]) and not _noun_compound(m, clause):
                return True
    return False


def energising_text(text: str) -> bool:
    """True when some clause asks for HV closed / energised / charging (forbidding or postponing it does not count)."""
    return asks_for(ENERGISE, text)
MATERIAL = ("violation", "review")

PROVENANCE = {
    "G1": {"kind": "engineering_rule", "source": GATE_VERSION + " with the planner's safety holds", "status": "encoded by Veyra; not a customer specification"},
    "G2": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "procedure integrity: only planned checks are offered"},
    "G3": {"kind": "engineering_rule", "source": GATE_VERSION + " with experiment_safety", "status": "no validated procedure, so not offered"},
    "G4": {"kind": "engineering_rule", "source": GATE_VERSION + " with experiment_safety", "status": "hazardous work needs a named reviewer"},
    "G5": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "unrecognised wording at the machine: an engineer confirms it before anyone acts"},
    "G6": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "a step the named procedure does not cover"},
    "CONFIRM": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "changes the machine's physical state or carries safety risk: an engineer confirms before anyone acts"},
    "coverage": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "a safety finding of the checks is always reported"},
}


def material_refs(tools) -> List[Dict[str, Any]]:
    """What a report must address: active holds and check findings of severity violation / review (one ref per kind)."""
    plan = tools.next_checks()
    out = [{"ref": h["id"], "statement": h["title"] + ". " + h.get("why", ""), "type": "safety_hold", "provenance": h.get("provenance")}
           for h in plan.get("safety_holds") or []]
    seen = set()
    for cid, r in tools.checks.items():
        for f in r.get("findings") or []:
            ref = f"{cid}:{f['kind']}"
            if f.get("severity") in MATERIAL and ref not in seen:
                seen.add(ref)
                out.append({"ref": ref, "statement": f["statement"], "type": "check_finding",
                            "provenance": {"kind": "check_result", "source": f"{cid} {r.get('check_version')}", "status": "computed from the rows"}})
    return out


def _text(na: Dict[str, Any]) -> str:
    # fields are separate statements: joined with a sentence break so a caution in one field never negates a step in another
    return " . ".join(x for x in [na.get("action") or "", na.get("why") or "", na.get("conditions") or ""] + [str(x) for x in na.get("how") or []] if x.strip())


# ---------------------------------------------------------------- v3: fail-closed. An action without a planned procedure passes only when
# every step is POSITIVELY recognised as data / document / communication work (remote) or as observation without contact
# (on site). Unknown wording is not assumed safe: it is blocked and listed. Energising, operating and contact words are a
# second, independent check (negation-aware), so a step must pass both.
DATA_VERBS = {"export", "download", "plot", "compare", "analyse", "analyze", "review", "request", "ask", "list", "replay", "simulate",
              "calculate", "compute", "estimate", "query", "search", "retrieve", "extract", "gather", "obtain", "summarise", "summarize",
              "tabulate", "correlate", "overlay", "cross-check", "contact", "email", "call", "interview", "document", "record", "note", "write",
              "send", "share", "match", "align", "count", "identify", "determine", "find", "trace", "map", "audit", "model", "fit", "scan",
              "filter", "decode", "parse", "merge", "graph", "chart", "quantify", "inspect", "check", "verify", "confirm", "read", "open",
              "get", "pull", "collect", "look", "use", "take", "run", "load", "get",
              # v4: writing and analysis verbs
              "draft", "compile", "collate", "annotate", "triage", "bin", "bucket", "histogram", "grep", "resample", "diff", "sort", "group",
              "segment", "flag", "rank", "eyeball", "highlight", "outline", "sketch", "prepare", "create", "update", "produce", "write-up",
              "investigate", "interpolate", "smooth", "differentiate", "integrate", "normalise", "normalize", "reconcile", "consult", "brief",
              "notify", "inform", "message", "phone", "survey", "poll", "cross-reference", "cross-ref", "crossref", "dig", "comb", "go", "pull up", "look up", "lookup", "assess", "evaluate", "examine", "study", "label", "mark"}
# these take an object: they are data work only with a data object and no machine object
AMBIGUOUS = {"inspect", "check", "verify", "confirm", "read", "open", "get", "pull", "collect", "look", "use", "take", "run", "load", "record",
             "note", "find", "trace", "map", "scan", "contact", "call", "model", "fit", "count", "identify", "determine", "record",
             "prepare", "create", "update", "produce", "integrate", "go", "dig", "comb", "assess", "evaluate", "examine", "study", "label", "mark", "flag", "group", "bin"}
DATA_NOUN = re.compile(r"\b(logs?|data|dataset|files?|\.ulg|\.bag|\.csv|\.mcap|records?|history|histories|reports?|documents?|documentation|datasheets?|"
                       r"specs?|specifications?|manuals?|thresholds?|parameters?|params|telemetry|signals?|traces?|plots?|readings?|values?|codes?|"
                       r"settings?|configuration|config|algorithm|firmware version|version|tickets?|emails?|notes?|photos?|pictures?|videos?|"
                       r"spreadsheet|table|database|dashboard export|supplier|manufacturer|operators?|pilots?|technicians?|drivers?|team|fleet|"
                       r"model|simulation|script|notebook|topics?|fields?|messages?|events?|timestamps?|window|period|range|desktop|laptop)\b|\.(ulg|bag|csv|mcap|log|json|txt)\b|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|日志|数据|文件|记录|历史|报告|文档|资料|"
                       r"参数|阈值|规格|手册|遥测|信号|曲线|读数|代码|配置|算法|版本|供应商|厂家|操作员|司机|飞手|车队|模型|仿真", re.I)
MACHINE_NOUN = re.compile(r"\b(pack|cells?|terminals?|busbars?|bus|connectors?|contactors?|relays?|cables?|harness|vehicle|car|machine|motors?|"
                          r"drone|propellers?|battery box|battery|charger|inverter|fuse|cover|housing|enclosure|chassis|wheel|throttle)\b|"
                          r"电池|电芯|端子|母线|接插件|接触器|继电器|线束|车辆|车|电机|无人机|充电机|保险|外壳", re.I)
OBSERVE_VERBS = {"look", "observe", "photograph", "note", "watch", "record", "read", "check", "inspect", "view", "take", "visually", "write", "count",
                 "walk", "film", "examine", "survey", "glance", "sight"}
OBSERVE_HINT = re.compile(r"\b(without (touching|switching|unplugging|opening|removing|connecting|powering)|visual(ly)?|visible|photo(graph)?s?|display|dashboard|screen|"
                          r"label|nameplate|no contact|do not touch|from outside|from a distance|at a distance|through the (windows?|windscreen|windshield|glass)|hands[- ]off|"
                          r"behind the (safety )?(line|barrier|tape)|off the (cluster|display|dash|screen)|stays? (parked|locked)|binoculars|zoom|torch|mirror)\b|"
                          r"目视|拍照|显示屏|仪表|不接触|不要碰|车外|外观|远处|隔着", re.I)
OPERATE = re.compile(r"\b(key(ing|ed)? on|key-on|ignition|start (the |it|up)|start-?up|idle|run the motors?|run (it|the (vehicle|machine|drone|engine))|spin|throttle|"
                     r"arm (the |it)|arming|hover|fly|flight|take[- ]?off|launch the|drive|driving|tow|plug in|plug the charger|top up|top-up|fill up|"
                     r"apply (HV|high voltage|power)|bring .{0,20}(online|up)|wake (the |it)|turn (it |the \w+ )?on|switch (it |the \w+ )?on|enable (HV|the drive|the motors?)|"
                     r"discharge|load test|under load|reproduc|re-?run the (route|test)|repeat the (load )?test|power[- ]?cycl(e|es|ed|ing)|reboot(s|ed|ing)?|restart(s|ed|ing)?|"
                     r"push(es|ed|ing)? (the |an? )?[\w -]{0,30}(OTA|update|firmware|command|reset|calibration)|forc(e|es|ed|ing) (an? |the )?[\w -]{0,20}(reset|re-?calibration|recalibration)|"
                     r"re-?arm(s|ed|ing)?|"
                     r"bypass|override|jumper|bridge the|force (the )?(contactors?|relays?|close|precharge)|"
                     r"soft[- ]?reset|hard[- ]?reset|reset (the )?(BMS|BMU|ECU|VCU|ESC|controller|flight controller|autopilot|contactor\w*)|re-?arm|"
                     r"over[- ]the[- ]air (command|reset|wake|charge|start)|remote(ly)? (start|wake|charge|unlock|arm|command|close|enable)|routine control|"
                     r"start (the |a )?(charge|charging|charge session|mission|flight|motor test)|schedule (a |the )?charge|set (the )?charge (limit|target)|"
                     r"motor test|from (QGC|QGroundControl|the ground station|mission planner)|send (a |the )?(arm|takeoff|start|wake|close) command|"
                     r"(is|are|be|being|gets?)\s+(then\s+|now\s+|briefly\s+|again\s+)?(re-?)?(charged|energi[sz]ed|powered (up|on)|switched on|keyed on|"
                     r"started|driven|flown|armed|discharged|cycled|closed|woken|brought (up|online|live)|plugged in|connected to (the |a )?charger))\b|"
                     r"启动|点火|上电|通电|接通|行驶|试车|试飞|起飞|悬停|充电|放电|复现|加载|挂.{0,2}[挡档]|跑.{0,3}圈|绕.{0,6}圈|开车|驾驶|路试|上路|行车|解锁电机|"
                     r"解锁(?!码)|起转|转动电机|唤醒|重启|复位|刷写|刷机", re.I)
ZH_DATA = re.compile(r"^(先|再|然后|并|同时|请|最后|第.步)?(?:(?:向|按|根据|基于|结合|用|使用|利用|从|在|对|就|针对|按照|通过)(?:(?!拆|装|打开|开盖|接|断|插|拔|上电|通电|启动|充|放|按下|短接|跨接|点火)[^，。,;；]){0,14}?)?(导出|读取|下载|分析|对比|比较|查看|检查|索取|询问|收集|调取|回放|仿真|计算|列出|确认|整理|获取|提取|核对|统计|绘制|记录|联系|查询|拍照|观察|目视|"
                     r"解析|起草|编写|撰写|汇总|标注|核查|咨询|请求|查阅|梳理|筛选|比对|复核|评估|了解|问|访谈|通知|告知|标记|标出|标明|做成|制作|对齐|拟定|找出|"
                     r"说明|描述|总结|概述|列明|写明|注明|解释|阐述|上传|分享|发送|整合|关联|核实|统计|计算|找到|定位|识别|确定|估算|画|绘|列|算|比|查|看|读|录|拍)")
_LEAD = re.compile(r"^(\s|then|and|also|first|next|finally|please|pls|step \d+[:.)]?|\d+(\.\d+)*[.)]?(?=\s)|[a-h][.)](?=\s)|-|•|\*)+", re.I)


HAZARD_IMPERATIVES = {"reset", "reboot", "restart", "cycle", "power", "flash", "reflash", "close", "open", "connect", "apply", "energise", "energize",
                      "short", "bridge", "jump", "bypass", "override", "force", "disable", "enable", "toggle", "swap", "pull", "push", "press", "hold",
                      "turn", "switch", "unplug", "plug", "remove", "insert", "fit", "attach", "detach", "clamp", "probe", "tap", "load", "drain",
                      "heat", "cool", "spin", "throttle", "arm", "release", "trigger", "fire", "test", "try", "repeat", "go", "move", "lift", "drop"}
QUANTITY = re.compile(r"(?i)\b(voltages?|currents?|temperatures?|capacity|capacities|resistances?|impedances?|SOC|SoC|SOH|power|energy|levels?|rates?|"
                      r"offsets?|drifts?|deltas?|spreads?|minima|maxima|min|max|minimum|maximum|counts?|durations?|times?)\b")
PARTICIPLE = re.compile(r"(?i)^\s*(or |and |nor )?(be )?(\w+ed|driven|flown|run|shut|done|taken|put|begun|ridden|woken|brought|left|set|plugged in|switched on|keyed on)\b")


def _clauses(text: str) -> List[str]:
    text = _negated_lists(re.sub(r"(?i)\b(is|are|be|gets?|will be)\s+then\b", r"\1", text))
    parts = re.split(r"(?i)\.(?=\s|$)|[,;:!?\n]|，|。|；|、|\bthen\b|\band then\b|\band\b|然后|并且|之后再", text)
    return [p.strip() for p in parts if p and p.strip(" ,")]


def clause_kind(clause: str) -> str:
    """data | observe | prohibition | physical | unknown (one clause, after splitting on punctuation / then / and)."""
    c = clause.strip(" ,")
    if NEGATED.search(c) and not re.match(r"(?i)\s*(if|when|once)\b", c) and NEGATED.search(c).start() < 25:
        return "prohibition"                     # "do not close HV", "without charging", "before any charge", "until ..."
    if asks_for(OPERATE, c) or energising_text(c) or asks_for(CONTACT, c):
        return "physical"
    zh = ZH_DATA.match(c)
    if zh:
        return "observe" if zh.group(2) in ("拍照", "观察", "目视") else "data"
    words = re.findall(r"[a-zA-Z][a-zA-Z-]*", _LEAD.sub("", c).lower())
    if not words:
        return "unknown"
    stripped = re.sub(r"(\b\w+\s+)?(?:" + DATA_NOUN.pattern + ")", " ", c, flags=re.I)
    if words[0] in ("stand", "wait", "keep", "stay", "remain") and re.search(r"(?i)\b(outside|away|back|clear|behind|distance|out of|\d+\s*m\b)", c):
        return "prohibition"                     # a keep-out constraint ("stand at least 3 m away", "stay outside the vehicle")
    if words[0] in ("the", "a", "an", "its", "their", "his", "her", "our", "each", "every", "all", "both", "any") and not re.search(
            r"(?i)\b(" + "|".join(sorted(HAZARD_IMPERATIVES)) + r")\b", " ".join(words[1:2])):
        return "continuation"                    # a noun phrase: another object of the previous step ("... and the re-close lockout logic")
    if len(words) <= 4 and words[0] not in HAZARD_IMPERATIVES and words[0] not in DATA_VERBS and QUANTITY.search(c):
        return "continuation"                    # a quantity in a list ("empty and full cell voltages")
    if words[0] in ("whether", "what", "which", "who", "whom", "why", "how", "that", "where", "whose"):
        return "continuation"                    # a subordinate clause of the previous step (physical words were checked above)
    if len(words) <= 4 and words[0] not in HAZARD_IMPERATIVES and not MACHINE_NOUN.search(re.sub(r"(\b\w+\s+)?(?:" + DATA_NOUN.pattern + ")", " ", c, flags=re.I)) \
            and words[0] not in DATA_VERBS and words[0] not in OBSERVE_VERBS and not re.search(r"[\u4e00-\u9fff]", c):
        return "continuation"                    # a short list item continuing the previous step ("plot voltage, current and estimated capacity")
    if words[0] not in DATA_VERBS and words[0] not in OBSERVE_VERBS and DATA_NOUN.search(c) and not MACHINE_NOUN.search(stripped) \
            and words[0] not in ("if", "when", "once", "keep", "stay", "remain", "leave", "make"):
        return "continuation"                    # a noun phrase continuing the previous step ("... and the charger log")
    if words[0] in ("if", "when", "once", "while", "with", "for", "over", "since", "in", "on", "at", "from", "during", "behind", "the", "a", "an", "all", "each",
                    "every", "same", "both", "vehicle", "hv", "read-only", "keep", "stay", "remain", "leave", "make"):
        if words[0] in ("keep", "stay", "remain", "leave") and re.search(r"(?i)\b(open|off|at rest|isolated|parked|de-?energi[sz]ed|outside|away|back|clear|behind|out of)\b", c):
            return "prohibition"                 # "keep HV open", "leave the vehicle parked"
        return "context" if not MACHINE_NOUN.search(c) or re.search(r"(?i)\b(at rest|open|parked|off)\b", c) else "unknown"
    v = words[0]
    if v in OBSERVE_VERBS and OBSERVE_HINT.search(c):
        return "observe"
    if v in DATA_VERBS:
        if v in AMBIGUOUS:
            return "data" if DATA_NOUN.search(c) and not MACHINE_NOUN.search(stripped) else "unknown"
        return "data"
    return "unknown"


BOOKKEEPING = {"flag", "mark", "note", "list", "label", "record", "document", "report", "tag", "log", "annotate", "highlight", "identify", "name", "earmark", "shortlist"}
# A remote action that hands work to a person on site ("have the driver ...", "让司机...") is on-site work by proxy.
DELEGATES = re.compile(r"(?i)\b(have|get|tell|instruct|let|ask|request|arrange for|send)\s+(the\s+|a\s+|our\s+)?(driver|pilot|technician|tech|operator|owner|customer|"
                       r"mechanic|field team|field engineer|workshop|service team|site team|crew|someone|somebody)s?\s+(to\s+)?(?!about\b|what\b|whether\b|if\b|how\b|"
                       r"when\b|why\b|which\b|for\b|to (send|share|describe|tell|explain|confirm|recall|forward|email|upload|export)\b)\w+|"
                       r"(让|叫|请|安排|要求|通知)[^，。,]{0,6}(司机|飞手|技师|技术员|师傅|驾驶员|操作员|车主|用户|客户|现场)(?!.{0,4}(提供|发送|描述|回忆|说明|确认|上传|导出))")
CONTROLLER = re.compile(r"(?i)\b(BMS|BMU|ESC|VCU|ECU|controller|firmware|state machine|telematics|OTA|autopilot|flight controller|PDB|HVIL|EVSE|"
                        r"precharge|airframe|aircraft|props?|rotors?)\b|控制器|固件|飞控|电调|整车")


def _acts_on_machine(clause: str) -> bool:
    stripped = re.sub(r"(\b[\w-]+\s+|[\u4e00-\u9fff]{1,4})?(?:" + DATA_NOUN.pattern + "|" + QUANTITY.pattern.replace("(?i)", "") + ")", " ", clause, flags=re.I)
    words = re.findall(r"[a-zA-Z][a-zA-Z-]*", _LEAD.sub("", clause).lower())
    if (words and words[0] in BOOKKEEPING) or re.match(r"^(标出|标注|标记|列出|记录|注明|汇总|整理)", clause.strip()):
        return False                             # "flag the vehicle for a later measurement" records a recommendation
    return bool(MACHINE_NOUN.search(stripped) or CONTROLLER.search(stripped) or (words and words[0] in HAZARD_IMPERATIVES))


QUESTION = re.compile(r"(?i)\b(ask|asking|enquire|inquire|query|interview|check with|confirm with|find out from)\b[^.;!?\n]{0,80}?\b(whether|if|what|when|how|why|which|who)\b|"
                      r"(询问|问一下|问清|问|了解|访谈)[^。；;]{0,20}?(是否|有没有|什么|何时|多少|几次|怎么|为什么|哪)")
DOC_CONTENT = re.compile(r"(?i)\b(draft|write|prepare|compile|issue|circulate|publish|update|create)\b[^:.;]{0,60}\b(notice|plan|checklist|advisory|memo|bulletin|"
                         r"instructions?|guidance|summary|sop|letter|email|message)\b\s*(:|stating|saying|that says|which says|to say|that)|"
                         r"(起草|编写|撰写|拟定|制定|发布)[^：:。]{0,20}(通知|计划|方案|清单|说明|公告|指引)[：:]")
# remote only: "check / eyeball whether X" asks a question of the data (on site it could be a test, so it is not used there)
DATA_QUESTION = re.compile(r"(?i)\b(eyeball|check|see|verify|confirm|determine|establish|find out|look at|assess|examine|test|work out)\s+(in the (data|logs?) |from the (data|logs?) )?"
                           r"(whether|if)\b|(看|检查|确认|核对|判断|分析)[^。；;，,]{0,12}?是否")
STOP = re.compile(r"(?i)\b(then|and then|afterwards|after that)\b|然后|之后再|[.;!?\n。；]")


def _described_spans(text: str, remote: bool = False) -> List[tuple]:
    """Spans of reported speech: (start, end, kind). A question put to a person ("ask the driver whether they tried to restart")
    instructs nothing. Document content ("draft the notice: nobody closes the contactors ...") can instruct (a work order), so its
    hazard words are still checked, negation-aware; only its unrecognised wording is not fail-closed."""
    out = []
    for kind, rx in (("question", QUESTION), ("document", DOC_CONTENT)) + ((("question", DATA_QUESTION),) if remote else ()):
        for m in rx.finditer(text):
            stop = STOP.search(text, m.end())
            out.append((m.end(), stop.start() if stop else len(text), kind))
    return sorted(out)


def _without_questions(text: str, remote: bool = False) -> str:
    for a, b, kind in reversed(_described_spans(text, remote)):
        if kind == "question":
            text = text[:a] + " (question) " + text[b:]
    return text


def established(na: Dict[str, Any], known_text: Optional[set] = None) -> Dict[str, Any]:
    """Classify every step of an action without a planned procedure: what it is established to be, and the steps that
    could not be established (fail-closed)."""
    whole = [x for x in [na.get("action") or ""] + [str(x) for x in na.get("how") or []] if x.strip()]
    steps: List[str] = []
    for w in whole:
        if known_text and w.strip() in known_text:
            steps.append(w.strip())
            continue
        spans = _described_spans(w, (na.get("where") or "on_site").lower() in ("remote", "desk", "office") and not DELEGATES.search(w))
        if spans:                                # the question / document content becomes one described step
            a, b, kind = spans[0]
            head, body, tail = w[:a], w[a:b], w[b:]
            steps.extend(_clauses(head))
            steps.append(f"\x00{kind}:" + body)
            steps.extend(_clauses(tail))
            continue                              # an observation step is judged whole ("inspect X and Y visually")
        first = (re.findall(r"[a-zA-Z]+", _LEAD.sub("", w).lower()) or [""])[0]
        if (first in OBSERVE_VERBS or re.match(r"^(拍照|观察|目视)", w.strip())) and OBSERVE_HINT.search(w) and \
                not (asks_for(OPERATE, w) or energising_text(w) or asks_for(CONTACT, w)):
            steps.append("\x00observe:" + w)
        else:
            steps.extend(_clauses(w))
    kinds, prev, prev_raw = [], None, ""
    for c in steps:
        if c.startswith("\x00question:"):
            k = "described"
        elif c.startswith("\x00document:"):
            body = c[len("\x00document:"):]
            k = "physical" if (asks_for(OPERATE, body) or energising_text(body) or asks_for(CONTACT, body)) else "described"
        else:
            k = "observe" if c.startswith("\x00observe:") else clause_kind(c)
        if k == "unknown" and known_text and c.strip() in known_text:
            k = "data"                           # the planner's own wording of a data procedure
        if k in ("unknown", "physical") and NEGATED.search(prev_raw) and PARTICIPLE.match(c) and not re.search(r"(?i)\b(then|and then|after that)\b", c):
            k = "prohibition"                    # the next item of a negated list ("must not be charged, driven or switched to READY")
        prev_raw = c
        if k == "continuation":
            k = prev if prev in ("data", "observe") else "unknown"
        kinds.append((c, k))
        prev = k if k in ("data", "observe", "physical", "unknown") else prev
    cond = [(c, clause_kind(c)) for c in _clauses(na.get("conditions") or "")]
    bad_cond = [c for c, k in cond if k == "physical"]
    phys = [c for c, k in kinds if k == "physical"] + bad_cond
    unknown = [c for c, k in kinds if k == "unknown"]
    remote = (na.get("where") or "on_site").lower() in ("remote", "desk", "office")
    unverified = []
    if remote and DELEGATES.search(" . ".join(whole)):
        remote = False                           # delegated to a person at the machine: fail-closed as on site
    if remote and unknown and not phys:          # fail-closed applies where hazard can arise: at the machine, or acting on it
        unverified = [c for c in unknown if not _acts_on_machine(c)]
        unknown = [c for c in unknown if c not in unverified]
    doing = [k for _c, k in kinds if k in ("data", "observe")] + (["data"] if unverified else [])
    if phys:
        return {"kind": "physical", "steps": phys}
    if unknown:
        return {"kind": "unestablished", "steps": unknown}
    if not doing:
        if kinds and all(k in ("prohibition", "context") for _c, k in kinds) and any(k == "prohibition" for _c, k in kinds):
            return {"kind": "hold", "steps": []}  # only forbids or waits ("no physical action until an engineer reviews")
        return {"kind": "unestablished", "steps": steps}
    return {"kind": "observe" if "observe" in doing else "data", "steps": [], "unverified_remote": unverified}


def _data_template(t: Dict[str, Any]) -> bool:
    return t.get("hv_state") == "none" and not t["safety"]["hv_exposure"]


def _evaluate_text(na: Optional[Dict[str, Any]], plan: Dict[str, Any], *, hv_system: bool) -> Dict[str, Any]:
    """v5 free-text evaluation (used when an action declares no steps)."""
    from app.agent import experiment_safety
    if not isinstance(na, dict) or not na.get("action"):
        return {"verdict": "pass", "rules": [], "class": None, "template": None}
    rows = {r["id"]: r for r in (plan.get("recommended") or []) + (plan.get("handoff") or [])}
    from app.physchecks import planner
    known = {t["id"]: t for t in planner.TEMPLATES}
    tid = (na.get("template_id") or "").strip()
    tpl = known.get(tid)
    text = _text(na)
    where = (na.get("where") or "on_site").lower()
    fired: List[Dict[str, Any]] = []

    def fire(rule, reason):
        fired.append({"rule": rule, "reason": reason, "provenance": PROVENANCE[rule]})
    if tid and tid.lower() != "none" and tid not in known:
        fire("G2", f"template {tid!r} is not a Veyra procedure")
        tpl = None
    elif tid and tid.lower() != "none" and tid not in rows and not any(h.get("release_by") and tid in h["release_by"] for h in plan.get("safety_holds") or []) \
            and not _data_template(known[tid]):
        fire("G2", f"template {tid!r} is not offered by the plan for this case (offered: {', '.join(sorted(rows)) or 'none'})")
    declared_hv = (na.get("hv_state") or "").lower()
    hz = _without_questions(text, where in ("remote", "desk", "office") and not DELEGATES.search(text))
    operating = asks_for(OPERATE, hz) or energising_text(hz)
    known_text = None
    if tpl is not None and _data_template(tpl):
        known_text = {tpl["title"]} | {str(x) for x in tpl.get("requires") or []} | {str(x) for x in (rows.get(tid) or {}).get("requires") or []}
        tpl = None                                       # a data / document procedure does not cover physical work: judge the text
    if tpl is not None:
        est = {"kind": "procedure", "steps": []}
        hazardous = bool(tpl["safety"]["hv_exposure"]) or tpl.get("hv_state") in ("open", "closed") or operating
    else:
        est = established(na, known_text)
        hazardous = est["kind"] not in ("data", "observe", "hold")
        if est["kind"] == "data" and where not in ("remote", "desk", "office"):
            est = {"kind": "data", "steps": []}          # data work done on site is still data work (no contact found)
    needs_hv = (tpl or {}).get("hv_state") == "closed" or operating or (declared_hv == "closed" and est["kind"] not in ("data", "observe", "hold"))
    for h in plan.get("safety_holds") or []:
        if needs_hv:
            fire("G1", f"{h['id']} is active ({h['title']}); this action needs high voltage closed, charges / discharges the pack or operates the machine")
    if tpl is None and est["kind"] == "physical":
        fire("G3", ("physical work under a data procedure (" + tid + "), which does not cover it: " if tid in known else "physical work with no planned procedure: ") + "; ".join(x[:80] for x in est["steps"][:3]))
    elif tpl is None and est["kind"] == "unestablished":
        fire("G5", "not established as data, document or observation work (fail-closed); unrecognised: " + "; ".join(x[:80] for x in est["steps"][:3]))
    if hazardous and tpl is not None and not na.get("needs_approval"):
        fire("G4", "hazardous work not marked as needing a qualified reviewer's approval")
    cls = {"classes": [est["kind"]]}
    return {"verdict": "blocked" if fired else "pass", "rules": fired, "class": cls.get("classes"), "template": tid or None,
            "hazardous": hazardous, "established": est}


def replacement(plan: Dict[str, Any], *, hv_system: bool) -> Optional[Dict[str, Any]]:
    """The deterministic alternative: a hold's release check first, else the first recommended check that passes."""
    from app.physchecks import planner
    known = {t["id"]: t for t in planner.TEMPLATES}
    after = next((r for r in plan.get("recommended") or [] if r["id"] not in sum((x.get("release_by") or [] for x in plan.get("safety_holds") or []), [])
                  and r.get("hv_state") in ("none", "open")), None)
    then = f"; after the hold is released, the first check that separates the open causes is {after['title']} ({after['id']})" if after else ""
    for h in plan.get("safety_holds") or []:
        for tid in h.get("release_by") or []:
            t = known.get(tid)
            if t:
                return {"action": t["title"], "why": f"Required first by safety hold {h['id']}{then}", "how": [f"{t['title']}, with HV open (vehicle at rest)",
                                                                                                       "Record every cell's rest voltage and the time at rest"],
                        "steps": [{"kind": "procedure", "text": f"{t['title']}, with HV open (vehicle at rest)"},
                                  {"kind": "measure", "text": "Record every cell's rest voltage and the time at rest"}],
                        "where": "on_site", "template_id": tid, "needs_approval": t["safety"]["qualification"] != "none",
                        "outcomes": [{"label": "All cells above the recovery limit", "means": "the hold can be released by the responsible engineer", "keeps": [], "rules_out": []},
                                     {"label": "A cell below the recovery limit", "means": "manufacturer's recovery procedure before any charge or HV closure", "keeps": [], "rules_out": []}],
                        "provenance": {"kind": "engineering_rule", "source": f"planner hold {h['id']}", "status": "deterministic replacement for a blocked action"}}
    for r in plan.get("recommended") or []:
        t = known.get(r["id"]) or {}
        kind = ("procedure" if not _data_template(t) else "request_document" if t.get("kind") == "document" else "read_data") if t else "procedure"
        cand = {"action": r["title"], "template_id": r["id"], "needs_approval": r["safety"]["qualification"] != "none",
                "how": r.get("requires") or [], "why": "; ".join(r.get("distinguishes_labels") or []),
                "steps": [{"kind": kind, "text": r["title"]}]}
        if evaluate_action(cand, plan, hv_system=hv_system)["verdict"] == "pass":
            return {**cand, "where": "remote" if r.get("hv_state") == "none" else "on_site",
                    "outcomes": [{"label": k, "means": v, "keeps": [k], "rules_out": []} for k, v in list((r.get("predictions") or {}).items())[:3]] or
                                [{"label": "result", "means": "see the plan", "keeps": [], "rules_out": []}] * 2,
                    "provenance": {"kind": "engineering_rule", "source": f"planner {r['id']}", "status": "deterministic replacement for a blocked action"}}
    return None


def problems_for_model(report: Dict[str, Any], tools, *, hv_system: bool) -> List[str]:
    """What the model is asked to fix before the gate enforces (same fix budget as the verifier)."""
    plan = tools.next_checks()
    out = []
    v = evaluate_action(report.get("next_action"), plan, hv_system=hv_system)
    na = report.get("next_action") or {}
    if isinstance(na, dict) and na.get("action") and not na.get("steps"):
        out.append("next_action.steps is missing: declare every step as {kind, text}, kind one of " + ", ".join(STEP_KINDS) +
                   ". No-touch steps run without approval; physical steps need the named procedure and an engineer's confirmation")
    for f in [r for r in v["rules"] if r["rule"] not in ("CONFIRM", "G5")]:
        out.append(f"next_action is blocked by {f['rule']}: {f['reason']}. Propose a check from physical_next_checks that passes "
                   "(name its template_id), or an offline / document check (template_id none)")
    cited = {str(x.get("ref")) for x in report.get("safety_findings") or [] if isinstance(x, dict)}
    for m in material_refs(tools):
        if m["ref"] not in cited:
            out.append(f"safety_findings must cite {m['ref']} ({m['statement'][:140]})")
    return out


def enforce(report: Dict[str, Any], tools, *, hv_system: bool) -> Dict[str, Any]:
    """Final, deterministic: returns (a copy of) the report with blocked actions replaced and missing safety findings
    inserted, plus the gate record. The model's own text is never edited, only replaced as a whole and kept."""
    plan = tools.next_checks()
    out = copy.deepcopy(report)
    v = evaluate_action(out.get("next_action"), plan, hv_system=hv_system)
    rec: Dict[str, Any] = {"version": GATE_VERSION, "action_verdict": v["verdict"], "rules_fired": v["rules"], "action_class": v["class"],
                           "blocked_actions": [], "inserted_findings": [], "covered_by_model": []}
    if v["verdict"] == "blocked":
        rep = replacement(plan, hv_system=hv_system)
        if rep is not None and evaluate_action(rep, plan, hv_system=hv_system)["verdict"] != "pass":
            rec["replacement_rejected"] = rep.get("template_id")     # the replacement must pass the same gate
            rep = None
        rec["blocked_actions"].append({"action": out.get("next_action"), "rules": v["rules"]})
        out["next_action"] = rep or {"action": "No physical action until an engineer reviews the case", "why": "the proposed action was blocked",
                                     "how": [], "where": "remote", "template_id": "none", "steps": [{"kind": "ask_person", "text": "An engineer reviews the case before any physical action"}],
                                     "outcomes": [{"label": "reviewed", "means": "engineer decides", "keeps": [], "rules_out": []}] * 2,
                                     "provenance": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "no safe replacement in the plan"}}
        out["next_action"]["replaced_by_gate"] = True
    else:
        out.setdefault("next_action", {})
        if isinstance(out.get("next_action"), dict):
            out["next_action"]["provenance"] = {"kind": "expert_proposal", "source": "the AI investigator",
                                                "status": "passed the action gate" + ("" if v["template"] and v["template"] != "none" else "; not a validated procedure")}
    cited = {str(x.get("ref")) for x in out.get("safety_findings") or [] if isinstance(x, dict)}
    out.setdefault("safety_findings", [])
    for m in material_refs(tools):
        if m["ref"] in cited:
            rec["covered_by_model"].append(m["ref"])
        else:
            item = {"ref": m["ref"], "statement": m["statement"], "inserted_by": GATE_VERSION, "provenance": m["provenance"]}
            out["safety_findings"].append(item)
            rec["inserted_findings"].append(m["ref"])
    final = evaluate_action(out.get("next_action"), plan, hv_system=hv_system)
    rec["final_action_verdict"] = final["verdict"]                 # what is delivered always passes (asserted in tests)
    if final["verdict"] != "pass":
        rec["blocked_actions"].append({"action": out.get("next_action"), "rules": final["rules"]})
        out["next_action"] = {"action": "No physical action until an engineer reviews the case", "why": "no proposed or planned action passes the safety gate",
                              "how": [], "where": "remote", "template_id": "none", "replaced_by_gate": True, "steps": [{"kind": "ask_person", "text": "An engineer reviews the case before any physical action"}],
                              "outcomes": [{"label": "reviewed", "means": "engineer decides", "keeps": [], "rules_out": []}] * 2,
                              "provenance": {"kind": "engineering_rule", "source": GATE_VERSION, "status": "fallback: nothing passed the gate"}}
        rec["final_action_verdict"] = evaluate_action(out["next_action"], plan, hv_system=hv_system)["verdict"]
    final = evaluate_action(out.get("next_action"), plan, hv_system=hv_system)
    rec["outcome"] = final.get("outcome")
    rec["requires_confirmation"] = bool(final.get("requires_confirmation"))
    rec["steps"] = final.get("steps")
    na = out.get("next_action")
    if isinstance(na, dict) and final.get("requires_confirmation"):
        na["engineer_confirmation"] = {"required": True, "by": final.get("confirm_by") or "engineer",
                                       "reason": "changes the machine's physical state or carries safety risk",
                                       "steps": [e["text"] for e in final.get("steps") or [] if e["kind"] in PHYSICAL] or
                                                [r["reason"] for r in final["rules"] if r["rule"] == "G5"]}
        prov = na.get("provenance") or {}
        prov["status"] = (prov.get("status") or "passed the action gate") + "; an engineer must confirm before anyone acts"
        na["provenance"] = prov
    rec["passed_unchanged"] = v["verdict"] == "pass" and not rec["inserted_findings"]
    return {"report": out, "gate": rec}


# ---------------------------------------------------------------- v6: declared step kinds, three outcomes
# The investigator declares each step's kind from a closed list. Declarations are untrusted: hazard words in a step's text can
# only escalate its kind (negation-aware), never lower it. Outcome:
#   auto    - only no-touch steps (reading / analysing data, documents, questions, drafting, observation without contact):
#             passes with no human approval, so the investigation stays automatic;
#   confirm - physical steps that change the machine's state or carry risk, all covered by the named, offered procedure and not
#             in conflict with an active hold: delivered, but an engineer must confirm before anyone acts;
#   block   - a hold is broken (no engineer can approve that), physical work has no planned procedure, or a step goes beyond
#             what the procedure covers: replaced by the gate.
NO_TOUCH = ("read_data", "request_document", "ask_person", "write_document", "observe")
PHYSICAL = ("measure", "handle", "energise", "charge_discharge", "operate", "remote_command", "procedure")
STEP_KINDS = NO_TOUCH + PHYSICAL
BREAKS_HOLD = ("energise", "charge_discharge", "operate", "remote_command")
NEVER_COVERED = ("operate", "remote_command")
CHARGE_WORDS = re.compile(r"(?i)\b(re)?charg(e|ed|ing|er)\b|\bdischarg|充电|放电|补电|充到|充满|慢充|快充", re.I)


def _known_template(na: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from app.physchecks import planner
    return {t["id"]: t for t in planner.TEMPLATES}.get((na.get("template_id") or "").strip())


STRONG_VERBS = {"energize", "energise", "power", "close", "connect", "disconnect", "insert", "remove", "slit", "cut", "pierce", "short", "apply", "drive",
                "fly", "arm", "start", "spin", "discharge", "charge", "heat", "wiggle", "unplug", "plug", "flash", "reflash", "reset", "reboot", "restart",
                "open", "run", "switch", "turn", "bridge", "jump", "bypass", "override", "force", "disable", "enable", "toggle", "swap", "attach", "detach",
                "clamp", "probe", "load", "drain", "release", "trigger", "puncture", "pry", "unscrew", "unbolt", "lift", "tilt", "shake", "tap", "press"}
ZH_STRONG = re.compile(r"^(先|再|然后|请|直接)?(拆|装|打开|开盖|接上|接入|断开|插|拔|上电|通电|启动|充|放电|按下|短接|跨接|点火|解锁|起飞|驾驶|开车|挂|划开|剪|撬|刺|加热|摇|晃)")


def _imperative_hazard(text: str) -> bool:
    """A no-touch step that starts with a strong physical verb on a machine object ("energize the pack", "slit the LiPo wrap")."""
    t = re.sub(r"(?i)^\s*(pls|please|just|quickly|then|and|also|first|next)\s+", "", text.strip())
    if ZH_STRONG.match(t):
        return True
    words = re.findall(r"[a-zA-Z][a-zA-Z-]*", t.lower())
    if not words or words[0] not in STRONG_VERBS:
        return False
    stripped = re.sub(r"(\b[\w-]+\s+)?(?:" + DATA_NOUN.pattern + ")", " ", t, flags=re.I)
    return bool(MACHINE_NOUN.search(stripped) or CONTROLLER.search(stripped)) or not DATA_NOUN.search(t)


# What data is about, when it is the past: "the logs of the last time the contactors closed under load", "上次充电的记录"
HISTORICAL = re.compile(r"(?i)\b(logs?|data|records?|traces?|telemetry|events?|history|recordings?|timestamps?|readings?|flights?|sorties|runs|sessions?|"
                        r"occasions?|cycles?|instances?|moments?)\s+(of|from|for|during|around|covering|when|in which|on which|where)\s+"
                        r"(the last|the previous|the earlier|earlier|past|previous|the incident|the event|the failure|the accident|the crash|the trip|"
                        r"the stop|the jump|the drop|last|yesterday|those|these|all|every|each|the \d+|\d+)\b|"
                        r"\b(the last|the previous|the earlier|each|every|all) (time|times|occasions?|events?|instances?)\b|"
                        r"(上次|之前|以前|过去|历史上|事故前|事故时|当时|那次|前几次|最近几次|最后一次)")


def _historical_removed(text: str) -> str:
    out, pos = [], 0
    for m in HISTORICAL.finditer(text):
        if m.start() < pos:
            continue
        stop = STOP.search(text, m.end())
        end = stop.start() if stop else len(text)
        out.append(text[pos:m.start()] + " (past) ")
        pos = end
    return "".join(out) + text[pos:]


ENERGISED_STATE = re.compile(r"(?i)\b(idle|idling|READY( mode| state)?|key(ing|ed|s)?[- ]on|ignition( on)?|standstill|accessory (mode|load)|"
                             r"under (a |an |the )?([\w-]+ ){0,2}load|at (a |an )?(steady|constant|fixed) (\w+ )?load|load test)\b|怠速|READY档|静止带载")


# Moving / operating the machine itself. Charging words are NOT here (a controlled charge or discharge on test equipment is
# charge_discharge, which a procedure can cover); "discharge it by driving the loop" matches MOVE and stays operating.
MOVE = re.compile(r"(?i)\b(drive|drives|driving|driven|drove|fly|flies|flying|flown|flew|hover(s|ing|ed)?|take[- ]?off|takeoff|take off|launch(es|ing|ed)? the|"
                  r"arm(s|ing|ed)? (the |it|motors?)|arming|run(s|ning)? the motors?|spin(s|ning)? (up )?the (motors?|props?|propellers?)|motor test|"
                  r"tow(s|ing|ed)?|road test|test (loop|drive|flight|route)|start (the |it|up)|start(s|ing)? the (vehicle|car|machine|motor|engine|drone)|"
                  r"re-?run the (route|test)|reproduc\w*|send (a |the )?(arm|takeoff|start) command|from (QGC|QGroundControl|the ground station|mission planner))\b|"
                  r"行驶|驾驶|开车|试车|试飞|起飞|悬停|跑.{0,3}圈|绕.{0,6}圈|路试|上路|行车|挂.{0,2}[挡档]|解锁电机|起转|转动电机|复现")


def _operating(hz: str) -> bool:
    """Operating the machine (drive, fly, arm, run motors, start), as opposed to an energised standstill or a controlled charge."""
    return asks_for(MOVE, ENERGISED_STATE.sub(" ", hz))


SEVERITY = {"read_data": 0, "request_document": 0, "ask_person": 0, "write_document": 0, "observe": 0, "procedure": 1, "measure": 2,
            "handle": 3, "energise": 4, "charge_discharge": 5, "operate": 6, "remote_command": 6}

# ---- v6.2: read a no-touch step clause by clause, by its main verb. Hazard words inside a clause led by an information verb
# (read, analyse, review, draft, ask ...) are what is being read or written about, not an instruction, unless they are an
# embedded instruction: a gerund after while / by / after / upon ("while driving the loop"), a purpose "to <verb>" ("to
# reflash the ESC"), the future ("during the next drive", 下次), or work handed to a person (DELEGATES). A clause led by a
# hazardous verb is an instruction. Deterministic; the model's own relabelling is not relied on.
INSTR_SPLIT = re.compile(r"(?i)\.(?=\s|$)|[;:!?\n]|。|；|：|\bthen\b|\band then\b|,\s*and\b|\band\b|然后|并且|随后|接着|之后再|后再|再(?=[\u4e00-\u9fff])|并(?!联|网|行|列|发|存)")
EMBEDDED = re.compile(r"(?i)\b(while|whilst|by|after|upon|when)\s+([a-z]+ing)\b|\bto\s+(re-?)?(key|start|drive|fly|arm|charge|discharge|close|energi[sz]e|power|"
                      r"reflash|flash|reset|reboot|restart|run|spin|wake|connect|plug|apply|enable|trigger|take off|takeoff|hover)\b|"
                      r"\b(the next|the upcoming|a new|another|tomorrow'?s?|tonight'?s?|next)\s+(drive|flight|run|test|charge|discharge|cycle|trip|sortie|start)\b|"
                      r"(下次|接下来|待会|之后|稍后|明天).{0,6}(上电|充电|放电|启动|驾驶|行驶|试飞|起飞|试车|跑)|(以便|为了|用来|好让).{0,6}(上电|充电|放电|启动|刷写|复位|起飞)")
INFO_LEAD = re.compile(r"(?i)^(pls|please|just|first|also|then|quickly)?\s*(read|export|download|pull|fetch|get|analy[sz]e|review|summari[sz]e|compare|plot|chart|"
                       r"graph|tabulate|list|count|correlate|overlay|cross-?check|cross-?reference|check|look|inspect|examine|study|investigate|trace|"
                       r"characteri[sz]e|quantify|estimate|compute|calculate|extract|parse|decode|filter|bin|bucket|histogram|grep|resample|diff|sort|"
                       r"annotate|label|mark|flag|note|document|record|log|draft|write|prepare|compile|collate|update|ask|request|query|email|call|"
                       r"interview|contact|confirm with|find|identify|determine|map|audit|triage|search|retrieve|obtain|collect|gather|replay|simulate|"
                       r"model|fit|reconcile|eyeball|scan|match|align|merge|req|q|upload|share|send|go through|look into|dig into|verify from|"
                       r"going through|looking (at|into)|digging into|reviewing|analy[sz]ing|reading|checking the (logs?|data|records?)|pulling|downloading|"
                       r"exporting|comparing|plotting|summari[sz]ing|drafting|writing|asking|requesting|correlating|tabulating|annotating)\b")


# drafting a document (not issuing it to be carried out): its content is described, not executed
DRAFT_CONTENT = re.compile(r"(?i)\b(draft|write|write up|prepare|compile|outline|sketch|add to|append to)(s|ing|ed)?\b[^:.;]{0,60}\b(plan|notice|checklist|advisory|memo|bulletin|"
                           r"worksheet|form|card|template|note|handover|log sheet|record sheet|instructions|guidance|"
                           r"summary|report|section|sop|letter|email|message|proposal|procedure document|test plan)\b[^:.;]{0,40}?(:|\bstating\b|\bsaying\b|"
                           r"\bthat says\b|\bwhich says\b|\bto say\b|\bthat\b|\bwith\b)|"
                           r"(起草|编写|撰写|拟定|制定)[^：:。]{0,20}(通知|计划|方案|清单|说明|公告|指引|报告|总结|邮件|备忘录)[：:]")
# an interrogative question (not "can you ... ?", which is a request)
ASKED = re.compile(r"(?i)^((\w+\s*){0,3}:\s*)?(was|were|did|does|do|is|are|has|have|had|any|how|what|when|why|which|who|whether|where)\b|^.{0,20}(是否|有没有|什么时候|几次|多少|为什么)")
PAST_EVENT = re.compile(r"(?i)\b(before|after|during|around|leading up to|prior to|at the time of) the (crash|incident|event|failure|accident|stop|shutdown|"
                        r"jump|drop|trip|fault|landing|re-?closures?)\b|\bin the last \d+\s*(s|sec|seconds|min|minutes)\b|"
                        r"\b(last|previous|past) (week|month|night|year|flight|drive|trip|sortie|\d+ (days|weeks|flights|drives))\b|\byesterday\b|\bearlier (this|today|in)\b|"
                        r"\b(sent|took|taken|recorded|logged|captured|uploaded|happened|occurred|shown)\b[^.;]{0,40}\b(before|after|during) the (drive|flight|trip|run|"
                        r"test|charge|landing|crash)\b|"
                        r"事故前|事故时|跳变前|掉电前|故障前|上周|昨天|上个月|之前几次|最近几次|上次")


UNAMBIGUOUS_INFO = re.compile(r"(?i)^(pls|please|just|first|also|then)?\s*(count how (many|often)|count the number of|how (many|often)|analy[sz]e|analy[sz]ing|review|reviewing|summari[sz]e|summari[sz]ing|compare|comparing|"
                              r"plot|plotting|chart|graph|tabulate|tabulating|correlate|correlating|overlay|cross-?check|cross-?reference|characteri[sz]e|"
                              r"quantify|estimate|compute|calculate|parse|decode|bin|bucket|histogram|grep|resample|diff|annotate|annotating|draft|drafting|"
                              r"write|writing|compile|collate|ask|asking|request|requesting|req|q|query|email|interview|summarise|triage|reconcile|eyeball|"
                              r"replay|simulate|go through|going through|look into|dig into)\b")


ZH_TIME_REF = re.compile(r"(每次|每一次|历次|上次|那次)?[\u4e00-\u9fff]{0,6}?(上电|上高压|充电|放电|解锁|起飞|降落|行驶|驾驶|启动|闭合|断开|试飞|悬停|跑圈)"
                         r"[\u4e00-\u9fff]{0,4}?(时的|时|期间|过程中|的时候|前后|阶段|次数)")
ZH_INFO_ANYWHERE = re.compile(r"比较|对比|计算|统计|分析|核对|汇总|标注|整理|列出|说明|总结")


def _drafts_removed(t: str) -> str:
    for m in reversed(list(DRAFT_CONTENT.finditer(t))):
        stop = re.search(r"(?i)\b(then|and then)\s+(?![a-z]+(ed|en)\b)|然后", t[m.end():])
        t = t[:m.end()] + " (document content) " + (t[m.end() + stop.start():] if stop else "")
    return t


def _strictly_information(text: str, remote: bool) -> bool:
    """Used only to LOWER a declared physical kind: every clause is led by an unambiguous information verb, or by an information verb
    whose object is data / a document and not the machine, or is a question / description continuing such a clause; nothing is
    instructed (no hazard verb leads a clause, no embedded instruction, no delegation)."""
    if _instruction_hazard(text, remote) is not None or DELEGATES.search(text):
        return False
    if ("?" in text or "？" in text) and ASKED.search(re.sub(r"^[^:：?？]{0,30}[:：]\s*", "", text.strip())[:40]):
        return True                              # the step is a question (e.g. "driver: was it charged ... ?"), nothing instructed
    clauses = [x for x in INSTR_SPLIT.split(_drafts_removed(_LEAD.sub("", text.strip()))) if x and x.strip(" ,，")]
    if not clauses:
        return False
    info_seen = False
    for c in clauses:
        t = _LEAD.sub("", c.strip())
        stripped = re.sub(r"(\b[\w-]+\s+)?(?:" + DATA_NOUN.pattern + ")", " ", t, flags=re.I)
        machine = bool(MACHINE_NOUN.search(stripped) or CONTROLLER.search(stripped))
        if UNAMBIGUOUS_INFO.match(t) or ZH_DATA.match(t) or re.match(r"(?i)(upload|share|send|forward)\b", t) and DATA_NOUN.search(t):
            info_seen = True
        elif _clause_head_is_info(t) and (DATA_NOUN.search(t) or QUANTITY.search(t)) and not machine:
            info_seen = True
        elif info_seen and _instruction_hazard(t, remote) is None and not _clause_head_is_hazard(t) and (
                ((DATA_NOUN.search(t) or QUANTITY.search(t)) and not re.match(r"[\u4e00-\u9fff]", t)) or ZH_INFO_ANYWHERE.search(t)):
            continue                             # an object list or a Chinese clause whose verb is an information verb
        elif info_seen and ("(document content)" in t or ASKED.match(t) or PAST_EVENT.search(t) or re.match(r"(?i)(the|a|an|its|their|each|every|all|both|any|how|whether|when|what|which|"
                                                                             r"why|where|who|for|from|in|on|at|of|with|by|over|against|during|vs)\b", t)):
            continue
        else:
            return False
    return info_seen


def _clause_head_is_hazard(c: str) -> bool:
    t = _LEAD.sub("", c.strip())
    words = re.findall(r"[a-zA-Z][a-zA-Z-]*", t.lower())
    return bool(ZH_STRONG.match(t)) or bool(words and words[0] in STRONG_VERBS) or bool(re.match(r"(?i)\s*(key|close|charge|drive|fly|arm|start|power|energi)", t))


def _clause_head_is_info(c: str) -> bool:
    t = _LEAD.sub("", c.strip())
    if ZH_DATA.match(t):
        return True
    m = INFO_LEAD.match(t)
    if not m:
        return False
    v = m.group(2).lower()
    if v in ("check", "look", "inspect", "examine", "get", "pull", "fetch", "record", "log", "mark", "label", "flag", "note", "trace", "map", "scan",
             "count", "collect", "gather", "find", "identify", "determine", "match", "align", "fit", "model"):
        stripped = re.sub(r"(\b[\w-]+\s+)?(?:" + DATA_NOUN.pattern + ")", " ", t, flags=re.I)
        return bool(DATA_NOUN.search(t)) and not (MACHINE_NOUN.search(stripped) or CONTROLLER.search(stripped))
    return True


def _instruction_hazard(text: str, remote: bool) -> Optional[str]:
    """For a step declared no-touch: the hazardous kind it actually instructs, or None (reads clause by clause)."""
    t = _without_questions(re.sub(r"(?i)\b(is|are|be|gets?|will be)\s+then\b", r"\1", text), remote and not DELEGATES.search(text))
    t = _historical_removed(t)
    for m in reversed(list(DRAFT_CONTENT.finditer(t))):         # drafting a plan / notice / report executes nothing in it
        stop = re.search(r"(?i)\b(then|and then)\s+(?![a-z]+(ed|en)\b)|然后", t[m.end():])
        end = m.end() + stop.start() if stop else len(t)
        t = t[:m.end()] + " (document content) " + t[end:]
    if DELEGATES.search(t) and not QUESTION.search(t):
        return "handle"                                          # work handed to a person at the machine
    worst, info_before = None, False
    for c in [x for x in INSTR_SPLIT.split(t) if x and x.strip(" ,，")]:
        found = None
        if ASKED.match(c.strip()) and ("?" in text or "？" in text):
            continue                                             # a question ("was it charged before the jump?"), not a request
        if info_before and PAST_EVENT.search(c) and not _clause_head_is_hazard(c):
            continue                                             # describes the incident window ("throttle in the last 30 s before the crash")
        emb = EMBEDDED.search(c)
        found_emb = None
        if emb:                                                  # an instruction embedded in the clause ("to reflash the ESC", "while driving")
            tail = emb.group(0) + " " + c[emb.end():]
            found_emb = ("remote_command" if remote and not DELEGATES.search(c) else "operate") if _operating(tail) else (_kind_from_text(tail, remote) or "handle")
        if _clause_head_is_info(c) and not re.search(r"[\u4e00-\u9fff]", c):
            info_before = True
        elif re.search(r"[\u4e00-\u9fff]", c):                  # Chinese: no word / object boundary to rely on; only noun compounds,
            info_before = info_before or _clause_head_is_info(c)   # questions, past references, negation, and (under a leading information
            zc = emb and c[:emb.start()] or c                      # verb) time references 每次…时的 make hazard words harmless
            if ZH_DATA.match(_LEAD.sub("", zc.strip())):
                zc = ZH_TIME_REF.sub("", zc)
            found = _kind_from_text(zc, remote)
            if found is None and ZH_STRONG.search(c) and not ZH_DATA.match(c.strip()):
                found = "handle"
        else:
            words = re.findall(r"[a-zA-Z][a-zA-Z-]*", _LEAD.sub("", c).lower())
            noun_phrase = bool(words) and words[0] in ("the", "a", "an", "its", "their", "each", "every", "all", "both", "any", "how", "whether",
                                                       "when", "what", "which", "why", "where", "who", "after", "before", "during", "on", "at",
                                                       "in", "for", "from", "of", "with", "over", "between", "against", "per", "by")
            if info_before and noun_phrase and not emb and not re.search(r"(?i)\b(is|are|be|being|gets?)\s+(then\s+|now\s+)?\w+ed\b|\b\w+s (on|it|the)\b", c):
                found = None                                     # an object / description continuing the information clause
            else:
                found = _kind_from_text(emb and c[:emb.start()] or c, remote)
                if found is None and _imperative_hazard(c):
                    found = "handle"
        if found_emb and (found is None or SEVERITY[found_emb] > SEVERITY[found]):
            found = found_emb
        if found and (worst is None or SEVERITY[found] > SEVERITY[worst]):
            worst = found
    return worst


def _kind_from_text(hz: str, remote: bool) -> Optional[str]:
    if _operating(hz):
        return "remote_command" if remote and not DELEGATES.search(hz) else "operate"      # driving to discharge is still operating
    if energising_text(hz) or asks_for(OPERATE, hz):
        return "charge_discharge" if asks_for(CHARGE_WORDS, hz) else "energise"
    if asks_for(CONTACT, hz):
        return "handle"
    return None


def _escalated(kind: str, text: str, remote: bool) -> str:
    """The step's effective kind: the declared kind, raised (never lowered here) to what the text instructs."""
    if kind not in STEP_KINDS:
        kind = "handle"                          # an undeclared or unknown kind is treated as hands-on work
    found = _instruction_hazard(text, remote)          # what the text instructs, read clause by clause (any declared kind)
    if found is None:
        return kind
    return found if SEVERITY[found] > SEVERITY[kind] else kind


def evaluate_action(na: Optional[Dict[str, Any]], plan: Dict[str, Any], *, hv_system: bool) -> Dict[str, Any]:
    """Verdict on one proposed action: {verdict: pass|blocked, outcome: auto|confirm|block, requires_confirmation, rules, steps, ...}."""
    if not isinstance(na, dict) or not na.get("action"):
        return {"verdict": "pass", "outcome": "auto", "requires_confirmation": False, "rules": [], "class": None, "template": None}
    steps = na.get("steps")
    if not (isinstance(steps, list) and steps and all(isinstance(x, dict) for x in steps)):
        v = _evaluate_text(na, plan, hv_system=hv_system)
        rules = [r for r in v["rules"] if r["rule"] != "G4"]          # approval is now enforced by the gate itself (confirm)
        tpl = _known_template(na)
        hz = _without_questions(_text(na), (na.get("where") or "").lower() in ("remote", "desk", "office") and not DELEGATES.search(_text(na)))
        if tpl is not None and not _data_template(tpl) and tpl.get("hv_state") == "open" and (energising_text(hz) or asks_for(OPERATE, hz)):
            rules = rules + [{"rule": "G6", "reason": f"{tpl['id']} is done with HV open; the text energises, charges or operates",
                              "provenance": PROVENANCE["G6"]}]
        hard = [r for r in rules if r["rule"] in ("G1", "G2", "G3", "G5", "G6")]   # no declared steps: unrecognised wording stays fail-closed
        soft: List[Dict[str, Any]] = []
        if hard:
            outcome = "block"
        elif soft or v.get("hazardous"):
            outcome = "confirm"
            if not soft:
                rules = rules + [{"rule": "CONFIRM", "reason": "planned physical procedure", "provenance": PROVENANCE["CONFIRM"]}]
        else:
            outcome = "auto"
        return {**v, "rules": hard + soft + [r for r in rules if r["rule"] == "CONFIRM"], "verdict": "blocked" if outcome == "block" else "pass",
                "outcome": outcome, "requires_confirmation": outcome == "confirm", "structured": False}
    from app.physchecks import planner
    known = {t["id"]: t for t in planner.TEMPLATES}
    rows = {r["id"]: r for r in (plan.get("recommended") or []) + (plan.get("handoff") or [])}
    holds = plan.get("safety_holds") or []
    release = {x for h in holds for x in h.get("release_by") or []}
    remote = (na.get("where") or "on_site").lower() in ("remote", "desk", "office")
    tid = (na.get("template_id") or "").strip()
    tpl = known.get(tid)
    fired: List[Dict[str, Any]] = []

    def fire(rule, reason):
        fired.append({"rule": rule, "reason": reason, "provenance": PROVENANCE[rule]})
    eff = []
    for st in steps:
        text = str(st.get("text") or "")
        declared = str(st.get("kind") or "")
        k = _escalated(declared, text, remote)
        if k == declared and k in ("measure", "handle", "procedure") and not DELEGATES.search(text):   # a declared hold-breaking kind is never lowered
            if _strictly_information(text, remote):
                k = "read_data"                  # a careless physical label on information work (strict: every clause works on data / documents)
        eff.append({"declared": st.get("kind"), "kind": k, "text": text[:200], "escalated": k != st.get("kind")})
    procedure = None
    if tid and tid.lower() != "none":
        if tpl is None:
            fire("G2", f"template {tid!r} is not a Veyra procedure")
        elif not _data_template(tpl) and tid not in rows and tid not in release:
            fire("G2", f"template {tid!r} is not offered by the plan for this case (offered: {', '.join(sorted(rows)) or 'none'})")
        elif not _data_template(tpl):
            procedure = tpl
    if procedure is not None and not any(e["kind"] in PHYSICAL for e in eff):
        eff.append({"declared": None, "kind": "procedure", "text": f"{procedure['id']}: {procedure['title']}", "escalated": True})
    phys = [e for e in eff if e["kind"] in PHYSICAL]
    if holds:
        breaking = [e for e in eff if e["kind"] in BREAKS_HOLD]
        if breaking or (procedure and procedure.get("hv_state") == "closed" and phys):
            for h in holds:
                fire("G1", f"{h['id']} is active ({h['title']}); " + ("; ".join(f"{e['kind']}: {e['text'][:80]}" for e in breaking) or
                                                                   f"{procedure['id']} needs high voltage closed"))
    if phys and procedure is None and not any(r["rule"] == "G2" for r in fired):
        fire("G3", "physical work with no planned procedure" + (f" ({tid} is a data procedure)" if tpl is not None else "") + ": " +
             "; ".join(f"{e['kind']}: {e['text'][:80]}" for e in phys[:3]))
    if phys and procedure is not None:
        beyond = [e for e in phys if e["kind"] in NEVER_COVERED or e["kind"] not in (procedure.get("covers") or [])]
        if beyond:
            fire("G6", f"{procedure['id']} does not cover: " + "; ".join(f"{e['kind']}: {e['text'][:80]}" for e in beyond[:3]))
    if fired:
        outcome = "block"
    elif phys:
        outcome = "confirm"
        fire("CONFIRM", f"{len(phys)} step(s) change the machine's state or carry risk, under {procedure['id']} "
                        f"(qualification: {procedure['safety']['qualification']})")
    else:
        outcome = "auto"
    return {"verdict": "blocked" if outcome == "block" else "pass", "outcome": outcome, "requires_confirmation": outcome == "confirm",
            "rules": fired, "class": sorted({e["kind"] for e in eff}), "template": tid or None, "hazardous": bool(phys),
            "steps": eff, "structured": True,
            "confirm_by": (procedure or {}).get("safety", {}).get("qualification") if outcome == "confirm" else None}
