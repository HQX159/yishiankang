# -*- coding: utf-8 -*-
"""糖尿病控糖提示（R1 适老化 · 糖尿病专项）

给一道食谱（`Recipe.ingredients` 那段食材文本）算出：

    1. 升糖档位 —— 低升糖 / 中升糖 / 高升糖 / 认不出来
    2. 碳水、糖的**粗略区间**（按食材的常见"一人份"用量估）
    3. 点名提醒其中的高升糖食材
    4. 每餐建议份量（大白话，不堆术语）
    5. 是否需要"建议咨询营养师/医生"的温和提示

⚠️ 数据口径（这个模块存在的意义就是把口径说清楚）
    · 档位有**明确出处**：WS/T 601-2018 附录A 的 GI 值 + 国家标准定义的分档界线，
      见 GI_SOURCE_SHORT / GI_BOUNDARY。每个食材还带国标原值（FOODS 第 3 字段），
      供页面「凭什么这么判」的展开层展示。
    · 主界面仍**只显示低/中/高三档**，不把数字怼到中老年用户脸上；
      数值只在「想核对的人」展开时才出现。
    · 碳水 / 糖是**按食材类别的常见一人份用量粗估**，不是按配料表称重算出来的。
      食谱文本里的数量写法五花八门（"2 个""适量""少许"），没法可靠换算成克，
      所以一律用「约 xx~yy g」的区间表述，并且刻意取整到 5 g。
    · 估算按**一人份**口径，与食谱标的"N 人份"无关（同样因为拿不到可靠数量）。
    · 本模块**不构成医疗建议**。所有对外文案都要带上免责说明。

纯标准库；`python -m utils.diabetes_advice` 可跑自测。
"""

import re

# ── 升糖档位 ──────────────────────────────────────────────────
TIER_LOW = 'low'
TIER_MID = 'mid'
TIER_HIGH = 'high'

TIER_LABEL = {
    TIER_LOW: '低升糖',
    TIER_MID: '中升糖',
    TIER_HIGH: '高升糖',
}
TIER_RANK = {TIER_LOW: 0, TIER_MID: 1, TIER_HIGH: 2}

# ── 食材类别 → 常见"一人份"的碳水 / 糖 参考区间（g） ──────────
# 这是全模块唯一需要维护的数字表。取值思路：按"这道菜里该食材大概会用到多少"，
# 落在常见的家常份量上，宁可给宽区间也不给一个假精确值。
SERVING_CARB = {
    'staple':  (20, 35),   # 主食（米饭 / 面条 / 薯类 / 面点）
    'veg':     (3, 8),     # 蔬菜
    'protein': (1, 4),     # 肉蛋鱼虾豆制品
    'fruit':   (10, 20),   # 水果
    'nut':     (2, 8),     # 坚果种子
    'sweet':   (5, 15),    # 糖 / 蜂蜜 / 甜味酱料
    'oil':     (0, 1),     # 食用油
}
SERVING_SUGAR = {
    'staple':  (0, 2),
    'veg':     (1, 4),
    'protein': (0, 1),
    'fruit':   (5, 12),
    'nut':     (1, 3),
    'sweet':   (5, 15),
    'oil':     (0, 0),
}

# ── 依据出处（页面上要原样展示给用户，别改写成模糊说法）──────
# 这是「凭什么判它高升糖」的答案：国家标准 + 明确的档位界线。
GI_SOURCE_SHORT = 'WS/T 601-2018《妊娠期糖尿病患者膳食指导》附录A'
GI_SOURCE_LONG = ('WS/T 601-2018《妊娠期糖尿病患者膳食指导》附录A「常见食物 GI 值」'
                  '（数据源自《中国食物成分表》）。')
GI_BOUNDARY = 'GI＜55 为低，55～70 为中，＞70 为高'
# 国标列出具体数值，但「同一食材不同做法差别很大」这件事必须自己说清楚：
# 面条 国标 37、家常白挂面实测偏高；红薯 国标 54 指生甘薯、熟红薯 77。
# 所以档位 = 国标值 + 家常做法修正，冲突时取「对糖友更安全」的一侧。
GI_NOTE = ('档位以国标 GI 值为基础，并按家常做法修正：同一食材换个做法差别很大，'
           '冲突时按对糖友更安全的一侧取档。')
GI_UNLISTED = '国标未收录，按常见 GI 资料定档'

# ── 与国标不一致的条目：为什么不照改（「数据说明」页逐条展示）──────
# 光说"按家常做法修正"是空话 —— 用户查到国标 37 而我们标高档，会觉得我们乱来。
# 所以每一条都要给出**具体**理由，页面上逐条列出来。
# ⚠️ 维护约定：FOODS 里凡是「国标档 ≠ 我们的档」的条目，这里必须有理由，
#    否则自测会失败（见 _selftest「冲突都能说清理由」）。少一条就是少一个交代。
CONFLICT_REASONS = {
    '白粥': '国标这个值对应普通大米粥，69.4 已经贴在中区上沿。粥熬得越久升糖越快，'
            '家里那种熬到浓稠的白粥更高。所以取高，偏保守。',
    '粥': '这是表里的短词：配料里写「小米粥」「南瓜粥」但没单独收录的，都落到它。'
          '国标 69.4 对应普通大米粥，贴在中区上沿；粥熬得越久升糖越快，'
          '所以凡是「粥」统一取高，不细分。',
    '面条': '国标自己也矛盾：WS/T 601 写「面条 GI 37」，另有公开资料引「面条 82」，'
            '差 45 分。说明「面条」这个词太笼统 —— 白挂面、手擀面、'
            '强化蛋白的细面差别极大。按家用白挂面取高。',
    '面包': '同一张国标里「面包 50」和「白面包 87.9」互相打架。'
            '家里买的甜面包、吐司普遍在 70 以上，取高。',
    '红薯': '国标 54 指的是「生甘薯」。糖友吃的是煮红薯、烤红薯，'
            '《中国食物成分表》「熟甘薯（红）77」，属于高。取高。',
    '土豆': '国标 62 是「煮马铃薯」，但同一来源的「马铃薯泥 73」已经是高。'
            '家常的土豆丝、土豆炖肉、土豆泥差别很大，取高。',
    '燕麦': '国标收的是「燕麦麸」，不是整粒燕麦，而且 55 正好卡在界线上。'
            '整粒燕麦片升糖比麸皮慢，所以取低。',
    '香蕉': '52 确实在低区，但熟透的香蕉含糖高、又容易一次吃两根。取中，偏保守。',
    '葡萄': 'GI 本身不高（43），但葡萄含糖量高、极易吃过量，'
            '所以按「实际会吃多少」取中。这一条是有意为之，不是没查国标。',
    '葡萄干': 'GI 64 在中区，但葡萄干含糖 60% 以上，一把就超量，血糖负荷很高。'
              '按实际吃法取高。',
    '冰糖': '国标收的是「蔗糖」65，属于同类参考、不是直接收录。'
            '纯糖类统一按高处理，不放松。',
}

