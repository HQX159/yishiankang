# -*- coding: utf-8 -*-
"""控糖膳食计划（多日）的提示词 —— 糖尿病专项

为什么要单独抽一个模块
    改造前这段提示词在 `routes/recipe_routes.py`（Web）和 `routes/api_v1.py`（小程序 / 鸿蒙）
    里各抄了一份，而且两份都还是通用的「健康膳食计划」：只排三餐、没有加餐、不要求碳水克数。
    同一个系统，单道食谱详情页讲升糖档位讲得很细，一转身做多日计划又退回通用营养餐 ——
    这是最容易被人一句问倒的地方（"你这计划凭什么说控糖？"）。

    所以：**模板只有一份，两个入口都调这里**。改口径只改这一个文件。

口径（为什么是这五条）
    1. **五餐制**：早 / 上午加餐 / 午 / 下午加餐 / 晚。加餐不是为了"多吃"，是为了把碳水
       摊开 —— 单餐碳水过高是餐后血糖飙升最直接的原因。这是糖尿病饮食治疗的基本做法。
    2. **每餐标碳水克数**：糖友真正要看的不是"热量多少大卡"，是"这顿吃进去多少碳水"。
       不标克数，"控糖"就只是一句形容词。
    3. **全天 130~180g**：这是给成年 2 型糖友的常见区间（具体应由医师/营养师按体重与
       用药调整），我们给区间而不是一个数，避免看起来像处方。
    4. **主食要粗不要细**：白粥 / 白米饭 / 馒头 / 糯米升糖快，是控糖里最典型的雷。
       但**不是不吃主食** —— 提示词里明确要"每餐有主食且限量"，不能让模型走向另一个极端。
    5. **蛋白质 + 蔬菜每餐都有**：蛋白质和膳食纤维能延缓餐后血糖上升，也让老人吃得饱。

⚠️ 本模块只负责"怎么问模型"，不负责判断生成的计划对不对；解析与展示在
   `routes/recipe_routes.py::parse_meal_plan` 与 `templates/recipe/meal_plan.html`。
⚠️ 所有对外文案都要带"不构成医疗建议"的免责说明。

纯标准库。自测：python -m utils.meal_plan_prompt
"""

# 一天五餐，顺序就是展示顺序。key 用于模板的 CSS 类，label 用于提示词与页面文案。
MEAL_SLOTS = [
    ('breakfast', '早餐'),
    ('snack_am', '上午加餐'),
    ('lunch', '午餐'),
    ('snack_pm', '下午加餐'),
    ('dinner', '晚餐'),
]

# 每餐碳水建议区间（克）。给区间不给单值：同一个人不同天、不同活动量本来就不一样。
CARB_RANGE = {
    'breakfast': (30, 45),
    'snack_am': (10, 15),
    'lunch': (45, 60),
    'snack_pm': (10, 15),
    'dinner': (40, 55),
}

# 全天碳水总量区间。130~180g 是常见的"适度控制"档，不是生酮也不是放开吃。
DAY_CARB_TOTAL = (130, 180)

# 提示词里要模型避开的典型高升糖主食/饮品。写具体名词比写"低 GI"有效得多 ——
# 模型对"低 GI"的理解经常漂移，对"别用白粥"这种具体指令却很听话。
_AVOID_ITEMS = '白粥、白米饭单独成餐、馒头、糯米制品、含糖饮料、果汁、蜂蜜、糕点'

DEFAULT_PREFERENCE = '无特殊偏好'


def _slot_lines(example=True):
    """生成提示词里的格式示例行（五餐各一行）。"""
    lines = []
    for _key, label in MEAL_SLOTS:
        carb = CARB_RANGE[_key]
        mid = (carb[0] + carb[1]) // 2
        if example:
            lines.append('%s：菜品名称 - 一句话描述（碳水约 %dg）' % (label, mid))
        else:
            lines.append('%s：' % label)
    return '\n'.join(lines)


