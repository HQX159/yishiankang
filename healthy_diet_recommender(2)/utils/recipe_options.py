# -*- coding: utf-8 -*-
"""R4 食谱三维度选项 —— 饮食目的 / 口味 / 用餐时间（纯标准库）。

设计要点
---------
1. **库里存英文 key，页面上显示中文**
   同 R1 控糖档位（TIER_LOW/MID/HIGH）的做法：文案随时可能改，
   库里历史数据不能被文案变更带跑。`label_of()` 负责翻译。

2. **三个维度都是"可选项"，缺省也有值**
   老用户直接点「开始生成」不填任何东西也能出结果
   （默认：**控糖** / 不挑 / 午餐）。
   默认给「控糖」而不是"普通养生"：本项目服务对象就是糖友，
   参数缺失时往**红线那一侧**兜底，比给一份不控糖的食谱安全。

3. **normalize() 同时认 key 和中文标签**
   小程序 / 第三方调用方可能直接传中文（"控糖"），一并接受；
   认不出来就回落到默认值，绝不因为一个非法值让生成失败。

4. **控糖是"红线"，不只是标签**
   选中控糖时，提示词里会追加一段**硬性食材约束**（见 build_prompt_clause），
   而不是只写一句"请控糖"—— 后者大模型经常听过就忘。

自测
    py -m utils.recipe_options
"""
import sys


# ---------------------------------------------------------------------------
# 三个维度
# ---------------------------------------------------------------------------
# (key, 中文标签, 一句话说明 —— 说明用于页面上的小字提示)
#
# 选项取舍（2026-10-18 收敛）：项目定位是**糖尿病饮食管理**，只保留跟糖友相关的目的。
# 已下架：muscle（增肌）、balanced（普通养生）—— 前者不属于控糖场景，
# 后者等于"什么都不限制"，跟控糖红线直接冲突，留着会让人误以为可以选。
# 保留 控脂减重 / 降压护心 是因为糖友三高共病太常见；清淡养胃照顾老年肠胃。
PURPOSES = [
    ("control_sugar", "控糖",     "少油少糖，优先选低升糖的食材"),
    ("control_fat",   "控脂减重", "低脂高纤，控制总热量"),
    ("control_bp",    "降压护心", "少盐少油，多钾多纤"),
    ("gentle",        "清淡养胃", "少刺激、好消化"),
]

FLAVORS = [
    ("plain",      "清淡原味"),
    ("savory",     "咸鲜"),
    ("sour_spicy", "酸辣"),
    ("sweet",      "微甜"),
    ("sauce",      "酱香"),
    ("any",        "不挑"),
]

MEAL_TIMES = [
    ("breakfast", "早餐"),
    ("lunch",     "午餐"),
    ("dinner",    "晚餐"),
    ("snack",     "加餐"),
    ("night",     "夜宵"),
]

OPTIONS = {
    "purpose": PURPOSES,
    "flavor": FLAVORS,
    "meal_time": MEAL_TIMES,
}

# 三个维度的中文名（拼提示词、做错误提示时用）
KIND_LABELS = {
    "purpose": "饮食目的",
    "flavor": "口味偏好",
    "meal_time": "用餐时间",
}

# 默认值 —— 三个维度都不填时的兜底档。
# purpose 故意兜到「控糖」：漏传参数的调用方（老版本小程序、第三方）宁可多一条
# 控糖约束，也不能给糖友生成一份不控糖的食谱；页面上的默认值另由
# infer_purpose() 按最新报告推断，这里只兜底。
DEFAULTS = {
    "purpose": "control_sugar",
    "flavor": "any",
    "meal_time": "lunch",
}

# 历史数据里出现过、但已经下架不再可选的 key —— **只用于显示**。
# 库里确实存着 1 条 purpose='muscle' 的老食谱（2026-10-18 查过），
# label_of 认不出来会渲染成「🎯 —」，看着像坏了；这里给它们留个中文名。
# ⚠️ 不代表它们还能被选中：normalize() 只认 OPTIONS 里的 key。
LEGACY_LABELS = {
    "purpose": {
        "muscle": "增肌",
        "balanced": "普通养生",
    },
}

_TABLE = {kind: {k: lab for k, lab, *_ in rows} for kind, rows in OPTIONS.items()}
_HINTS = {kind: {row[0]: (row[2] if len(row) > 2 else "") for row in rows}
          for kind, rows in OPTIONS.items()}


