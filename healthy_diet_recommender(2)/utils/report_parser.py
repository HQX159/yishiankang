# utils/report_parser.py
"""
健康报告结构化解析 —— 把 OCR 出来的报告文本变成能用的数字

为什么需要它
    现在 `HealthReport.content_text` 只是一坨 OCR 原文，直接截 500 字丢给大模型。
    血糖、血脂、血压到底是多少，系统并不知道，所以：
      - 做不了趋势、做不了评分、做不了量化分析
      - 大模型拿到的是一堆噪声，只能靠猜
    本模块把原文里的指标抠成结构化字典。

设计取舍
    - **纯规则 + 纯标准库**（只 import re）。不依赖大模型、数据库、Flask，
      也不引入 pandas / scikit-learn，因此**不会动 NumPy 版本、不影响 YOLO 训练环境**。
    - 可以脱离服务单独自测：python utils/report_parser.py
    - 抽不到的指标一律为 None，**不猜、不填默认值**。

与其他模块的分工
    report_parser = 抽指标（抽到多少算多少）
    risk_guard    = 判危险（按阈值决定要不要拦）
    两者的血糖 / 血压正则目前各写了一份，后续统一为 report_parser 供数（见开发思路实施记录）。

自测
    python utils/report_parser.py
"""
import re

# ---------------------------------------------------------------------------
# 指标规格：键 / 中文名 / 正则 / 合理范围 / 单位换算
# ---------------------------------------------------------------------------
# 换算说明：mg/dL → mmol/L 的系数按检验科常用值
_MGDL_TO_MMOL_GLU = 1 / 18.0        # 葡萄糖
_MGDL_TO_MMOL_CHOL = 1 / 38.67      # 总胆固醇 / LDL / HDL
_MGDL_TO_MMOL_TG = 1 / 88.57        # 甘油三酯
_MGDL_TO_UMOL_UA = 59.48            # 尿酸 mg/dL → µmol/L
KPA_TO_MMHG = 7.5                   # 血压 kPa → mmHg（1 kPa = 7.5 mmHg）—— 公开，risk_guard 共用
_KPA_TO_MMHG = KPA_TO_MMHG          # 内部旧名

# µmol/L 有两种写法：U+00B5 的 µ 和 U+03BC 的 μ，实际文档里都出现过，两个都收
_UNIT_MMOL = (r"(?P<u>mmol/L|mmol/l|mmol|毫摩尔每升|毫摩尔|mg/dL|mg/dl|mg/L"
              r"|µmol/L|μmol/L|umol/L|µmol/l|μmol/l)")
_UNIT_MMHG = r"(?P<u>mmHg|mmhg|毫米汞柱|kPa|kpa)"
_NUM = r"(?P<v>\d+(?:\.\d+)?)"


# 指标名与数值之间的分隔符文本 —— 公开导出，供 risk_guard 等下游复用。
# 为什么必须共用一份：`report_parser` 决定"能不能抽出来"，`risk_guard` 决定
# "要不要拦下来"。两处各写一份写法规则就会出现空档，实测：
#   `空腹血糖(FPG) 17.8 mmol/L` —— 解析抽得到，风险扫描却漏了，17.8 不弹就医警告。
# 同一个数值只因为写法不同就被区别对待，是安全问题，不是体验问题。
NAME_GAP = (r"\s*(?:[:：=＝]|为|是|约|大约)?\s*"
            r"(?:[（(][^（()）\n]{1,16}[）)])?\s*")


def _sep(char_gap=0):
    """（内部保留的旧名，等价于 NAME_GAP；新代码直接用 NAME_GAP 即可。）

    覆盖实际报告单上的三类写法：
      - 标点：`空腹血糖：5.6` / `空腹血糖=5.6`
      - 助词：`空腹血糖为 5.6` / `约 5.6`
      - **括号代号**：`空腹血糖(FPG) 5.6` / `糖化血红蛋白(HbA1c) 6.1%`
        —— 这是检验报告单上最常见的写法之一。早期版本不吞括号，
        整项直接漏抽（实测 `空腹血糖(FPG) 5.6 mmol/L` 抽 0 项）。

    括号内容限 1~16 个字符、且不含括号与换行，避免把
    `空腹血糖(参考区间3.9-6.1) 5.6` 这类长注释吞过头。
    """
    return NAME_GAP


def _unit_factor(unit, table):
    """按单位文本返回换算系数；无单位或已是标准单位则返回 1.0。"""
    if not unit:
        return 1.0
    u = unit.lower().replace(" ", "")
    if "mg" not in u:
        return 1.0
    return table


# 每条规格拆成三段，而不是一整条 pattern：
#   names —— 指标名的各种写法（别名全收在这里）
#   value —— 数值与单位那一段
#   pattern —— 由 `names + NAME_GAP + value` 拼出（见本定义之后的组装循环）
# 为什么要拆：除了"抽出数值"，还要能单独回答"这份报告到底提没提过这个指标" ——
# 指标区为空时，"报告里根本没有血糖" 和 "写了血糖但没读出来" 要给出完全不同的文案。
# 两个问题必须共用同一套别名，否则会出现"检测说提到了、抽取却抽不出来"的自相矛盾。
# ---------------------------------------------------------------------------
# 裸「血糖」的上下文守卫
# ---------------------------------------------------------------------------
# 为什么不能只靠正则的负向断言：`餐后 2h 血糖 14.2` 里「血糖」前面是**空格**，
# 字符级 `(?<!餐后)` 完全挡不住 —— 实测真实库 5 份报告都因此凭空多出一条
# "未注明时点血糖 14.2"（和餐后血糖同一个值，纯属重复）。
# 所以退一步看**前面这一小句**里有没有时点限定词：有就说明这个「血糖」
# 其实属于上面那条更具体的规则，这里必须让开。
_GLUCOSE_TIMING_RE = re.compile(
    r"(?:空腹|餐后|餐前|随机|睡前|凌晨|夜间|早晨|午后|"
    r"[0-9一二两]+\s*(?:小时|h|H))"
)

# 往回看多远算"同一小句"。太短会漏（`餐后 2 小时  血糖`），太长会跨句误伤。
_GUARD_WINDOW = 16
_SENTENCE_BREAKS = "。；;\n，,、"