# ── 食材表：名称 → (升糖档位, 类别, GI值, 来源) ────────────────
#   GI值 / 来源 两个字段：
#     'exact' —— 国标表里就是这个名字，数值可直接挂给用户看
#     'near'  —— 国标表里是同类名（吐司→白面包、冰糖→蔗糖），展示时注明「参考」
#     None    —— 国标未收录。**不编数**，页面上如实说没收录
# ⚠️ 维护约定
#   · 只收"大部分公开 GI 表口径一致"的常见食材；有争议的按**保守档**收（宁高不低），
#     并在行尾注明原因。查不到把握的一律不收 —— 认不出来的走"未收录"，不猜。
#   · 匹配用"最早出现优先、同位置取最长"（见 _find_hit），所以同时收
#     「玉米」和「玉米油」是安全的。
FOODS = {
    # ---- 主食 / 淀粉类 ----
    '杂粮饭': (TIER_LOW, 'staple', None, None),
    '糙米': (TIER_HIGH, 'staple', 87.0, 'exact'),
    '燕麦': (TIER_LOW, 'staple', 55.0, 'near'),
    '荞麦': (TIER_LOW, 'staple', 54.0, 'exact'),
    '藜麦': (TIER_LOW, 'staple', None, None),
    '山药': (TIER_LOW, 'staple', 51.0, 'exact'),
    '芋头': (TIER_LOW, 'staple', 47.7, 'exact'),
    '全麦面包': (TIER_MID, 'staple', None, None),
    '小米粥': (TIER_MID, 'staple', 61.5, 'exact'),
    '玉米': (TIER_MID, 'staple', 55.0, 'exact'),
    '粉丝': (TIER_HIGH, 'staple', None, None),
    '年糕': (TIER_HIGH, 'staple', 87.0, 'near'),
    '白米饭': (TIER_HIGH, 'staple', 83.2, 'exact'),
    '米饭': (TIER_HIGH, 'staple', 83.2, 'exact'),
    '白粥': (TIER_HIGH, 'staple', 69.4, 'exact'),
    '粥': (TIER_HIGH, 'staple', 69.4, 'exact'),
    '面条': (TIER_HIGH, 'staple', 37.0, 'exact'),
    '馒头': (TIER_HIGH, 'staple', 88.1, 'exact'),
    '包子': (TIER_HIGH, 'staple', None, None),
    '饺子': (TIER_HIGH, 'staple', None, None),
    '烧饼': (TIER_HIGH, 'staple', 79.6, 'near'),
    '油条': (TIER_HIGH, 'staple', 74.9, 'exact'),
    '面包': (TIER_HIGH, 'staple', 50.0, 'exact'),
    '吐司': (TIER_HIGH, 'staple', 87.9, 'near'),
    '米粉': (TIER_HIGH, 'staple', None, None),
    '糯米': (TIER_HIGH, 'staple', 87.0, 'near'),
    '红薯': (TIER_HIGH, 'staple', 54.0, 'exact'),
    '土豆': (TIER_HIGH, 'staple', 62.0, 'exact'),
    '南瓜': (TIER_HIGH, 'staple', 75.0, 'exact'),
    '面粉': (TIER_HIGH, 'staple', None, None),
    '淀粉': (TIER_HIGH, 'staple', None, None),
    '生粉': (TIER_HIGH, 'staple', None, None),
    '蛋糕': (TIER_HIGH, 'staple', None, None),
    '饼干': (TIER_HIGH, 'staple', 72.0, 'near'),

    # ---- 蔬菜 ----
    '西兰花': (TIER_LOW, 'veg', None, None),
    '花椰菜': (TIER_LOW, 'veg', None, None),
    '菜花': (TIER_LOW, 'veg', None, None),
    '菠菜': (TIER_LOW, 'veg', None, None),
    '生菜': (TIER_LOW, 'veg', None, None),
    '小白菜': (TIER_LOW, 'veg', None, None),
    '白菜': (TIER_LOW, 'veg', None, None),
    '油菜': (TIER_LOW, 'veg', None, None),
    '卷心菜': (TIER_LOW, 'veg', None, None),
    '芹菜': (TIER_LOW, 'veg', None, None),
    '黄瓜': (TIER_LOW, 'veg', None, None),
    '西红柿': (TIER_LOW, 'veg', None, None),
    '番茄': (TIER_LOW, 'veg', None, None),
    '茄子': (TIER_LOW, 'veg', None, None),
    '青椒': (TIER_LOW, 'veg', None, None),
    '洋葱': (TIER_LOW, 'veg', None, None),
    '苦瓜': (TIER_LOW, 'veg', None, None),
    '冬瓜': (TIER_LOW, 'veg', None, None),
    '丝瓜': (TIER_LOW, 'veg', None, None),
    '芦笋': (TIER_LOW, 'veg', None, None),
    '竹笋': (TIER_LOW, 'veg', None, None),
    '豆角': (TIER_LOW, 'veg', None, None),
    '豌豆': (TIER_LOW, 'veg', None, None),
    '毛豆': (TIER_LOW, 'veg', None, None),
    '香菇': (TIER_LOW, 'veg', None, None),
    '蘑菇': (TIER_LOW, 'veg', None, None),
    '金针菇': (TIER_LOW, 'veg', None, None),
    '木耳': (TIER_LOW, 'veg', None, None),
    '海带': (TIER_LOW, 'veg', None, None),
    '紫菜': (TIER_LOW, 'veg', None, None),
    '白萝卜': (TIER_LOW, 'veg', None, None),
    '莲藕': (TIER_MID, 'veg', None, None),
    '胡萝卜': (TIER_HIGH, 'veg', None, None),  # 熟制后 GI 偏高，按保守档收

    # ---- 蛋白（肉蛋鱼虾奶豆） ----
    '鸡蛋': (TIER_LOW, 'protein', None, None),
    '豆腐': (TIER_LOW, 'protein', None, None),
    '豆浆': (TIER_LOW, 'protein', None, None),
    '黄豆': (TIER_LOW, 'protein', None, None),
    '绿豆': (TIER_LOW, 'protein', None, None),
    '红豆': (TIER_LOW, 'protein', None, None),
    '鸡胸肉': (TIER_LOW, 'protein', None, None),
    '鸡肉': (TIER_LOW, 'protein', None, None),
    '牛肉': (TIER_LOW, 'protein', None, None),
    '瘦猪肉': (TIER_LOW, 'protein', None, None),
    '猪肉': (TIER_LOW, 'protein', None, None),
    '羊肉': (TIER_LOW, 'protein', None, None),
    '鲈鱼': (TIER_LOW, 'protein', None, None),
    '鲫鱼': (TIER_LOW, 'protein', None, None),
    '三文鱼': (TIER_LOW, 'protein', None, None),
    '带鱼': (TIER_LOW, 'protein', None, None),
    '虾': (TIER_LOW, 'protein', None, None),
    '鱼': (TIER_LOW, 'protein', None, None),
    '牛奶': (TIER_LOW, 'protein', None, None),
    '酸奶': (TIER_LOW, 'protein', None, None),
    '腐竹': (TIER_MID, 'protein', None, None),

    # 高脂部位 / 加工肉（R5 第 4 项补，2026-10-20）
    # 为什么必须单列：这些词原来一个都认不出来（"五花肉"里不含连续的"猪肉"），
    # 整道菜直接掉进"未收录"。就算认出来，"猪肉 = 低升糖"也会被读成"可以放开吃"。
    # 档位仍按 GI 给（肉几乎不含碳水，GI 确实低），"油多 / 盐多"另走 CAUTIONS。
    '五花肉': (TIER_LOW, 'protein', None, None),
    '猪五花': (TIER_LOW, 'protein', None, None),
    '肥牛': (TIER_LOW, 'protein', None, None),
    '肥羊': (TIER_LOW, 'protein', None, None),
    '排骨': (TIER_LOW, 'protein', None, None),
    '猪蹄': (TIER_LOW, 'protein', None, None),
    '鸡翅': (TIER_LOW, 'protein', None, None),
    '鸡腿': (TIER_LOW, 'protein', None, None),
    '鸭肉': (TIER_LOW, 'protein', None, None),
    '鹅肉': (TIER_LOW, 'protein', None, None),
    '猪肝': (TIER_LOW, 'protein', None, None),
    '肥肠': (TIER_LOW, 'protein', None, None),
    '培根': (TIER_MID, 'protein', None, None),
    '腊肉': (TIER_MID, 'protein', None, None),
    '香肠': (TIER_MID, 'protein', None, None),
    '火腿肠': (TIER_MID, 'protein', None, None),
    '午餐肉': (TIER_MID, 'protein', None, None),
    '肉丸': (TIER_MID, 'protein', None, None),
    '炸鸡': (TIER_MID, 'protein', None, None),

    # ---- 水果 ----
    '苹果': (TIER_LOW, 'fruit', 36.0, 'exact'),
    '梨': (TIER_LOW, 'fruit', 36.0, 'exact'),
    '桃子': (TIER_LOW, 'fruit', 28.0, 'near'),
    '樱桃': (TIER_LOW, 'fruit', 22.0, 'exact'),
    '柚子': (TIER_LOW, 'fruit', 25.0, 'near'),
    '草莓': (TIER_LOW, 'fruit', None, None),
    '蓝莓': (TIER_LOW, 'fruit', None, None),
    '猕猴桃': (TIER_LOW, 'fruit', 52.0, 'exact'),
    '橘子': (TIER_LOW, 'fruit', 43.0, 'near'),
    '橙子': (TIER_LOW, 'fruit', None, None),
    '葡萄': (TIER_MID, 'fruit', 43.0, 'exact'),  # GI 本身不算高，但含糖量高、极易吃过量 → 保守归「中」
    '香蕉': (TIER_MID, 'fruit', 52.0, 'exact'),
    '芒果': (TIER_MID, 'fruit', 55.0, 'exact'),
    '菠萝': (TIER_MID, 'fruit', 66.0, 'exact'),
    '哈密瓜': (TIER_MID, 'fruit', None, None),
    '西瓜': (TIER_HIGH, 'fruit', 72.0, 'exact'),
    '荔枝': (TIER_HIGH, 'fruit', None, None),
    '龙眼': (TIER_HIGH, 'fruit', None, None),
    '红枣': (TIER_HIGH, 'fruit', None, None),
    '大枣': (TIER_HIGH, 'fruit', None, None),
    '葡萄干': (TIER_HIGH, 'fruit', 64.0, 'exact'),

    # ---- 坚果 / 油 ----
    '花生': (TIER_LOW, 'nut', None, None),
    '核桃': (TIER_LOW, 'nut', None, None),
    '杏仁': (TIER_LOW, 'nut', None, None),
    '腰果': (TIER_LOW, 'nut', None, None),
    '芝麻': (TIER_LOW, 'nut', None, None),
    '花生油': (TIER_LOW, 'oil', None, None),
    '橄榄油': (TIER_LOW, 'oil', None, None),
    '菜籽油': (TIER_LOW, 'oil', None, None),
    '玉米油': (TIER_LOW, 'oil', None, None),
    '大豆油': (TIER_LOW, 'oil', None, None),
    '香油': (TIER_LOW, 'oil', None, None),
    '食用油': (TIER_LOW, 'oil', None, None),
    '植物油': (TIER_LOW, 'oil', None, None),

    # ---- 糖 / 甜味调料 ----
    '番茄酱': (TIER_MID, 'sweet', None, None),
    '沙拉酱': (TIER_MID, 'sweet', None, None),
    '蚝油': (TIER_MID, 'sweet', None, None),
    '酱油': (TIER_LOW, 'sweet', None, None),
    '白糖': (TIER_HIGH, 'sweet', 83.8, 'near'),
    '红糖': (TIER_HIGH, 'sweet', None, None),
    '冰糖': (TIER_HIGH, 'sweet', 65.0, 'near'),
    '蜂蜜': (TIER_HIGH, 'sweet', 73.0, 'exact'),
    '果酱': (TIER_HIGH, 'sweet', None, None),
    '炼乳': (TIER_HIGH, 'sweet', None, None),
    '可乐': (TIER_HIGH, 'sweet', None, None),
    '果汁': (TIER_HIGH, 'sweet', None, None),
    '糖': (TIER_HIGH, 'sweet', 83.8, 'near'),  # 兜底：更长带"糖"的名字先被匹配掉
}

