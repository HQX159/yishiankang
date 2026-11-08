"""食品科普目录。份量为编辑整理的单次示例，不是个体可食上限。

碳水按所列来源的每 100g 数据（或原文份量）换算。无可靠数据留空。

升糖信息分三层，越往下越不确定，界面上必须能区分：

1. GI 数值（来自 WS/T 601—2018 附录A，`FOODS` 里的 exact/near 原值）
2. 只有快慢档位（同一张表给的 low/mid/high，没有具体数值）
3. 都没有 → 明确写「暂无数据」，绝不拿相似食物顶替

**用户真正要的答案是 GL，不是 GI。** GL = GI × 这一份的碳水 ÷ 100，
它才回答「我吃这一份会不会飙」；只给 GI 会把西瓜（GI 72、一份 150g 只 GL 8.2）
冤枉成不能吃，把馒头（GI 88、一份 50g GL 20.7）放过一半。
"""
import math
import json
import re
from pathlib import Path
from utils.diabetes_advice import FOODS, GI_SOURCE_SHORT, GI_BOUNDARY

# 分档界线。GI 与 GI_BOUNDARY 文案必须一致；GL 用国际通用口径。
GI_LOW_MAX = 55      # ＜55 慢
GI_MID_MAX = 70      # 55～70 中，＞70 快
GL_LOW_MAX = 10      # ≤10 低
GL_MID_MAX = 19      # 11～19 中，≥20 高

RISE_LABELS = {'slow': '升糖慢', 'mid': '升糖中等', 'fast': '升糖快'}
RISE_UNKNOWN = '升糖快慢暂无数据'
GI_LEVEL_TO_RISE = {'low': 'slow', 'mid': 'mid', 'high': 'fast'}

SOURCES = [
    {'title': 'ADA：糖尿病与完整水果', 'url': 'https://diabetes.org/food-nutrition/reading-food-labels/fruit'},
    {'title': 'NHS：碳水参考表（2026，水果及零食）', 'url': 'https://sfh-tr.nhs.uk/media/buuhrpb3/pil202601-03-crt-carbohydrate-reference-tables.pdf'},
    {'title': 'NHS：常见食品碳水含量', 'url': 'https://www.southtees.nhs.uk/resources/carbohydrate-content-of-foods/'},
    {'title': 'NHS：糖尿病加餐选择', 'url': 'https://www.uhsussex.nhs.uk/resources/diabetes-and-snacks/'},
    {'title': 'CDC：想吃甜食时如何控制份量', 'url': 'https://www.cdc.gov/diabetes/healthy-eating/people-with-diabetes-dessert.html'},
    {'title': 'ADA：看标签中的总碳水和食用份量', 'url': 'https://diabetes.org/food-nutrition/reading-food-labels/making-sense-food-labels'},
    # 6：中式食品的主力来源。中国疾控中心营养与健康所的《中国食物成分表》在线查询，
    #    每 100g 的能量/蛋白/脂肪/碳水均为官方测定值。抓取脚本见 .workbuddy/scripts/probe_cn_food_db.py
    {'title': '中国疾病预防控制中心营养与健康所：中国食物成分表（食物营养成分查询平台）',
     'url': 'https://nlc.chinanutri.cn/fq/'},
    # 7：辣条不在中国食物成分表里，此来源是该文逐项引用的一款辣条包装营养成分表原值
    {'title': '云南省疾病预防控制中心：怎么看食品包装营养成分表（辣条实例）',
     'url': 'https://www.yncdc.cn/jkzt/jkzs/2026/17680.shtml'},
    # 8：GL 的定义、「1 个 GL ≈ 1 克葡萄糖当量」以及低/中/高分档都出自这一篇。
    #    页面上凡是写「1 个 GL 约等于 1 克葡萄糖」的地方，依据就是它。
    {'title': '悉尼大学人类营养研究所：血糖负荷与糖尿病（GL 的定义与分档）',
     'url': 'https://glycemicindex.com/2021/11/glycemic-load-and-diabetes/'},
]
NOTICE = ('这是一般控糖科普，参考量不是个人安全上限，也不是每种都能额外吃一份。'
          '加餐应计入全天饮食；是否需要加餐、具体份量需结合血糖、用药及营养师计划。'
          '孕期、儿童、肾病或低血糖处理不适用本页通用份量。')
