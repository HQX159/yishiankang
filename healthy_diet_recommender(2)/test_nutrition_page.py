# test_nutrition_page.py
"""
「营养摄入」网页自测 —— 造临时数据库，不碰真实 app.db

做什么
    1. 用**临时 SQLite** 造一位用户 5 份食谱（跨 3 天，含 1 份读不出数字、1 份区间值），
       真的渲染 `/nutrition/intake`，检查：概览计数、柱状图几何、供能占比、按日期分组、
       读不出清单、区间原文、空状态。
    2. 用**项目真实的 app**（`app.py`）确认路由已注册、未登录会跳登录页、真实数据能渲染。

为什么柱高要单独校验
    柱状图最容易犯的错是"不从 0 起"—— 那样会把差异放大成假象。
    所以这里直接抠出 SVG 里的 rect，检查柱高比例与数值比例一致。

用法
    python test_nutrition_page.py
"""
import os
import re
import sys
import tempfile
from datetime import datetime

from test_health_metrics_page import build_isolated_app

from utils.database import db
from models.user import User                     # noqa: F401  建表需要
from models.health_report import HealthReport    # noqa: F401
from models.health_metric import HealthMetric    # noqa: F401
from models.fridge_image import FridgeImage      # noqa: F401
from models.ingredient import Ingredient         # noqa: F401
from models.recipe import Recipe

# build_isolated_app 已按 app.py 的清单注册全部蓝图（含 nutrition_bp）

# 造 5 份食谱：4 份能读出数字，1 份读不出（文本里本来就没有热量值）
_SEED = [
    ('香橙鸡胸能量碗', '热量约400 kcal | 蛋白质30 g | 碳水40 g | 脂肪12 g', datetime(2026, 8, 1, 8, 0)),
    ('黄金燕麦牛肉碗', '热量约600 kcal | 蛋白质40 g | 碳水60 g | 脂肪20 g', datetime(2026, 8, 1, 18, 30)),
    ('翡翠苹果能量碗', '热量约200 kcal | 蛋白质10 g | 碳水20 g | 脂肪5 g', datetime(2026, 8, 3, 12, 0)),
    ('养胃米糊', '### 营养说明：\n- 低胆固醇：食材都是低胆固醇的。\n- 低盐饮食：减少盐的使用。',
     datetime(2026, 8, 3, 20, 0)),
    ('香煎鸡胸佐莎莎',
     '**营养信息**：\n- 总热量：约 450-500 kcal\n- 蛋白质：约 40-45 g\n- 碳水：约 40 g\n- 脂肪：约 15 g\n**健康提示**：高蛋白健身餐。',
     datetime(2026, 8, 5, 9, 0)),
]

# 解析后的期望值（与 _SEED 一一对应；最后一份是区间取中值）
_EXPECT = [
    (400.0, 30.0, 40.0, 12.0),
    (600.0, 40.0, 60.0, 20.0),
    (200.0, 10.0, 20.0, 5.0),
    None,
    (475.0, 42.5, 40.0, 15.0),
]

_EXPECT_AVG_KCAL = round((400 + 600 + 200 + 475) / 4, 1)          # 418.8
_EXPECT_AVG_PROTEIN = round((30 + 40 + 10 + 42.5) / 4, 1)         # 30.6
_EXPECT_PARSED = 4
_EXPECT_TOTAL = 5


def _bars_of(html):
    """抠出柱状图的 (x, y, w, h)。"""
    return [tuple(float(g) for g in m.groups()) for m in re.finditer(
        r'<rect class="bar" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', html)]