def label_of(kind, key):
    """key → 中文标签；已下架的历史 key 也能翻译；都不认识给 '—'（不抛异常）。"""
    table = _TABLE.get(kind, {})
    if key in table:
        return table[key]
    return LEGACY_LABELS.get(kind, {}).get(key) or "—"


def hint_of(kind, key):
    """key → 一句话说明（可能为空串）。"""
    return _HINTS.get(kind, {}).get(key) or ""


def normalize(kind, value):
    """把外部传入的值收敛成合法的 key。

    接受：合法 key / 中文标签 / 空 / 非法值。
    后三种一律回落到该维度的默认值 —— 生成流程不能因为一个脏参数挂掉。
    """
    table = _TABLE.get(kind)
    if not table:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in table:
            return value
        for k, lab in table.items():
            if value == lab:
                return k            # 容忍直接传中文
    return DEFAULTS.get(kind)


def normalize_all(data):
    """一次性收敛三个维度。data 可以是 dict 或任何支持 .get 的对象。

    Returns:
        {'purpose': ..., 'flavor': ..., 'meal_time': ...}
    """
    getter = getattr(data, "get", None)
    if getter is None:                      # None / 列表 / 字符串等，一律当"没传"
        getter = lambda _kind: None         # noqa: E731
    return {kind: normalize(kind, getter(kind)) for kind in OPTIONS}


def options_for_template():
    """模板渲染用：把三个维度的 (key, label, hint) 原样递出去。"""
    return {
        "purposes": PURPOSES,
        "flavors": FLAVORS,
        "meal_times": MEAL_TIMES,
        "kinds": KIND_LABELS,
    }


# ---------------------------------------------------------------------------
# 提示词拼接
# ---------------------------------------------------------------------------
SUGAR_HARD_RULES = (
    "【控糖硬性要求】\n"
    "- 优先选用升糖慢的食材（杂粮、豆类、绿叶菜、菌菇、鱼虾、鸡胸肉等）；\n"
    "- 不要用白米饭、白粥、白馒头、白面条、含糖饮料、甜点、蜂蜜糖浆；\n"
    "- 土豆、南瓜、山药、芋头、玉米这类淀粉多的食材按**主食**算，用了就减少主食总量；\n"
    "- 烹饪少油少糖，不用糖调味；勾芡、糖醋、拔丝类做法不要用；\n"
    "- 如果用户指定了高升糖的食材，保留它但**减量**，并在健康提示里说明原因和替代建议。"
)


# 饮食目的 → 知识库条件名（rag.HEALTH_KNOWLEDGE 的键）。
# 用户明确选了「控糖」，就该注入糖尿病那块知识 —— 比靠关键词碰运气可靠：
# 关键词匹配只看报告和输入框里有没有那几个字，"控糖"两个字本身很容易漏。
KNOWLEDGE_CONDITIONS = {
    "control_sugar": "糖尿病",
    "control_fat": "高血脂",
    "control_bp": "高血压",
}


def purpose_knowledge_condition(purpose):
    """目的 key → 需要注入的知识库条件名；没有对应知识就返回 None。"""
    return KNOWLEDGE_CONDITIONS.get(normalize("purpose", purpose))


def build_prompt_clause(purpose=None, flavor=None, meal_time=None):
    """把三个维度拼成提示词里的「本次生成要求」段。

    返回 str；三个维度都取默认值时也会明确写出来 ——
    "没要求"和"要求均衡"对模型是两句不同的话。
    """
    p = normalize("purpose", purpose)
    f = normalize("flavor", flavor)
    m = normalize("meal_time", meal_time)

    lines = ["## 本次生成要求"]
    lines.append("- %s：%s（%s）" % (KIND_LABELS["purpose"], label_of("purpose", p), hint_of("purpose", p)))
    if f == "any":
        lines.append("- %s：不挑，按这道菜最合适的做法来" % KIND_LABELS["flavor"])
    else:
        lines.append("- %s：%s" % (KIND_LABELS["flavor"], label_of("flavor", f)))
    lines.append("- %s：%s，请按这个时段的分量来设计"
                 % (KIND_LABELS["meal_time"], label_of("meal_time", m)))

    text = "\n".join(lines)

    # 控糖不只是"选个标签" —— 追加硬性食材约束
    if p == "control_sugar":
        text += "\n\n" + SUGAR_HARD_RULES

    return text


