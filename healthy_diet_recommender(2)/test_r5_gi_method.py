# test_r5_gi_method.py
"""R5 第 4 项 E · 「升糖标签是怎么来的」整页说明 · 自测

这一页回答的是老师那句质疑里最硬的一半：
    「放出来就要可信 —— 怎么让用户相信？」
展开层（_gi_why.html）只说"这道菜这几味"，用户仍会问：
    「你们整张表凭什么？是不是挑着对自己有利的写？」

所以这里验的是**这一页敢不敢把不利信息摆出来**：
    1. 匿名就能看（不必注册）—— 把可信度锁在登录门后等于不给看
    2. 三个数字自洽，且跟着 FOODS 走（模板里一个都不写死）
    3. 与国标不一致的 11 条**全部**列出，每条都有具体理由
    4. 顺序：国标值最低的排前面（最反直觉的先解释，不藏）
    5. 未收录的照实说"不给数字"，三条规则齐全
    6. 三条承诺里有「不悄悄改数字」
    7. 文案里不留 markdown 星号（`**生甘薯**` 会原样显示，中老年用户看不懂）

用法
    python test_r5_gi_method.py
"""
import os
import re
import tempfile

TMPDIR = tempfile.mkdtemp(prefix='gi_method_')
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


def text_of(html):
    """粗洗成纯文本：只用来判断"这句话有没有出现"，不追求精确。"""
    t = re.sub(r'<[^>]+>', '', html)
    return re.sub(r'\s+', ' ', t)


INGREDIENTS = '- 白米饭 1 碗\n- 面条 100 克\n- 鸡蛋 1 个'