# 最长优先匹配
_FOOD_NAMES = sorted(FOODS.keys(), key=len, reverse=True)

# ── 档位之外的提醒（R5 第 4 项收口，2026-10-20）─────────────────
# 这一段解决的是老师那句质疑里最硬的一环：
#   「GI 低」≠「可以多吃」。五花肉的 GI 确实低（肉几乎不含碳水），
#   但脂肪约三成、热量接近瘦肉的两倍 —— 档位没说错，是**解读**缺了一环。
# 所以：档位一个字不改（改档位等于篡改国标口径），另挂一条提醒。
# 一道菜最多出一条，fat 优先于 oil，避免两张黄条叠在一起。
CAUTION_FAT = 'fat'
CAUTION_SALT = 'salt'
CAUTION_LABEL = {CAUTION_FAT: '油多', CAUTION_SALT: '盐多'}

# 名字 → ((类别, ...), 一句解释)
CAUTIONS = {
    # 高脂部位：油是瘦肉的两倍往上
    '五花肉': ((CAUTION_FAT,), '肥瘦相间，脂肪约三成，热量接近瘦肉的两倍'),
    '猪五花': ((CAUTION_FAT,), '肥瘦相间，脂肪约三成，热量接近瘦肉的两倍'),
    '肥牛': ((CAUTION_FAT,), '肥瘦相间，脂肪占比高'),
    '肥羊': ((CAUTION_FAT,), '肥瘦相间，脂肪占比高'),
    '排骨': ((CAUTION_FAT,), '带骨带肥，看着不多、油不少'),
    '猪蹄': ((CAUTION_FAT,), '皮和皮下脂肪多'),
    '鸡翅': ((CAUTION_FAT,), '鸡皮含脂肪多'),
    '鸡腿': ((CAUTION_FAT,), '比鸡胸肉油得多'),
    '鸭肉': ((CAUTION_FAT,), '皮下脂肪厚，烤鸭、酱鸭尤其油'),
    '鹅肉': ((CAUTION_FAT,), '皮下脂肪厚，油比鸡肉多得多'),
    '肥肠': ((CAUTION_FAT,), '脂肪含量高，卤煮、爆炒都很油'),
    '猪肝': ((CAUTION_FAT,), '胆固醇高，糖友常合并血脂异常，一个月吃一两次就够'),
    # 加工肉：油 + 盐，两项都高
    '培根': ((CAUTION_FAT, CAUTION_SALT), '肥肉多、盐多，属加工肉'),
    '腊肉': ((CAUTION_FAT, CAUTION_SALT), '肥肉多、盐多，属加工肉'),
    '香肠': ((CAUTION_FAT, CAUTION_SALT), '常掺淀粉和肥肉，盐也高'),
    '火腿肠': ((CAUTION_FAT, CAUTION_SALT), '常掺淀粉和肥肉，盐也高'),
    '午餐肉': ((CAUTION_FAT, CAUTION_SALT), '常掺淀粉和肥肉，盐也高'),
    '肉丸': ((CAUTION_FAT, CAUTION_SALT), '常掺淀粉和肥肉，盐也高'),
    '炸鸡': ((CAUTION_FAT,), '裹粉油炸，油和碳水都不低'),
}

