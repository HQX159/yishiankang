# test_upload_risk_page.py
"""
「上传报告即提示风险」自测 —— 造临时数据库，不碰真实 app.db

做什么
    1. 网页版 / 小程序版**上传报告**时，若报告里有危值 → 立刻给提示，
       不用等用户点"生成食谱"才发现。
    2. 确认最关键的一点：**上传本身仍然是成功的** —— 报告存下来了、指标也落库了，
       只是顺带多一条提示。把上传做成"失败"是最容易犯的错，这里专门守住。
    3. 偏高（warn）只提醒、不拦；正常报告不打扰（宁可漏报不可误报那一侧的回归）。
    4. 文案分场景：上传时不能说"因此本次没有生成食谱"——那会儿还没到生成那一步。
    5. 三条成功路径（危险 / 偏高 / 正常）**都停在本页**并展示指标面板，不 redirect ——
       报告已经传上去了，用户此刻最想看的是"这份报告读出了什么数字"。

为什么 mock OCR
    真实 OCR 依赖 pytesseract 与本机语言包，离线跑不稳。
    这里把 PDF 解析换成固定文本；被测的是"上传之后的判断与展示"，不是 OCR 本身。

用法
    python test_upload_risk_page.py
"""
import os
import shutil
import sys
import tempfile
import types
from io import BytesIO

from test_health_metrics_page import build_isolated_app

from utils.database import db
from utils.auth import generate_jwt_token
from models.user import User                     # noqa: F401  建表需要
from models.health_report import HealthReport
from models.health_metric import HealthMetric
from models.recipe import Recipe                 # noqa: F401
from models.fridge_image import FridgeImage      # noqa: F401
from models.ingredient import Ingredient         # noqa: F401

import routes.upload_routes as upload_routes
import routes.api_v1 as api_v1

# 危值：血糖 17.2 + 血压 185/115 → 必然 blocked
_CRITICAL_TEXT = (
    "身高 170 cm 体重 78 kg 空腹血糖 17.2 mmol/L 血压 185/115 mmHg 总胆固醇 6.4 mmol/L"
)
# 偏高但未到危险：空腹血糖 8.9 → 只提醒
_WARN_TEXT = "身高 170 cm 体重 78 kg 空腹血糖 8.9 mmol/L 总胆固醇 6.4 mmol/L"
# 全部正常 → 不打扰
_NORMAL_TEXT = "身高 170 cm 体重 70 kg 空腹血糖 5.4 mmol/L 血压 118/76 mmHg"

_WEB_UPLOAD = '/api/upload/upload_health_report'
_API_UPLOAD = '/api/v1/upload/health_report'

# OCR 替身：读这里的内容当作"从报告里提取出的文字"
_STATE = {'text': _NORMAL_TEXT}


class _FakePage:
    def extract_text(self):
        return _STATE['text']


class _FakePdfReader:
    def __init__(self, f):
        self.pages = [_FakePage()]


