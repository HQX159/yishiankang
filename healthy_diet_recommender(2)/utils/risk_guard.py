# utils/risk_guard.py
"""
健康风险拦截 —— 生成食谱前的一道安全闸门

为什么需要它
    现在只要用户传过健康报告，系统就会直接生成食谱。如果报告里的血糖 / 血压
    已经到了危险水平，正确行为是**先提示尽快就医**，而不是继续推荐今天吃什么。

设计取舍
    - 纯规则 + 纯标准库（只 import re，外加同包的 report_parser 取写法构件）。
      不调用大模型、不读数据库、不依赖 Flask，
      所以可以脱离服务单独自测，也不会影响任何正在运行的进程。
    - **宁可漏报，不可误报**。误报会让正常用户看不到食谱；
      因此只有"明确危险"才拦截（critical），"偏高"只提示（warn）。
    - 阈值参考《中国2型糖尿病防治指南(2020年版)》《中国高血压防治指南(2018修订版)》。
      如与医师判断冲突，一律以医师为准。
    - 同一套阈值服务**两个时机**，靠 context 参数只换文案、不换判定：
        context='generate' —— 生成食谱 / 膳食计划前拦截（默认）
        context='upload'   —— 报告刚上传完，顺手提醒
      为什么必须分开：上传时还没到生成那一步，说"因此本次没有生成食谱"是错的。

自测
    python -m utils.risk_guard
"""
import re

# 与 report_parser 共用同一套「名字—数值」分隔写法（含括号代号容忍）和 kPa 换算系数。
# 为什么必须共用：report_parser 决定"能不能抽出来"，本模块决定"要不要拦下来"。
# 两处各写一份写法规则就会出现空档 —— 实测 `空腹血糖(FPG) 17.8 mmol/L`
# 解析抽得到、风险却漏了，同一个数值只因为写法不同就不弹就医警告。
# 因此本文件自测请用 `python -m utils.risk_guard`（需要包上下文才能 import utils.report_parser）。
from utils.report_parser import NAME_GAP, KPA_TO_MMHG

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_CRITICAL = "critical"

DISCLAIMER = (
    "本建议基于公开膳食指南的通用原则生成，不能替代医师的诊断与处方。"
    "如有不适请及时就医。"
)

# 血糖阈值（统一换算为 mmol/L）
GLUCOSE_CRITICAL = 16.7      # 达到此值视为高血糖危象风险
GLUCOSE_WARN_FASTING = 7.0   # 空腹血糖偏高
GLUCOSE_WARN_POST = 11.1     # 餐后 / 随机血糖偏高
GLUCOSE_SANE_MAX = 60.0      # 超过此值基本是 OCR 误读，直接忽略

# 糖化血红蛋白阈值（%）
HBA1C_WARN = 9.0

# 血压阈值（mmHg）
BP_SYS_CRITICAL = 180
BP_DIA_CRITICAL = 110
BP_SYS_WARN = 160
BP_DIA_WARN = 100
BP_SANE_MAX = 300.0

# 报告原文里直接出现的危险信号词
CRITICAL_PHRASES = (
    "危急值", "酮症", "酮症酸中毒", "酸中毒", "高渗",
    "立即就医", "尽快就医", "急诊", "建议住院", "住院治疗",
)
WARN_PHRASES = (
    "严重过敏", "过敏性休克", "急性过敏",
)

# 否定词：出现在信号词前 3 个字内则不算命中（例如"无酮症""未见酸中毒"）
_NEGATIONS = ("无", "未见", "未", "不", "排除", "否认", "阴性")

# 短语**之后**的否定标记。报告单上「酮症：阴性」「酮症(-)」这种后置写法极常见，
# 只看前缀会把它当成阳性命中 —— 与"否定词窗口太窄"是同一类误报，一并覆盖。
_POST_NEGATIONS = ("阴性", "(-)", "（-）", "（－）", "未见异常")

# 句末标点：否定作用域不跨句。
_CLAUSE_END = "。；;！!？?\n"

# 转折词：出现即说明否定作用已被推翻（"无酮症，但出现酸中毒" → 后半句是阳性）
_TURN_WORDS = ("但", "但是", "然而", "不过", "却")

# 否定词到信号词的最大跨度（字）。超过就认为否定早已结束、管不到这里了 ——
# 防止「无糖尿病史，……（40 字后）……酮症」被判成否定。
_NEG_SCOPE_MAX = 30