def _bare_glucose_guard(text, match):
    """这个「血糖」是不是真的没写时点？"""
    window = text[max(0, match.start() - _GUARD_WINDOW):match.start()]
    # 只认同一小句里的限定词：`空腹血糖 8.9。血糖 9.2` 里的第二处必须放过
    cut = max(window.rfind(ch) for ch in _SENTENCE_BREAKS)
    if cut >= 0:
        window = window[cut + 1:]
    if _GLUCOSE_TIMING_RE.search(window):
        return False
    # 「今日出现低血糖 3.2」是**症状叙述**，不是检验读数。抽进来会让趋势图多一个假点。
    if window.rstrip().endswith("低"):
        return False
    return True


# 「餐后 1 小时」与「餐后 2 小时」是两个不同时点（见 _SPECS 里的注释）。
# 通用的「餐后N小时血糖」规则天生也能匹配「餐后1小时血糖」，所以要让它让开。
_ONE_HOUR_RE = re.compile(r"餐后\s*(?:1|一)\s*(?:小时|h|H)")


def _not_one_hour(text, match):
    """这条「餐后…血糖」是不是 1 小时的？是就让给 postprandial_1h_glucose。"""
    return _ONE_HOUR_RE.search(match.group(0) or "") is None


_SPECS = [
    # ── 基础 ──
    {
        "key": "height", "label": "身高", "unit": "cm", "order": 1,
        "names": r"身高",
        "value": r"(?P<v>\d{2,3}(?:\.\d+)?)(?!\d)\s*(?P<u>cm|厘米|CM)?",
        "lo": 50, "hi": 250, "factor": None,
    },
    {
        "key": "weight", "label": "体重", "unit": "kg", "order": 2,
        # 负向断言：别把「体重指数 / 体质量指数」当成体重，那是 BMI
        "names": r"体重(?!指数|质量)",
        "value": r"(?P<v>\d{1,3}(?:\.\d+)?)(?!\d)\s*(?P<u>kg|公斤|千克|KG)?",
        "lo": 10, "hi": 300, "factor": None,
    },
    {
        "key": "bmi", "label": "BMI", "unit": "", "order": 3,
        "names": r"(?:BMI|体重指数|体质量指数)",
        "value": r"(?P<v>\d{1,2}(?:\.\d+)?)(?!\d)",
        "lo": 10, "hi": 60, "factor": None,
    },

    # ── 血糖 ──
    # 别名按"检验科报告单上真实出现过"的口径收，不做无根据的泛化：
    #   空腹血糖 = 空腹血糖 / 空腹葡萄糖 / **葡萄糖**（生化单的标准名）/ GLU / FPG / 血糖(空腹)
    # 裸写的「血糖」**单独一条 spec**（见下面 unspecified_glucose），不并进空腹这一项：
    #   ① 它既可能是空腹也可能是随机，归成"空腹"会写错名字；
    #   ② 并进来会从左边抢先匹配掉「随机血糖」「餐后血糖」，属误判来源。
    {
        "key": "fasting_glucose", "label": "空腹血糖", "unit": "mmol/L", "order": 5,
        "names": (r"(?:空腹血糖|空腹葡萄糖|葡萄糖|GLU|FPG"
                  r"|血糖\s*(?:值)?\s*[（(]\s*空腹\s*[）)])(?:值)?"),
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 1.0, "hi": 40.0, "factor": _MGDL_TO_MMOL_GLU,
    },
    # 「餐后 1 小时」单独成一个指标（2026-10-22 补）：
    #   糖友一天常测「空腹 + 餐后 1 小时 + 餐后 2 小时」，三者混成一个数就看不出
    #   "峰值什么时候来、多久能落下去"。所以 1 小时归 postprandial_1h_glucose。
    # ⚠️ 这带来一个必须挡住的问题：下面通用的「餐后N小时血糖」也能匹配「餐后1小时血糖」，
    #   同一条文本会落两行、页面上出现同一个数两个名字。所以给通用规则挂 `_not_one_hour`
    #   守卫让开（真实库 14 份报告里 0 份提到 1 小时，改动前已核实）。
    {
        "key": "postprandial_1h_glucose", "label": "餐后1小时血糖", "unit": "mmol/L", "order": 6,
        "names": (r"(?:餐后\s*(?:1|一)\s*(?:小时|h|H)\s*血糖"
                  r"|1\s*[hH]\s*PG|1\s*小时\s*PG)(?:值)?"),
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 1.0, "hi": 45.0, "factor": _MGDL_TO_MMOL_GLU,
    },
    {
        "key": "postprandial_glucose", "label": "餐后血糖", "unit": "mmol/L", "order": 7,
        "names": (r"(?:餐后\s*[0-9一二两]+\s*(?:小时|h|H)\s*血糖|餐后血糖"
                  r"|2\s*[hH]\s*PG|2\s*小时\s*PG)(?:值)?"),
        "guard": _not_one_hour,
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 1.0, "hi": 45.0, "factor": _MGDL_TO_MMOL_GLU,
    },
    {
        "key": "random_glucose", "label": "随机血糖", "unit": "mmol/L", "order": 7,
        "names": r"(?:随机血糖|随机葡萄糖)",
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 1.0, "hi": 45.0, "factor": _MGDL_TO_MMOL_GLU,
    },
    # 裸写的「血糖」（2026-10-22 补）：家用血糖仪截图、手写随访单、体检小结里最常见的写法。
    # 不抽的话这类报告**抽 0 项、风险也不判** —— 实测「血糖 9.2 mmol/L」被判成正常，
    # 这是"文字读对了但数字没落地"的安全空档，比抽不到更危险。
    # 归到独立 key、名字里带「未注明时点」，阈值取空腹口径（更严的那一侧），
    # 不硬猜成空腹或餐后 —— 猜错就等于替用户改了一个医学前提。
    # ⚠️ 这条规则的命门是"别抢走带时点的写法"：空腹 / 随机 / 餐后血糖、餐后2小时血糖
    # 都必须归到各自那条规则上去。只靠正则负向断言做不到（见 guard 的注释）。
    # 后面跟 `(空腹)` 的写法归 fasting_glucose，这里用 lookahead 让开，
    # 否则同一份报告会落两条、页面上并排出现两个不同名字的同一个数。
    # 负向断言挡不住「餐后 2h 血糖」（"血糖"前是空格），真正的判断交给 `guard`
    # 去看前面一小句里有没有时点词 —— 实测只用 lookbehind 时，真实库 5 份报告
    # 都凭空多出一条 14.2，与餐后血糖重复。
    {
        "key": "unspecified_glucose", "label": "血糖（未注明时点）", "unit": "mmol/L", "order": 7,
        "names": r"血糖(?:值)?(?!\s*[（(]\s*空腹)",
        "guard": _bare_glucose_guard,
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 1.0, "hi": 40.0, "factor": _MGDL_TO_MMOL_GLU,
    },
    {
        "key": "hba1c", "label": "糖化血红蛋白", "unit": "%", "order": 8,
        "names": r"(?:糖化血红蛋白|糖化\s*Hb|HbA1c|HbA1C|GHb|A1c)(?:值)?",
        "value": _NUM + r"\s*(?P<u>%|％)?",
        "lo": 2.0, "hi": 25.0, "factor": None,
    },

    # ── 血脂 ──
    # `(?<!蛋白)胆固醇` 的负向断言必须保留：否则「高密度脂蛋白胆固醇」会被当成总胆固醇。
    {
        "key": "total_cholesterol", "label": "总胆固醇", "unit": "mmol/L", "order": 10,
        "names": r"(?:总胆固醇|(?<!蛋白)胆固醇|TCHO|CHOL|TC)(?:值)?",
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 0.5, "hi": 25.0, "factor": _MGDL_TO_MMOL_CHOL,
    },
    {
        "key": "triglyceride", "label": "甘油三酯", "unit": "mmol/L", "order": 11,
        "names": r"(?:甘油三酯|三酰甘油|TG)(?:值)?",
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 0.1, "hi": 40.0, "factor": _MGDL_TO_MMOL_TG,
    },
    {
        "key": "ldl", "label": "低密度脂蛋白", "unit": "mmol/L", "order": 12,
        "names": r"(?:低密度脂蛋白(?:胆固醇)?|低密度胆固醇|LDL-?C?)(?:值)?",
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 0.1, "hi": 20.0, "factor": _MGDL_TO_MMOL_CHOL,
    },
    {
        "key": "hdl", "label": "高密度脂蛋白", "unit": "mmol/L", "order": 13,
        "names": r"(?:高密度脂蛋白(?:胆固醇)?|高密度胆固醇|HDL-?C?)(?:值)?",
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 0.1, "hi": 10.0, "factor": _MGDL_TO_MMOL_CHOL,
    },

    # ── 尿酸 ──
    # `UA` 两侧加英文边界，避免在英文串里误命中。
    {
        "key": "uric_acid", "label": "尿酸", "unit": "µmol/L", "order": 15,
        "names": r"(?:血尿酸|尿酸|(?<![A-Za-z])UA(?![A-Za-z])|URIC)(?:值)?",
        "value": _NUM + r"\s*" + _UNIT_MMOL + r"?",
        "lo": 50.0, "hi": 1500.0, "factor": _MGDL_TO_UMOL_UA,
    },
]

