# utils/nutrition_parser.py
"""
把食谱的 `nutrition_info` 自由文本里的营养数字读出来 —— 供「营养摄入」页作图与汇总

为什么要有这个模块
    `Recipe.nutrition_info` 存的是大模型输出的**一段话**，不是数字。
    想画图、想汇总，就得先把数字抠出来。
    本模块**只读文本**：不写库、不改表结构，因此对现有流程零影响。

为什么不再过一次大模型
    同一段文字不该过两次模型 —— 多花钱、结果还不稳定（同样的输入未必同样的输出）。
    实际数据里这份文本格式相当规整（大模型照着 prompt 输出），纯规则足够，还快。

口径（重要）
    - 只取**第一段**营养信息。一份文本里可能混了好几份食谱（模型有时会多给一个备选），
      取主推的那份 —— 也就是文本里第一个出现的。
    - 数值明显不合理的**直接丢弃**：宁可抽不到，也不猜。
      例如「热量 3500 kcal」这种明显串行或胡说的，不采用。
    - 区间值（如「450-500 kcal」）取**中值**作图，同时保留原文，页面会标出来。
    - 单位「千焦 / kJ」会换算成 kcal（1 kJ = 0.239 kcal）。
    - 不做任何"这份够不够健康"的判断 —— 那是营养学口径，本模块只负责读数字。

自测
    python utils/nutrition_parser.py
"""
import re

# 从「热量」所在行往后看这么多字符，在这段窗口里找各项营养素。
# 设窗口是为了不让文本后面的"备选方案""健康提示"里的数字被误当成主数据。
_WINDOW = 260

# 合理范围：超出即视为模型胡说或串行，直接丢弃
_KCAL_MIN, _KCAL_MAX = 1.0, 3000.0
_MACRO_MIN, _MACRO_MAX = 0.0, 500.0

# 三大营养素产热系数（kcal/g）—— 只用于"反算校验"，不做医学判断
_KCAL_PER_G = {'protein': 4.0, 'carb': 4.0, 'fat': 9.0}

# 反算出的热量与文本标注热量相对偏差超过这个比例 → 这份数据自相矛盾
_CONSISTENCY_TOLERANCE = 0.35

# 热量单位 → 换算到 kcal 的系数
_KCAL_UNITS = {
    'kcal': 1.0,
    '千卡': 1.0,
    '大卡': 1.0,
    '千焦': 0.239005736,
    'kj': 0.239005736,
}

# 区间分隔符：半角/全角连字符、波浪号、破折号，以及中文"至"
_RANGE_SEP = r'[\-~～—–至]'
_NUM = r'\d+(?:\.\d+)?'
_APPROX = r'(?:约|大约|≈)?'

_KCAL_RE = re.compile(
    r'(?:总)?热量\s*[:：]?\s*' + _APPROX + r'\s*'
    r'(' + _NUM + r')\s*(?:' + _RANGE_SEP + r'\s*(' + _NUM + r')\s*)?'
    r'(kcal|千卡|大卡|千焦|kJ|KJ|kj)'
)


def _macro_re(names):
    """构造单个营养素的匹配式：名称 → 可选冒号 → 可选"约" → 数字 → 单位（g/克）。"""
    return re.compile(
        r'(?:' + names + r')\s*[:：]?\s*' + _APPROX + r'\s*'
        r'(' + _NUM + r')\s*(?:' + _RANGE_SEP + r'\s*(' + _NUM + r')\s*)?'
        r'(?:g|G|克)'
    )


# 注意：「碳水化合物」必须排在「碳水」前面，否则长者匹配不到
_CARB_RE = _macro_re(r'碳水化合物|碳水')
_PROTEIN_RE = _macro_re(r'蛋白质')
_FAT_RE = _macro_re(r'脂肪')


def _to_float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _fmt_num(value):
    """7.0 → '7'，14.50 → '14.5'"""
    if value is None:
        return ''
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f'{round(number, 2)}'.rstrip('0').rstrip('.')


def _pick(match):
    """
    从一个匹配结果里取值。

    Returns:
        (中值, 是否区间, 区间原文)
        匹配为空或值非法时返回 (None, False, None)
    """
    if not match:
        return None, False, None

    low = _to_float(match.group(1))
    high = _to_float(match.group(2))
    if low is None:
        return None, False, None
    if high is None:
        return low, False, None
    if high < low:                    # 写反了也不报错，纠正过来
        low, high = high, low
    return (low + high) / 2.0, True, f'{_fmt_num(low)}-{_fmt_num(high)}'