# ---------------------------------------------------------------------------
# 结构化指标键 → (中文名, 偏高阈值, 危险阈值, 单位)
# 只收录指南里有明确切点、且本项目已在用的四项（血糖 / 糖化 / 血压）。
# 血脂、尿酸、BMI 的"正常范围"属医学口径，**未确认前一律不判定**。
# 判定用的常量与下面 check_health_risk() 用的是同一批，保证两处口径不会漂。
# ---------------------------------------------------------------------------
_METRIC_THRESHOLDS = {
    'fasting_glucose':      ('空腹血糖',     GLUCOSE_WARN_FASTING, GLUCOSE_CRITICAL, 'mmol/L'),
    'postprandial_glucose': ('餐后血糖',     GLUCOSE_WARN_POST,    GLUCOSE_CRITICAL, 'mmol/L'),
    # 餐后 1 小时（2026-10-22 新增指标）：**不新造标准**，直接沿用餐后那一档（11.1 / 16.7）。
    # 1 小时的值通常高于 2 小时，同一个阈值只会"少报"不会"多报" —— 符合"宁可漏报不可误报"。
    'postprandial_1h_glucose': ('餐后1小时血糖', GLUCOSE_WARN_POST, GLUCOSE_CRITICAL, 'mmol/L'),
    'random_glucose':       ('随机血糖',     GLUCOSE_WARN_POST,    GLUCOSE_CRITICAL, 'mmol/L'),
    # 报告只写「血糖 9.2」、没注明时点时用这一档：阈值取**空腹**的 7.0（更严的那一侧）。
    # 宁可多提示一句让人去核实时点，也不让 9.2 这种明显偏高的数字被当成"正常"放过。
    'unspecified_glucose':  ('血糖（未注明时点）', GLUCOSE_WARN_FASTING, GLUCOSE_CRITICAL, 'mmol/L'),
    'hba1c':                ('糖化血红蛋白', HBA1C_WARN,           None,             '%'),
    'systolic_bp':          ('收缩压',       BP_SYS_WARN,          BP_SYS_CRITICAL,  'mmHg'),
    'diastolic_bp':         ('舒张压',       BP_DIA_WARN,          BP_DIA_CRITICAL,  'mmHg'),
}


# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
# alternation 顺序即优先级（Python re 取最左、同位置取第一个能匹配的备选）：
# 「空腹血糖 / 随机血糖 / 餐后血糖」这类带限定的必须排在裸「血糖」前面，
# 否则会被「血糖」抢先匹配、丢掉"空腹"这个关键限定，阈值也就用错了。
_GLUCOSE_RE = re.compile(
    r"(?P<kind>空腹血糖|空腹葡萄糖|葡萄糖|"
    r"餐后\s*[0-9一二两]+\s*(?:小时|h|H)\s*血糖|餐后血糖|2\s*[hH]\s*PG|"
    r"随机血糖|随机葡萄糖|"
    r"血糖\s*(?:值)?\s*[（(]\s*空腹\s*[）)]|GLU|FPG|血糖)(?:值)?"
    + NAME_GAP +
    r"(?P<val>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mmol/L|mmol/l|mmol|毫摩尔每升|毫摩尔|mg/dL|mg/dl)?",
    re.IGNORECASE,
)

_HBA1C_RE = re.compile(
    r"(?:糖化血红蛋白|糖化\s*Hb|HbA1c|HbA1C|GHb|A1c)" + NAME_GAP
    + r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>%|％|百分)?",
    re.IGNORECASE,
)

# 数值宽度用 \d{1,3}：kPa 口径下舒张压可能只有一位（`14/9 kPa`）；
# 放宽后不会放进噪声，下面的 40~300 mmHg 区间照样兜住。
_BP_RE = re.compile(
    r"(?P<kind>收缩压|舒张压|高压|低压|血压)" + NAME_GAP
    + r"(?P<val>\d{1,3}(?:\.\d+)?)"
    r"(?:\s*/\s*(?P<val2>\d{1,3}(?:\.\d+)?))?\s*"
    r"(?P<unit>mmHg|mmhg|毫米汞柱|kPa|kpa)?",
    re.IGNORECASE,
)

_ORDER = {LEVEL_OK: 0, LEVEL_WARN: 1, LEVEL_CRITICAL: 2}

# 对外暴露一份"档位高低"的顺序表 —— utils/visit_advice.py 要按严重程度排序，
# 再写一份字符串列表就会出现"两处顺序不一致"的低级 bug。
LEVEL_ORDER = _ORDER