# 血压：合并写法（145/92）与单独写法（收缩压 145）
# 数值用 \d{1,3} 而不是 \d{2,3}：kPa 单位下舒张压可能只有一位（如 `14/9 kPa`）。
# 放宽一位不会放进噪声 —— 下面 _parse_blood_pressure 的 40~300 mmHg 区间会兜住。
_BP_COMBINED_NAMES = r"(?:血压|BP)"
_BP_COMBINED_RE = re.compile(
    _BP_COMBINED_NAMES + NAME_GAP
    + r"(?P<s>\d{1,3}(?:\.\d+)?)\s*/\s*(?P<d>\d{1,3}(?:\.\d+)?)\s*"
    + _UNIT_MMHG + r"?",
    re.IGNORECASE,
)
_BP_SINGLE_SPECS = [
    {
        "key": "systolic_bp", "label": "收缩压", "unit": "mmHg", "order": 4,
        "names": r"(?:收缩压|高压)",
        "value": _NUM + r"\s*" + _UNIT_MMHG + r"?",
        "lo": 40, "hi": 300, "factor": None,
        "unit_convert": {"kpa": _KPA_TO_MMHG},
    },
    {
        "key": "diastolic_bp", "label": "舒张压", "unit": "mmHg", "order": 4.5,
        "names": r"(?:舒张压|低压)",
        "value": _NUM + r"\s*" + _UNIT_MMHG + r"?",
        "lo": 20, "hi": 200, "factor": None,
        "unit_convert": {"kpa": _KPA_TO_MMHG},
    },
]

# 组装完整匹配表达式：names + 分隔符 + value。
# 只在这里生成一次 —— 抽取数值（parse_health_report）与判断"是否提到过"
# （detect_metric_names）因此永远共用同一套别名，不会各写一份然后漂掉。
for _spec in _SPECS + _BP_SINGLE_SPECS:
    _spec["pattern"] = _spec["names"] + NAME_GAP + _spec["value"]

# 身高 / 体重 的**合并写法** —— 体检单常把两个指标塞进同一格、共用一个表头：
#     `身高 / 体重 170 cm / 82 kg`   `身高/体重 170/82`   `身高/体重：170cm/82kg`
# ⚠️ 为什么必须单独一条规则（2026-10-14 修）：
#     "身高"与"体重"之间夹着 `/ 体重`，NAME_GAP 吞不下 → "身高"整项匹配失败；
#     而**"体重"会抓走后面的 170**（那其实是身高）→ 身高丢失、体重落库 170 kg。
#     错误数字会一路进趋势图与分析，比"抽不到"更隐蔽。
#     注意：分列的 `身高 170 cm  体重 82 kg` 本来就能正确解析，这条规则**要求两个值之间有斜杠**，
#     所以不会去抢分列写法的活（也不会把 BMI 那类误当进来）。
_HEIGHT_WEIGHT_RE = re.compile(
    r"(?:身高|身长)\s*[/／]?\s*(?:体重|重量)?" + NAME_GAP
    + r"(?P<h>\d{2,3}(?:\.\d+)?)\s*(?:cm|厘米|CM)?"
    + r"\s*[/／]\s*"
    + r"(?P<w>\d{1,3}(?:\.\d+)?)\s*(?:kg|公斤|千克|KG)?",
    re.IGNORECASE,
)