CATEGORIES = {'all': '全部', 'fruit': '水果', 'snack': '零食', 'drink': '饮品', 'staple': '主食'}
FILTERS = {'all': '全部', 'recommend': '优先选择', 'limit': '注意份量', 'occasional': '少选替换', 'high': '升糖较快', 'reference': '营养资料'}
TIPS = [
    {'title': '升糖快慢，也要看份量', 'text': 'GI 表示等量碳水下的升糖速度；实际吃多少、成熟度和烹调方式也影响餐后血糖。高 GI 不等于绝对不能吃。'},
    # 用户 2026-11-06 指出「GI 不是 GL」—— 这两个数在页面上必须各有名字，
    # 否则同一个食物的「升糖快」和「负荷低」会被读成自相矛盾。
    {'title': 'GI 和 GL 不是一回事', 'text': 'GI 只比「同样多碳水」时谁升得快，不管您吃了多少；GL 才管「这一份」的实际负担（GL = GI × 这一份的碳水 ÷ 100）。西瓜 GI 高但一小份 GL 低；馒头 GI 高、一份 GL 也高。GL 本身不标单位，1 个 GL 约等于 1 克葡萄糖的升糖效果。'},
    {'title': '吃完整水果，少喝果汁', 'text': '水果不必一概忌口。分次吃完整水果，避免一大盘吃不停；果汁、果干和糖水罐头要单独看待。'},
    {'title': '无糖、咸味也可能含碳水', 'text': '无糖饼干仍可能含淀粉，薯片咸也不代表低碳水。包装食品优先看营养表的碳水化合物和实际吃下的净重。'},
]


def _norm_name(name):
    """去掉括号里的补充说明和空格，用于严格比对。

    「苹果（去皮生食）」→「苹果」、「AD 钙奶」→「AD钙奶」。
    只做这一步规整，**不做任何包含/模糊匹配**（见 _gi_evidence）。
    """
    text = re.sub(r'[（(].*?[)）]', '', str(name or ''))
    return re.sub(r'[\s·]', '', text).strip()


def _gi_evidence(name, aliases=()):
    """按「整名或别名完全相等」查权威 GI 表，返回 (gi, 档位, 命中的键, 依据类型)。

    只认完全相等，绝不按包含关系匹配。实测反例（全都是真错配）：
        黑芝麻汤圆 → 芝麻    汤圆是糯米加糖，跟芝麻是两回事
        牛奶巧克力 → 牛奶    一个高糖一个低升糖
        菠萝蜜     → 菠萝    两种水果
        荔枝干     → 荔枝    脱水后同一份量差好几倍
        虾片       → 虾      虾片是淀粉膨化
        含糖汽水   → 糖      匹配到单字泛词
    宁可显示「暂无数据」，也不能张冠李戴 —— 这是本模块的底线。
    """
    for candidate in (name, *aliases):
        key = _norm_name(candidate)
        if key in FOODS:
            level, _category, gi, kind = FOODS[key]
            return (gi if kind in ('exact', 'near') else None), level, key, (kind or '')
    return None, None, '', ''


def _rise(gi, level):
    """升糖快慢档位。有数值按数值判，没数值才退到档位，都没有就是「暂无」。"""
    if gi is not None:
        if gi < GI_LOW_MAX:
            return 'slow', RISE_LABELS['slow']
        if gi <= GI_MID_MAX:
            return 'mid', RISE_LABELS['mid']
        return 'fast', RISE_LABELS['fast']
    rise_key = GI_LEVEL_TO_RISE.get(level or '')
    if rise_key:
        return rise_key, RISE_LABELS[rise_key]
    return 'none', RISE_UNKNOWN


def glycemic_load(gi, carb_per_100, amount):
    """血糖负荷 GL = GI × 这一份的碳水 ÷ 100，返回 (数值, 档位文字)。

    这是「我吃这一份会不会飙」的答案。缺任何一项就不算，返回 (None, '')。
    """
    if gi is None or carb_per_100 is None or amount is None:
        return None, ''
    try:
        carb = float(carb_per_100) * float(amount) / 100.0
        value = round(float(gi) * carb / 100.0, 1)
    except (TypeError, ValueError):
        return None, ''
    if not math.isfinite(value):
        return None, ''
    if value <= GL_LOW_MAX:
        return value, '低'
    if value <= GL_MID_MAX:
        return value, '中'
    return value, '高'


def _rise_hint(rise_key, gl_label):
    """档位和负荷打架时点一句，免得老人被单一指标误导（西瓜就是典型）。"""
    if rise_key == 'fast' and gl_label == '低':
        return '这个食物本身升糖快，但这一份量小，实际负担不高；别加量。'
    if rise_key == 'slow' and gl_label == '高':
        return '这个食物升糖慢，但这一份量大，总量还是要算进去。'
    return ''