# 空腹口径的血糖叫法：带"空腹"字样，或检验科标准名「葡萄糖 / GLU / FPG」。
# 用来选阈值（空腹 7.0 vs 餐后/随机 11.1），必须和 report_parser 的归类一致。
_FASTING_ALIASES = ("葡萄糖", "GLU", "FPG")


def _is_fasting_kind(kind):
    if "空腹" in kind:
        return True
    return kind.strip().upper() in _FASTING_ALIASES


def _is_bare_glucose_kind(kind):
    """裸写的「血糖」—— 报告里没注明是空腹、餐后还是随机。

    与 report_parser 的 `unspecified_glucose` 同一口径。这类值**按空腹阈值判**
    （7.0，更严的一侧），因为漏报的代价比多提示一句大；
    但对外文案必须把"没注明时点"这个前提说出来，不能默认它就是空腹。
    """
    return re.sub(r"\s+", "", kind or "") == "血糖"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _finding(name, value, unit, level, message):
    return {
        "name": name,
        "value": value,
        "unit": unit or "",
        "level": level,
        "message": message,
    }


def _num(value):
    """浮点数去掉多余的 .0；None 原样返回。"""
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def _display(value, unit, fallback_unit):
    """把数值 + 单位拼成给人看的字符串：去多余小数、数字与单位间留一个空格。"""
    text = f"{_num(value)}"
    unit_text = unit or fallback_unit or ""
    if unit_text and not unit_text.startswith(" "):
        unit_text = " " + unit_text
    return f"{text}{unit_text}"


def _to_mmol(value, unit):
    """把血糖统一换算成 mmol/L。无单位且数值偏大时，按 mg/dL 处理。"""
    u = (unit or "").lower().replace(" ", "")
    if "mg" in u:
        return value / 18.0
    if not u and value > 33:
        return value / 18.0
    return value


def _is_negated(text, idx, phrase):
    """判断信号词在 idx 处是否处于**否定作用域**内（在否定范围内 → 不算阳性命中）。

    为什么不能只看"前面 3 个字"
        报告单大量使用**列举式否定**：「报告未检出酮症、酸中毒、渗漏、高渗等危急描述」。
        旧规则里"未"只否定了紧邻的"酮症"，跨过顿号后的「酸中毒」「高渗」就丢了否定上下文，
        被当成真有危急值 → 整份报告误判 critical。真实报告里的"未见A、B、C"是常态，
        这个误报会直接吓到用户，属安全问题。

    判定规则（作用域以**小句**为界，不跨句末标点）
        1. 短语后面紧邻否定标记（阴性 / (-) ）→ 否定；
        2. 短语之前的小句内出现否定词，且从该否定词到短语之间：
           - 没有数字（有数字说明中间插入了另一组指标，否定作用已结束）
           - 没有转折词（"无A，但出现B" 的 B 是阳性）
           - 跨度不超过 _NEG_SCOPE_MAX 字
           → 判为否定；
        3. 其余情况 → 正面命中。
    """
    # ---- 1. 后置否定：酮症：阴性 / 酮症(-) ----
    tail = text[idx + len(phrase): idx + len(phrase) + 6]
    if any(mark in tail for mark in _POST_NEGATIONS):
        return True

    # ---- 2. 前置否定：取同小句内、短语之前的文字作为作用域 ----
    clause_start = 0
    for i in range(idx - 1, -1, -1):
        if text[i] in _CLAUSE_END:
            clause_start = i + 1
            break
    window = text[clause_start:idx]

    # 取**最靠右**的否定词（它才是离短语最近、最可能管到这里的那个）
    cut = -1
    for neg in _NEGATIONS:
        pos = window.rfind(neg)
        if pos >= 0 and pos + len(neg) > cut:
            cut = pos + len(neg)
    if cut < 0:
        return False                                # 小句内没有否定词 → 正面

    between = window[cut:]
    if len(between) > _NEG_SCOPE_MAX:
        return False                                # 跨得太远，否定管不到这里
    if re.search(r"\d", between):
        return False                                # 中间夹了数字 → 已在讲别的指标
    if any(word in between for word in _TURN_WORDS):
        return False                                # 转折词推翻前面的否定
    return True