def main():
    print('=' * 78)
    print('R5 第 4 项 E · 升糖标签说明页 · 自测')
    print('=' * 78)

    m = da.build_method_page()

    # ── 一、数据层 ────────────────────────────────────────────────────────
    print('\n=== 1) 数据自洽 ===')
    check('三类相加等于总数',
          m['exact_count'] + m['near_count'] + m['unlisted_count'] == m['total'],
          (m['exact_count'], m['near_count'], m['unlisted_count'], m['total']))
    check('「有国标值」= 直接对应 + 同类参考',
          m['listed_count'] == m['exact_count'] + m['near_count'])
    check('总数跟着 FOODS 走', m['total'] == len(da.FOODS), (m['total'], len(da.FOODS)))
    check('冲突条数与 FOODS 实际冲突数一致',
          m['conflict_count'] == 11, m['conflict_count'])
    check('每条冲突都有理由（不能只列数字不解释）',
          all(r['why'] for r in m['conflicts']),
          [r['name'] for r in m['conflicts'] if not r['why']])
    check('冲突按国标值升序', [r['gi'] for r in m['conflicts']]
          == sorted(r['gi'] for r in m['conflicts']),
          [r['gi'] for r in m['conflicts']])
    check('最反直觉的面条（国标 37）排第一',
          m['conflicts'][0]['name'] == '面条', m['conflicts'][0]['name'])
    check('面条的理由点出国标自相矛盾（37 与 82 都写了）',
          '37' in m['conflicts'][0]['why'] and '82' in m['conflicts'][0]['why'],
          m['conflicts'][0]['why'])
    check('每条冲突都同时给出「国标档」和「我们的档」',
          all(r['gi_tier_label'] and r['our_label'] for r in m['conflicts']))
    check('冲突里写的档位与 FOODS 一致',
          all(da.TIER_LABEL[da.FOODS[r['name']][0]] == r['our_label'] for r in m['conflicts']))

    # 文案卫生：markdown 星号在页面上会原样显示（中老年用户会当成乱码）
    stars = [r['name'] for r in m['conflicts'] if '**' in r['why']]
    check('理由里没有 markdown 星号', not stars, stars)
    check('承诺里也没有 markdown 星号', not any('**' in s for s in m['promises']))

    # ── 二、匿名可访问（这是刻意的设计，不是漏了登录）────────────────────
    print('\n=== 2) 匿名可看 ===')
    app = create_app()
    app.config['TESTING'] = True
    anon = app.test_client()          # 不登录
    resp = anon.get('/api/recipes/gi-method')
    check('未登录也能打开（不跳登录页）', resp.status_code == 200,
          '%s %s' % (resp.status_code, resp.headers.get('Location')))
    page = html_of(resp)
    check('不是重定向到登录', 'login' not in (resp.headers.get('Location') or ''))

    # ── 三、页面内容 ──────────────────────────────────────────────────────
    print('\n=== 3) 页面内容 ===')
    check('标题点明这一页在讲什么', '升糖标签是怎么来的' in page)
    check('写清标准号', 'WS/T 601-2018' in page, page[:200])
    check('写清分档界线原话', '55' in page and '70' in page and '低' in page)
    check('说明「妊娠期」这个名字为什么不影响数据可用',
          '妊娠期' in page and '无关' in page)

    check('三个数字都渲染出来（总数）', '>%d<' % m['total'] in page or str(m['total']) in page)
    check('三个数字都渲染出来（有国标值）', str(m['listed_count']) in page)
    check('三个数字都渲染出来（未收录）', str(m['unlisted_count']) in page)

    print('\n=== 4) 11 条冲突全部列出 ===')
    body = text_of(page)
    missing = [r['name'] for r in m['conflicts'] if r['name'] not in body]
    check('11 条一条不落', not missing, missing)
    check('每条都写了国标值', all(('GI %s' % r['gi_text']) in page for r in m['conflicts']),
          [r['name'] for r in m['conflicts'] if ('GI %s' % r['gi_text']) not in page])
    check('每条都写了「按国标算 X，我们标 Y」',
          page.count('按国标算') == m['conflict_count'], page.count('按国标算'))
    check('每条的理由都渲染进页面',
          all(r['why'][:12] in body for r in m['conflicts']),
          [r['name'] for r in m['conflicts'] if r['why'][:12] not in body])
    check('冲突条目在页面上是有序列表（<ol>）',
          re.search(r'<ol class="gi-method-conflicts"', page) is not None)

    print('\n=== 5) 未收录与承诺 ===')
    check('明说国标没收录的条数', str(m['unlisted_count']) in page)
    check('给出未收录的具体例子', m['unlisted_sample'][0] in body, m['unlisted_sample'][:3])
    check('「不编数字」写出来了', '不编数字' in page)
    check('「不悄悄改数字」写出来了', '不悄悄改数字' in page)
    check('免责：不替代医生', '不替代医生' in page)
    check('三条承诺齐全', all(p[:10] in body for p in m['promises']),
          [p[:10] for p in m['promises'] if p[:10] not in body])

    print('\n=== 6) 与详情页打通 ===')
    with app.app_context():
        db.create_all()
        u = User(username='method_tester')
        u.set_password('pw123456')
        db.session.add(u)
        db.session.commit()
        r = Recipe(user_id=u.id, title='说明页测试菜', ingredients=INGREDIENTS,
                   instructions='1. 洗净\n2. 煮熟', servings=1)
        db.session.add(r)
        db.session.commit()
        recipe_id = r.id

    c2 = app.test_client()
    c2.post('/api/user/login',
            data={'identifier': 'method_tester', 'password': 'pw123456'},
            follow_redirects=True)
    detail = html_of(c2.get('/api/recipes/recipes/%s' % recipe_id))
    check('详情页依据块里有指向说明页的链接',
          '/api/recipes/gi-method' in detail, detail[-400:])
    check('链接文案说清点进去能看到什么',
          '整张表是怎么来的' in detail)
    check('链接在 <details> 里（不打扰不看的人）',
          detail.find('/api/recipes/gi-method') > detail.find('<details class="gi-why"'))

    print('\n' + '-' * 78)
    print('通过 %d/%d' % (passed, total))
    return passed == total


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(0 if main() else 1)