def build_meal_plan_prompt(days, preference='', health_text='', ingredients_text='',
                           risk_note=''):
    """拼出控糖膳食计划的提示词。

    参数都是"已经准备好的文本"，本函数不查库、不调模型。
        days             计划天数（整数）
        preference       用户填的饮食偏好，空则用 DEFAULT_PREFERENCE
        health_text      健康报告摘要（build_report_context 的产物）
        ingredients_text 冰箱食材，逗号分隔
        risk_note        偏高指标提醒（已含【必须提醒】字样），空则不加

    ⚠️ 危险值不该走到这里 —— 调用方必须先过 risk_guard，被拦就不要生成。
    """
    days = max(1, int(days))
    pref = (preference or '').strip() or DEFAULT_PREFERENCE
    health = (health_text or '').strip() or '暂无健康报告'
    ings = (ingredients_text or '').strip() or '未检测到食材'

    lo, hi = DAY_CARB_TOTAL
    b = CARB_RANGE['breakfast']
    lu = CARB_RANGE['lunch']
    di = CARB_RANGE['dinner']
    sn = CARB_RANGE['snack_am']

    return f"""请为一位 2 型糖尿病患者安排 {days} 天的控糖膳食计划。

用户偏好：{pref}
用户健康报告摘要：{health}
冰箱现有食材：{ings}{risk_note}
【必须遵守的控糖原则】
1. 每天固定五餐：早餐、上午加餐、午餐、下午加餐、晚餐。加餐是必须的，
   目的是把碳水分散到全天，避免某一餐碳水过高把餐后血糖顶上去。
2. 每一餐都要给出碳水克数，写在描述最后，统一格式「（碳水约 XXg）」。
   没有克数的计划对糖友没有意义，务必写全。
3. 全天碳水合计控制在 {lo}~{hi}g：早餐 {b[0]}~{b[1]}g、午餐 {lu[0]}~{lu[1]}g、
   晚餐 {di[0]}~{di[1]}g、两次加餐各 {sn[0]}~{sn[1]}g。
4. 主食要有，但要粗不要细：优先糙米、燕麦、荞麦、藜麦、杂豆、玉米、薯类，
   每餐主食控制在生重 50~75g（约小半碗到一小碗）。不要用{_AVOID_ITEMS}。
5. 每一餐都要有蛋白质（鸡蛋、鱼、瘦肉、豆腐或豆制品）和不少于主食量的绿叶蔬菜。
6. 水果只放在加餐吃，每次不超过一个拳头大，优先柚子、苹果、草莓、樱桃这类。
   不用{_AVOID_ITEMS}里的任何一项。
7. 做法用蒸、煮、炖、凉拌，少油少盐，不放糖。

【输出格式】严格按下面的样子写，不要加表格、不要加额外标题、不要写营养成分表：
第 1 天：
{_slot_lines()}

第 2 天：
{_slot_lines()}

……依此类推，共 {days} 天。

总体控糖建议：
（写 2~3 条针对这位用户的饮食建议，围绕怎么吃、什么时候吃、哪一餐要特别注意；
 不要写"多运动""保持好心情"这类与饮食无关的空话）"""


def estimate_max_tokens(days):
    """按天数估一个够用的 max_tokens。

    原来写死 2000：30 天的计划根本写不完，模型会写一半就断，用户看到的是残缺计划。
    这里按"每天 5 餐 × 约 60 token + 总体建议"估，并留余量。
    """
    days = max(1, int(days))
    return min(8000, 500 + days * 340)


def _self_test():
    """自测：提示词该有的都有、不该有的没有。"""
    p = build_meal_plan_prompt(3, preference='清淡少油',
                               health_text='空腹血糖 7.2 mmol/L 偏高',
                               ingredients_text='西兰花, 鸡胸肉, 燕麦',
                               risk_note='\n【必须提醒】空腹血糖偏高')
    ok = 0
    fail = []

    def check(name, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            fail.append(name)

    check('是 2 型糖尿病语境', '2 型糖尿病' in p)
    check('三天', '3 天' in p)
    check('偏好代入', '清淡少油' in p)
    check('报告代入', '空腹血糖 7.2' in p)
    check('食材代入', '西兰花' in p)
    check('风险提醒代入', '【必须提醒】' in p)
    for _k, label in MEAL_SLOTS:
        check('含%s' % label, ('%s：' % label) in p)
    check('要求碳水克数', '碳水约 XXg' in p)
    check('给出全天区间', '130~180g' in p)
    check('明确要主食', '主食要有' in p)
    check('禁白粥', '白粥' in p)
    check('要求蛋白质', '蛋白质' in p)
    check('要求蔬菜', '绿叶蔬菜' in p)
    check('不要空话', '多运动' in p)  # 是"不要写"的语境，出现即可
    check('不含通用老话', '健康膳食计划' not in p)
    check('不含营养均衡兜底', '营养均衡' not in p)

    # 空值兜底
    p2 = build_meal_plan_prompt(1)
    check('空偏好兜底', '无特殊偏好' in p2)
    check('空报告兜底', '暂无健康报告' in p2)
    check('空食材兜底', '未检测到食材' in p2)

    # token 估算
    check('3 天 token 够用', estimate_max_tokens(3) > 1000)
    check('30 天不超上限', estimate_max_tokens(30) <= 8000)
    check('30 天明显大于 3 天', estimate_max_tokens(30) > estimate_max_tokens(3))
    check('天数下限 1', estimate_max_tokens(0) == estimate_max_tokens(1))

    print('通过 %d 项，失败 %d 项' % (ok, len(fail)))
    for f in fail:
        print('  FAIL', f)
    return len(fail) == 0


if __name__ == '__main__':
    raise SystemExit(0 if _self_test() else 1)