# 这些蔬菜本身升糖低，但特别吸油 —— 只在"确实放了不少油"时作为补充说明
OILY_VEG = {'茄子', '豆角', '干豆角', '香菇', '蘑菇'}

# 油量警戒线（ml）。
# ⚠️ 为什么必须有这条线：一开始的写法是"有吸油菜 + 放了油就提示"，
#    结果真实库里 39 份食谱有 21 份被提示 —— 而那些恰恰是「植物油 3ml」「香油 1ml」
#    的少油食谱。对着少油菜说"油要算进去"，比不提示更伤可信度。
#    所以改成**看厨子自己写的量**，写"适量/少许/几滴"的一律不判定（宁可不说）。
OIL_ALERT_ML = 20
# 只认带明确单位的写法。"1 勺"不认 —— 分不清大小勺，猜了就会错。
_OIL_UNITS = (
    ('汤匙', 15.0), ('大勺', 15.0), ('茶匙', 5.0), ('小勺', 5.0),
    ('毫升', 1.0), ('ml', 1.0), ('克', 1.0), ('g', 1.0),
)
_OIL_NUM_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(汤匙|大勺|茶匙|小勺|毫升|ml|克|g)',
    re.IGNORECASE)

# ── 对外文案 ──────────────────────────────────────────────────
# 三档的"建议份量"只讲**怎么吃**；"换成什么"交给高升糖那张警告条去讲，
# 两边不重复（早先两段都写了"白米饭→杂粮饭"，读起来像说了两遍）。
PORTION_TEXT = {
    TIER_LOW: '可以按平常份量吃，主食一小碗以内（熟重约 100 g）就好。',
    TIER_MID: '主食建议比平时少三分之一，多配绿叶菜；'
              '吃饭时先夹菜，再吃肉蛋，最后吃主食。',
    TIER_HIGH: '主食建议减半（约半小碗），配菜要够；'
               '吃完别马上坐下，走一走更稳。',
    'unknown': '这次没能认全食材，先给通用建议：主食一小碗以内、先菜后饭、少油少糖。',
}

CONSULT_TEXT = ('具体份量建议结合您的用药和血糖情况来定；'
                '拿不准的时候，可以问问营养师或医生。')

ESTIMATE_NOTE = ('以上是粗估，不是称重计算：升糖快慢只分低 / 中 / 高三档，'
                 '碳水与糖按食材的常见一人份用量估算（食谱里的数量写法不统一，没法换算成克），'
                 '按一人份口径给出。仅供参考，不能替代医生或营养师的个体化建议。')

UNKNOWN_NOTE = ('食材里没能认出可以估算的品种，所以这次不给碳水 / 糖的数字，'
                '只给通用建议 —— 与其给一个不准的数，不如不给。')

# ── 「档位之外」的提醒文案（R5 第 4 项补）─────────────────────
# ⚠️ 措辞底线：不制造恐慌、也不和上面的档位打架。
#    下面每一句都要能回答"既然低升糖，为什么还要少吃"。
# ⚠️ 标题**不能**写死「低升糖」：腊肉、香肠这类给的是「中升糖」档，
#    标题里再出现「别看低升糖」就自相矛盾了。用中性的「升糖慢」。
FAT_CAUTION_HEAD = '油多，别因为升糖慢就多夹'
FAT_CAUTION_BODY = ('上面这几味的升糖确实慢（肉几乎不含碳水），但脂肪多、热量高。'
                    '血糖不会一下子冲上去，可吃多了体重和血脂都会往上走，'
                    '胰岛素抵抗跟着变差 —— 一餐吃到掌心大小（生重 50 g 上下）就够了。'
                    '上面那条「建议份量」讲的是主食，肉要按这个量另外算。')
FAT_CAUTION_SALT = '其中还有带盐的加工肉，合并高血压的话要再少一点。'
OIL_CAUTION_BODY = ('按 1 克油约 9 千卡算，{ml} ml 差不多 {kcal} 千卡（一碗米饭约 200 千卡，可以对着比一下）。'
                    '升糖速度不是问题，油多的菜要紧的是总热量，血糖回落也会更慢。'
                    '下次能少一勺就少一勺。')
OIL_CAUTION_OILY = '{veg}这类菜特别吸油，吃进去的往往比锅里倒的还多。'


