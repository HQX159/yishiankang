# test_upload_metrics_inline.py
"""
「健康报告」页上传后的展示自测 —— 造临时数据库，不碰真实 app.db

背景（2026-09-15 改动）
    「上传健康报告」与「查看健康指标」原先是一页 + 一个独立页。现在合并成一个页面：
    上传区在上、指标区在下。上传成功**不跳转**，只是原地多出成功提示与（必要时）就医卡片。
    因此本文件里有几条断言是**有意翻转**的，见下。

本文件负责「走一遍上传流程」的那些事
    1. 上传成功后停在本页，并就地画出刚读出来的指标（卡片 + 趋势图）。
    2. 上传表单**不再收起** —— 页面是同一个，用户可能马上要再传一份。
    3. 不能读不出数字就瞎编：报告里确实没有数字时给"读不出"的说明，不给假数。
    4. 数据按用户隔离；危险报告是"就医卡片 + 指标面板"同时出现。
    5. 一行指标概览（共几项 / 几项需就医）。
    6. 把系统读到的报告原文摆出来；且"读不出"的口径**不随刷新而改变**
       （2026-10-12 修：旧版按"这次是否刚上传"判，刷新一下就说回"还没上传过"）。

    合并页 **GET** 时的渲染由 `test_health_metrics_page.py` 覆盖（那条链不需要 mock OCR）。

为什么 mock OCR
    同 test_upload_risk_page.py：真实 OCR 依赖本机语言包，离线跑不稳。
    被测的是"上传之后的展示"，不是 OCR。

为什么趋势那一段用 save_metrics_for_text 预埋历史点
    连着传两份报告的 measured_at 可能落在同一秒，排序就变成不确定的，
    断言会时好时坏。预埋明确日期的历史点，再传一份新的，趋势才是确定的。

用法
    python test_upload_metrics_inline.py
"""
import os
import shutil
import sys
import tempfile
import types
from datetime import datetime
from io import BytesIO

from test_health_metrics_page import build_isolated_app

from utils.database import db
from utils.metric_store import save_metrics_for_text
from models.user import User                     # noqa: F401  建表需要
from models.health_report import HealthReport
from models.health_metric import HealthMetric    # noqa: F401
from models.recipe import Recipe                 # noqa: F401
from models.fridge_image import FridgeImage      # noqa: F401
from models.ingredient import Ingredient         # noqa: F401

import routes.upload_routes as upload_routes

# 有数字、且指标正常 → 面板应有卡片、无就医卡片
_NORMAL_TEXT = "身高 172 cm 体重 70 kg 血压 118/76 mmHg 空腹血糖 6.1 mmol/L 总胆固醇 5.2 mmol/L"
# 危险值 → 就医卡片 + 指标面板同时出现
_CRITICAL_TEXT = "身高 170 cm 体重 78 kg 空腹血糖 17.2 mmol/L 血压 185/115 mmHg"
# 通篇没有可解析的数字（胃镜报告那种）→ 面板应说明"读不出"，不给假数
_NO_NUMBER_TEXT = "胃镜检查所见：慢性胃炎，建议消化内科随访，注意饮食清淡。"
# 提到了血糖、却没跟数值（表格被打散 / 只列了项目名没列结果）
# → 文案必须承认"没认出来"，不能说成"报告里没有糖尿病指标"（那是撒谎）
_MENTIONED_TEXT = "空腹血糖 见附表  糖化血红蛋白 送检中  总胆固醇 见附表"

_WEB_UPLOAD = '/api/upload/upload_health_report'