def _has_positive_phrase(text, phrase):
    """判断信号词是否"正面"出现（不在任何否定作用域内）。"""
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return False
        if not _is_negated(text, idx, phrase):
            return True
        start = idx + len(phrase)


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------
# 按时机准备的说明文案。判定结果（level / blocked / findings）与时机无关，
# 只有这段话要跟着场景变 —— 上传时尚未生成食谱，措辞必须对得上事实。
_MESSAGES = {
    "generate": {
        LEVEL_CRITICAL: (
            "根据您上传的健康报告，部分指标已达到需要及时就医的水平，"
            "因此本次没有生成食谱。请尽快携带报告到内分泌科或心血管内科就诊，"
            "由医师确定治疗方案与饮食要求。"
        ),
        LEVEL_WARN: "报告中有偏高指标，食谱已生成，请结合医师意见参考。",
    },
    "upload": {
        LEVEL_CRITICAL: (
            "您上传的报告中有指标已达到需要及时就医的水平。"
            "请尽快携带报告到内分泌科或心血管内科就诊，"
            "由医师确定治疗方案与饮食要求。"
        ),
        LEVEL_WARN: "报告中有偏高指标，生成食谱时会一并参考，请结合医师意见。",
    },
}


def check_health_risk(text, context="generate"):
    """
    扫描健康报告文本，判断是否存在危险指标。

    Args:
        text: 健康报告解析出的文本（可为 None / 空）
        context: 调用时机，决定 message 措辞
                 "generate" —— 生成食谱 / 计划前拦截（默认）
                 "upload"   —— 报告刚上传完顺带提醒（只提示，不阻断上传）

    Returns:
        dict:
            level    -- "ok" | "warn" | "critical"
            blocked  -- True 时调用方**不应生成食谱**，改为提示就医
            findings -- 命中的指标列表
            message  -- 可直接展示给用户的中文说明
            disclaimer -- 免责声明文案
    """
    source = text or ""
    findings = []
    messages = _MESSAGES.get(context) or _MESSAGES["generate"]

    if not source.strip():
        return {
            "level": LEVEL_OK,
            "blocked": False,
            "findings": [],
            "message": "",
            "disclaimer": DISCLAIMER,
        }

    # ---- 1. 血糖 ----
    for match in _GLUCOSE_RE.finditer(source):
        kind = re.sub(r"\s+", "", match.group("kind"))
        raw = float(match.group("val"))
        unit = match.group("unit") or ""
        value = _to_mmol(raw, unit)

        if value > GLUCOSE_SANE_MAX:
            continue  # 明显是 OCR 误读（人不可能到 60 mmol/L 以上）
        if value < 1.0:
            continue  # 明显误读

        shown = _display(raw, unit, " mmol/L")
        bare_kind = _is_bare_glucose_kind(kind)
        if value >= GLUCOSE_CRITICAL:
            findings.append(_finding(
                kind, raw, unit, LEVEL_CRITICAL,
                f"{kind} {shown}，已达危险水平，需尽快就医",
            ))
        else:
            # 裸「血糖」也走空腹阈值（见 _is_bare_glucose_kind），文案里交代前提。
            limit = (GLUCOSE_WARN_FASTING
                     if (_is_fasting_kind(kind) or bare_kind)
                     else GLUCOSE_WARN_POST)
            if value >= limit:
                if bare_kind:
                    message = (f"血糖 {shown} 偏高。报告没写明是空腹还是餐后，这里按空腹标准看；"
                               f"如果是餐后两小时内测的，这个数值通常不算高。"
                               f"请核实测量时间，就诊时告诉医生")
                else:
                    message = f"{kind} {shown} 偏高，建议结合医师意见调整饮食"
                findings.append(_finding(kind, raw, unit, LEVEL_WARN, message))

    # ---- 2. 糖化血红蛋白 ----
    for match in _HBA1C_RE.finditer(source):
        raw = float(match.group("val"))
        if raw <= 0 or raw > 25:
            continue
        if raw >= HBA1C_WARN:
            findings.append(_finding(
                "糖化血红蛋白", raw, match.group("unit") or "%", LEVEL_WARN,
                f"糖化血红蛋白 {raw}%，提示近三个月血糖控制不佳",
            ))

    # ---- 3. 血压 ----
    for match in _BP_RE.finditer(source):
        kind = match.group("kind")
        raw = float(match.group("val"))
        unit = match.group("unit") or "mmHg"
        second = match.group("val2")

        # kPa 先换成 mmHg 再判档：本项目对外统一 mmHg 口径，
        # 阈值常量也是 mmHg。不换算的话 `16/10.7 kPa`（=120/80）会被当成低血压丢弃。
        if "kpa" in unit.lower():
            raw = raw * KPA_TO_MMHG
            second = (float(second) * KPA_TO_MMHG) if second else None
            unit = "mmHg"
        else:
            second = float(second) if second else None

        if kind in ("舒张压", "低压"):
            sys_v, dia_v = None, raw
        else:
            sys_v = raw
            dia_v = second

        if sys_v is not None and sys_v > BP_SANE_MAX:
            continue
        if dia_v is not None and dia_v > BP_SANE_MAX:
            continue
        if sys_v is not None and sys_v < 40:
            continue
        if dia_v is not None and dia_v < 20:
            continue

        if dia_v is not None:
            shown = f"{_num(sys_v) if sys_v is not None else '?'}/{_num(dia_v)}"
        else:
            shown = _num(sys_v if sys_v is not None else dia_v)

        if (sys_v is not None and sys_v >= BP_SYS_CRITICAL) or \
           (dia_v is not None and dia_v >= BP_DIA_CRITICAL):
            findings.append(_finding(
                "血压", sys_v if sys_v is not None else dia_v, unit, LEVEL_CRITICAL,
                f"血压 {shown} {unit}，已达高血压危象水平，需尽快就医",
            ))
        elif (sys_v is not None and sys_v >= BP_SYS_WARN) or \
             (dia_v is not None and dia_v >= BP_DIA_WARN):
            findings.append(_finding(
                "血压", sys_v if sys_v is not None else dia_v, unit, LEVEL_WARN,
                f"血压 {shown} {unit} 偏高，建议结合医师意见调整饮食",
            ))

    # ---- 4. 报告原文里的危险信号词 ----
    for phrase in CRITICAL_PHRASES:
        if _has_positive_phrase(source, phrase):
            findings.append(_finding(
                "报告提示", phrase, "", LEVEL_CRITICAL,
                f"报告中出现「{phrase}」，请以医师意见为准",
            ))
    for phrase in WARN_PHRASES:
        if _has_positive_phrase(source, phrase):
            findings.append(_finding(
                "过敏提示", phrase, "", LEVEL_WARN,
                f"报告中出现「{phrase}」，生成食谱后请务必核对食材",
            ))

    # ---- 5. 汇总 ----
    level = LEVEL_OK
    for item in findings:
        if _ORDER[item["level"]] > _ORDER[level]:
            level = item["level"]

    if level == LEVEL_CRITICAL:
        message = messages[LEVEL_CRITICAL]
    elif level == LEVEL_WARN:
        message = messages[LEVEL_WARN]
    else:
        message = ""

    return {
        "level": level,
        "blocked": level == LEVEL_CRITICAL,
        "findings": findings,
        "message": message,
        "disclaimer": DISCLAIMER,
    }