def label_of_tier(tier):
    """档位 → 中文标签；认不出来时给一个诚实的说法。"""
    return TIER_LABEL.get(tier, '未收录')


def _tier_of_gi(gi):
    """按国标界线把 GI 值换算成档位（<55 低 / 55~70 中 / >70 高）。

    GI 为 None（国标未收录）时返回 None —— 调用方据此判断"这味到底有没有依据"，
    不能拿 None 当 0 处理。
    """
    if gi is None:
        return None
    if gi < 55:
        return TIER_LOW
    if gi <= 70:
        return TIER_MID
    return TIER_HIGH


def _gi_text(gi):
    """GI 数值 → 展示用文本：87.0 → '87'，83.2 → '83.2'。

    故意去掉无意义的 .0 —— "GI 87" 比 "GI 87.0" 更像人话，也少占一个字符位。
    """
    if gi is None:
        return None
    return '%g' % gi


def _clean(line):
    """去掉项目符号 / markdown 记号 / 空白，只留文字。"""
    out = []
    for ch in (line or ''):
        if ch in '-*•·#—–`>：:;；,，\t\r\n':
            out.append(' ')
        else:
            out.append(ch)
    return ''.join(out).strip()


# ── 否定说法：清单里"不放糖""无糖酸奶"不等于真的放了糖 ──────────
# 为什么需要：本表里有兜底的「糖」→ 高升糖。于是
#   · 白胡椒粉 少许（不加糖、不勾芡）
#   · 香醋 3ml（拌菜用，不额外加糖）
#   · 原味无糖酸奶 100g
# 都会被判成"含糖 → 高升糖"，跟同一份食谱正文里写的"碳水仅 12g、升糖负荷低"自相矛盾。
# 这类误判全部落在**控糖目的**生成的食谱上，正是最不能出错的那批。
#
# 判定口径（故意比 risk_guard._is_negated 窄得多）：
#   只认**紧贴食材名**的否定。risk_guard 那是给体检报告整句叙述用的，
#   作用域放到 30 字；食材行很短，同样放宽会把真食材也否掉
#   （「不粘锅刷食用油」里那个"不"跟油没关系）。
_NEG_PREFIX_RE = re.compile(
    r'(?:不|无|未|没|零|免|忌)'
    r'(?:(?:额外|另外|再|多|太多|过多|很多|其[他它]|任何|加|放|添|用|含|勾|要))*$'
)
# 向前看多少字。够覆盖「不 + 额外 + 加」这类最长写法，又不至于跨到上一个词。
_NEG_LOOKBACK = 8


def _is_negated_name(text, idx):
    """食材名出现在 idx 处时，是否被前面的否定词"否掉"了（→ 不算这份食谱里有它）。

    只认贴着名字的短否定：`不加糖` / `不额外加糖` / `无糖` / `零添加糖`。
    否定词与名字之间只允许出现「额外 / 再 / 加 / 放 / 用 / 含 / 勾」这类填充词，
    出现别的字（如「不粘锅刷油」里的"粘锅刷"）就不算否定 —— 宁可漏判成高升糖，
    也不要把真食材悄悄吃掉。
    """
    return bool(_NEG_PREFIX_RE.search(text[max(0, idx - _NEG_LOOKBACK):idx]))


def _find_hit(text):
    """找**最靠前**的已知食材名；同一位置取最长（`玉米油` 不会退成 `玉米`）。

    Returns:
        (name, idx) 或 None

    ⚠️ 这里必须是"最靠前优先"而不是"最长者优先"：`无糖酸奶` 里「糖」比「酸奶」靠前，
       只有按位置推进，才能先判掉被否定的「糖」再认出「酸奶」。
    """
    best = None
    for name in _FOOD_NAMES:                 # 已按长度降序
        idx = text.find(name)
        if idx < 0:
            continue
        if best is None or idx < best[1]:    # 严格小于 → 同位置保留更长的那个
            best = (name, idx)
    return best


def scan_ingredients(text):
    """扫食材文本。

    Returns:
        (found, unmatched)
        found     —— 认出来的食材名列表（按首次出现顺序、去重）
        unmatched —— 一个字都没认出来的食材行（原样，供前端提示"还有 N 项没收录"）

    否定写法的那个位置**不算**这份食谱用了该食材（见 `_is_negated_name`）。
    """
    found, seen, unmatched = [], set(), []
    for raw in (text or '').splitlines():
        line = _clean(raw)
        if not line:
            continue
        hit_any = False
        # 反复取最靠前的匹配并"吃掉"已匹配的片段，
        # 这样 "土豆炖牛肉" 能同时认出 土豆 和 牛肉，而不是只认第一个。
        while True:
            hit = _find_hit(line)
            if not hit:
                break
            name, idx = hit
            # 先挖空（保持长度不变），再判否定 —— 挖空的是名字本身，
            # 前面的否定前缀不受影响；挖空也保证下一轮不会重复命中同一个词。
            line = line[:idx] + ' ' * len(name) + line[idx + len(name):]
            if _is_negated_name(line, idx):
                continue                    # 这一处是被否掉的，继续找同一行后面的
            hit_any = True
            if name not in seen:
                seen.add(name)
                found.append(name)
        if not hit_any:
            unmatched.append(_clean(raw))
    return found, unmatched