def _window_around(text, match):
    """取「热量」所在那一行的开头起、往后 _WINDOW 个字符的片段。"""
    start = text.rfind('\n', 0, match.start()) + 1
    return text[start:start + _WINDOW]


def parse_nutrition(text):
    """
    从一段食谱文本里读出营养数字。

    Args:
        text: `Recipe.nutrition_info` 的内容，可为 None / 空

    Returns:
        dict 或 None（**读不出热量就返回 None**，由调用方决定怎么展示）：
            kcal        -- 热量（kcal，区间取中值）
            protein     -- 蛋白质（g）或 None
            carb        -- 碳水（g）或 None
            fat         -- 脂肪（g）或 None
            kcal_range  -- 热量原文是区间时给出，如 '450-500'，否则 None
            macro_ranges-- {营养素: 区间原文}，只有确实是区间时才有键
            macro_kcal  -- 三大营养素反算出的热量，用于校验
            consistent  -- 反算值与标注值是否相符；缺项时为 None
            complete    -- 四项是否齐全
    """
    raw = text or ''
    if not raw.strip():
        return None

    kcal_match = _KCAL_RE.search(raw)
    if not kcal_match:
        return None

    kcal, kcal_is_range, kcal_range = _pick(kcal_match)
    if kcal is None:
        return None

    unit = (kcal_match.group(3) or 'kcal')
    factor = _KCAL_UNITS.get(unit.lower(), 1.0)
    kcal *= factor
    if not (_KCAL_MIN <= kcal <= _KCAL_MAX):
        return None

    window = _window_around(raw, kcal_match)

    values = {}
    macro_ranges = {}
    for key, pattern in (('protein', _PROTEIN_RE),
                         ('carb', _CARB_RE),
                         ('fat', _FAT_RE)):
        value, is_range, range_text = _pick(pattern.search(window))
        if value is None or not (_MACRO_MIN <= value <= _MACRO_MAX):
            values[key] = None
            continue
        values[key] = round(value, 2)
        if is_range:
            macro_ranges[key] = range_text

    # 反算校验：三大营养素产热之和 vs 文本标注的热量
    macro_kcal = None
    consistent = None
    if all(values[key] is not None for key in _KCAL_PER_G):
        macro_kcal = round(
            sum(values[key] * _KCAL_PER_G[key] for key in _KCAL_PER_G), 1)
        if kcal:
            consistent = abs(macro_kcal - kcal) / kcal <= _CONSISTENCY_TOLERANCE

    return {
        'kcal': round(kcal, 1),
        'protein': values['protein'],
        'carb': values['carb'],
        'fat': values['fat'],
        'kcal_range': kcal_range if kcal_is_range else None,
        'macro_ranges': macro_ranges,
        'macro_kcal': macro_kcal,
        'consistent': consistent,
        'complete': all(values[key] is not None for key in _KCAL_PER_G),
    }


# 营养素展示名（页面与汇总共用一份，避免各处写法不一致）
LABELS = {
    'kcal': '热量',
    'protein': '蛋白质',
    'carb': '碳水',
    'fat': '脂肪',
}

UNITS = {
    'kcal': 'kcal',
    'protein': 'g',
    'carb': 'g',
    'fat': 'g',
}