_STATE = {'text': _NORMAL_TEXT}
# 这次自测一共发过几次上传请求。第八节拿它核对"报告确实入库"——
# 写死数字(原来的 ==4)每加一条上传用例就会假失败，用计数器才是真正的"每传必落库"。
_UPLOADS = {'n': 0}


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
    print('「健康报告」页上传后展示自测')
    print('=' * 78)

    tmpdir = tempfile.mkdtemp(prefix='upload_metrics_test_')
    db_path = os.path.join(tmpdir, 'm.db')
    fake_pdf = os.path.join(tmpdir, 'report.pdf')
    with open(fake_pdf, 'wb') as fh:
        fh.write(b'%PDF-1.4 fake')

    app = build_isolated_app(db_path)
    app.config['UPLOAD_FOLDER_HEALTH_REPORTS'] = tmpdir
    app.config['UPLOAD_FOLDER_FRIDGE_IMAGES'] = tmpdir

    # 2026-10-14：解析已下沉到 utils/report_ocr，upload_routes 不再持有 PyPDF2。
    # 桩只需要打在 sys.modules 上 —— report_ocr 是**函数内** import PyPDF2，
    # 每次调用都从 sys.modules 重新取，所以假模块照样生效。
    real_pypdf2_module = sys.modules.get('PyPDF2')
    real_upload_save = upload_routes.save_uploaded_file
    sys.modules['PyPDF2'] = _FakePyPDF2
    upload_routes.save_uploaded_file = lambda file, folder: (fake_pdf, 'report.pdf')

    client = app.test_client()

    def upload(text, user_id):
        _STATE['text'] = text
        _UPLOADS['n'] += 1
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess.pop('_flashes', None)
        data = {'file': (BytesIO(b'%PDF-1.4'), 'report.pdf')}
        return client.post(_WEB_UPLOAD, data=data, content_type='multipart/form-data')

    try:
        # ══════════ 一、还没传过报告时打开页面：表单在、指标区说明为什么是空的 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get(_WEB_UPLOAD)
        html = resp.get_data(as_text=True)
        check('GET 返回 200', resp.status_code == 200, str(resp.status_code))
        check('GET 时显示上传表单', 'id="selectBtn"' in html)
        check('GET 时指标区标题在（只是还没数据）', '我的健康指标' in html)
        check('GET 无数据时说明"还没有可展示的指标"', '还没有可展示的指标' in html)
        check('GET 无数据时不画空卡片', 'class="metric-grid"' not in html)
        check('GET 无数据时不提示"这份报告读不出"（还没传过报告）',
              '这份报告里没有读出' not in html)
        check('还没传过报告时不出现空的"报告原文"块', 'report-source' not in html)

        # ══════════ 二、上传后 · 页内出指标 ══════════
        resp = upload(_NORMAL_TEXT, user_id=1)
        html = resp.get_data(as_text=True)
        check('上传后返回 200（停在本页，不跳转）',
              resp.status_code == 200, str(resp.status_code))
        check('页面提示"健康报告上传成功"', '健康报告上传成功' in html)
        check('出现指标卡片区',
              'class="metric-grid"' in html and 'class="metric-card' in html)
        check('出现一行指标概览', '指标概览' in html)
        check('卡片里就是刚读出的血糖 6.1', 'metric-value">6.1' in html, '未找到 6.1')
        check('卡片里也有血压', 'metric-value">118' in html and 'metric-value">76' in html)
        # ── 有意翻转：合并成一页之后表单不再收起，用户可能马上要再传一份 ──
        check('上传表单仍然在（同页，不再收起）', 'id="selectBtn"' in html)
        check('给出下一步（去生成食谱）', '去生成食谱' in html)
        check('给出返回首页出口', '返回首页' in html)
        check('页面带免责声明', '不能替代医师' in html)
        check('正常报告不出现就医卡片', 'risk-alert-item' not in html)
        # 原文是"核对用"的补充：有指标时也要能看到系统到底读到了什么
        check('有指标时也给出报告原文供核对',
              'report-source' in html and '系统从' in html)

        # ══════════ 三、GET 也出指标（合并后历史指标只有这里能看） ══════════
        resp = client.get(_WEB_UPLOAD)
        html_get = resp.get_data(as_text=True)
        check('有数据后 GET 也画出指标卡片', 'class="metric-grid"' in html_get)
        check('GET 看到的就是刚上传的 6.1', 'metric-value">6.1' in html_get)
        check('GET 时表单与指标同时在', 'id="selectBtn"' in html_get)

        # ══════════ 四、趋势：预埋历史点 + 新传一份 → 三点折线 ══════════
        with app.app_context():
            save_metrics_for_text(5, "空腹血糖 8.9 mmol/L",
                                  report_id=9001, measured_at=datetime(2026, 7, 1, 8, 0))
            save_metrics_for_text(5, "空腹血糖 7.6 mmol/L",
                                  report_id=9002, measured_at=datetime(2026, 8, 10, 8, 0))
        resp = upload(_NORMAL_TEXT, user_id=5)          # 血糖 6.1，时间最新
        html = resp.get_data(as_text=True)
        check('三点数据画出折线', '<polyline' in html)
        check('点数量正确', '共 3 个数据点' in html, '未找到"共 3 个数据点"')
        check('最新值取的是本次上传的 6.1', 'metric-value">6.1' in html)
        check('给出与上次对比', '较上次' in html)

        # ══════════ 五、读不出数字：说明白，不编数 ══════════
        resp = upload(_NO_NUMBER_TEXT, user_id=6)
        html = resp.get_data(as_text=True)
        check('读不出数字时仍是成功页', resp.status_code == 200
              and '健康报告上传成功' in html, str(resp.status_code))
        # 2026-10-14 改口径：不再笼统说"没读出指标"，而是按报告里到底有没有这些指标
        # 分三种说法。这份是胃镜文本 → 该说"报告里没有糖尿病相关指标"，
        # 并引导用户去传血糖 / 生化报告单（光说"读不出"，用户不知道该干嘛）。
        # ⚠️ 红线：一份胃镜报告**证明不了**血糖正常，文案只能说"看不出"，不能说"没问题"。
        check('胃镜报告 → 说明"没有糖尿病相关指标"并给出路',
              '没有血糖、糖化血红蛋白' in html and '看不出血糖' in html
              and '上传血糖或生化检验报告单' in html)
        # 空态文案里带文件名，是在 Python 层拼好再交给模板的。
        # 若改回用 Jinja 的 ~ 拼接，`| e` 带来的 Markup 会让整个字符串被转义，
        # 页面就会出现字面量的 "<br>" —— 这条守着别再踩回去。
        check('空态文案里的换行/加粗没有被转义成字面量',
              '&lt;br&gt;' not in html and '&lt;b&gt;' not in html)
        check('长空态文案左对齐（居中会显得散）', 'chart-empty is-long' in html)
        check('不硬画空卡片', 'class="metric-grid"' not in html)
        check('读不出也不猜数字（不显示用户 5 的数据）',
              'metric-value">6.1' not in html)
        # ② 报告里明明写了血糖、只是没读出数值 → 必须坦白"没认出来"。
        #    这里若说成"报告里没有糖尿病指标"，就是在对用户撒谎。
        resp_m = upload(_MENTIONED_TEXT, user_id=8)
        html_m = resp_m.get_data(as_text=True)
        check('提到血糖却读不出 → 说"没能读出具体数值"',
              '没能读出具体数值' in html_m)
        check('提到血糖却读不出 → 不说"没有相关指标"',
              '没有血糖、糖化血红蛋白' not in html_m)
        # ① 文件根本没读到文字 → 与报告类型无关，该指向"换电子版 PDF"
        resp_e = upload('', user_id=9)
        html_e = resp_e.get_data(as_text=True)
        check('没读到文字 → 说明并建议换电子版',
              '没有读到任何文字' in html_e and '电子版 PDF' in html_e)
        # ── 报告原文：让用户自己判断"是报告里没写数字"还是"文件没读到字" ──
        check('列出系统读到的原文（可折叠）',
              'report-source' in html and '系统从' in html)
        check('原文就是报告里实际写的字', '慢性胃炎' in html)
        check('原文区标出提取到的字数', ('共 %d 字' % len(_NO_NUMBER_TEXT)) in html,
              f'未找到"共 {len(_NO_NUMBER_TEXT)} 字"')

        # ══════════ 五之二、刷新后口径不能变（本次修的核心问题） ══════════
        # 旧版判"要不要说读不出"，依据是"这一次是不是刚上传"（模板里的 just_uploaded）。
        # 于是用户传完看到的是"这份报告读不出"，一刷新/从别处点回来就变回
        # "还没有可展示的指标 / 上传第一份体检报告后…" —— 看起来像上传功能坏了。
        # 现在按"这个用户有没有传过报告"判，刷新后必须仍然说"读不出"，且原文还在。
        with client.session_transaction() as sess:
            sess['user_id'] = 6
            sess.pop('_flashes', None)
        resp = client.get(_WEB_UPLOAD)
        html_again = resp.get_data(as_text=True)
        check('刷新后仍说明"报告里没有糖尿病相关指标"（不退回"还没上传过"）',
              '没有血糖、糖化血红蛋白' in html_again)
        check('刷新后不再误报"还没上传过"',
              '还没有可展示的指标' not in html_again)
        check('刷新后原文仍在（用户可核对）', '慢性胃炎' in html_again)

        # ══════════ 六、危险报告：就医卡片 + 指标面板同时出现 ══════════
        resp = upload(_CRITICAL_TEXT, user_id=7)
        html = resp.get_data(as_text=True)
        check('危险报告仍是 200（上传成功）', resp.status_code == 200, str(resp.status_code))
        check('出现就医卡片', 'risk-alert-item' in html and '需就医' in html)
        check('就医卡片与指标面板共存', 'class="metric-grid"' in html)
        check('危值也画进了卡片（17.2）', 'metric-value">17.2' in html)
        check('概览点出有几项需就医', '项需就医' in html)
        check('危险态表单仍在（同页，可再传）', 'id="selectBtn"' in html)
        check('就医卡片的按钮锚到本页指标区，不再送人去别的页面',
              'href="#metrics"' in html)

        # ══════════ 七、数据隔离：用户 1 的面板不该带用户 7 的危值 ══════════
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        resp = client.get(_WEB_UPLOAD)
        html = resp.get_data(as_text=True)
        check('用户 1 看到的是自己的数据', 'metric-value">6.1' in html)
        check('看不到别人的危值', 'metric-value">17.2' not in html)

        # ══════════ 八、落库没被展示逻辑连累 ══════════
        with app.app_context():
            check('报告确实入库了',
                  HealthReport.query.count() == _UPLOADS['n'],
                  f"传了 {_UPLOADS['n']} 次，库里 {HealthReport.query.count()} 份")
            check('指标确实入库了',
                  HealthMetric.query.count() > 0, str(HealthMetric.query.count()))

    finally:
        upload_routes.save_uploaded_file = real_upload_save
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
