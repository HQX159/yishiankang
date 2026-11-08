# test_r5_gi_evidence.py
"""R5 第 4 项 · 升糖标签「依据」自测（造临时库，不碰真实 app.db）

回答的是用户那句质问：
    「没改动前也一直写哪味食材高/低升糖，但怎么让用户知道你这就一定是高/低升糖？
      要个依据，还是默认用户知道？」

所以这里验的不是"标签有没有"，而是**依据链在不在、准不准**：
    1. 数据层：154 条全是 4 元组；GI 值落在合理范围；非冲突项的档位必须与 GI 自洽
    2. 糙米已纠正为「高」、芋头为「低」（这两条是核对国标后改的）
    3. 页面出现依据块：标准号、档位界线、每味国标值
    4. 与国标不一致的那几味，页面明写「按国标算 X 档，我们按家常做法取 Y」
    5. 国标未收录的食材，明说没收录，**不编数**
    6. ★主层不被数字污染★：食材标签区仍是「白米饭 · 高升糖」，不挂 GI 数字
       （中老年用户不需要看数字；数字只在展开层给想核对的人）

用法
    python test_r5_gi_evidence.py
"""
import os
import re
import tempfile

TMPDIR = tempfile.mkdtemp(prefix='gi_ev_')
DB_PATH = os.path.join(TMPDIR, 't.db').replace('\\', '/')
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH   # 必须早于 import app

from app import create_app                                    # noqa: E402
from utils.database import db                                 # noqa: E402
from models.user import User                                  # noqa: E402
from models.recipe import Recipe                              # noqa: E402
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


INGREDIENTS = ('- 白米饭 1 碗\n'
               '- 面条 100 克\n'
               '- 糙米 50 克\n'
               '- 鸡蛋 1 个\n'
               '- 神秘香料 少许')