def _food(key, name, category, choice, portion, measure, carb, note, aliases=(), source=1, gi_name=None, fast=False, unit='g'):
    gi, level, gi_key, kind = _gi_evidence(gi_name or name, aliases)
    rise_key, rise_label = _rise(gi, level)
    gi_label = ('低 GI' if gi < 55 else '中 GI' if gi <= 70 else '高 GI') if gi is not None else 'GI 暂无可靠数值'
    food = dict(id=key, name=name, category=category, category_label=CATEGORIES[category],
                choice=choice, choice_label=FILTERS[choice], portion=portion, measure=measure,
                unit=unit, unit_label='毫升' if unit == 'ml' else '克', carb_per_100=carb,
                note=note, aliases=list(aliases), source=SOURCES[source], gi=gi,
                gi_label=gi_label, gi_source=GI_SOURCE_SHORT if gi is not None else '',
                gi_reference=kind == 'near', gi_key=gi_key, gi_level=level,
                rise_key=rise_key, rise_label=rise_label,
                rise_basis='gi' if gi is not None else ('level' if level else 'none'),
                fast=fast or rise_key == 'fast')
    food['gl_ref'], food['gl_label'] = glycemic_load(gi, carb, portion)
    food['rise_hint'] = _rise_hint(rise_key, food['gl_label'])
    food['craving'] = craving_guide(food)
    return food


def craving_guide(food, carb=None):
    if food.get('nutrition_only'):
        return {'amount': None, 'measure': '按可食部称重', 'carb': None,
                'message': '已收录营养资料，可填写食用量换算；个人参考份量尚未整理。',
                'plan': '计入原有饮食计划。', 'boundary': '营养数值不代表个人可食上限。'}
    rate = food['carb_per_100'] if carb is None else carb
    needs_label = food['id'] in ('milktea', 'soda', 'sugarfree') and rate is None
    return {
        'amount': None if needs_label else food['portion'],
        'measure': food['measure'],
        'carb': round(rate * food['portion'] / 100, 1) if rate is not None else None,
        'message': ('先查这款食品的营养标签，才能给出有依据的份量换算。'
                    if needs_label else '偶尔想吃时，可以先按下面的示例分出一小份，吃完收起，不直接拿整袋或整盘吃。'),
        'plan': '把这份计入原有饮食计划，避免再叠加其他零食；不要为了吃它跳过正餐或自行加减药。',
        'boundary': '这是解馋的小份示例，不是“吃到这里就一定安全”的上限。若血糖高于个人目标、身体不适，或已有特殊饮食限制，先按医嘱处理。',
    }