# 做成"模块对象"，因为 api_v1 里是**函数内** `import PyPDF2`（走 sys.modules），
# 而 upload_routes 里是模块级 import（走模块属性）—— 两处都得替掉。
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
    print('「上传报告即提示风险」自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='upload_risk_test_')
    db_path = os.path.join(tmpdir, 'u.db')
    fake_pdf = os.path.join(tmpdir, 'report.pdf')
    with open(fake_pdf, 'wb') as fh:
        fh.write(b'%PDF-1.4 fake')

    app = build_isolated_app(db_path)
    # upload_routes 会读这两个配置项，隔离 app 里没带，补上
    app.config['UPLOAD_FOLDER_HEALTH_REPORTS'] = tmpdir
    app.config['UPLOAD_FOLDER_FRIDGE_IMAGES'] = tmpdir

    # ── 替换 OCR 与落盘（只在本进程内生效，测完恢复） ──
    # 2026-10-14：解析已下沉到 utils/report_ocr，两个路由模块都不再 import PyPDF2。
    # 桩统一打在 sys.modules 上：report_ocr 是函数内 import，每次调用重新从 sys.modules 取。
    real_pypdf2_module = sys.modules.get('PyPDF2')
    real_upload_save = upload_routes.save_uploaded_file
    real_api_save = api_v1.save_uploaded_file

    sys.modules['PyPDF2'] = _FakePyPDF2
    upload_routes.save_uploaded_file = lambda file, folder: (fake_pdf, 'report.pdf')
    api_v1.save_uploaded_file = lambda file, folder: (fake_pdf, 'report.pdf')

    client = app.test_client()

    def read_flashes():
        with client.session_transaction() as sess:
            return [list(x) for x in sess.get('_flashes', [])]

    def upload(text, api=False, user_id=1):
        _STATE['text'] = text
        data = {'file': (BytesIO(b'%PDF-1.4'), 'report.pdf')}
        if api:
            with app.app_context():
                token = generate_jwt_token(user_id)
            return client.post(_API_UPLOAD, data=data,
                               content_type='multipart/form-data',
                               headers={'Authorization': f'Bearer {token}'})
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            # 隔离 app 的 /dashboard 只返回纯文本、不渲染模板，flash 不会被消费，
            # 会一直堆在同一个 session 里 —— 每次上传前清掉，否则上一轮的消息串到这一轮
            sess.pop('_flashes', None)
        return client.post(_WEB_UPLOAD, data=data, content_type='multipart/form-data')

    try:
        # ══════════ 一、网页版 · 危值报告 ══════════
        resp = upload(_CRITICAL_TEXT, api=False, user_id=1)
        html = resp.get_data(as_text=True)

        check('网页上传危值报告返回 200（不是错误页/重定向）',
              resp.status_code == 200, str(resp.status_code))
        check('报告确实上传成功（flash 仍是 success）',
              any(c == 'success' for c, _ in read_flashes())
              or '健康报告上传成功' in html)
        check('出现就医卡片（专属标题）', '报告中的指标需要尽快就医' in html)
        check('没有沿用生成场景的标题', '本次未生成食谱' not in html)
        check('文案不说"没有生成食谱"', '没有生成食谱' not in html)
        check('逐条列出超标项', 'risk-alert-item' in html)
        check('血糖危值可见', '17.2' in html)
        check('血压危象可见', '185' in html and '115' in html)
        check('超标项打「需就医」标签', '需就医' in html)
        check('给出就诊科室', '内分泌科' in html and '心血管内科' in html)
        check('提醒不要自行改药', '不要自行加量或停药' in html)
        check('附免责声明', '不能替代医师' in html)
        # ── 2026-09-15 合并成一页后这几条**有意翻转**：上传与看指标现在同一个页面，
        #    表单不再收起（用户随时可以再传一份），就医卡片的按钮也改成锚到本页指标区。 ──
        check('表单仍在同一页（合并后不再收起）', 'id="selectBtn"' in html)
        check('就医卡片按钮锚到本页指标区（不再送人去别的页面）', 'href="#metrics"' in html)
        check('兜底出口仍有「生成食谱」', '生成食谱' in html)
        check('不再提示"上传新的健康报告"', '上传新的健康报告' not in html)

        # ══════════ 二、网页版 · 偏高 / 正常 ══════════
        # 注意：2026-09-15 起，三条成功路径**统一停在本页**（页内出指标面板），
        # 不再 redirect 到仪表盘 —— 所以这里断言 200 + 面板，而不是 302。
        # 另外 flash 现在会被 base.html 真正渲染消费，断言要改看 HTML 文本。
        resp = upload(_WARN_TEXT, user_id=2)
        html = resp.get_data(as_text=True)
        check('网页上传偏高报告返回 200（上传照常成功）',
              resp.status_code == 200, str(resp.status_code))
        check('偏高给出 warning 提示（写在页面上）',
              '8.9' in html, '页面里没找到偏高提示的数值')
        check('偏高不摆就医卡片（只提示，不拦截）',
              'risk-alert-item' not in html)
        check('偏高也能就地看到指标面板', 'class="metric-grid"' in html)
        # 同上：合并成一页后表单不再收起
        check('偏高时表单仍在同一页', 'id="selectBtn"' in html)
        check('偏高时提示上传成功', '健康报告上传成功' in html)

        resp = upload(_NORMAL_TEXT, user_id=3)
        html = resp.get_data(as_text=True)
        check('网页上传正常报告返回 200（上传照常成功）',
              resp.status_code == 200, str(resp.status_code))
        check('正常报告提示上传成功', '健康报告上传成功' in html)
        check('正常报告不出现就医卡片', 'risk-alert-item' not in html)
        check('正常报告也能就地看到指标（血糖 5.4）', '>5.4<' in html, '未找到 5.4')

        # ══════════ 三、小程序接口 · 危值报告 ══════════
        resp = upload(_CRITICAL_TEXT, api=True, user_id=1)
        payload = resp.get_json() or {}
        data = payload.get('data') or {}

        check('API 上传返回 200', resp.status_code == 200, str(resp.status_code))
        check('API code = 0（上传是成功的，不是错误）',
              payload.get('code') == 0, str(payload.get('code')))
        check('API 返回了报告 id（确实落库）',
              bool((data.get('report') or {}).get('id')),
              str(data.get('report')))
        risk = data.get('risk') or {}
        check('API 带 data.risk', bool(risk), str(list(data.keys())))
        check('API risk.blocked = true', risk.get('blocked') is True, str(risk.get('blocked')))
        check('API risk.level = critical', risk.get('level') == 'critical',
              str(risk.get('level')))
        check('API risk 含多条 findings',
              len(risk.get('findings') or []) >= 2, str(risk.get('findings'))[:120])
        check('API 文案不说"没有生成食谱"',
              '没有生成食谱' not in (risk.get('message') or ''))
        check('API 带免责声明', bool(risk.get('disclaimer')))

        # ══════════ 四、小程序接口 · 正常报告 ══════════
        resp = upload(_NORMAL_TEXT, api=True, user_id=3)
        data = (resp.get_json() or {}).get('data') or {}
        risk = data.get('risk') or {}
        check('API 正常报告 code = 0',
              (resp.get_json() or {}).get('code') == 0)
        check('API 正常报告 blocked = false', risk.get('blocked') is False,
              str(risk.get('blocked')))
        check('API 正常报告 level = ok', risk.get('level') == 'ok',
              str(risk.get('level')))
        check('API 正常报告 findings 为空', risk.get('findings') == [])

        # ══════════ 五、数据确实写进去了（上传没被"提示"连累） ══════════
        with app.app_context():
            # 注意：落库的 content_text 末尾会多一个 "\n"（PDF 逐页拼接所致），
            # 所以这里用 startswith 而不是等值比较，免得测试自己坑自己
            critical_rows = HealthReport.query.filter(
                HealthReport.content_text.startswith(_CRITICAL_TEXT)).all()
            check('危值报告在库里（网页 + API 各一条）',
                  len(critical_rows) == 2, str(len(critical_rows)))
            check('正常报告也在库里',
                  HealthReport.query.filter(
                      HealthReport.content_text.startswith(_NORMAL_TEXT)
                  ).count() == 2)

            metric_rows = 0
            for r in critical_rows:
                metric_rows += HealthMetric.query.filter_by(report_id=r.id).count()
            check('危值报告的指标也落库了（提示不影响落库）',
                  metric_rows > 0, str(metric_rows))

        # ══════════ 六、真实 app 的模板能渲染（只读回归） ══════════
        print('-' * 78)
        _probe_risk = {
            'level': 'critical', 'blocked': True,
            'message': '测试文案：部分指标已达需要及时就医的水平',
            'findings': [{'level': 'critical', 'name': '空腹血糖', 'value': 17.2,
                          'unit': 'mmol/L',
                          'message': '空腹血糖 17.2 mmol/L，已达危险水平，需尽快就医'}],
            'disclaimer': '不能替代医师的诊断与处方。',
        }
        try:
            from app import app as real_app
            from flask import render_template

            with real_app.test_request_context('/'):
                gen = render_template('recipe/generate.html', risk=_probe_risk)
            check('生成页默认标题未被改坏',
                  '建议尽快就医，本次未生成食谱' in gen)
            check('生成页按钮仍是「上传新报告 + 查看指标」',
                  '上传新的健康报告' in gen and '查看我的健康指标' in gen)

            with real_app.test_request_context('/'):
                up = render_template('upload/upload_health.html', risk=_probe_risk)
            check('上传页用专属标题', '报告中的指标需要尽快就医' in up)
            check('上传页渲染出卡片', 'risk-alert-item' in up)
            check('上传页表单仍在（同页）', 'id="selectBtn"' in up)
            check('上传页就医按钮锚到本页指标区', 'href="#metrics"' in up)
            check('上传页不再出现「上传新的健康报告」按钮',
                  '上传新的健康报告' not in up)
            # 故意**不传 metrics**：调用方漏传时应该是"显示空状态"，不能 500。
            # 这一段是模板的健壮性回归（曾经因为 Jinja Undefined 取属性直接炸）。
            check('漏传 metrics 时指标区显示空状态而不是报错',
                  '还没有可展示的指标' in up)

            with real_app.test_request_context('/'):
                plain = render_template('upload/upload_health.html')
            check('不带 risk 时上传页照常显示表单', 'id="selectBtn"' in plain)
            check('不带 risk 时没有卡片', 'risk-alert-item' not in plain)
        except Exception as exc:                            # noqa: BLE001
            check('真实 app 模板渲染', False, f'{type(exc).__name__}: {exc}')

    finally:
        upload_routes.save_uploaded_file = real_upload_save
        api_v1.save_uploaded_file = real_api_save
        if real_pypdf2_module is not None:
            sys.modules['PyPDF2'] = real_pypdf2_module
        else:
            sys.modules.pop('PyPDF2', None)
        try:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