def main():
    print('=' * 78)
    print('R5 第 4 项 · 升糖标签依据 · 自测')
    print('=' * 78)

    # ── 一、数据层 ────────────────────────────────────────────────────────
    print('\n=== 1) 食材表结构 ===')
    bad_shape = [(n, v) for n, v in da.FOODS.items() if not (isinstance(v, tuple) and len(v) == 4)]
    check('154 条全是 4 元组', not bad_shape, str(bad_shape[:3]))
    check('条数仍是 154（10-20 补 19 条高脂/加工肉）', len(da.FOODS) == 154, len(da.FOODS))

    bad_gi = [(n, v[2]) for n, v in da.FOODS.items()
              if v[2] is not None and not (0 < v[2] <= 110)]
    check('GI 值都在 0~110（没有离谱数）', not bad_gi, str(bad_gi[:5]))

    bad_src = [(n, v[3]) for n, v in da.FOODS.items()
               if (v[2] is None) != (v[3] is None)]
    check('GI 值与来源标记同进同退（不会一个有值一个没有）', not bad_src, str(bad_src[:5]))

    bad_src2 = [(n, v[3]) for n, v in da.FOODS.items()
                if v[3] is not None and v[3] not in ('exact', 'near')]
    check('来源标记只有 exact / near / None', not bad_src2, str(bad_src2[:5]))

    # 档位自洽：没被"按做法修正"的，档位必须等于按国标值算出来的档
    drift = []
    for name, (tier, _cat, gi, _src) in da.FOODS.items():
        if gi is None:
            continue
        if da._tier_of_gi(gi) != tier:
            drift.append((name, tier, gi, da._tier_of_gi(gi)))
    check('与国标不一致的只有 11 条（多一条或少一条都要重新核）',
          len(drift) == 11, '%d 条: %s' % (len(drift), drift))

    print('\n=== 2) 两条纠正 ===')
    check('糙米已改为「高」', da.FOODS['糙米'][0] == da.TIER_HIGH, da.FOODS['糙米'])
    check('糙米 GI 挂的是国标 87', da.FOODS['糙米'][2] == 87.0, da.FOODS['糙米'])
    check('芋头已改为「低」', da.FOODS['芋头'][0] == da.TIER_LOW, da.FOODS['芋头'])
    check('芋头 GI 挂的是国标 47.7', da.FOODS['芋头'][2] == 47.7, da.FOODS['芋头'])

    print('\n=== 3) build_advice 带出依据字段 ===')
    a = da.build_advice(INGREDIENTS)
    by = {it['name']: it for it in a['food_items']}
    check('白米饭带出国标 GI', by['白米饭']['gi_text'] == '83.2', by['白米饭'])
    check('白米饭不算冲突', by['白米饭']['gi_conflict'] is False, by['白米饭'])
    check('面条标记为「与国标冲突」', by['面条']['gi_conflict'] is True, by['面条'])
    check('面条带出国标低档', by['面条']['gi_tier_label'] == '低升糖', by['面条'])
    check('鸡蛋没有 GI（国标未收录）', by['鸡蛋']['gi'] is None, by['鸡蛋'])
    check('有国标值的 3 味（白米饭/面条/糙米）', a['gi_listed_count'] == 3, a['gi_listed_count'])
    check('国标未收录 1 味（鸡蛋）', a['gi_unlisted_count'] == 1, a['gi_unlisted_count'])
    check('按做法修正 1 味（面条）', a['gi_conflict_count'] == 1, a['gi_conflict_count'])
    check('完全认不出 1 项（神秘香料）', a['unmatched_count'] == 1, a['unmatched_count'])
    check('出处点名国标号', 'WS/T 601-2018' in a['gi_source'], a['gi_source'])
    check('界线写的是 55 / 70', '55' in a['gi_boundary'] and '70' in a['gi_boundary'],
          a['gi_boundary'])
    check('整整一道菜按最高档提醒（糙米为高）', a['tier'] == 'high', a['tier'])

    # ── 二、页面 ─────────────────────────────────────────────────────────
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    with app.app_context():
        db.create_all()
        u = User(username='gi_tester')
        u.set_password('pw123456')
        db.session.add(u)
        db.session.commit()
        rid = u.id
        r = Recipe(user_id=rid, title='依据测试菜', ingredients=INGREDIENTS,
                   instructions='1. 洗净\n2. 煮熟', servings=1)
        db.session.add(r)
        db.session.commit()
        recipe_id = r.id

    client.post('/api/user/login',
                data={'identifier': 'gi_tester', 'password': 'pw123456'},
                follow_redirects=True)
    page = html_of(client.get('/api/recipes/recipes/%s' % recipe_id))

    print('\n=== 4) 页面依据块 ===')
    check('页面有「凭什么这么判」入口', '凭什么这么判' in page)
    check('依据块是按钮触发且默认收起（body 带 hidden）',
          'gi-why-toggle' in page
          and 'aria-expanded="false"' in page
          and re.search(r'<div class="gi-why-body" hidden', page) is not None)
    check('写清标准号', 'WS/T 601-2018' in page)
    check('写清档位界线', '国标' in page and '55' in page and '70' in page)
    check('列出白米饭的国标值', 'GI 83.2' in page, page[page.find('gi-why-list'):][:200])
    check('列出糙米的国标值', 'GI 87' in page)
    check('面条那行说明「按国标算低档」', '按国标算低升糖档' in page)
    check('面条那行也说明「按家常做法取高」', '按家常做法取高升糖' in page)
    # 这份测试食材里只有「鸡蛋」是认出来但国标没数值的（青椒/胡萝卜不在食材里）
    check('明说国标没有数值的项数', '在国标 GI 表里没有数值' in page and '1 味' in page)
    check('未收录的食材被点名', '味食材（鸡蛋）在国标' in page)
    check('「依据」二字不重复', page.count('依据：</b>依据') == 0)
    check('主层说清"完全没认出来"的项数（不重复占两处）',
          '另有 1 项食材暂时没收录' in page and '1 项完全没认出来' not in page)
    check('写出「整道菜取最高的一味」这条规则', '取最高的那一味' in page)
    check('写出「按做法修正」的口径', '家常做法' in page)

    print('\n=== 5) 主层不被数字污染 ===')
    tags = re.findall(r'<span class="gi-tag gi-tag-\w+">([^<]*)</span>', page)
    check('食材标签仍在（白米饭 · 高升糖）', any('白米饭' in t for t in tags), str(tags[:5]))
    check('食材标签里没有 GI 数字', not any('GI' in t for t in tags), str(tags))
    badges = re.findall(r'<span class="gi-badge gi-badge-\w+">([^<]*)</span>', page)
    check('徽标仍是纯档位文案', badges and all('GI' not in b for b in badges), str(badges))

    print('\n=== 6) 兜底 ===')
    a2 = da.build_advice('神秘香料 少许')
    check('全认不出时照样给出处（不空白）', 'WS/T 601-2018' in a2['gi_source'], a2['gi_source'])
    check('全认不出时不算冲突', a2['gi_conflict_count'] == 0, a2['gi_conflict_count'])
    check('全认不出时档位 unknown', a2['tier'] == 'unknown', a2['tier'])

    print('\n' + '-' * 78)
    print('通过 %d/%d' % (passed, total))
    print('临时库：%s' % TMPDIR)
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