FOOD_GUIDE = [
    _food('apple', '苹果', 'fruit', 'recommend', 100, '切块后称重', 10, '保留可食果皮，不用苹果汁代替。', ('红富士',)),
    _food('strawberry', '草莓', 'fruit', 'recommend', 80, '约 5 颗，大小不一请称重', 6, '选新鲜原果，不蘸糖或炼乳。'),
    _food('blueberry', '蓝莓', 'fruit', 'recommend', 80, '一小把', 9, '冻鲜原果也可；蓝莓酱需另看配料。'),
    _food('pear', '梨', 'fruit', 'recommend', 100, '切块后称重', 11, '分次食用，不把整大个梨当作一小份。'),
    _food('peach', '桃', 'fruit', 'recommend', 100, '切块后称重', 8, '新鲜桃与糖水黄桃罐头不是同一种选择。', ('桃子', '水蜜桃')),
    _food('plum', '李子', 'fruit', 'recommend', 80, '小份称重', 9, '选原果，避免腌渍加糖产品。'),
    _food('papaya', '木瓜', 'fruit', 'recommend', 80, '小份切块', 9, '不额外加糖或椰奶。'),
    _food('raspberry', '覆盆子', 'fruit', 'recommend', 80, '一小把', 5, '原果优于果酱。', ('树莓',)),
    _food('banana', '香蕉', 'fruit', 'limit', 60, '去皮后称重，约小半根至一小根', 20, '熟透程度影响升糖；不要连续吃多根。'),
    _food('grape', '葡萄', 'fruit', 'limit', 80, '无籽葡萄一小把', 15, 'GI 不高也需要控制总量，先装小碟再吃。'),
    _food('mango', '芒果', 'fruit', 'limit', 80, '去皮去核后称重', 14, '不加糖，不做芒果奶昔。'),
    _food('pineapple', '菠萝', 'fruit', 'limit', 80, '约两小片', 10, '新鲜果块优于糖水罐头。', ('凤梨',)),
    _food('persimmon', '柿子', 'fruit', 'limit', 50, '一小份切块', 20, '碳水相对集中；柿饼需另算。'),
    _food('pomegranate', '石榴', 'fruit', 'limit', 40, '一小把果粒', 16, '吃果粒，不用石榴汁替代。'),
    _food('watermelon', '西瓜', 'fruit', 'limit', 150, '去皮后称重的一小碟', None, 'GI 较高不代表完全禁吃；小份吃，不抱着半个西瓜挖着吃。'),
    _food('lychee', '荔枝', 'fruit', 'limit', 60, '去皮去核后的一小份', None, '先分装小份，不因颗粒小而连续吃。'),
    _food('longan', '龙眼', 'fruit', 'limit', 60, '去皮去核后的一小份', None, '鲜龙眼与干桂圆分开计算。', ('鲜桂圆',)),
    _food('raisin', '葡萄干', 'snack', 'limit', 15, '一小撮', 73, '脱水后碳水集中，不能按鲜葡萄的份量吃。'),
    _food('yogurt', '原味无添加糖酸奶', 'snack', 'recommend', 125, '一小杯', 5, '核对标签确认无添加糖；仍含乳糖，风味酸奶不能套用。', ('无糖酸奶',), source=2),
    _food('nuts', '原味无盐坚果', 'snack', 'recommend', 15, '一小把，去壳可食部', None, '能量密度高，不能无限量；避开糖衣、蜜烤及过敏原。', ('坚果',), source=3),
    _food('egg', '水煮蛋', 'snack', 'recommend', 50, '约 1 个，去壳', None, '可作为加餐蛋白来源，结合当天蛋类摄入安排。', ('鸡蛋',), source=3),
    _food('cracker', '苏打饼干', 'snack', 'limit', 16, '约 2 片，以包装净重为准', 62.5, '咸味不等于低碳水；此值为原味薄脆饼干同类参考。', ('梳打饼干',), source=2),
    _food('digestive', '消化饼干', 'snack', 'occasional', 15, '约 1 片', 66.7, '全麦、粗粮宣传不能替代营养表。', source=2),
    _food('sugarfree', '无糖饼干', 'snack', 'limit', 15, '称重后看包装标签', None, '无添加蔗糖仍可能有较多淀粉，不套用普通饼干的精确值。'),
    _food('crisps', '薯片', 'snack', 'occasional', 20, '分装一小碟', 42.9, '通常油盐较多，建议换成无盐坚果；品牌差异较大。', ('薯条片',), source=2),
    _food('popcorn', '原味爆米花', 'snack', 'limit', 20, '一小杯', 50, '不含糖衣的参考；焦糖爆米花不能套用。', source=2),
    _food('sweetpopcorn', '甜爆米花', 'snack', 'occasional', 15, '分装一小份', 80, '糖衣会增加碳水，优先换原味少油款。', ('焦糖爆米花',), source=2),
    _food('chocolate', '牛奶巧克力', 'snack', 'occasional', 10, '一小块', 60, '糖和脂肪都需注意；低 GI 不代表适合多吃。', source=2),
    _food('cake', '海绵蛋糕', 'snack', 'occasional', 20, '很小一块', 55.6, '糕点通常含精制面粉和糖，不宜作为日常加餐。', ('蛋糕',), source=2),
    _food('icecream', '香草冰淇淋', 'snack', 'occasional', 50, '一小球', 20, '不同品牌糖脂含量差异大，以标签为准。', ('冰淇淋', '雪糕'), source=2),
    # ---- 2026-11-06 补库：中式加工零食（中国食物成分表）----
    # 上一轮 64 条照 USDA 补，补进来的多是西式食品，中式零食与饮品几乎空白：
    # 14 个常见搜索词 13 个全空，连「馒头」都搜不到。以下是按「糖友实际会问」补的。
    _food('latiao', '辣条（调味面制品）', 'snack', 'occasional', 20, '小半包，以包装净重为准', 45.5,
          '主料是小麦粉加油盐。「辣条」品牌差异极大，另有样本达 70g/100g，务必以手里这包的营养表为准。',
          ('辣片', '大面筋', '素牛筋'), source=7),
    _food('instantnoodle', '方便面（油炸，含调料）', 'snack', 'occasional', 50, '约半块面饼，调料只放半包', 61.6,
          '半块面饼已接近一碗米饭的碳水；少喝汤。别把一包当一餐、更不要两包叠着吃。',
          ('泡面', '速食面'), source=6),
    _food('ham', '火腿肠', 'snack', 'limit', 40, '约 1 根，去包装称重', 15.6,
          '淀粉含量随等级不同（普通级更高），钠也偏高；它不是肉，别当蛋白质主力。', ('火腿',), source=6),
    _food('beefjerky', '牛肉干', 'snack', 'limit', 25, '约 3～4 片', 1.9,
          '碳水很低，但蛋白质、脂肪和钠都高；一小份就够，别整包吃完。', ('风干牛肉',), source=6),
    _food('driedtofu', '豆腐干', 'snack', 'recommend', 50, '约 2～3 块', 11.5,
          '碳水比肉低、蛋白较好，适合当加餐。卤制款钠偏高，别就着当零食无限吃。',
          ('豆干', '香干', '白干'), source=6),
    _food('suji', '素鸡', 'snack', 'recommend', 50, '约半段', 4.2,
          '豆制品，碳水低。油炸做法会额外加不少脂肪。', ('素肠',), source=6),
    _food('jelly', '果冻（水果冻）', 'snack', 'occasional', 100, '约 2 小杯', 17.2,
          '主要成分是糖和水，不顶饱，容易连着吃好几杯。', ('果冻',), source=6),
    _food('shaqima', '沙琪玛', 'snack', 'occasional', 20, '1 小块', 55.1,
          '面粉加糖加油，能量密度很高，一小块就到量。', ('萨其马', '沙其马'), source=6),
    _food('mahua', '麻花', 'snack', 'occasional', 20, '半根', 53.4, '油炸面食，碳水和油都高。', source=6),
    _food('youtiao', '油条', 'snack', 'occasional', 30, '半根', 51.0,
          '油炸面食。常和粥、豆浆一起吃，注意别把两份主食叠在一起。', ('炸油条',), source=6),
    _food('jianbing', '煎饼', 'snack', 'occasional', 40, '约 1/4 张', 70.0,
          '以面粉为主，摊得薄但面积大；加薄脆和酱后能量更高。', source=6),
    _food('springroll', '春卷', 'snack', 'occasional', 40, '约 1 个', 34.8, '外皮是面粉，油炸款脂肪高。', ('炸春卷',), source=6),
    _food('shaobing', '烧饼（加糖）', 'snack', 'occasional', 50, '半个', 62.7,
          '碳水集中，本质是主食不是零食；吃了要相应减少正餐主食。', ('酥烧饼',), source=6),
    _food('guoba', '锅巴', 'snack', 'occasional', 20, '一小把', 59.2, '油炸米制品，碳水和油都高。', source=6),
    _food('shrimpcracker', '虾片', 'snack', 'occasional', 15, '约 5 片', 59.5,
          '淀粉制品、不是真虾，油炸后脂肪很高。', ('龙虾片', '虾条'), source=6),
    _food('ricecracker', '雪米饼', 'snack', 'occasional', 20, '约 2 片', 73.5, '膨化米制品，往往还带糖和油。', ('米饼',), source=6),
    _food('eggroll', '蛋酥卷', 'snack', 'occasional', 20, '约 2 卷', 53.7, '面粉加蛋加糖，酥脆容易一次吃多。', ('蛋卷',), source=6),
    _food('chocopie', '巧克力派', 'snack', 'occasional', 25, '1 个', 65.7,
          '蛋糕体加糖加油，一个就接近一份主食的碳水。', source=6),
    _food('sandwichcookie', '夹心饼干', 'snack', 'occasional', 20, '约 3 片', 75.3,
          '夹心部分主要是糖和油，碳水很集中。', ('三明治饼干',), source=6),
    _food('buttercookie', '曲奇饼（加奶油）', 'snack', 'occasional', 20, '约 2 块', 63.9, '黄油和糖比例高，两三块就到量。', ('曲奇', '奶油曲奇'), source=6),
    _food('mooncake', '月饼（豆沙）', 'snack', 'occasional', 40, '1/4 个', 65.6,
          '月饼能量密度极高，1/4 个已经相当于一份主食。', ('月饼', '豆沙月饼'), source=6),
    _food('tangyuan', '黑芝麻汤圆', 'snack', 'occasional', 40, '约 3 个', 44.2,
          '糯米加糖馅，升糖快且黏；3 个为一份，别连汤一起喝。', ('汤圆', '元宵'), source=6),
    _food('milkcandy', '奶糖', 'snack', 'occasional', 10, '约 2 粒', 84.5, '几乎全是糖，两三粒就到一份碳水量。', ('大白兔奶糖',), source=6),
    _food('gummy', '水果味软糖', 'snack', 'occasional', 10, '约 3 粒', 85.6, '糖加胶体，基本是纯糖。', ('软糖', '水果糖'), source=6),
    _food('hawthornstick', '山楂条', 'snack', 'occasional', 15, '约 3 条', 74.6,
          '山楂本身不算甜，但做成条要加大量糖。', ('山楂糕', '金糕条'), source=6),
    _food('hawthornroll', '山楂果丹皮', 'snack', 'occasional', 15, '1 卷', 80.0,
          '含糖很高。「山楂助消化」不等于可以多吃。', ('果丹皮', '山楂卷'), source=6),
    _food('preservedplum', '九制梅肉', 'snack', 'occasional', 15, '约 3 粒', 70.8,
          '蜜饯类是糖渍，咸甜口同样要算碳水；钠也偏高。', ('话梅', '梅肉', '蜜饯'), source=6),
    _food('jiangmitiao', '江米条', 'snack', 'occasional', 20, '约 3 根', 78.1, '油炸糯米条，裹糖后碳水很高。', ('糯米条',), source=6),
    _food('taosu', '桃酥', 'snack', 'occasional', 25, '1 块', 65.1, '油起酥，碳水和脂肪都高。', ('核桃酥',), source=6),
    _food('mungbeancake', '绿豆糕', 'snack', 'occasional', 25, '1 块', 73.4,
          '含糖和油，不会因为是「绿豆」就适合多吃。', ('绿豆饼',), source=6),
    _food('milk', '纯牛奶', 'drink', 'recommend', 200, '一小杯', 5, '含天然乳糖；不能当水不限量喝。', ('牛奶',), source=2, unit='ml'),
    _food('juice', '橙汁', 'drink', 'occasional', 100, '小半杯，仅作换算示例', 10, '即使无添加糖也含碳水，日常优先完整水果和白水。', ('纯橙汁',), source=2, unit='ml'),
    _food('milktea', '奶茶', 'drink', 'occasional', 100, '小半杯，仅作换算示例', None, '糖度、杯量和配料差异很大，珍珠也含淀粉；优先无糖茶。', unit='ml'),
    _food('soda', '含糖汽水', 'drink', 'occasional', 100, '小半杯，仅作换算示例', 10.7,
          '液体糖容易快速摄入，日常建议用白水替换。此值按可口可乐类（10.7g/100ml）取，无糖款另看标签。',
          ('可乐', '汽水'), source=6, fast=True, unit='ml'),
    # ---- 2026-11-06 补库：饮品（中国食物成分表）----
    # 原来饮品只有 4 条（纯牛奶/橙汁/奶茶/含糖汽水），豆浆、乳酸菌饮料、啤酒全都搜不到。
    _food('soymilk', '豆浆（无糖）', 'drink', 'recommend', 200, '一小杯', 1.1,
          '选不加糖的。市售甜豆浆要另算糖，不能套用本值。', ('豆浆', '无糖豆浆'), source=6, unit='ml'),
    _food('soydrink', '豆奶', 'drink', 'recommend', 200, '一杯', 1.8,
          '植物蛋白饮料，碳水低；注意配料表有没有加糖。', ('豆乳',), source=6, unit='ml'),
    _food('lactodrink', '酸乳饮料（乳酸菌饮料）', 'drink', 'occasional', 100, '小瓶，按净含量换算', 12.1,
          '含糖乳饮料，蛋白质很少、糖不少；不能代替牛奶或酸奶。', ('乳酸菌饮料', '酸乳饮料'), source=6, unit='ml'),
    _food('adcalcium', 'AD钙奶', 'drink', 'occasional', 100, '小瓶，按净含量换算', 12.3,
          '属于含乳饮料，含糖量高于纯牛奶。', ('AD 钙奶', '钙奶'), source=6, unit='ml'),
    _food('walnutmilk', '核桃乳', 'drink', 'limit', 200, '一罐', 5.5,
          '植物蛋白饮料，通常加了糖；不能代替吃坚果或喝牛奶。', ('核桃奶', '核桃露'), source=6, unit='ml'),
    _food('almonddrink', '杏仁露', 'drink', 'limit', 200, '一罐', 6.8, '市售多为含糖款，按标签核对。', ('杏仁奶',), source=6, unit='ml'),
    _food('beer', '啤酒', 'drink', 'occasional', 330, '一听', 4.3,
          '酒精本身会影响血糖调节，空腹饮酒有低血糖风险；这里的碳水只是其中一部分。', ('扎啤',), source=6, unit='ml'),
    _food('ricewine', '黄酒', 'drink', 'occasional', 100, '一小杯', 8.6,
          '黄酒常额外加糖，碳水高于白酒；同样注意空腹饮酒风险。', ('花雕酒',), source=6, unit='ml'),
    _food('coconutdrink', '椰子汁饮料', 'drink', 'limit', 200, '一杯', 6.6,
          '椰子汁饮料不是椰子水，通常加糖；配料表第一位是水就说明浓度低。', ('椰子汁', '椰汁'), source=6, unit='ml'),
    _food('rice', '白米饭', 'staple', 'limit', 50, '熟重，少量作换算', 33.3, '计入正餐主食，不宜吃完正餐又额外加一份。', ('米饭',), source=2),
    _food('toast', '白面包', 'staple', 'occasional', 36, '约一片', 41.7, '可用配料清楚的全谷物食物替换一部分。', ('白吐司',), source=2),
    # 中式主食：原来 16 条主食里一半是西式的（藜麦、布格麦、库斯库斯、意面），
    # 连「馒头」「包子」都搜不到 —— 这是最常被问的两样。
    _food('steamedbun', '馒头', 'staple', 'limit', 50, '半个，熟重', 47.0,
          '本质是主食，吃了要相应减少其他主食，不能当加餐白吃。', ('白馒头', '馍'), source=6),
    _food('baozi', '包子（猪肉馅）', 'staple', 'limit', 100, '1 个，熟重', 28.6,
          '皮是精制面粉，馅里油盐不少；一个就接近一碗饭的碳水。', ('肉包子', '肉包'), source=6),
    # 罐装八宝粥按净含量标「克」，所以归主食、不用毫升，避免和纯液体饮品混在一张表里
    _food('eighttreasure', '八宝粥（无糖）', 'staple', 'limit', 250, '一罐', 9.3,
          '无糖款仍有米和豆的淀粉，一罐约相当于半碗饭。', ('八宝粥', '无糖八宝粥'), source=6),
    _food('sweetpotato', '红薯', 'staple', 'limit', 55, '熟重小份', 27.3, '用来替换部分主食，不能当蔬菜随意加吃；GI 随做法变化。', ('地瓜', '甘薯'), source=2),
    _food('oats', '原味燕麦片', 'staple', 'recommend', 25, '干重，煮后食用', 70, '选不加糖的原味压片，速溶甜麦片另看标签。', ('燕麦片',), source=2),
]


