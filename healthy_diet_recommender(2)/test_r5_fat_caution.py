# test_r5_fat_caution.py
"""R5 第 4 项收口 · 「低升糖 ≠ 可以多吃」自测（造临时库，不碰真实 app.db）

要回答的是老师那句质疑里最硬的一环：
    「食材高升糖 / 低升糖放出来就要可信 —— 你凭什么让人信？」

之前的做法只回答了「升糖快慢凭什么这么判」（国标 GI + 依据展开块），
但漏了一半：**五花肉的 GI 确实低**，只写「低升糖」，用户会读成"可以放开吃"。
所以这一版加了「档位之外」的提醒，本测试验的就是它：

    1. 数据层：CAUTIONS 里的名字必须都在 FOODS 里（否则永远匹配不上）
    2. 档位不篡改：加了提醒，五花肉仍然按 GI 判「低升糖」
    3. 提醒要点名是哪几味，并给出具体限量（掌心大小 / 50 g）
    4. ★不误伤★：写着「植物油 3ml」「香油 1ml」的少油菜**不能**出提醒
       （第一版按"菜里有吸油菜"判断，真实库 39 份里误报 21 份，全被撤回）
    5. 油量只有 ≥ 20 ml 才提，且要把油量折成热量说清楚
    6. 两个页面都生效：食谱详情页、社区帖子详情页
    7. 依据块里那句「GI 只说升糖有多快，不说能不能多吃」必须在

用法
    python test_r5_fat_caution.py
"""
import os
import tempfile

TMPDIR = tempfile.mkdtemp(prefix='fat_c_')
DB_PATH = os.path.join(TMPDIR, 't.db').replace('\\', '/')
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH   # 必须早于 import app

from app import create_app                                    # noqa: E402
from utils.database import db                                 # noqa: E402
from models.user import User                                  # noqa: E402
from models.recipe import Recipe                              # noqa: E402
from models.community import CommunityPost               # noqa: E402
import utils.diabetes_advice as da                            # noqa: E402

passed = 0
total = 0


def check(name, cond, detail=''):
    global passed, total
    total += 1
    if cond:
        passed += 1
        print('[PASS] %s' % name)
    else:
        print('[FAIL] %s   %s' % (name, detail))


def html_of(resp):
    return resp.get_data(as_text=True)


# 三份对照食材
FAT_DISH = ('- 五花肉 200 g（切厚片）\n'
            '- 青椒 100 g\n'
            '- 生抽 10 ml')
# 真实库里最常见的"少油"写法 —— 这份绝对不能出黄条
LIGHT_DISH = ('- 茄子 150 g（约半根）\n'
              '- 西红柿 200 g\n'
              '- 鸡蛋 2 个\n'
              '- 植物油 3 ml（约半茶匙）\n'
              '- 食盐 0.5 g')
HEAVY_OIL_DISH = ('- 茄子 300 g\n'
                  '- 食用油 30 ml\n'
                  '- 蒜 2 瓣')