def warn_summary(result):
    """把 warn 级发现拼成一行文字，方便塞进食谱或提示里。"""
    items = [f["message"] for f in (result or {}).get("findings", [])
             if f["level"] == LEVEL_WARN]
    return "；".join(dict.fromkeys(items))


def evaluate_metric(metric_key, value, unit=''):
    """
    给**结构化存储的单个指标值**判个档，供展示页标红 / 标黄用。

    与 check_health_risk() 的分工：
        check_health_risk  = 扫**报告原文**，抓的是"这一次报告要不要拦"
        evaluate_metric    = 看**库里的单个数值**，抓的是"这一行要不要标色"
    两者共用同一批阈值常量（见文件顶部），不会各说各话。

    Args:
        metric_key: `models/health_metric.py` 里的 metric_key，如 fasting_glucose
        value:      数值
        unit:       单位，仅用于拼提示文案

    Returns:
        dict: {"level", "label", "value", "unit", "message"}
              该指标没有确认口径时返回 **None**（调用方显示"—"即可，不要瞎标）
    """
    rule = _METRIC_THRESHOLDS.get(metric_key)
    if rule is None or value is None:
        return None

    label, warn_at, critical_at, default_unit = rule
    unit_text = unit or default_unit
    shown = _display(value, unit_text, default_unit)

    if critical_at is not None and value >= critical_at:
        level = LEVEL_CRITICAL
        message = f"{label} {shown}，已达危险水平，建议尽快就医"
    elif value >= warn_at:
        level = LEVEL_WARN
        message = f"{label} {shown} 偏高，建议结合医师意见"
    else:
        level = LEVEL_OK
        message = f"{label} {shown} 未见明显偏高"

    return {
        "level": level,
        "label": label,
        "value": _num(value),
        "unit": unit_text,
        "message": message,
    }