def _load_usda_snapshot():
    """加载随代码发布的离线营养快照；查询不依赖外部网络或密钥。"""
    path = Path(__file__).resolve().parents[1] / 'data/food_usda_snapshot.json'
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    if snapshot.get('schema_version') != 1:
        raise ValueError('食品营养快照版本不受支持')
    existing = {food['id']: food for food in FOOD_GUIDE}
    seen = set()
    for row in snapshot['foods']:
        key = row['id']
        rate = row['nutrition']['carbohydrate_g']
        if key in seen or row['basis_unit'] != 'g' or row['basis_amount'] != 100:
            raise ValueError('食品营养快照存在重复 ID 或计量基准错误')
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 <= rate <= 100:
            raise ValueError('食品营养快照碳水字段无效')
        seen.add(key)
        if row['operation'] == 'enrich':
            food = existing[key]
            if food['carb_per_100'] is not None or food['unit'] != 'g':
                raise ValueError('禁止静默覆盖已有营养值或不同单位')
        elif row['operation'] == 'append' and key not in existing:
            food = _food(key, row['name'], row['category'], 'reference', 100,
                         '100 克仅为营养换算基准，不是建议份量', rate,
                         '按所列生熟状态和可食部称重；品牌、配方不同需看实际标签。',
                         row['aliases'])
            # 只标「营养资料，参考份量未整理」；GI 由 _food() 按严格匹配带出来，
            # 不能再在这里抹成 None —— 否则「柚子/橘子/樱桃」明明有权威 GI，
            # 页面上却显示「暂无数据」。
            food.update(nutrition_only=True)
            FOOD_GUIDE.append(food)
            existing[key] = food
        else:
            raise ValueError('食品营养快照操作或 ID 无效')
        food.update(carb_per_100=rate, nutrition=row['nutrition'],
                    nutrition_provenance=row['source'],
                    carbohydrate_definition=snapshot['carbohydrate_definition'],
                    source_description=row['expected_description'],
                    verified_on=snapshot['verified_on'],
                    source={'title': 'USDA FoodData Central · SR Legacy（2018）', 'url': row['source']['url']})
        # 快照可能刚好把原来空着的碳水补上，血糖负荷要跟着重算，否则一直挂着 None。
        food['gl_ref'], food['gl_label'] = glycemic_load(food['gi'], rate, food['portion'])
        food['rise_hint'] = _rise_hint(food['rise_key'], food['gl_label'])
        food['craving'] = craving_guide(food)
    SOURCES.append({'title': 'USDA FoodData Central：食品成分公开数据（SR Legacy 2018）',
                    'url': 'https://fdc.nal.usda.gov/download-datasets/'})