# 过敏史
_ALLERGY_RE = re.compile(
    r"(?:过敏史|过敏原|已知过敏|食物过敏)" + _sep() + r"([^\n\r。；;]{1,60})"
)
_ALLERGY_EMPTY = ("无", "未见", "未", "不", "否认", "阴性", "暂无", "否认药物", "-", "—")

# 展示顺序
_DISPLAY_ORDER = [
    "height", "weight", "bmi",
    "systolic_bp", "diastolic_bp",
    "fasting_glucose", "postprandial_1h_glucose", "postprandial_glucose", "random_glucose",
    "unspecified_glucose", "hba1c",
    "total_cholesterol", "triglyceride", "ldl", "hdl",
    "uric_acid",
]

_ALL_KEYS = [s["key"] for s in _SPECS] + [s["key"] for s in _BP_SINGLE_SPECS]
_LABELS = {s["key"]: s["label"] for s in _SPECS + _BP_SINGLE_SPECS}
_UNITS = {s["key"]: s["unit"] for s in _SPECS + _BP_SINGLE_SPECS}
# 按 key 取规格（合并写法解析要复用 lo/hi 做范围校验，避免范围规则两处各写一份漂掉）
_SPEC_BY_KEY = {s["key"]: s for s in _SPECS + _BP_SINGLE_SPECS}

# 对外暴露展示顺序（下划线版是本模块自用，公开版供落库 / 前端排序复用）
DISPLAY_ORDER = _DISPLAY_ORDER
_DISPLAY_INDEX = {key: idx for idx, key in enumerate(_DISPLAY_ORDER)}


def label_of(key):
    """指标 key → 中文名（未登记的原样返回），供下游拼用户可见文案。"""
    return _LABELS.get(key) or key

# ---------------------------------------------------------------------------
# 指标分组 —— ⚠️ **产品口径**，只用来决定页面文案，不参与任何诊断结论
# ---------------------------------------------------------------------------
# 糖尿病核心：血糖 + 糖化。缺了它们就谈不上"看这个人糖尿病方面的情况"。
# `unspecified_glucose` 也算核心 —— 只写「血糖 9.2」的报告同样是在讲血糖，
# 漏了它，这类报告会被判成"看不出血糖情况"并劝用户再传一份（等于自己抽到了还说没有）。
CORE_DIABETES_KEYS = ("fasting_glucose", "postprandial_1h_glucose", "postprandial_glucose",
                      "random_glucose", "unspecified_glucose", "hba1c")
# 代谢相关：血脂、血压、尿酸、BMI —— 与代谢综合征相关，但不等于糖尿病本身。
METABOLIC_KEYS = CORE_DIABETES_KEYS + (
    "systolic_bp", "diastolic_bp",
    "total_cholesterol", "triglyceride", "ldl", "hdl",
    "uric_acid", "bmi",
)


def detect_metric_names(text):
    """报告里**提到过**哪些指标 —— 只看名字，不要求后面跟着数值。

    为什么需要它
        指标区为空时有两种完全不同的情况，文案必须分开说：
          ① 这份报告本身就不含这些指标（胃镜 / 影像 / 病理这类叙述型报告）
             → 该告诉用户"看不出血糖情况，想看得传血糖报告单"；
          ② 报告里明明写了指标名，只是没读出数值（排版被打散 / 规则没覆盖）
             → 该承认"没认出来"，这是系统的问题，不是用户的。
        只看名字就能把两者分开，不需要任何医学判断。

    Returns:
        list[str]: 命中的指标 key，按展示顺序排列；没命中返回 []
    """
    source = text or ""
    if not source.strip():
        return []

    hits = []
    for spec in _SPECS + _BP_SINGLE_SPECS:
        try:
            guard = spec.get("guard")
            if guard is None:
                hit = re.search(spec["names"], source, re.IGNORECASE)
            else:
                # 带守卫的规则（目前只有裸「血糖」）要跳过"其实属于更具体写法的那些位置"，
                # 否则「餐后 2 小时血糖」会让它误报一个 unspecified_glucose。
                hit = next((m for m in re.finditer(spec["names"], source, re.IGNORECASE)
                            if guard(source, m)), None)
            if hit:
                hits.append(spec["key"])
        except re.error:                      # 名字写法异常不该拖垮上传主流程
            continue

    # 合并写法「血压 145/92」：一个名字覆盖收缩压 + 舒张压两项
    if re.search(_BP_COMBINED_NAMES, source, re.IGNORECASE):
        for key in ("systolic_bp", "diastolic_bp"):
            if key not in hits:
                hits.append(key)

    hits.sort(key=lambda k: _DISPLAY_INDEX.get(k, len(_DISPLAY_INDEX)))
    return hits


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _round(value, digits=2):
    return round(value + 0.0, digits)


def _first_valid(text, spec, hit=None):
    """按规格在文本里找第一个落在合理范围内的数值，返回 (值, 单位)。

    两次换算机会，顺序固定：
      1. `factor` —— 该指标固定的单位换算（如血糖一律 mg/dL → mmol/L）
      2. `unit_convert` —— 按**实际读到的单位**触发的换算（如血压读到 kPa 才换）
    范围校验放在换算之后 —— 否则 `19.3 kPa` 这个合法值会先被
    mmHg 的 40~300 区间判死，整项漏抽（实测过的坑）。

    hit：可选的 dict，传了就把**被采用那一次**的原文写进 hit['text']。
         给「餐后血糖」判定显示名用（见 _pp_label）：同一个 key 下，
         报告写「餐后2小时血糖」和只写「餐后血糖」不该显示成同一个名字。
    """
    for match in re.finditer(spec["pattern"], text, re.IGNORECASE):
        guard = spec.get("guard")
        if guard and not guard(text, match):
            continue
        raw = float(match.group("v"))
        unit = match.groupdict().get("u") or ""
        factor = spec.get("factor")
        value = raw * (_unit_factor(unit, factor) if factor else 1.0)

        unit_convert = spec.get("unit_convert") or {}
        u = (unit or "").lower().replace(" ", "")
        for marker, k in unit_convert.items():
            if marker in u:
                value = raw * k
                break

        if spec["lo"] <= value <= spec["hi"]:
            if hit is not None:
                hit["text"] = match.group(0)
            return _round(value), unit
    return None, ""