def summarize(entries):
    """
    汇总一批解析结果（纯算术）。

    Args:
        entries: [parse_nutrition() 的返回值或 None, ...]

    Returns:
        dict:
            total     -- 传入条数
            parsed    -- 读出热量的条数
            complete  -- 四项齐全的条数
            suspicious-- 自相矛盾的条数（反算热量与标注值差太多）
            avg       -- {kcal/protein/carb/fat: 平均值或 None}
            energy_ratio -- 三大营养素供能占比 {protein/carb/fat: 百分比} 或 None
    """
    items = [e for e in (entries or []) if e]
    total = len(entries or [])

    def _avg(key):
        values = [e[key] for e in items if e.get(key) is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    avg = {key: _avg(key) for key in ('kcal', 'protein', 'carb', 'fat')}

    # 供能占比：用各营养素的平均克数换算成热量再求比例
    energy_ratio = None
    if all(avg.get(key) is not None for key in _KCAL_PER_G):
        energy = {key: avg[key] * _KCAL_PER_G[key] for key in _KCAL_PER_G}
        energy_total = sum(energy.values())
        if energy_total > 0:
            energy_ratio = {
                key: round(value / energy_total * 100.0, 1)
                for key, value in energy.items()
            }

    return {
        'total': total,
        'parsed': len(items),
        'complete': sum(1 for e in items if e.get('complete')),
        'suspicious': sum(1 for e in items if e.get('consistent') is False),
        'avg': avg,
        'energy_ratio': energy_ratio,
    }


# ---------------------------------------------------------------------------
# 自测：直接运行本文件即可
# ---------------------------------------------------------------------------
# 下面 6 条是**真实数据**（从 instance/app.db 的 recipe.nutrition_info 里抄的），
# 覆盖了实际出现过的各种写法；其余为构造的边界情况。
_REAL_CASES = [
    ('标准管道格式', '热量约580 kcal | 蛋白质42 g | 碳水52 g | 脂肪22 g\n\n💡 健康提示：此餐富含优质蛋白。',
     {'kcal': 580, 'protein': 42, 'carb': 52, 'fat': 22}),
    ('带"约"与空格', '热量约 320 kcal | 蛋白质 42 g | 碳水 25 g | 脂肪 12 g\n\n💡 健康提示：增肌关键。',
     {'kcal': 320, 'protein': 42, 'carb': 25, 'fat': 12}),
    ('无空格的 g', '热量约485 kcal | 蛋白质约52g | 碳水约38g | 脂肪约12g\n\n💡 健康提示：增肌。',
     {'kcal': 485, 'protein': 52, 'carb': 38, 'fat': 12}),
    ('带小数', '热量约285 kcal | 蛋白质14.5 g | 碳水38 g | 脂肪8.5 g\n\n💡 健康提示：针对慢性胃炎。',
     {'kcal': 285, 'protein': 14.5, 'carb': 38, 'fat': 8.5}),
    ('前面带 markdown 标题', '**营养信息**：热量约195 kcal | 蛋白质22 g | 碳水18 g | 脂肪4.5 g\n**健康提示**：针对胃炎。',
     {'kcal': 195, 'protein': 22, 'carb': 18, 'fat': 4.5}),
    ('项目符号格式（旧 prompt）',
     '**营养信息**：\n- 总热量：约 450-500 kcal\n- 蛋白质：约 40-45 g\n- 碳水：约 40 g\n- 脂肪：约 15 g\n**健康提示**：高蛋白健身餐。',
     {'kcal': 475, 'protein': 42.5, 'carb': 40, 'fat': 15}),
]

_EDGE_CASES = [
    ('文本里没有热量数字 → None', '### 营养说明：\n- 低胆固醇：鸡胸肉和鸡蛋都是低胆固醇食材。\n- 低盐饮食：减少盐的使用。', None),
    ('空文本 → None', '', None),
    ('None → None', None, None),
    ('只有热量没有营养素的 → 部分解析',
     '热量约300 kcal，非常适合当早餐。', {'kcal': 300, 'protein': None, 'carb': None, 'fat': None}),
    ('热量离谱（串行/胡说）→ None', '热量约3500 kcal | 蛋白质99 g', None),
    ('热量为 0 → None', '热量约0 kcal | 蛋白质0 g', None),
    ('千焦换算成 kcal', '热量约1000 千焦 | 蛋白质10 g', {'kcal': 239.0, 'protein': 10, 'carb': None, 'fat': None}),
    ('多份食谱时取第一份',
     '热量约320 kcal | 蛋白质35g | 碳水38g | 脂肪8g\n（若以3个鸡蛋替代鸡胸肉，热量约280 kcal，蛋白质27g，碳水40g，脂肪12g）',
     {'kcal': 320, 'protein': 35, 'carb': 38, 'fat': 8}),
    ('带 markdown 小标题的营养信息',
     '**营养信息**：热量约195 kcal | 蛋白质22 g\n**健康提示**：本餐脂肪不多。',
     {'kcal': 195, 'protein': 22, 'carb': None, 'fat': None}),
]


def _self_test():
    passed = 0
    total = 0

    def check(name, condition, detail=''):
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f'[PASS] {name}')
        else:
            print(f'[FAIL] {name}   {detail}')

    print('=' * 78)
    print('nutrition_parser 自测')
    print('=' * 78)

    for name, text, expected in _REAL_CASES + _EDGE_CASES:
        result = parse_nutrition(text)
        if expected is None:
            check(name, result is None, f'期望 None，实际 {result}')
            continue
        if result is None:
            check(name, False, '期望有结果，实际 None')
            continue
        ok = all(
            (result.get(key) is None and value is None) or
            (result.get(key) is not None and value is not None and abs(result[key] - value) < 0.05)
            for key, value in expected.items()
        )
        check(name, ok, f'期望 {expected}，实际 {{{", ".join(f"{k}={result.get(k)}" for k in expected)}}}')

    # 区间原文被保留
    ranged = parse_nutrition(_REAL_CASES[5][1])
    check('区间热量保留原文', ranged['kcal_range'] == '450-500', str(ranged['kcal_range']))
    check('区间营养素保留原文', ranged['macro_ranges'].get('protein') == '40-45',
          str(ranged['macro_ranges']))

    # 反算校验：真实数据里营养素产热之和应与标注热量基本吻合
    normal = parse_nutrition(_REAL_CASES[0][1])
    check('正常数据反算相符', normal['consistent'] is True,
          f"标注 {normal['kcal']} vs 反算 {normal['macro_kcal']}")
    check('四项齐全标记正确', normal['complete'] is True)

    # 反算校验：明显对不上的要能识别出来
    bogus = parse_nutrition('热量约200 kcal | 蛋白质100 g | 碳水100 g | 脂肪100 g')
    check('自相矛盾的数据被识破', bogus['consistent'] is False,
          f"标注 {bogus['kcal']} vs 反算 {bogus['macro_kcal']}")

    # 区间值取中值
    check('区间取中值', abs(parse_nutrition(_REAL_CASES[5][1])['kcal'] - 475) < 0.05)

    # 反着写的区间也能纠正
    reversed_range = parse_nutrition('热量约500-400 kcal')
    check('写反的区间被纠正', abs(reversed_range['kcal'] - 450) < 0.05, str(reversed_range['kcal']))

    # 全角冒号 / 中文单位
    check('全角冒号可用', parse_nutrition('热量：约 300 千卡')['kcal'] == 300)
    check('大卡单位可用', parse_nutrition('热量约 300 大卡')['kcal'] == 300)

    # 不误把「脂肪酸」当成脂肪
    no_fat = parse_nutrition('热量约300 kcal | 蛋白质20 g\n健康提示：富含单不饱和脂肪酸，有益心血管。')
    check('"脂肪酸"不被当成脂肪值', no_fat['fat'] is None, str(no_fat['fat']))

    # 汇总
    entries = [parse_nutrition(t) for _, t, _ in _REAL_CASES]
    summary = summarize(entries)
    check('汇总：条数正确', summary['total'] == 6 and summary['parsed'] == 6, str(summary['total']))
    check('汇总：平均热量为 6 条的中值',
          abs(summary['avg']['kcal'] - round((580 + 320 + 485 + 285 + 195 + 475) / 6, 2)) < 0.05,
          str(summary['avg']['kcal']))
    ratio = summary['energy_ratio']
    check('汇总：供能占比合计约 100%',
          ratio is not None and abs(sum(ratio.values()) - 100) < 0.3, str(ratio))
    # 这 6 条的平均值是：蛋白质 35.8g / 碳水 35.2g / 脂肪 12.3g
    # → 供能占比约 蛋白 36.3% / 碳水 35.6% / 脂肪 28.1%（脂肪克数少但产热系数高，所以降不下来）
    check('汇总：脂肪供能占比最低',
          ratio and ratio['fat'] < ratio['carb'] and ratio['fat'] < ratio['protein'], str(ratio))
    check('汇总：三项占比都在 0~100 之间',
          ratio and all(0 < v < 100 for v in ratio.values()), str(ratio))

    # 汇总：空输入不炸
    empty = summarize([])
    check('空列表汇总不报错', empty['total'] == 0 and empty['avg']['kcal'] is None)
    check('全 None 汇总不报错', summarize([None, None])['parsed'] == 0)

    # 汇总：只有部分营养素时不给供能占比
    partial = summarize([parse_nutrition('热量约300 kcal | 蛋白质20 g')])
    check('缺项时不算供能占比', partial['energy_ratio'] is None)

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    import sys
    sys.exit(0 if _self_test() else 1)