# ---------------------------------------------------------------------------
# 从健康报告推断「目的」默认值
# ---------------------------------------------------------------------------
# 判定阈值用**糖尿病前期**的界值，不是确诊线 ——
# 本项目的服务对象明确包括"糖尿病前期 / 糖代谢异常"（见 docs/01-产品与方案/功能路线图.md），
# 默认给糖友和糖前期用户都选上「控糖」，他们想改随时能改；
# 反过来（该控糖却默认普通养生）糖友每次都得自己想起来点一下。
#
# 阈值出处：《中国 2 型糖尿病防治指南》—— 空腹血糖受损 ≥6.1 mmol/L；
# 糖耐量减低 2h 血糖 ≥7.8 mmol/L；糖化血红蛋白 ≥5.7% 提示糖代谢异常。
# ⚠️ 这只是给一个**更贴合的默认值**，不是诊断，页面上会写清"可以自己改"。
_SUGAR_THRESHOLDS = (
    ("hba1c", 5.7),                 # 糖化血红蛋白 %
    ("fasting_glucose", 6.1),       # 空腹血糖 mmol/L
    ("postprandial_glucose", 7.8),  # 餐后 2h 血糖 mmol/L
    ("random_glucose", 7.8),        # 随机血糖 mmol/L
)

# 报告里直接写了这些字样，也认为该走控糖
_SUGAR_WORDS = ("糖尿病", "糖耐量", "糖代谢", "胰岛素抵抗", "控糖")


def infer_purpose(report_text):
    """从健康报告推断「目的」默认值。

    Returns:
        'control_sugar' —— 有糖代谢异常迹象（或报告明确写了糖尿病）；
        None            —— 看不出来，保持普通默认。

    ⚠️ 不返回其它目的：血脂/血压那几项要么缺参考区间的口径，
       要么容易误判，宁可不动（用户自己选，一秒钟的事）。
    """
    source = report_text or ""
    if not source.strip():
        return None

    # 先看数值：有血糖类指标且越界
    try:
        from utils.report_parser import parse_health_report
        metrics = (parse_health_report(source) or {}).get("metrics") or {}
    except Exception:
        metrics = {}

    for key, limit in _SUGAR_THRESHOLDS:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and value >= limit:
            return "control_sugar"

    # 再看文字：报告里明写了糖尿病相关诊断
    if any(word in source for word in _SUGAR_WORDS):
        return "control_sugar"

    return None


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------
_passed = 0
_failed = 0