# 「餐后血糖」这一项的中文名，跟着报告原文走（2026-10-23）。
# 一份报告里「餐后 2 小时血糖 12.3」和只写「餐后血糖 8.2」是两件事：
# 后者没说时点，**不替它补"2 小时"**；「餐后 3 小时血糖」也不该被叫成笼统的「餐后血糖」。
_PP_HOUR_RE = re.compile(
    r"餐后\s*([0-9一二两]+)\s*(?:小时|h|H)\s*血糖"
    r"|([0-9一二两]+)\s*[hH]\s*PG",
    re.IGNORECASE,
)
_CN_DIGIT = {"一": "1", "二": "2", "两": "2"}


def _pp_label(hit_text):
    """命中的原文 → 这一项该叫什么。

    找不到时点（报告只写「餐后血糖」）时返回「餐后血糖」，
    宁可笼统也不替报告补一个它没写的时点。
    """
    m = _PP_HOUR_RE.search(hit_text or "")
    if not m:
        return _LABELS["postprandial_glucose"]
    num = m.group(1) or m.group(2) or ""
    return "餐后%s小时血糖" % _CN_DIGIT.get(num, num)


def _bp_to_mmhg(value, unit):
    """血压统一换算成 mmHg；单位不是 kPa 时原样返回。"""
    if "kpa" in (unit or "").lower().replace(" ", ""):
        return value * _KPA_TO_MMHG
    return value


def _parse_blood_pressure(text, metrics):
    """血压：先试合并写法 145/92，再试收缩压 / 舒张压分开写。

    合并写法读到 kPa 时也要换算（`血压 16/10.7 kPa` = 120/80 mmHg），
    换算放在区间校验之前 —— 否则 16 会被 mmHg 的 40 下限判死。

    血压一律取整：临床读数本来就是整数 mmHg，kPa 换过来的 80.25 这种
    小数显示出来只会让人困惑。
    """
    for match in _BP_COMBINED_RE.finditer(text):
        unit = match.groupdict().get("u") or ""
        sys_v = _bp_to_mmhg(float(match.group("s")), unit)
        dia_v = _bp_to_mmhg(float(match.group("d")), unit)
        if 40 <= sys_v <= 300 and 20 <= dia_v <= 200 and sys_v > dia_v:
            metrics["systolic_bp"] = _round(sys_v, 0)
            metrics["diastolic_bp"] = _round(dia_v, 0)
            return

    for spec in _BP_SINGLE_SPECS:
        value, _ = _first_valid(text, spec)
        if value is not None:
            metrics[spec["key"]] = _round(value, 0)


def _parse_height_weight_combined(text):
    """解析「身高 / 体重 170 cm / 82 kg」这类**合并写法**，返回 {指标键: 数值}。

    命中不了就返回 {}，交给常规的 _SPECS 扫描。

    单独成一条规则的原因见 _HEIGHT_WEIGHT_RE 上方的注释 —— 简言之：
    常规规则下这种写法会让体重抓走身高值（170），是真错数据，不是漏抽。
    两个值都用 _SPEC_BY_KEY 里的 lo/hi 做范围校验，防止怪值混进来。
    """
    out = {}
    match = _HEIGHT_WEIGHT_RE.search(text or "")
    if not match:
        return out
    for key, group in (("height", "h"), ("weight", "w")):
        spec = _SPEC_BY_KEY[key]
        try:
            value = _round(float(match.group(group)))
        except (TypeError, ValueError):
            continue
        if spec["lo"] <= value <= spec["hi"]:
            out[key] = value
    return out


def _parse_allergies(text):
    """过敏史 → 列表。明确写"无 / 未见"时返回空列表。"""
    for match in _ALLERGY_RE.finditer(text):
        chunk = match.group(1).strip()
        if not chunk:
            continue
        head = chunk[:2]
        if any(head.startswith(word) for word in _ALLERGY_EMPTY):
            return []
        parts = re.split(r"[、,，/／;；\s]+", chunk)
        items = []
        for part in parts:
            item = part.strip(" ：:。.-—")
            if not item or len(item) > 12:
                continue
            if any(item.startswith(word) for word in _ALLERGY_EMPTY):
                continue
            if item not in items:
                items.append(item)
        if items:
            return items[:10]
    return []


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def parse_health_report(text):
    """
    解析健康报告文本，抽出结构化指标。

    Args:
        text: OCR / PDF 提取出的报告文本，可为 None

    Returns:
        dict:
            metrics  -- {指标键: 数值}，抽不到的键不会出现
            labels   -- {指标键: 中文名}
            units    -- {指标键: 单位}
            allergies-- [过敏原字符串]
            missing  -- [没抽到的指标中文名]
            found    -- 抽到几个指标
    """
    source = text or ""
    metrics = {}

    if not source.strip():
        return {
            "metrics": {}, "labels": {}, "units": {}, "allergies": [],
            "missing": [_LABELS[k] for k in _ALL_KEYS],
            "found": 0,
        }

    # 合并写法优先：「身高 / 体重 170 cm / 82 kg」。
    # 命中的键必须在下面的常规扫描里**跳过** —— 否则"体重"会再抓一次 170，
    # 把刚解析对的 82 覆盖掉（这正是本次要修的 bug）。
    combined = _parse_height_weight_combined(source)
    metrics.update(combined)

    pp_hit = {}                      # 「餐后血糖」被采用那次的原文，用来定显示名
    for spec in _SPECS:
        if spec["key"] in combined:
            continue
        value, _unit = _first_valid(
            source, spec,
            hit=pp_hit if spec["key"] == "postprandial_glucose" else None,
        )
        if value is not None:
            metrics[spec["key"]] = value

    _parse_blood_pressure(source, metrics)

    # 身高 + 体重 都在、但报告没给 BMI 时，自己算一个
    if "bmi" not in metrics and "height" in metrics and "weight" in metrics:
        height_m = metrics["height"] / 100.0
        if height_m > 0:
            bmi = metrics["weight"] / (height_m ** 2)
            if 10 <= bmi <= 60:
                metrics["bmi"] = _round(bmi, 1)

    allergies = _parse_allergies(source)
    missing = [_LABELS[k] for k in _ALL_KEYS if k not in metrics]

    labels = {k: _LABELS[k] for k in metrics}
    if "postprandial_glucose" in metrics:
        # 报告写「餐后2小时血糖」就叫「餐后2小时血糖」，只写「餐后血糖」就叫「餐后血糖」
        labels["postprandial_glucose"] = _pp_label(pp_hit.get("text"))

    return {
        "metrics": metrics,
        "labels": labels,
        "units": {k: _UNITS[k] for k in metrics},
        "allergies": allergies,
        "missing": missing,
        "found": len(metrics),
    }


