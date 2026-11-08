# test_r5_bare_glucose.py
"""
「报告里只写『血糖 9.2』」端到端自测 —— 造临时数据库，不碰真实 app.db

背景（2026-10-22 修）
    用户在报告 / 血糖仪截图里只写「血糖 9.2 mmol/L」，没注明是空腹还是餐后。
    改前这条链是**断的**：
      ① `report_parser` 故意不收裸写的「血糖」（怕抢走「随机血糖」「餐后血糖」）
         → 抽 0 项，指标面板空着，页面还说"看不出血糖情况"；
      ② `risk_guard` 虽然认裸「血糖」，但按**餐后**阈值 11.1 判 → 9.2 不够格，`risk=ok`。
    文字明明读对了，数字却没落地、也不提示。这是安全空档，不是体验问题。

本文件钉住的口径
    1. 裸写「血糖」归到独立 key `unspecified_glucose`，名字里带「未注明时点」，
       **不冒称「空腹血糖」**（猜错时点就等于替用户改了一个医学前提）。
    2. 阈值取空腹的 7.0（更严的一侧）→ 9.2 要提示；但文案必须交代"没写明时点"，
       并说明餐后测的通常不算高，避免把餐后 9.2 的用户吓一跳。
    3. 带明确时点的写法（空腹 / 随机 / 餐后 / 血糖(空腹)）**一条都不能被抢走**。
    4. 「低血糖 3.2」是症状叙述，不当检验读数。
    5. 只写裸血糖的报告**不能**被判成"看不出血糖情况"（自己抽到了还说没有）。

用法
    python test_r5_bare_glucose.py
"""
import os
import sys
import tempfile
import types
from io import BytesIO
from html.parser import HTMLParser


class MetricCards(HTMLParser):
    """Read card classes as tokens so adding a link class does not break checks."""
    def __init__(self):
        super().__init__()
        self.cards = []
        self.tag = None
        self.depth = 0
        self.classes = set()
        self.text = []

    def handle_starttag(self, tag, attrs):
        classes = set(dict(attrs).get('class', '').split())
        if self.tag is None and 'metric-card' in classes:
            self.tag, self.depth, self.classes, self.text = tag, 1, classes, []
        elif self.tag == tag:
            self.depth += 1

    def handle_data(self, data):
        if self.tag is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if self.tag == tag:
            self.depth -= 1
            if self.depth == 0:
                self.cards.append((self.classes, ''.join(self.text)))
                self.tag = None


def has_glucose_card(html, status):
    parser = MetricCards()
    parser.feed(html)
    return any(status in classes and '血糖（未注明时点）' in text
               for classes, text in parser.cards)

from test_health_metrics_page import build_isolated_app

from utils.database import db
from models.user import User                     # noqa: F401  建表需要
from models.health_report import HealthReport     # noqa: F401
from models.health_metric import HealthMetric
from models.recipe import Recipe                  # noqa: F401
from models.fridge_image import FridgeImage        # noqa: F401
from models.ingredient import Ingredient           # noqa: F401

import routes.upload_routes as upload_routes

_WEB_UPLOAD = '/api/upload/upload_health_report'

# 只写裸血糖 · 偏高（改前：抽 0 项 + 不报警）
_BARE_HIGH = '血糖 9.2 mmol/L'
# 只写裸血糖 · 正常（不能误报）
_BARE_OK = '血糖 5.6 mmol/L'
# 带明确时点，一条都不该被裸血糖抢走
_TIMED = '空腹血糖 5.4 mmol/L  随机血糖 9.0 mmol/L  餐后2小时血糖 12.3 mmol/L'
# 症状叙述，不是检验读数
_LOW_NARRATION = '今日出现低血糖 3.2 mmol/L，进食后缓解'

_STATE = {'text': _BARE_HIGH}


class _FakePage:
    def extract_text(self):
        return _STATE['text']


class _FakePdfReader:
    def __init__(self, f):
        self.pages = [_FakePage()]