def ok(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  PASS  %s" % name)
    else:
        _failed += 1
        print("  FAIL  %s   %s" % (name, extra))


def _self_test():
    print("=== 1) 选项表 ===")
    ok("三个维度都有选项", set(OPTIONS) == {"purpose", "flavor", "meal_time"})
    ok("目的 4 项（已收敛）", len(PURPOSES) == 4, [k for k, *_ in PURPOSES])
    ok("已下架 增肌/普通养生", all(k not in dict((k, 1) for k, *_ in PURPOSES)
                                 for k in ("muscle", "balanced")))
    ok("口味 6 项", len(FLAVORS) == 6, len(FLAVORS))
    ok("用餐时间 5 项", len(MEAL_TIMES) == 5, len(MEAL_TIMES))
    ok("所有 key 唯一",
       all(len({k for k, *_ in OPTIONS[kind]}) == len(OPTIONS[kind]) for kind in OPTIONS))
    ok("每个选项都有中文标签", all(lab for k in OPTIONS for _k, lab, *_ in OPTIONS[k]))

    print("=== 2) 默认值 ===")
    ok("默认口味=不挑", DEFAULTS["flavor"] == "any")
    ok("默认目的=控糖（红线兜底）", DEFAULTS["purpose"] == "control_sugar")
    ok("默认用餐时间=午餐", DEFAULTS["meal_time"] == "lunch")
    ok("默认值本身必须是合法选项",
       all(DEFAULTS[k] in [x for x, *_ in OPTIONS[k]] for k in DEFAULTS))

    print("=== 3) label_of / normalize ===")
    ok("label_of 认 key", label_of("purpose", "control_sugar") == "控糖")
    ok("label_of 认不出给占位", label_of("purpose", "nope") == "—")
    ok("已下架 key 仍能显示（老数据）", label_of("purpose", "muscle") == "增肌",
       label_of("purpose", "muscle"))
    ok("已下架 key 也能显示（普通养生）", label_of("purpose", "balanced") == "普通养生")
    ok("normalize 认 key", normalize("flavor", "sour_spicy") == "sour_spicy")
    ok("normalize 容忍中文标签", normalize("purpose", "控糖") == "control_sugar")
    ok("normalize 容忍前后空格", normalize("purpose", " 控脂减重 ") == "control_fat")
    ok("normalize 空值回落默认", normalize("meal_time", "") == "lunch")
    ok("normalize None 回落默认", normalize("meal_time", None) == "lunch")
    ok("normalize 非法值回落默认", normalize("purpose", "<script>") == "control_sugar")
    ok("下架的 key 不能再被选中", normalize("purpose", "balanced") == "control_sugar",
       normalize("purpose", "balanced"))
    ok("下架的 key 用中文传也一样", normalize("purpose", "增肌") == "control_sugar")
    ok("normalize 未知维度返回 None", normalize("unknown_kind", "x") is None)
    ok("normalize_all 一次收敛三个", normalize_all({}) == DEFAULTS)
    ok("normalize_all 容忍 None", normalize_all(None) == DEFAULTS)
    ok("normalize_all 中文也被收敛",
       normalize_all({"purpose": "控糖", "flavor": "酸辣", "meal_time": "夜宵"})
       == {"purpose": "control_sugar", "flavor": "sour_spicy", "meal_time": "night"})

    print("=== 4) 提示词拼接 ===")
    clause = build_prompt_clause("control_sugar", "plain", "lunch")
    ok("含饮食目的", "饮食目的：控糖" in clause)
    ok("含口味", "口味偏好：清淡原味" in clause)
    ok("含用餐时间", "用餐时间：午餐" in clause)
    ok("控糖有硬性食材约束", "控糖硬性要求" in clause and "白米饭" in clause)
    plain = build_prompt_clause()
    ok("默认档就是控糖（红线兜底）", "控糖硬性要求" in plain, plain[:60])
    ok("不挑口味有专门说法", "不挑" in plain)
    ok("全部取默认也能拼出完整段", plain.count("- ") >= 3, plain)
    ok("非法值不会让拼接炸掉", isinstance(build_prompt_clause("xx", "yy", "zz"), str))
    ok("非控糖目的不带控糖硬约束",
       "控糖硬性要求" not in build_prompt_clause("gentle", "plain", "lunch"))

    print("=== 5) 按报告推断目的 ===")
    ok("空报告 -> 不推断", infer_purpose("") is None)
    ok("None -> 不推断", infer_purpose(None) is None)
    report = ("某医院检验报告单\n身高 170cm 体重 78kg\n"
              "空腹血糖 8.9 mmol/L\n糖化血红蛋白 7.8 %\n")
    ok("空腹血糖 8.9 -> 控糖", infer_purpose(report) == "control_sugar", infer_purpose(report))
    ok("糖化 7.8 -> 控糖", infer_purpose("糖化血红蛋白 7.8 %") == "control_sugar")
    ok("糖化 5.9（前期）-> 控糖", infer_purpose("糖化血红蛋白 5.9 %") == "control_sugar")
    ok("空腹 6.3（受损）-> 控糖", infer_purpose("空腹血糖 6.3 mmol/L") == "control_sugar")
    ok("空腹 5.2（正常）-> 不推断", infer_purpose("空腹血糖 5.2 mmol/L") is None,
       infer_purpose("空腹血糖 5.2 mmol/L"))
    ok("指标正常但写了糖尿病 -> 控糖", infer_purpose("既往史：2型糖尿病，空腹血糖 5.2 mmol/L")
       == "control_sugar")
    ok("跟糖无关的报告 -> 不推断", infer_purpose("胃镜所见：慢性浅表性胃炎") is None)
    ok("血脂高不推断成控糖", infer_purpose("总胆固醇 6.4 mmol/L 甘油三酯 2.8 mmol/L") is None)
    ok("推断结果一定是合法 key", infer_purpose(report) in [k for k, *_ in PURPOSES])

    print()
    print("=" * 46)
    print("  通过 %d 项，失败 %d 项" % (_passed, _failed))
    print("=" * 46)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_self_test())