def main():
    print('=' * 78)
    print('R5 第 4 项收口 · 低升糖不等于可以多吃 · 自测')
    print('=' * 78)

    # ── 一、数据层 ────────────────────────────────────────────────────────
    print('\n=== 1) 提醒表结构 ===')
    missing = [n for n in da.CAUTIONS if n not in da.FOODS]
    check('CAUTIONS 里的名字都在 FOODS 里（否则永远匹配不上）', not missing, str(missing))

    bad_kind = [(n, ks) for n, (ks, _t) in da.CAUTIONS.items()
                if not ks or any(k not in (da.CAUTION_FAT, da.CAUTION_SALT) for k in ks)]
    check('标签只用 fat / salt 两种', not bad_kind, str(bad_kind[:3]))

    no_why = [n for n, (_ks, why) in da.CAUTIONS.items() if not why or len(why) < 6]
    check('每一条都写了原因（不是空标签）', not no_why, str(no_why[:3]))

    check('高脂部位几乎都在 protein 类',
          all(da.FOODS[n][1] == 'protein' for n in da.CAUTIONS),
          [n for n in da.CAUTIONS if da.FOODS[n][1] != 'protein'])

    # ── 二、build_advice ─────────────────────────────────────────────────
    print('\n=== 2) 判定：档位不动，另挂提醒 ===')
    a = da.build_advice(FAT_DISH)
    names = [it['name'] for it in a['food_items']]
    check('五花肉能被认出来', '五花肉' in names, names)
    check('档位仍按 GI 判低升糖（没篡改口径）', a['tier'] == da.TIER_LOW, a['tier'])
    check('另有一条 fat 提醒', bool(a['caution']) and a['caution']['kind'] == da.CAUTION_FAT, a['caution'])
    check('提醒点名五花肉', '五花肉' in a['caution']['names'], a['caution'] and a['caution']['names'])
    check('给出具体限量（掌心大小）', '掌心' in a['caution']['body'])
    check('说明份量建议讲的是主食', '主食' in a['caution']['body'])
    check('标题里不出现「低升糖」三个字（否则与中档自相矛盾）',
          '低升糖' not in a['caution']['head'], a['caution']['head'])
    check('提醒行里带「油多」标签',
          any('油多' in r['labels'] for r in a['caution']['rows']), a['caution']['rows'])

    a = da.build_advice('- 腊肉 50 g\n- 蒜苗 100 g')
    check('加工肉判中升糖（不硬塞低档）', a['tier'] == da.TIER_MID, a['tier'])
    labels = [lb for r in (a['caution']['rows'] if a['caution'] else []) for lb in r['labels']]
    check('加工肉同时标「油多 + 盐多」', '盐多' in labels, labels)

    check('瘦肉不触发（别把正常菜也标黄）',
          da.build_advice('- 鸡胸肉 150 g')['caution'] is None)

    print('\n=== 3) 用油：只看作者自己写的量 ===')
    a = da.build_advice(LIGHT_DISH)
    check('★ 植物油 3 ml 不提示（真实库 39 份里 21 份是这种，不能误伤）',
          a['caution'] is None, a['caution'])
    a = da.build_advice(HEAVY_OIL_DISH)
    check('食用油 30 ml 才提示油', bool(a['caution']) and a['caution']['kind'] == 'oil',
          a['caution'])
    check('标题写出实际油量', '30' in a['caution']['head'], a['caution']['head'])
    check('把油折成热量说清楚（9 kcal/g）', '千卡' in a['caution']['body'])
    check('茄子这类吸油菜补一句', '吸油' in a['caution']['body'])
    check('油提示的标题不挂蔬菜名（「用了约 30 ml 油：茄子」不通）',
          a['caution']['names'] == [], a['caution']['names'])

    check('「油 适量」判不出来就不说（宁可不说）',
          da.build_advice('- 花生油 适量\n- 青菜 200 g')['caution'] is None)
    check('「1 勺」不猜（分不清大小勺）',
          da.build_advice('- 食用油 1 勺\n- 青菜 200 g')['caution'] is None)
    check('两种油加起来算总量', da._oil_ml('- 食用油 15 ml\n- 香油 8 ml') == 23.0,
          da._oil_ml('- 食用油 15 ml\n- 香油 8 ml'))
    check('「2 汤匙」按 15 ml/汤匙 折算', da._oil_ml('- 食用油 2 汤匙') == 30.0,
          da._oil_ml('- 食用油 2 汤匙'))

    a = da.build_advice('- 五花肉 200 g\n- 香肠 50 g')
    check('一道菜最多一条提醒（fat 优先）', a['caution']['kind'] == da.CAUTION_FAT)
    a = da.build_advice('- 红烧五花肉 300 g\n- 土豆 200 g')
    check('高升糖警告与油多提醒并存、互不覆盖',
          a['tier'] == da.TIER_HIGH and a['caution'] is not None, (a['tier'], a['caution']))

    # ── 三、页面 ─────────────────────────────────────────────────────────
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        u = User(username='fat_tester')
        u.set_password('pw123456')
        db.session.add(u)
        db.session.commit()
        uid = u.id
        r1 = Recipe(user_id=uid, title='五花肉测试菜', ingredients=FAT_DISH,
                    instructions='1. 切片\n2. 煸炒', servings=1)
        r2 = Recipe(user_id=uid, title='少油测试菜', ingredients=LIGHT_DISH,
                    instructions='1. 洗净\n2. 蒸熟', servings=1)
        db.session.add_all([r1, r2])
        db.session.commit()
        rid_fat, rid_light = r1.id, r2.id
        db.session.add(CommunityPost(recipe_id=rid_fat, user_id=uid))
        db.session.commit()

    client.post('/api/user/login',
                data={'identifier': 'fat_tester', 'password': 'pw123456'},
                follow_redirects=True)
    page = html_of(client.get('/api/recipes/recipes/%s' % rid_fat))
    light = html_of(client.get('/api/recipes/recipes/%s' % rid_light))

    print('\n=== 4) 食谱详情页 ===')
    check('出现 gi-caution 块', 'gi-caution gi-caution-fat' in page,
          page[page.find('gi-portion'):][:300])
    check('标题点名五花肉', '五花肉' in page.split('gi-caution-head')[1][:200]
          if 'gi-caution-head' in page else False)
    check('写清为什么（脂肪 / 热量）', '脂肪' in page and '热量' in page)
    check('档位徽标仍是低升糖（两件事分开说）', 'gi-badge gi-badge-low' in page)
    check('★ 少油测试菜不出黄条', 'gi-caution' not in light,
          light[light.find('gi-portion'):][:200])
    check('少油菜的档位照常显示', 'gi-badge gi-badge-low' in light)

    print('\n=== 5) 依据块补的那句话 ===')
    check('明说 GI 只讲快慢、不讲能不能多吃',
          '升糖有多快' in page and '能不能多吃' in page)
    check('解释了为什么档位低的菜也会另有提醒', '档位低的菜也可能另给一条提醒' in page)
    check('这句话在收起层里（不占主界面）',
          page.find('gi-why-body') < page.find('升糖有多快'))

    print('\n=== 6) 社区帖子详情页也要有 ===')
    cpage = html_of(client.get('/api/community/post/1'))
    check('社区页出现同一条提醒', 'gi-caution' in cpage,
          cpage[cpage.find('gi-portion'):][:200])
    check('社区页也点名五花肉', '五花肉' in cpage)

    print()
    print('-' * 78)
    print('通过 %d/%d' % (passed, total))
    print('临时库：%s' % TMPDIR)
    return passed == total


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    if not main():
        sys.exit(1)