def format_metrics(parsed):
    """把解析结果拼成一行中文，供展示或塞进大模型 prompt。"""
    metrics = (parsed or {}).get("metrics") or {}
    if not metrics:
        return ""

    parts = []
    for key in _DISPLAY_ORDER:
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, float) and value == int(value):
            value = int(value)
        unit = _UNITS.get(key) or ""
        if key in ("systolic_bp", "diastolic_bp"):
            continue  # 血压合并显示
        # 数字与单位之间留一个空格；百分号除外（中文习惯写 7.8%）
        gap = "" if unit in ("%", "％") else " "
        parts.append(f"{_LABELS[key]} {value}{gap}{unit}".strip())

    sys_v = metrics.get("systolic_bp")
    dia_v = metrics.get("diastolic_bp")
    if sys_v is not None or dia_v is not None:
        bp = f"{int(sys_v) if sys_v is not None else '?'}/{int(dia_v) if dia_v is not None else '?'}"
        parts.append(f"血压 {bp} mmHg")

    allergies = (parsed or {}).get("allergies") or []
    if allergies:
        parts.append("过敏史 " + "、".join(allergies))

    return " | ".join(parts)


def build_report_context(text, raw_limit=500):
    """
    给大模型用的报告上下文：有结构化指标就放前面，再附原文摘录；抽不到则退回原文。

    这是本模块与生成流程的对接点，抽不到指标时行为与改动前完全一致（安全兜底）。
    """
    source = text or ""
    parsed = parse_health_report(source)
    digest = format_metrics(parsed)

    if not digest:
        return source[:raw_limit]

    sections = [f"## 报告结构化指标\n{digest}"]
    excerpt = source[:raw_limit].strip()
    if excerpt:
        sections.append(f"## 报告原文摘录\n{excerpt}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# 自测：直接运行本文件即可，不需要启动 Flask
# ---------------------------------------------------------------------------
_FULL_REPORT = """
某医院检验报告单
姓名：张某某   性别：男   年龄：52
身高 170cm   体重 78.5kg
血压 145/92 mmHg
空腹血糖 8.9 mmol/L
糖化血红蛋白 7.8 %
总胆固醇 6.4 mmol/L
甘油三酯 2.8 mmol/L
低密度脂蛋白胆固醇 4.1 mmol/L
高密度脂蛋白胆固醇 1.1 mmol/L
尿酸 520 µmol/L
过敏史：花生、海鲜
"""

_CASES = [
    {
        "name": "完整报告全抽出",
        "text": _FULL_REPORT,
        "check": lambda r: (
            r["metrics"].get("height") == 170
            and r["metrics"].get("weight") == 78.5
            and r["metrics"].get("systolic_bp") == 145
            and r["metrics"].get("diastolic_bp") == 92
            and r["metrics"].get("fasting_glucose") == 8.9
            and r["metrics"].get("hba1c") == 7.8
            and r["metrics"].get("total_cholesterol") == 6.4
            and r["metrics"].get("triglyceride") == 2.8
            and r["metrics"].get("ldl") == 4.1
            and r["metrics"].get("hdl") == 1.1
            and r["metrics"].get("uric_acid") == 520
            and r["allergies"] == ["花生", "海鲜"]
        ),
    },
    {
        "name": "名字后带括号代号",
        "text": "空腹血糖(FPG) 5.6 mmol/L  糖化血红蛋白(HbA1c) 6.1 %",
        "check": lambda r: r["metrics"].get("fasting_glucose") == 5.6
        and r["metrics"].get("hba1c") == 6.1,
    },
    {
        "name": "检验科标准别名",
        "text": ("葡萄糖 5.6 mmol/L  总胆固醇(TC) 4.8 mmol/L  "
                 "甘油三酯(TG) 1.7 mmol/L  尿酸(UA) 420 umol/L"),
        "check": lambda r: r["metrics"].get("fasting_glucose") == 5.6
        and r["metrics"].get("total_cholesterol") == 4.8
        and r["metrics"].get("triglyceride") == 1.7
        and r["metrics"].get("uric_acid") == 420,
    },
    {
        "name": "括号里是参考区间",
        "text": "空腹血糖(参考区间3.9-6.1) 5.6 mmol/L",
        "check": lambda r: r["metrics"].get("fasting_glucose") == 5.6,
    },
    {
        "name": "随机血糖不被当空腹血糖",
        "text": "随机血糖 9.0 mmol/L",
        "check": lambda r: r["metrics"].get("random_glucose") == 9.0
        and "fasting_glucose" not in r["metrics"],
    },
    # ── 裸写的「血糖」（2026-10-22 补：改前这份报告抽 0 项，9.2 还被判成正常）──
    {
        "name": "裸写血糖归到未注明时点",
        "text": "血糖 9.2 mmol/L",
        "check": lambda r: r["metrics"].get("unspecified_glucose") == 9.2
        and "fasting_glucose" not in r["metrics"]
        and "random_glucose" not in r["metrics"],
    },
    {
        "name": "裸写血糖·「值」与冒号写法都认",
        "text": "血糖值为 9.2  血糖：6.1",
        "check": lambda r: r["metrics"].get("unspecified_glucose") == 9.2,
    },
    {
        "name": "血糖仪 / 指尖血写法",
        "text": "指尖血糖 9.2 mmol/L",
        "check": lambda r: r["metrics"].get("unspecified_glucose") == 9.2,
    },
    {
        "name": "带时点的写法不被裸血糖抢走",
        "text": "空腹血糖 5.4  随机血糖 9.0  餐后2小时血糖 12.3",
        "check": lambda r: r["metrics"].get("fasting_glucose") == 5.4
        and r["metrics"].get("random_glucose") == 9.0
        and r["metrics"].get("postprandial_glucose") == 12.3
        and "unspecified_glucose" not in r["metrics"],
    },
    {
        "name": "血糖(空腹) 仍归空腹",
        "text": "血糖(空腹) 5.6 mmol/L",
        "check": lambda r: r["metrics"].get("fasting_glucose") == 5.6
        and "unspecified_glucose" not in r["metrics"],
    },
    {
        "name": "「低血糖 3.2」是症状叙述，不当读数",
        "text": "今日出现低血糖 3.2 mmol/L",
        "check": lambda r: "unspecified_glucose" not in r["metrics"],
    },
    {
        "name": "同一份报告里两种写法各留一条",
        "text": "空腹血糖 5.4 mmol/L\n血糖 9.2 mmol/L",
        "check": lambda r: r["metrics"].get("fasting_glucose") == 5.4
        and r["metrics"].get("unspecified_glucose") == 9.2,
    },
    # ⚠️ 下面三条是**真实库跑出来的误报**钉住的（2026-10-22 第一版只用了 lookbehind，
    #    结果 5 份真实报告都凭空多出一条 "未注明时点血糖 14.2"，与餐后血糖重复）。
    #    「餐后 2h 血糖」里"血糖"前面是空格，字符级 lookbehind 挡不住。
    {
        "name": "餐后2h血糖（数字+单位紧贴）不被裸血糖抢",
        "text": "2 餐后 2h 血糖 14.2 mmol/L 目标 < 10.0 偏高",
        "check": lambda r: r["metrics"].get("postprandial_glucose") == 14.2
        and "unspecified_glucose" not in r["metrics"],
    },
    {
        "name": "餐后2小时血糖（带空格）不被裸血糖抢",
        "text": "餐后 2 小时血糖 14.2 mmol/L",
        "check": lambda r: r["metrics"].get("postprandial_glucose") == 14.2
        and "unspecified_glucose" not in r["metrics"],
    },
    # 「餐后血糖」这一项的显示名跟着原文走（2026-10-23）：
    # 报告写了时点才写时点，没写就老实叫「餐后血糖」，不替它补 2 小时。
    {
        "name": "报告写「餐后2小时」时显示名带时点",
        "text": "餐后2小时血糖 12.3 mmol/L",
        "check": lambda r: r["labels"].get("postprandial_glucose") == "餐后2小时血糖",
    },
    {
        "name": "报告只写「餐后血糖」时显示名不带时点",
        "text": "餐后血糖 8.2 mmol/L",
        "check": lambda r: r["labels"].get("postprandial_glucose") == "餐后血糖",
    },
    {
        "name": "报告写「餐后3小时」时如实叫 3 小时",
        "text": "餐后3小时血糖 9.9 mmol/L",
        "check": lambda r: r["labels"].get("postprandial_glucose") == "餐后3小时血糖",
    },
    {
        "name": "2hPG 简写也算 2 小时",
        "text": "2hPG 9.5 mmol/L",
        "check": lambda r: r["labels"].get("postprandial_glucose") == "餐后2小时血糖",
    },
    {
        "name": "「餐后血糖」在前、「餐后2小时」在后：按被采用的那条取名",
        "text": "餐后血糖 8.2  餐后2小时血糖 12.3 mmol/L",
        "check": lambda r: r["metrics"].get("postprandial_glucose") == 8.2
        and r["labels"].get("postprandial_glucose") == "餐后血糖",
    },
    {
        "name": "跨句的裸血糖仍然抽得到",
        "text": "空腹血糖 8.9 mmol/L。血糖 9.2 mmol/L",
        "check": lambda r: r["metrics"].get("fasting_glucose") == 8.9
        and r["metrics"].get("unspecified_glucose") == 9.2,
    },
    {
        "name": "「血糖 ≥16.7」带不等号不是读数",
        "text": "未触及危象阈值（血糖 ≥16.7 mmol/L 或收缩压 ≥180 mmHg）",
        "check": lambda r: "unspecified_glucose" not in r["metrics"],
    },
    # ── 餐后 1 小时 与 餐后 2 小时 分开（2026-10-22：血糖记录卡片要三个时点）──
    # 通用规则「餐后N小时血糖」天生也能匹配「餐后1小时血糖」，必须靠 `_not_one_hour` 让开，
    # 否则同一条文本会落两行、页面上同一个数出现两个名字。
    {
        "name": "餐后1小时归独立指标",
        "text": "餐后1小时血糖 11.8 mmol/L",
        "check": lambda r: r["metrics"].get("postprandial_1h_glucose") == 11.8
        and "postprandial_glucose" not in r["metrics"],
    },
    {
        "name": "餐后1小时·带空格与「值」也认",
        "text": "餐后 1 小时血糖值 10.2",
        "check": lambda r: r["metrics"].get("postprandial_1h_glucose") == 10.2
        and "postprandial_glucose" not in r["metrics"],
    },
    {
        "name": "餐后1小时与2小时各留一条",
        "text": "餐后 1 小时血糖 11.8\n餐后 2 小时血糖 9.4",
        "check": lambda r: r["metrics"].get("postprandial_1h_glucose") == 11.8
        and r["metrics"].get("postprandial_glucose") == 9.4,
    },
    {
        "name": "餐后2h 不会被 1 小时规则抢走",
        "text": "餐后 2h 血糖 14.2 mmol/L",
        "check": lambda r: r["metrics"].get("postprandial_glucose") == 14.2
        and "postprandial_1h_glucose" not in r["metrics"],
    },
    # ── 身高/体重 合并写法（2026-10-14 修：体重曾抓走身高值 170）──
    {
        "name": "身高体重合并写法·带单位",
        "text": "7 身高 / 体重 170 cm / 82 kg— — 腰围 94 cm，已达男性中心性肥胖标准",
        "check": lambda r: r["metrics"].get("height") == 170
        and r["metrics"].get("weight") == 82,
    },
    {
        "name": "身高体重合并写法·紧凑无单位",
        "text": "身高/体重 170/82",
        "check": lambda r: r["metrics"].get("height") == 170
        and r["metrics"].get("weight") == 82,
    },
    {
        "name": "身高体重合并写法·全角斜杠带冒号",
        "text": "身高／体重：170cm／82kg",
        "check": lambda r: r["metrics"].get("height") == 170
        and r["metrics"].get("weight") == 82,
    },
    {
        "name": "身高体重分列写法不受影响",
        "text": "身高 170 cm  体重 82 kg",
        "check": lambda r: r["metrics"].get("height") == 170
        and r["metrics"].get("weight") == 82,
    },
    {
        "name": "体重单独写不串到身高",
        "text": "体重 82 kg",
        "check": lambda r: r["metrics"].get("weight") == 82
        and "height" not in r["metrics"],
    },
    {
        "name": "体重不抓身高单位的数字",
        "text": "身高 170 cm，体重 82 kg，BMI 28.4",
        "check": lambda r: r["metrics"].get("weight") == 82
        and r["metrics"].get("height") == 170,
    },
    {
        "name": "HDL 带代号不误当总胆固醇",
        "text": "高密度脂蛋白胆固醇(HDL-C) 1.3 mmol/L",
        "check": lambda r: r["metrics"].get("hdl") == 1.3
        and "total_cholesterol" not in r["metrics"],
    },
    {
        "name": "血压用 kPa 换算",
        "text": "血压 16/10.7 kPa",
        "check": lambda r: r["metrics"].get("systolic_bp") == 120
        and r["metrics"].get("diastolic_bp") == 80,
    },
    {
        "name": "身高体重自动算 BMI",
        "text": "身高 170cm 体重 78.5kg",
        "check": lambda r: r["metrics"].get("bmi") == 27.2,
    },
    {
        "name": "报告已给 BMI 则不重算",
        "text": "身高 170cm 体重 78.5kg BMI 24.0",
        "check": lambda r: r["metrics"].get("bmi") == 24.0,
    },
    {
        "name": "mg/dL 换算成 mmol/L",
        "text": "空腹血糖 160 mg/dL 总胆固醇 250 mg/dL",
        "check": lambda r: abs(r["metrics"].get("fasting_glucose", 0) - 8.89) < 0.05
        and abs(r["metrics"].get("total_cholesterol", 0) - 6.46) < 0.05,
    },
    {
        "name": "收缩压舒张压分开写",
        "text": "收缩压 152 mmHg，舒张压 96 mmHg",
        "check": lambda r: r["metrics"].get("systolic_bp") == 152
        and r["metrics"].get("diastolic_bp") == 96,
    },
    {
        "name": "高密度脂蛋白不误当总胆固醇",
        "text": "高密度脂蛋白胆固醇 1.3 mmol/L",
        "check": lambda r: "total_cholesterol" not in r["metrics"] and r["metrics"].get("hdl") == 1.3,
    },
    {
        "name": "不合理值直接丢弃",
        "text": "空腹血糖 3500",
        "check": lambda r: "fasting_glucose" not in r["metrics"],
    },
    {
        "name": "过敏史为空",
        "text": "过敏史：无",
        "check": lambda r: r["allergies"] == [],
    },
    {
        "name": "只有部分指标时 missing 补齐",
        "text": "血压 130/85 mmHg",
        "check": lambda r: r["metrics"].get("systolic_bp") == 130
        and "空腹血糖" in r["missing"]
        and r["found"] == 2,
    },
    {
        "name": "空文本不报错",
        "text": "",
        "check": lambda r: r["found"] == 0 and r["metrics"] == {},
    },
    {
        "name": "None 不报错",
        "text": None,
        "check": lambda r: r["found"] == 0,
    },
    {
        "name": "无指标时上下文退回原文",
        "text": "本次体检未见明显异常，建议规律作息。",
        "check": lambda r: False,  # 由下方单独校验 build_report_context
    },
]


def _self_test():
    import sys

    passed = 0
    total = 0
    print("=" * 78)
    print("report_parser 自测")
    print("=" * 78)

    for case in _CASES:
        if case["name"] == "无指标时上下文退回原文":
            continue
        total += 1
        result = parse_health_report(case["text"])
        try:
            ok = bool(case["check"](result))
        except Exception as exc:
            ok = False
            print(f"[FAIL] {case['name']} 校验时出错: {exc}")
        if ok:
            passed += 1
            print(f"[PASS] {case['name']:<22} 抽到 {result['found']} 项")
        else:
            print(f"[FAIL] {case['name']}")
            print(f"       metrics={result['metrics']}")
            print(f"       allergies={result['allergies']}")

    # ---- detect_metric_names：判断"这份报告到底提没提过这些指标" ----
    # 它是"报告与糖尿病无关"这句文案的唯一判据，必须能扛住叙述型报告。
    name_cases = [
        ("提到血糖", "空腹血糖 5.6 mmol/L", ["fasting_glucose"]),
        # 裸写「血糖」也算提到了血糖 —— 否则这类报告会被判成"看不出血糖情况"，
        # 自己明明抽到了数字，却劝用户再传一份血糖报告。
        ("裸写血糖也算提到", "血糖 9.2 mmol/L", ["unspecified_glucose"]),
        ("带时点的不算裸写", "随机血糖 9.0 mmol/L", ["random_glucose"]),
        ("带代号也认", "葡萄糖(FPG) 5.6", ["fasting_glucose"]),
        ("胃镜报告不含指标", "内镜提示: 慢性胃炎，食管黏膜光滑，未见糜烂", []),
        ("血常规不含指标", "血常规：白细胞 6.2，红细胞 4.8", []),
        ("血压合并算两项", "血压 145/92 mmHg", ["systolic_bp", "diastolic_bp"]),
        ("体重指数不算体重", "体重指数 24.0", ["bmi"]),
        ("空文本返回空", "", []),
        ("None 返回空", None, []),
    ]
    total += len(name_cases)
    for name, text, expect in name_cases:
        got = detect_metric_names(text)
        if got == expect:
            passed += 1
            print(f"[PASS] {name:<22} {got}")
        else:
            print(f"[FAIL] {name}")
            print(f"       期望 {expect}，实际 {got}")

    # 上下文构造
    total += 2
    ctx_hit = build_report_context(_FULL_REPORT)
    if "报告结构化指标" in ctx_hit and "空腹血糖 8.9 mmol/L" in ctx_hit:
        passed += 1
        print("[PASS] 有指标时带上结构化摘要")
    else:
        print(f"[FAIL] 有指标时带上结构化摘要\n       {ctx_hit[:160]}")

    raw = "本次体检未见明显异常，建议规律作息。"
    ctx_miss = build_report_context(raw)
    if ctx_miss == raw:
        passed += 1
        print("[PASS] 无指标时原样退回")
    else:
        print(f"[FAIL] 无指标时原样退回\n       {ctx_miss[:160]}")

    print("-" * 78)
    print("完整报告解析结果：")
    print("   " + format_metrics(parse_health_report(_FULL_REPORT)))
    print("-" * 78)
    print(f"通过 {passed}/{total}")
    return passed == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if _self_test() else 1)