_FakePyPDF2 = types.ModuleType('PyPDF2')
_FakePyPDF2.PdfReader = _FakePdfReader


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
    print('「报告只写血糖 9.2」端到端自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='bare_glucose_test_')
    db_path = os.path.join(tmpdir, 'm.db')
    fake_pdf = os.path.join(tmpdir, 'report.pdf')
    with open(fake_pdf, 'wb') as fh:
        fh.write(b'%PDF-1.4 fake')

    app = build_isolated_app(db_path)
    app.config['UPLOAD_FOLDER_HEALTH_REPORTS'] = tmpdir
    app.config['UPLOAD_FOLDER_FRIDGE_IMAGES'] = tmpdir

    real_pypdf2 = sys.modules.get('PyPDF2')
    real_save = upload_routes.save_uploaded_file
    sys.modules['PyPDF2'] = _FakePyPDF2
    upload_routes.save_uploaded_file = lambda file, folder: (fake_pdf, 'report.pdf')

    client = app.test_client()

    def upload(text, user_id):
        _STATE['text'] = text
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess.pop('_flashes', None)
        data = {'file': (BytesIO(b'%PDF-1.4'), 'report.pdf')}
        return client.post(_WEB_UPLOAD, data=data, content_type='multipart/form-data')

    try:
        # ══════════ 一、裸血糖 9.2：抽得到、标得黄、说得清 ══════════
        resp = upload(_BARE_HIGH, user_id=1)
        html = resp.get_data(as_text=True)
        check('上传后返回 200', resp.status_code == 200, str(resp.status_code))

        with app.app_context():
            rows = HealthMetric.query.filter_by(user_id=1).all()
            keys = {r.metric_key: r.value for r in rows}
            # 只落一条，且 key 是独立的「未注明时点」，不能写成 fasting_glucose
            check('落库 key 是 unspecified_glucose', 'unspecified_glucose' in keys, str(keys))
            check('落库值是 9.2', keys.get('unspecified_glucose') == 9.2, str(keys))
            check('没有冒称空腹血糖', 'fasting_glucose' not in keys, str(keys))

        check('卡片上写「血糖（未注明时点）」', '血糖（未注明时点）' in html)
        check('卡片值就是 9.2', 'metric-value">9.2' in html, '未找到 9.2')
        check('这张卡片标黄（is-warn）', has_glucose_card(html, 'is-warn'))
        # warn 级走 flash 提示（红卡只留给 critical），所以这里断言 flash 里的话
        check('页面顶部出现了偏高提示', 'flash-message' in html and '偏高' in html)
        check('warn 不弹红卡（红卡是 critical 的事）', 'class="risk-alert"' not in html)
        check('提示里交代了「没写明」', '没写明' in html)
        check('提示里说明了餐后测的通常不算高', '餐后' in html and '不算高' in html)
        check('没有被判成"看不出血糖情况"', '看不出血糖' not in html)
        check('没有被判成"读不出指标"', '这份报告里没有读出' not in html)

        # ══════════ 二、就诊沟通清单也会出现（warn 触发） ══════════
        check('出现就诊沟通清单', 'class="visit-card' in html, '未找到 visit-card')

        # ══════════ 三、裸血糖正常值不误报 ══════════
        resp = upload(_BARE_OK, user_id=2)
        html = resp.get_data(as_text=True)
        with app.app_context():
            rows = HealthMetric.query.filter_by(user_id=2).all()
            keys = {r.metric_key: r.value for r in rows}
            check('正常值时也落库', keys.get('unspecified_glucose') == 5.6, str(keys))
        check('5.6 这张卡片标绿（is-ok）', has_glucose_card(html, 'is-ok'))
        check('5.6 不弹风险卡片', 'class="risk-alert"' not in html)
        check('5.6 不弹就诊清单', 'class="visit-card' not in html)

        # ══════════ 四、带时点的写法一条都不能被抢走 ══════════
        resp = upload(_TIMED, user_id=3)
        with app.app_context():
            rows = HealthMetric.query.filter_by(user_id=3).all()
            keys = {r.metric_key: r.value for r in rows}
            check('空腹血糖仍归 fasting_glucose', keys.get('fasting_glucose') == 5.4, str(keys))
            check('随机血糖仍归 random_glucose', keys.get('random_glucose') == 9.0, str(keys))
            check('餐后血糖仍归 postprandial_glucose',
                  keys.get('postprandial_glucose') == 12.3, str(keys))
            check('没有多出一条 unspecified_glucose',
                  'unspecified_glucose' not in keys, str(keys))

        # ══════════ 五、低血糖叙述不当读数 ══════════
        resp = upload(_LOW_NARRATION, user_id=4)
        with app.app_context():
            rows = HealthMetric.query.filter_by(user_id=4).all()
            keys = {r.metric_key: r.value for r in rows}
            check('「低血糖 3.2」没有落成血糖读数',
                  'unspecified_glucose' not in keys, str(keys))

        # ══════════ 六、用户隔离：别人的值不会串过来 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 4
        resp = client.get(_WEB_UPLOAD)
        html = resp.get_data(as_text=True)
        check('用户 4 的页面看不到用户 1 的 9.2', 'metric-value">9.2' not in html)

    finally:
        upload_routes.save_uploaded_file = real_save
        if real_pypdf2 is not None:
            sys.modules['PyPDF2'] = real_pypdf2
        else:
            sys.modules.pop('PyPDF2', None)
        # Windows 下不 dispose 会 PermissionError
        try:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        except Exception:
            pass

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