# ---------------------------------------------------------------------------
# 连续偏高升级（R5 第 3 项）
# ---------------------------------------------------------------------------
# 为什么只做 warn 级升级、不做"从 critical 再升"
#     critical 已经是最高的处置口径（尽快就医），再升也没有更强的话可说，
#     再设一档只会让文案失控。
# 为什么必须传"连续天数"进来，而不是在这里读库
#     本模块头部的设计取舍写得很清楚：**不读数据库、不依赖 Flask**，
#     所以能脱离服务单独自测。连续天数由 utils/visit_advice.py 从
#     health_metric 算好传进来，这里只做纯函数判断。
def escalate_level(level, streak_days=0, streak_warn=3):
    """连续多天偏高时把档位抬到至少要提醒。

    Args:
        level:       当前档位（来自 evaluate_metric / check_health_risk）
        streak_days: 连续偏高的天数（同一天多次只算一天）
        streak_warn: 几天算"连续"

    Returns:
        升级后的档位。只会**抬高**、不会降低；critical 原样返回。
    """
    if level == LEVEL_CRITICAL:
        return LEVEL_CRITICAL
    try:
        streak = int(streak_days or 0)
    except (TypeError, ValueError):
        streak = 0
    if streak >= max(1, int(streak_warn)) and _ORDER[level] < _ORDER[LEVEL_WARN]:
        return LEVEL_WARN
    return level


# ---------------------------------------------------------------------------
# 自测：直接运行本文件即可，不需要启动 Flask
# ---------------------------------------------------------------------------
_CASES = [
    ("正常报告", "空腹血糖 5.4 mmol/L，总胆固醇 4.2 mmol/L，血压 118/76 mmHg", "ok"),
    ("空腹血糖偏高", "空腹血糖 8.9 mmol/L", "warn"),
    ("空腹血糖危险", "空腹血糖 17.8 mmol/L", "critical"),
    ("餐后血糖危险", "餐后2小时血糖 18.2 mmol/L", "critical"),
    ("血压危象", "血压 185/115 mmHg", "critical"),
    ("舒张压危象", "舒张压 112 mmHg", "critical"),
    ("血压偏高", "血压 165/102 mmHg", "warn"),
    ("mg/dL 单位", "血糖 350 mg/dL", "critical"),
    ("危急值字样", "检验结论：血糖 9.1 mmol/L，标注危急值", "critical"),
    ("住院建议", "建议住院治疗", "critical"),
    ("否定词不算命中", "无酮症，未见酸中毒，空腹血糖 5.1 mmol/L", "ok"),
    # ── 否定作用域（2026-10-14 修：列举式否定曾被逐项误报 critical）──
    ("列举式否定不误报",
     "报告未检出酮症、酸中毒、渗漏、高渗等危急描述，血糖未达高血糖危象阈值", "ok"),
    ("列举式否定·顿号空格混用", "未见酮症 酸中毒 高渗", "ok"),
    ("后置否定不误报", "尿酮症：阴性；血糖 5.1 mmol/L", "ok"),
    ("后置否定括号写法", "酮症(-)", "ok"),
    ("真阳性仍拦", "报告提示酮症酸中毒，建议住院治疗", "critical"),
    ("转折词推翻否定", "既往无酮症病史，但本次出现酸中毒", "critical"),
    ("句末标点断否定作用域", "既往无酮症病史。本次检查提示酮症", "critical"),
    ("否定跨太远不生效",
     "无糖尿病家族史，饮食规律，运动规律，体重稳定，本次门诊复查结果如下：酮症", "critical"),
    ("否定后又出现数字仍拦", "无酮症，血糖 17.8 mmol/L 提示高渗风险", "critical"),
    ("严重过敏", "既往严重过敏史：花生、海鲜", "warn"),
    ("糖化血红蛋白偏高", "糖化血红蛋白 9.6%", "warn"),
    ("没有数值不误报", "血糖控制良好，建议继续保持", "ok"),
    ("OCR 误读不误报", "血糖 3500", "ok"),
    ("括号代号不丢警戒", "空腹血糖(FPG) 17.8 mmol/L", "critical"),
    ("葡萄糖按空腹口径判", "葡萄糖 8.9 mmol/L", "warn"),
    ("GLU 缩写也算数", "GLU 17.8 mmol/L", "critical"),
    ("随机血糖用餐后阈值", "随机血糖 9.0 mmol/L", "ok"),
    # ── 裸写的「血糖」（2026-10-22 补：改前 9.2 按餐后 11.1 判，直接放过）──
    ("裸写血糖按空腹阈值判", "血糖 9.2 mmol/L", "warn"),
    ("裸写血糖·「值」写法也认", "血糖值为 9.2", "warn"),
    ("裸写血糖正常不误报", "血糖 5.6 mmol/L", "ok"),
    ("裸写血糖危值照拦", "血糖 18.0 mmol/L", "critical"),
    ("裸写血糖不吃掉随机血糖", "随机血糖 9.0 mmol/L", "ok"),
    ("kPa 血压正常不误报", "血压 16/10.7 kPa", "ok"),
    ("kPa 血压危象照样拦", "血压 24/14.7 kPa", "critical"),
    ("空文本", "", "ok"),
]