_load_usda_snapshot()


def catalog(query='', category='all', choice='all'):
    query = query.strip().casefold()
    if len(query) > 60:
        raise ValueError('搜索词请控制在 60 个字符以内')
    if category not in CATEGORIES or choice not in FILTERS:
        raise ValueError('请选择有效的食品分类或筛选条件')
    # 只做目录候选搜索，不把「苹果汁」自动诊断成「苹果」。
    return [f for f in FOOD_GUIDE
            if (category == 'all' or f['category'] == category)
            and (choice == 'all' or (choice == 'high' and f['fast']) or f['choice'] == choice)
            and (not query or any(query in n.casefold() for n in [f['name'], *f['aliases']]))]


def portion_result(food_id, amount=None, label_carb=None):
    food = next((f for f in FOOD_GUIDE if f['id'] == food_id), None)
    if food is None:
        raise LookupError('该食品尚未收录，请重新选择')
    if amount is None:
        amount = food['portion']
    try:
        if isinstance(amount, bool):
            raise ValueError()
        amount = float(amount)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('请输入有效的食用量') from None
    if not math.isfinite(amount) or amount <= 0 or amount > 5000:
        raise ValueError('食用量应大于 0，且不超过 5000 克或毫升')
    carb = food['carb_per_100']
    from_label = label_carb not in (None, '')
    if from_label:
        try:
            if isinstance(label_carb, bool):
                raise ValueError()
            carb = float(label_carb)
        except (ValueError, TypeError, OverflowError):
            raise ValueError('标签碳水请输入 0 到 100 之间的数字') from None
        if not math.isfinite(carb) or carb < 0 or carb > 100:
            raise ValueError('标签碳水请输入 0 到 100 之间的数字')
    # 这一份的血糖负荷：用户填的量才是真正吃到肚子里的量，所以按实际量重算，
    # 不用目录里的示例份量。
    gl, gl_label = glycemic_load(food['gi'], carb, amount)
    return {'food': food, 'amount': amount, 'carb': round(carb * amount / 100, 1) if carb is not None else None,
            'gl': gl, 'gl_label': gl_label,
            'rise_label': food['rise_label'], 'rise_key': food['rise_key'],
            'rise_hint': _rise_hint(food['rise_key'], gl_label),
            'above_reference': not food.get('nutrition_only', False) and amount > food['portion'], 'notice': NOTICE,
            'from_label': from_label, 'label_carb': carb if from_label else None,
            'craving': craving_guide(food, carb)}


def label_portion(name, amount, label_carb, unit='g'):
    """未收录包装食品：只按用户提供的标签换算，不生成可食量或 GI。"""
    name = str(name or '').strip()
    if not name or len(name) > 60:
        raise ValueError('食品名称请填写 1 到 60 个字符')
    if unit not in ('g', 'ml'):
        raise ValueError('请选择克或毫升，须与包装标签一致')
    try:
        if isinstance(amount, bool) or isinstance(label_carb, bool):
            raise ValueError()
        amount, rate = float(amount), float(label_carb)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('请填写食用量和标签上的碳水数值') from None
    if not math.isfinite(amount) or not 0 < amount <= 5000:
        raise ValueError('食用量应大于 0，且不超过 5000 克或毫升')
    if not math.isfinite(rate) or not 0 <= rate <= 100:
        raise ValueError('每 100 克或毫升的碳水应在 0 到 100 克之间')
    return {'name': name, 'amount': amount, 'unit': unit,
            'unit_label': '毫升' if unit == 'ml' else '克',
            'label_carb': rate, 'carb': round(amount * rate / 100, 1),
            'source': '用户填写的包装标签', 'suitability': '暂无法判断',
            'notice': '仅换算碳水，不代表适合您吃，也不提供个人安全食用量。'}