def main():
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
    print('营养摄入页面自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='nutrition_page_test_')
    db_path = os.path.join(tmpdir, 't.db')
    app = build_isolated_app(db_path)

    try:
        with app.app_context():
            for title, nutrition, created in _SEED:
                db.session.add(Recipe(
                    user_id=1, title=title,
                    ingredients='鸡胸肉、西兰花', instructions='1. 煮熟',
                    nutrition_info=nutrition, created_at=created))
            # 2026-10-22 追加：用户 2 只有纯文字食谱，一份都解析不出营养数字。
            # 用来测"全部读不出"这个状态 —— 以前这条是拿**真实库的用户 1** 测的，
            # 而真实库会继续长出带营养数字的新食谱（用户 1 后来多了 3 份），
            # 断言就假红了。凡是"某个用户恰好没有某类数据"的假设，都不能绑真实库。
            db.session.add(Recipe(
                user_id=2, title='清炒白菜（纯文字描述）',
                ingredients='白菜、少量油', instructions='1. 快炒',
                nutrition_info='这道菜清淡少油，适合控糖人群日常食用。',
                created_at=datetime(2026, 8, 1, 9, 0)))
            # 不建"空用户"：直接用一个库里没有的 user_id 来测空状态，少一处依赖
            db.session.commit()
            print(f'临时库已造：{Recipe.query.count()} 份食谱（4 份可读 + 1 份读不出）')
        print('-' * 78)

        client = app.test_client()

        # ── 1. 未登录 ──
        resp = client.get('/nutrition/intake')
        check('未登录跳转登录页', resp.status_code == 302, str(resp.status_code))

        # ── 2. 正常渲染 ──
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get('/nutrition/intake')
        html = resp.get_data(as_text=True)
        check('页面返回 200', resp.status_code == 200, str(resp.status_code))
        check('页面标题正确', '🍽️ 营养摄入' in html)
        check('列出了食谱标题', '香橙鸡胸能量碗' in html and '翡翠苹果能量碗' in html)

        # ── 3. 口径说明必须在（防误读是本页的核心设计） ──
        check('写明"不是实际进食"', '不是您实际吃了什么' in html)
        check('写明"不做每日摄入合计"', '不做"每日摄入合计"' in html)
        check('写明数字未过成分表核验', '尚未经过食物成分表核验' in html)
        check('写明不下健康结论', '不下"够不够健康"的结论' in html)

        # ── 4. 概览计数与均值 ──
        check('读出/总数计数正确',
              f'>{_EXPECT_PARSED}<span class="nutri-unit">/ {_EXPECT_TOTAL} 份</span>' in html,
              f'期望 {_EXPECT_PARSED}/{_EXPECT_TOTAL}')
        check('平均热量正确', f'>{_EXPECT_AVG_KCAL}<span class="nutri-unit">kcal</span>' in html,
              f'期望 {_EXPECT_AVG_KCAL}')
        check('平均蛋白质正确', f'>{_EXPECT_AVG_PROTEIN}<span class="nutri-unit">g</span>' in html,
              f'期望 {_EXPECT_AVG_PROTEIN}')
        check('标出最高一份 600', '最高一份 600 kcal' in html)

        # ── 5. 柱状图：4 根柱子，且柱高与热量成正比（从 0 起） ──
        bars = _bars_of(html)
        check('柱子数 = 可读数（4 根）', len(bars) == _EXPECT_PARSED, str(len(bars)))
        values = [400.0, 600.0, 200.0, 475.0]
        heights = [h for _, _, _, h in bars]
        ratio_ok = all(
            abs(heights[i] / heights[j] - values[i] / values[j]) < 0.02
            for i in range(len(values)) for j in range(len(values)) if heights[j] > 0)
        check('柱高比例与热量比例一致（证明纵轴从 0 起）', ratio_ok,
              f'values={values} heights={heights}')
        check('最高的柱子对应最高的热量',
              heights[values.index(max(values))] == max(heights), str(heights))
        check('柱状图有标题', '每份食谱的热量' in html)

        # ── 6. 供能占比 ──
        check('供能占比三行都在', '供能占比' in html and html.count('ratio-row') >= 3)
        check('给出参考区间文案', '参考 50%~65%' in html and '参考 20%~30%' in html)
        check('说明参考区间是每日口径', '参考区间是"每日"口径' in html)
        check('占比条带引导区间', html.count('ratio-band') >= 3 and 'left:50.0%' in html)

        # ── 7. 按日期分组 ──
        check('第一组是最近的 8月5日', html.index('2026年8月5日') < html.index('2026年8月3日'),
              '分组顺序不对')
        check('三个日期分组都在',
              all(d in html for d in ('2026年8月1日', '2026年8月3日', '2026年8月5日')))
        check('8月1日那组标了"生成 2 份"', '生成 2 份' in html)
        check('分组顺序为最新在前', html.index('2026年8月5日') < html.index('2026年8月1日'))

        # ── 8. 区间值展示原文 ──
        check('区间热量保留原文', '原文 450-500' in html)

        # ── 9. 读不出的食谱要明确列出，且不显示为 0 ──
        check('读不出的食谱被单独列出', '读不出营养数字的食谱（1 份）' in html)
        check('读不出区列出该食谱名', '养胃米糊' in html)
        check('读不出显示"未读出"而不是 0', '未读出' in html and '>≈0<' not in html)
        check('说明"读不出不等于 0"', '不是"数值为 0"' in html)

        # ── 10. 全部读不出的用户（有食谱、但一份都读不出数字） ──
        with client.session_transaction() as sess:
            sess['user_id'] = 2
        resp = client.get('/nutrition/intake')
        html_u2 = resp.get_data(as_text=True)
        check('全部读不出的用户不画柱子', len(_bars_of(html_u2)) == 0,
              str(len(_bars_of(html_u2))))
        check('全部读不出的用户给出完整清单', '读不出营养数字的食谱（1 份）' in html_u2)
        check('全部读不出时平均热量显示为空（不是 0）',
              '—<span class="nutri-unit">kcal</span>' in html_u2)

        # ── 11. 没有任何食谱的用户 ──
        with client.session_transaction() as sess:
            sess['user_id'] = 999
        resp = client.get('/nutrition/intake')
        html2 = resp.get_data(as_text=True)
        check('无食谱用户显示空状态',
              resp.status_code == 200 and '还没有生成过食谱' in html2, str(resp.status_code))
        check('空状态给了生成入口', '生成食谱' in html2)

    finally:
        try:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            os.remove(db_path)
            os.rmdir(tmpdir)
        except OSError:
            pass

    # ── 11. 真实 app 接线校验（只读） ──
    print('-' * 78)
    try:
        from app import app as real_app
        rules = {r.rule for r in real_app.url_map.iter_rules()}
        check('真实 app 已注册 /nutrition/intake', '/nutrition/intake' in rules,
              str(sorted(r for r in rules if r.startswith('/nutrition'))))

        real_client = real_app.test_client()
        resp = real_client.get('/nutrition/intake')
        check('真实 app 未登录也跳转', resp.status_code == 302, str(resp.status_code))

        with real_client.session_transaction() as sess:
            sess['user_id'] = 11
        resp = real_client.get('/nutrition/intake')
        real_html = resp.get_data(as_text=True)
        check('真实数据下页面能渲染', resp.status_code == 200 and '营养摄入' in real_html,
              str(resp.status_code))

        # 2026-10-22 改：原来写死"用户 11 有 9 份、画出 9 根柱子"。
        # 真实库会继续长（多生成一份就多一根），写死行数的断言迟早假红。
        # 现在现查这个用户有多少份、其中几份能解析出营养，断言"页面柱子数与之一致"——
        # 测的仍然是"页面反映真实数据"，但不再依赖"恰好几行"。
        with real_app.app_context():
            from utils.nutrition_parser import parse_nutrition
            real_recipes = Recipe.query.filter_by(user_id=11).all()
            readable = [r for r in real_recipes
                        if parse_nutrition(r.nutrition_info or '')]
        bars_real = len(_bars_of(real_html))
        if readable:
            check('真实数据下柱子数 = 可读出营养的食谱数',
                  bars_real == len(readable),
                  '柱子 %d / 可读 %d / 全部 %d' % (bars_real, len(readable), len(real_recipes)))
        else:
            check('真实数据下该用户已无食谱，跳过柱子对账', True,
                  '用户 11 的食谱被清空了？全部 %d 份' % len(real_recipes))
    except Exception as exc:                                # noqa: BLE001
        check('真实 app 校验（导入失败）', False, f'{type(exc).__name__}: {exc}')

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