def _self_test():
    passed = 0
    print("=" * 74)
    print("risk_guard 自测")
    print("=" * 74)
    for name, text, expect in _CASES:
        result = check_health_risk(text)
        flag = "PASS" if result["level"] == expect else "FAIL"
        if flag == "PASS":
            passed += 1
        else:
            print(f"[{flag}] {name}")
            print(f"       期望 {expect}，实际 {result['level']}")
            print(f"       命中 {[f['message'] for f in result['findings']]}")
            continue
        brief = result["findings"][0]["message"] if result["findings"] else "未命中任何风险"
        print(f"[{flag}] {name:<16} {result['level']:<8} {brief}")

    # ---- 结构化指标判档（展示页用） ----
    metric_cases = [
        ("空腹血糖 5.4", "fasting_glucose", 5.4, LEVEL_OK),
        ("空腹血糖 8.9（边界偏高）", "fasting_glucose", 8.9, LEVEL_WARN),
        ("空腹血糖 7.0（恰在阈值）", "fasting_glucose", 7.0, LEVEL_WARN),
        ("空腹血糖 16.7（恰在危值）", "fasting_glucose", 16.7, LEVEL_CRITICAL),
        ("餐后血糖 11.1 偏高", "postprandial_glucose", 11.1, LEVEL_WARN),
        # 未注明时点的血糖卡片：阈值与空腹同档，名字里必须带「未注明时点」
        ("未注明时点 9.2 偏高", "unspecified_glucose", 9.2, LEVEL_WARN),
        ("未注明时点 6.5 正常", "unspecified_glucose", 6.5, LEVEL_OK),
        ("未注明时点 16.7 危值", "unspecified_glucose", 16.7, LEVEL_CRITICAL),
        # 餐后 1 小时：沿用餐后那一档（不新造标准）
        ("餐后1小时 11.8 偏高", "postprandial_1h_glucose", 11.8, LEVEL_WARN),
        ("餐后1小时 8.0 正常", "postprandial_1h_glucose", 8.0, LEVEL_OK),
        ("餐后1小时 16.7 危值", "postprandial_1h_glucose", 16.7, LEVEL_CRITICAL),
        ("糖化 10.2 偏高", "hba1c", 10.2, LEVEL_WARN),
        ("糖化 7.8 正常", "hba1c", 7.8, LEVEL_OK),
        ("收缩压 185 危象", "systolic_bp", 185, LEVEL_CRITICAL),
        ("舒张压 100 偏高", "diastolic_bp", 100, LEVEL_WARN),
        ("总胆固醇无口径 → None", "total_cholesterol", 6.4, None),
        ("身高无口径 → None", "height", 172, None),
        ("value 为 None → None", "fasting_glucose", None, None),
        ("未知键 → None", "not_a_key", 1.0, None),
    ]
    total = len(_CASES) + len(metric_cases)
    for name, key, value, expect in metric_cases:
        result = evaluate_metric(key, value)
        actual = result["level"] if result else None
        if actual == expect:
            passed += 1
            print(f"[PASS] {name:<24} {actual}")
        else:
            print(f"[FAIL] {name}")
            print(f"       期望 {expect}，实际 {actual}")

    # ---- 裸写「血糖」的文案口径（2026-10-22）----
    # 判得严（按空腹 7.0）就必须说得明白：名字不能冒称"空腹血糖"，
    # 文案必须交代"没写明时点"，否则餐后 9.2 的用户会被自己的报告吓一跳。
    _bare = check_health_risk("血糖 9.2 mmol/L")
    _bare_f = _bare["findings"][0] if _bare["findings"] else {}
    _bare_msg = _bare_f.get("message") or ""
    bare_cases = [
        ("裸血糖 finding 名字不含「空腹」", "空腹" not in (_bare_f.get("name") or "")),
        ("裸血糖文案交代没写时点", "没写明" in _bare_msg),
        ("裸血糖文案给出餐后的解释", "餐后" in _bare_msg),
        ("裸血糖文案不冒称「空腹血糖」", "空腹血糖" not in _bare_msg),
        ("空腹写法的文案不掺「没写明」",
         "没写明" not in check_health_risk("空腹血糖 9.2 mmol/L")["findings"][0]["message"]),
        # 「餐后 2h 血糖 14.2」里的"血糖"前面是空格，曾经被当成裸血糖（真实库 5 份报告踩到）
        ("餐后2h血糖的文案不掺「没写明」",
         "没写明" not in check_health_risk("餐后 2h 血糖 14.2 mmol/L")["findings"][0]["message"]),
    ]
    total += len(bare_cases)
    for name, ok in bare_cases:
        if ok:
            passed += 1
            print(f"[PASS] {name}")
        else:
            print(f"[FAIL] {name}")

    # ---- 连续偏高升级（R5 第 3 项）----
    # 只抬高不降低；critical 原样返回；非法 streak 当 0 处理（不抛异常）
    escalate_cases = [
        ("ok + 连续 3 天 → warn", LEVEL_OK, 3, 3, LEVEL_WARN),
        ("ok + 连续 2 天 → 不升", LEVEL_OK, 2, 3, LEVEL_OK),
        ("ok + 连续 5 天（阈值 5）→ warn", LEVEL_OK, 5, 5, LEVEL_WARN),
        ("ok + 连续 4 天（阈值 5）→ 不升", LEVEL_OK, 4, 5, LEVEL_OK),
        ("warn + 连续 3 天 → 仍是 warn", LEVEL_WARN, 3, 3, LEVEL_WARN),
        ("critical 不被降级", LEVEL_CRITICAL, 0, 3, LEVEL_CRITICAL),
        ("critical + 连续 9 天 → 仍是 critical", LEVEL_CRITICAL, 9, 3, LEVEL_CRITICAL),
        ("streak 为 None 不抛异常", LEVEL_OK, None, 3, LEVEL_OK),
        ("streak 为字符串也能算", LEVEL_OK, '3', 3, LEVEL_WARN),
    ]
    total += len(escalate_cases)
    for name, lv, streak, need, expect in escalate_cases:
        actual = escalate_level(lv, streak, need)
        if actual == expect:
            passed += 1
            print(f"[PASS] {name:<28} {actual}")
        else:
            print(f"[FAIL] {name}")
            print(f"       期望 {expect}，实际 {actual}")

    # ---- 场景文案：判定必须一致，措辞按 upload / generate 分开 ----
    # 最后一项 expect_no_recipe = 文案里是否应出现"没有生成食谱"
    context_cases = [
        ("上传: critical 不提\"未生成食谱\"", "空腹血糖 17.8 mmol/L", "upload", LEVEL_CRITICAL, False),
        ("生成: critical 说明\"未生成食谱\"", "空腹血糖 17.8 mmol/L", "generate", LEVEL_CRITICAL, True),
        ("上传: warn 不提\"已生成食谱\"", "空腹血糖 8.9 mmol/L", "upload", LEVEL_WARN, False),
        ("生成: warn 说\"食谱已生成\"", "空腹血糖 8.9 mmol/L", "generate", LEVEL_WARN, False),
        ("上传: ok 文案为空", "空腹血糖 5.4 mmol/L", "upload", LEVEL_OK, False),
        ("未知 context 退回 generate 文案", "空腹血糖 17.8 mmol/L", "whatever", LEVEL_CRITICAL, True),
    ]
    total += len(context_cases) + 1
    for name, text, ctx, expect_level, expect_no_recipe in context_cases:
        r = check_health_risk(text, context=ctx)
        has_phrase = "没有生成食谱" in r["message"]
        if r["level"] == expect_level and has_phrase == expect_no_recipe:
            passed += 1
            print(f"[PASS] {name:<24} {r['level']}")
        else:
            print(f"[FAIL] {name}")
            print(f"       期望 {expect_level} / 含\"未生成食谱\"={expect_no_recipe}，"
                  f"实际 {r['level']} / {has_phrase}")
            print(f"       文案 {r['message']}")

    probe = "空腹血糖 17.8 mmol/L，血压 185/115 mmHg"
    up, gen = check_health_risk(probe, "upload"), check_health_risk(probe, "generate")
    if (up["level"] == gen["level"] and up["blocked"] == gen["blocked"]
            and up["findings"] == gen["findings"]):
        passed += 1
        print(f"[PASS] {'换 context 不改变判定':<24} {up['level']} / {len(up['findings'])} 项")
    else:
        print("[FAIL] 换 context 不改变判定")
        print(f"       upload={up['level']}/{len(up['findings'])}  "
              f"generate={gen['level']}/{len(gen['findings'])}")

    print("-" * 74)
    print(f"通过 {passed}/{total}")
    return passed == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)