def _round_range(lo, hi):
    """把区间**往外**取整到 5 g。

    往外取整 = 区间只会变宽、不会变窄 —— 宁可说"约 5~20 g"，
    也不要给出一个看起来更精确、实际更可能出错的值。
    下界给个 5 g 的地板：只要不是纯油，写"约 0~15 g"读起来很怪。
    """
    lo = int(lo)
    hi = int(hi)
    hi = ((hi + 4) // 5) * 5
    lo5 = (lo // 5) * 5
    # ⚠️ 这里必须用**取整前**的 lo 判断，不能写完 lo5 再看 lo5：
    #    lo=1 时 lo5 已经是 0，再判 `if lo5 > 0` 就永远不成立（踩过）。
    if lo > 0 and lo5 == 0:
        lo5 = 5
    # 取整后可能出现"约 5~5 g"这种退化区间，往宽了补一格
    if hi <= lo5:
        hi = lo5 + 5
    return lo5, hi


def _sum_ranges(foods, table):
    lo = hi = 0
    for name in foods:
        cat = FOODS[name][1]
        a, b = table.get(cat, (0, 0))
        lo += a
        hi += b
    return _round_range(lo, hi)


_OIL_NAMES = tuple(n for n, v in FOODS.items() if v[1] == 'oil')


def _oil_ml(text):
    """估这道菜放了多少油（ml）。判不出来返回 None。

    ⚠️ 只认作者自己写了**明确单位**的量（3ml / 5 g / 1 汤匙）。
       写"适量""少许""几滴"的一律当判不出来 —— 猜一个数出来比不说更糟，
       而且这类写法的菜多半本来就放得少。
    """
    if not text:
        return None
    total, hit = 0.0, False
    for seg in re.split(r'[\n,，、;；]', text):
        if not any(n in seg for n in _OIL_NAMES):
            continue
        m = _OIL_NUM_RE.search(seg)
        if not m:
            continue
        hit = True
        qty = float(m.group(1))
        unit = m.group(2).lower()
        for u, factor in _OIL_UNITS:
            if unit == u.lower():
                total += qty * factor
                break
    return total if hit else None


def build_advice(ingredients_text, servings=None):
    """把食材文本变成一张"控糖提示"卡片要用的数据。

    Args:
        ingredients_text: `Recipe.ingredients`（多行 / 逗号分隔的食材文本，允许 markdown 符号）
        servings:         食谱标注的份数。**不参与计算**（估算固定按一人份口径），
                          仅原样带回，方便调用方展示。

    Returns:
        dict，字段见下方 `return`。

    ⚠️ 字段名叫 `food_items` 而不是 `items` —— Jinja 里 `advice.items` 会先命中
       dict 自带的 `.items()` 方法（模板里报 "builtin_function_or_method has no len()"），
       换个名字绕开这个坑。
    """
    foods, unmatched = scan_ingredients(ingredients_text)

    if not foods:
        tier = 'unknown'
    else:
        ranks = [TIER_RANK[FOODS[f][0]] for f in foods]
        # 保守取最高档：只要有一味高升糖食材，整道菜就按"高"提醒（宁可提醒过头）
        top = max(ranks)
        tier = {0: TIER_LOW, 1: TIER_MID, 2: TIER_HIGH}[top]

    carb = sugar = None
    if foods:
        carb = _sum_ranges(foods, SERVING_CARB)
        sugar = _sum_ranges(foods, SERVING_SUGAR)

    # 高升糖的排前面，提醒更醒目
    items = sorted(
        ({
            'name': f,
            'tier': FOODS[f][0],
            'label': label_of_tier(FOODS[f][0]),
            'gi': FOODS[f][2],                      # 国标原值，None = 国标未收录
            'gi_text': _gi_text(FOODS[f][2]),
            'gi_src': FOODS[f][3],                  # 'exact' / 'near' / None
            # 按国标值应该是哪一档 —— 与我们的档位不同就说明"按做法修正过"
            'gi_tier': _tier_of_gi(FOODS[f][2]),
        } for f in foods),
        key=lambda it: -TIER_RANK[it['tier']],
    )
    for it in items:
        it['gi_conflict'] = bool(it['gi_tier'] and it['gi_tier'] != it['tier'])
        it['gi_tier_label'] = label_of_tier(it['gi_tier']) if it['gi_tier'] else None
    high_names = [it['name'] for it in items if it['tier'] == TIER_HIGH]
    mid_names = [it['name'] for it in items if it['tier'] == TIER_MID]
    listed = [it for it in items if it['gi'] is not None]

    # ── 档位之外：高脂 / 吸油菜的提醒（R5 第 4 项）─────────────
    # 「低升糖」只说明升糖有多快，不说明能不能多吃。这里给的是另一半答案。
    # 一道菜最多一条：fat 优先于 oil（油多的菜必然也有油，不必说两遍）。
    fat_hits = [it['name'] for it in items if it['name'] in CAUTIONS]
    oil_ml = _oil_ml(ingredients_text)
    oily_veg = [f for f in foods if f in OILY_VEG]
    caution = None
    if fat_hits:
        kinds, rows = set(), []
        for nm in fat_hits:
            ks, why = CAUTIONS[nm]
            kinds.update(ks)
            rows.append({'name': nm, 'why': why,
                         'labels': [CAUTION_LABEL[k] for k in ks]})
        body = FAT_CAUTION_BODY + (FAT_CAUTION_SALT if CAUTION_SALT in kinds else '')
        caution = {'kind': CAUTION_FAT, 'head': FAT_CAUTION_HEAD,
                   'body': body, 'rows': rows, 'names': fat_hits}
    elif oil_ml is not None and oil_ml >= OIL_ALERT_ML:
        # 判据是**作者自己写的油量**，不是"菜里有没有吸油的品种"（见 OIL_ALERT_ML 注释）
        ml = int(round(oil_ml))
        body = OIL_CAUTION_BODY.format(ml=ml, kcal=int(round(oil_ml * 9)))
        if oily_veg:
            body += OIL_CAUTION_OILY.format(veg='、'.join(oily_veg[:2]))
        # names 留空：标题写成「用了约 30 ml 油：茄子」不通 ——
        # 吸油菜名只在正文里当补充说明，不挂在标题后面。
        caution = {'kind': 'oil', 'head': '这道菜用了约 %d ml 油' % ml,
                   'body': body, 'rows': [], 'names': []}

    return {
        'tier': tier,
        'tier_label': label_of_tier(tier),
        'known': bool(foods),
        'food_items': items,
        'high_names': high_names,
        'mid_names': mid_names,
        'caution': caution,                  # None / dict(head, body, rows, names)
        'carb': carb,                        # (lo, hi) 或 None
        'sugar': sugar,                      # (lo, hi) 或 None
        'carb_text': ('约 %d~%d g' % carb) if carb else None,
        'sugar_text': ('约 %d~%d g' % sugar) if sugar else None,
        'portion_text': PORTION_TEXT.get(tier, PORTION_TEXT['unknown']),
        'consult': tier == TIER_HIGH,
        'consult_text': CONSULT_TEXT,
        'note': ESTIMATE_NOTE if foods else UNKNOWN_NOTE,
        'unmatched': unmatched,
        'unmatched_count': len(unmatched),
        'servings': servings,

        # ── 依据（「凭什么判它高升糖」的答案，供页面展开层展示）────────
        'gi_source': GI_SOURCE_SHORT,        # 一句点名出处，给折行标题用
        'gi_source_long': GI_SOURCE_LONG,    # 完整出处，展开层正文
        'gi_boundary': GI_BOUNDARY,          # 档位界线原话
        'gi_note': GI_NOTE,                  # 为什么要按做法修正
        'gi_unlisted': GI_UNLISTED,          # 国标未收录怎么说
        'gi_listed_count': len(listed),      # 有国标值的食材数
        'gi_unlisted_count': len(foods) - len(listed),
        # 未收录的**点名**：只说"有 3 味没收录"用户不知道是哪几味，
        # 而恰恰是这几味的档位最没有国标依据，必须让用户看见。
        'gi_unlisted_names': [it['name'] for it in items if it['gi'] is None][:8],
        'gi_unlisted_more': max(0, len(foods) - len(listed) - 8),
        'gi_conflict_count': sum(1 for it in items if it['gi_conflict']),
        # 模板里别做"先切片再过滤"，那样会少显示 —— 这里就备好该显示的行
        'gi_rows': listed[:10],
        'gi_rows_more': max(0, len(listed) - 10),
    }


# ── 「数据说明」页（R5 第 4 项 E）────────────────────────────────
METHOD_UNLISTED_RULES = (
    '不编数字。查不到就不写，页面上如实说「国标未收录」。',
    '档位仍要给：按常见 GI 资料定档，并在展开层注明这一档没有国标数字可挂。',
    '不因为「没数字」就一律按低升糖放过 —— 拿不准的按对糖友更安全的一侧取。',
)

METHOD_PROMISES = (
    '不承诺「吃了血糖一定怎么样」。食物只是影响因素之一，一个标签不能当结论。',
    '不替代医生或营养师。用药、剂量、能不能停药，只有看诊的医生能定。',
    '不悄悄改数字。与国标不一致的条目全部列在下面，包括对我们不利的那几条。',
)


def build_method_page():
    """组装「升糖标签是怎么来的」这一页要用的全部数据。

    为什么在这里算、而不是模板里写死
      · 数字必须跟着 FOODS 走：表里加一味、改一条档位，页面上「共 154 味 / 40 味有国标值」
        要跟着变。否则过几天这页就成了假话 —— 而这一页存在的唯一意义就是「可核对」。
      · 模板只排版，不参与统计。

    纯标准库、不依赖 Flask，可直接 `python -m utils.diabetes_advice` 自测。
    """
    exact, near, unlisted = [], [], []
    for name, (tier, _cat, gi, src) in FOODS.items():
        if gi is None:
            unlisted.append(name)
        elif src == 'near':
            near.append(name)
        else:
            exact.append(name)

    conflicts = []
    for name, (tier, _cat, gi, src) in FOODS.items():
        if gi is None:
            continue
        gi_tier = _tier_of_gi(gi)
        if not gi_tier or gi_tier == tier:
            continue
        conflicts.append({
            'name': name,
            'gi': gi,
            'gi_text': _gi_text(gi),
            'gi_tier_label': label_of_tier(gi_tier),
            'our_tier': tier,
            'our_label': label_of_tier(tier),
            'src': src,
            'why': CONFLICT_REASONS.get(name, ''),
        })
    # 国标值从低到高排：「国标 37 却标高档」这种最反直觉的放最前面，
    # 用户第一眼看到的就是最该被解释的那一条。
    conflicts.sort(key=lambda r: (r['gi'], r['name']))

    return {
        'source_short': GI_SOURCE_SHORT,
        'source_long': GI_SOURCE_LONG,
        'boundary': GI_BOUNDARY,
        'note': GI_NOTE,
        'total': len(FOODS),
        'exact_count': len(exact),
        'near_count': len(near),
        'listed_count': len(exact) + len(near),
        'unlisted_count': len(unlisted),
        'unlisted_sample': unlisted[:12],
        'unlisted_more': max(0, len(unlisted) - 12),
        'unlisted_rules': METHOD_UNLISTED_RULES,
        'promises': METHOD_PROMISES,
        'conflicts': conflicts,
        'conflict_count': len(conflicts),
    }


# ── 自测 ──────────────────────────────────────────────────────
def _selftest():
    passed = failed = 0

    def ok(name, cond, extra=''):
        nonlocal passed, failed
        if cond:
            passed += 1
            print('  PASS  %s' % name)
        else:
            failed += 1
            print('  FAIL  %s   %s' % (name, extra))

    print('=== 1) 食材识别 ===')
    found, unmatched = scan_ingredients('- 西兰花 200g\n- 鸡胸肉 1 块\n- 橄榄油 少许')
    ok('认出三样', found == ['西兰花', '鸡胸肉', '橄榄油'], str(found))
    ok('没有漏行', unmatched == [], str(unmatched))

    found, _ = scan_ingredients('土豆炖牛肉')
    ok('连在一起的两种食材都能认出', set(found) == {'土豆', '牛肉'}, str(found))

    found, _ = scan_ingredients('2 个')
    ok('纯数量行认不出来', found == [], str(found))

    found, unmatched = scan_ingredients('少许神秘香料')
    ok('未收录食材进 unmatched', found == [] and len(unmatched) == 1, str(unmatched))

    print('=== 2) 最长优先（不许把"玉米油"认成"玉米"） ===')
    found, _ = scan_ingredients('玉米油')
    ok('玉米油 → 油，不是玉米', found == ['玉米油'], str(found))

    print('=== 2b) 否定说法：「不加糖 / 无糖酸奶」不是放了糖 ===')
    a = build_advice('- 白胡椒粉 少许（不加糖、不勾芡）')
    ok('「不加糖」不再判高升糖', a['tier'] != TIER_HIGH and a['high_names'] == [],
       '%s %s' % (a['tier'], a['high_names']))
    a = build_advice('- 香醋 3ml（拌菜用，不额外加糖）')
    ok('「不额外加糖」同样跳过', a['tier'] != TIER_HIGH and a['high_names'] == [],
       '%s %s' % (a['tier'], a['high_names']))
    found, _ = scan_ingredients('原味无糖酸奶 100g')
    ok('「无糖酸奶」要认出酸奶、不额外算糖', found == ['酸奶'], str(found))
    found, _ = scan_ingredients('不放太多糖')
    ok('「不放太多糖」跳过', found == [], str(found))

    print('=== 2c) 别过度否定（真食材不许被吃掉） ===')
    a = build_advice('- 白糖 5g')
    ok('光写白糖仍是高升糖', a['tier'] == TIER_HIGH and a['high_names'] == ['白糖'], str(a['high_names']))
    found, _ = scan_ingredients('不粘锅刷食用油')
    ok('「不粘锅」里的"不"否不掉油', found == ['食用油'], str(found))
    a = build_advice('- 无糖酸奶\n- 白糖')
    ok('同一行/多行都只跳过被否定的那处', a['high_names'] == ['白糖'], str(a['high_names']))

    print('=== 3) 升糖档位 ===')
    a = build_advice('- 西兰花\n- 鸡胸肉\n- 橄榄油')
    ok('全低升糖 → low', a['tier'] == 'low', a['tier'])
    a = build_advice('- 白米饭\n- 西兰花')
    ok('含白米饭 → high', a['tier'] == 'high', a['tier'])
    ok('点名高升糖食材', a['high_names'] == ['白米饭'], str(a['high_names']))
    a = build_advice('- 小米粥\n- 菠菜')
    ok('含中升糖 → mid', a['tier'] == 'mid', a['tier'])
    a = build_advice('少许神秘香料')
    ok('认不出 → unknown', a['tier'] == 'unknown', a['tier'])
    ok('认不出时不给数字', a['carb'] is None and a['carb_text'] is None)
    ok('认不出时用兜底文案', a['portion_text'] == PORTION_TEXT['unknown'])

    print('=== 4) 碳水 / 糖区间 ===')
    a = build_advice('- 白米饭\n- 西兰花\n- 鸡胸肉')
    lo, hi = a['carb']
    ok('区间取整到 5 的倍数', lo % 5 == 0 and hi % 5 == 0, str(a['carb']))
    ok('高的不低于低的', hi >= lo, str(a['carb']))
    ok('碳水落在主食+蔬菜+蛋白的合理带', 20 <= lo and hi <= 50, str(a['carb']))
    ok('糖远低于碳水（这菜不是甜的）', a['sugar'][1] <= 10, str(a['sugar']))

    a = build_advice('- 苹果\n- 蜂蜜')
    ok('水果+蜂蜜的糖明显更高', a['sugar'][1] >= 15, str(a['sugar']))

    for t in ('- 西兰花\n- 鸡胸肉', '- 白米饭', '- 苹果', '- 少许香料'):
        c = build_advice(t)
        if c['carb']:
            ok('区间不退化（lo<hi）: %s' % t.replace('\n', '/'), c['carb'][0] < c['carb'][1], str(c['carb']))
        if c['sugar']:
            ok('糖区间不退化: %s' % t.replace('\n', '/'), c['sugar'][0] < c['sugar'][1], str(c['sugar']))

    print('=== 5) 咨询提示与免责 ===')
    ok('高升糖才提示咨询', build_advice('- 白米饭')['consult'] is True)
    ok('低升糖不打扰用户', build_advice('- 西兰花')['consult'] is False)
    ok('始终带免责说明', '不能替代' in ESTIMATE_NOTE)
    ok('提示文案不是命令语气', '建议' in CONSULT_TEXT)

    print('=== 6) 健壮性 ===')
    for bad in (None, '', '   ', '\n\n'):
        r = build_advice(bad)
        assert r['tier'] == 'unknown', bad
    ok('空 / None 输入不炸', True)
    a = build_advice('- 西兰花', servings=2)
    ok('servings 只带回、不参与计算', a['servings'] == 2 and a['tier'] == 'low')
    ok('同一食材重复出现只算一次', build_advice('- 鸡蛋\n- 鸡蛋')['carb'] == build_advice('- 鸡蛋')['carb'])

    print()
    print('=== 7) 档位之外的提醒（低升糖 ≠ 可以多吃）===')
    a = build_advice('- 五花肉 200g')
    ok('五花肉能认出来（原来整道菜掉进"未收录"）',
       a['known'] and '五花肉' in [x['name'] for x in a['food_items']])
    ok('五花肉档位仍按 GI 给低升糖（没篡改口径）', a['tier'] == TIER_LOW)
    ok('但另给一条"油多"提醒', bool(a['caution']) and a['caution']['kind'] == CAUTION_FAT)
    ok('提醒标题不带档位词（否则与中档自相矛盾）',
       '低升糖' not in a['caution']['head'])
    ok('提醒点名是哪几味', '五花肉' in a['caution']['names'])
    ok('限量说法具体到量（掌心大小）', '掌心' in a['caution']['body'])
    ok('加工肉同时标油多 + 盐多',
       '盐多' in [lb for r in build_advice('- 腊肉 50g')['caution']['rows'] for lb in r['labels']])
    ok('瘦肉不触发提醒（别把正常菜也标黄）', build_advice('- 鸡胸肉 150g')['caution'] is None)
    ok('吸油菜 + 真放了油 → 提示油',
       build_advice('- 茄子 300g\n- 食用油 30ml')['caution']['kind'] == 'oil')
    ok('普通青菜 + 少量油不提示（噪音控制）',
       build_advice('- 西兰花 200g\n- 食用油 5ml')['caution'] is None)
    ok('一道菜最多一条提醒', build_advice('- 五花肉 200g\n- 香肠 50g')['caution']['kind'] == CAUTION_FAT)
    a = build_advice('- 红烧五花肉 300g\n- 土豆 200g')
    ok('高升糖与油多并存、互不覆盖', a['tier'] == TIER_HIGH and a['caution'] is not None)

    print()
    print('=== 8) 数据说明页（build_method_page）===')
    m = build_method_page()
    ok('三类相加等于总数',
       m['exact_count'] + m['near_count'] + m['unlisted_count'] == m['total'])
    ok('「有国标值」= 直接对应 + 同类参考',
       m['listed_count'] == m['exact_count'] + m['near_count'])
    ok('冲突条目都写了理由', all(r['why'] for r in m['conflicts']),
       [r['name'] for r in m['conflicts'] if not r['why']])
    ok('没有过时的理由残留（FOODS 里已删的条目要清）',
       not (set(CONFLICT_REASONS) - set(FOODS)),
       sorted(set(CONFLICT_REASONS) - set(FOODS)))
    # 反向校验：FOODS 里凡是「国标档 ≠ 我们的档」都必须进表。
    # 以后补词条若产生新冲突而没写理由，这里立刻红 —— 不然那一条就是"没交代"。
    names = [r['name'] for r in m['conflicts']]
    missing = [n for n, (tier, _c, gi, _s) in FOODS.items()
               if gi is not None and _tier_of_gi(gi) and _tier_of_gi(gi) != tier
               and n not in names]
    ok('所有档位冲突都进表（新加词条必须补理由）', not missing, missing)
    ok('冲突按国标值升序（最反直觉的排最前）',
       [r['gi'] for r in m['conflicts']] == sorted(r['gi'] for r in m['conflicts']))
    ok('面条排第一，且理由点出国标自相矛盾（37 vs 82）',
       m['conflicts'][0]['name'] == '面条' and '37' in m['conflicts'][0]['why']
       and '82' in m['conflicts'][0]['why'])
    ok('总数跟着 FOODS 走，没在别处写死', m['total'] == len(FOODS))
    ok('未收录点名最多 12 味', len(m['unlisted_sample']) <= 12)
    ok('未收录：点名 + 其余 = 总数',
       len(m['unlisted_sample']) + m['unlisted_more'] == m['unlisted_count'])
    ok('「不编数」三条规则齐全', len(m['unlisted_rules']) == 3)
    ok('三条承诺齐全（含"不悄悄改数字"）',
       len(m['promises']) == 3 and any('不悄悄改' in s for s in m['promises']))

    print('-' * 60)
    print('通过 %d/%d' % (passed, passed + failed))
    return failed == 0


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(0 if _selftest() else 1)
