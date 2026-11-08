# test_upload_fridge_message.py
"""
「冰箱图识别完到底怎么说」自测 —— 造临时数据库，不碰真实 app.db

做什么
    1. **认出 0 项时不能说"成功"**。2026-10-23 之前这里是无条件
       flash('冰箱照片上传并食材检测成功', 'success')，于是用户看到
       "检测成功，共 0 项"，只会以为系统坏了。
    2. 0 项时要说清下一步（离近一点 / 换张清楚的 / 手动添加），
       而且必须是 warning 而不是 error —— 照片确实传上去了，不算失败。
    3. 认出东西时要把**数量和名字**说出来（"已认出 3 种：番茄、鸡蛋、青菜"），
       超过 5 项只说前 5 个 + "等 N 项"，别把一屏塞满。
    4. 小程序 / 鸿蒙走的 `/api/v1/upload/fridge_image` 同一口径：
       `code` 仍是 0（上传成功），但 `message` 不能说"识别成功"。

为什么 mock detect_ingredients
    真实识别走视觉大模型（glm-4v-flash）+ YOLO 兜底，离线跑不稳、还会真花额度。
    这里被测的是"拿到结果之后怎么对用户说"，不是识别本身准不准。

用法
    python test_upload_fridge_message.py
"""
import os
import sys
import tempfile
from io import BytesIO

from test_health_metrics_page import build_isolated_app

from utils.database import db
from utils.auth import generate_jwt_token
from models.user import User                     # noqa: F401  建表需要
from models.recipe import Recipe                 # noqa: F401
from models.fridge_image import FridgeImage
from models.ingredient import Ingredient         # noqa: F401

import routes.upload_routes as upload_routes
import routes.api_v1 as api_v1

_WEB_UPLOAD = '/api/upload/upload_fridge_image'
_API_UPLOAD = '/api/v1/upload/fridge_image'

# 每轮要返回什么识别结果，由上传辅助函数设置
_STATE = {'detections': []}


def _three_items():
    return [
        {'name': '番茄', 'confidence': 0.91},
        {'name': '鸡蛋', 'confidence': 0.88},
        {'name': '青菜', 'confidence': 0.83},
    ]


def _eight_items():
    names = ['番茄', '鸡蛋', '青菜', '土豆', '胡萝卜', '豆腐', '香菇', '黄瓜']
    return [{'name': n, 'confidence': 0.8} for n in names]


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
    print('「冰箱图识别后怎么提示」自测')
    print('=' * 78)

    # ══════════ 一、纯函数：文案本身 ══════════
    msg, cat = upload_routes._fridge_detect_message([])
    check('0 项 → warning（不是 error，照片确实传上去了）', cat == 'warning', cat)
    check('0 项 → 不说"成功"', '成功' not in msg, msg)
    check('0 项 → 说清没认出', '没认出' in msg, msg)
    check('0 项 → 给出下一步（重拍建议）',
          ('再拍' in msg or '重拍' in msg or '再传' in msg), msg)
    # 项目里没有"手动添加食材"的入口，文案不能承诺它（2026-10-23 核过）
    check('0 项 → 不承诺"手动添加"这个不存在的功能',
          '手动添加' not in msg and '手动' not in msg, msg)

    msg3, cat3 = upload_routes._fridge_detect_message(_three_items())
    check('3 项 → success', cat3 == 'success', cat3)
    check('3 项 → 说出数量 3', '3 种' in msg3, msg3)
    check('3 项 → 列出名字', '番茄' in msg3 and '青菜' in msg3, msg3)
    check('3 项 → 不出现"等 N 项"', '等' not in msg3, msg3)

    msg8, cat8 = upload_routes._fridge_detect_message(_eight_items())
    check('8 项 → 数量说 8', '8 种' in msg8, msg8)
    check('8 项 → 折叠成"等 8 项"', '等 8 项' in msg8, msg8)
    check('8 项 → 只列前 5 个（第 6 个名字不出现）', '豆腐' not in msg8, msg8)

    # 脏数据不能把页面搞崩
    msg_none, cat_none = upload_routes._fridge_detect_message(None)
    check('None → 当 0 项处理', cat_none == 'warning', cat_none)
    msg_dirty, cat_dirty = upload_routes._fridge_detect_message(
        [{'name': '番茄'}, None, {'name': ''}, {'no_name': 1}])
    check('脏数据（None / 空名）不崩，只数有效名字', '1 种' in msg_dirty, msg_dirty)

    # ══════════ 二、网页端：走完整路由 ══════════
    tmpdir = tempfile.mkdtemp(prefix='fridge_msg_test_')
    db_path = os.path.join(tmpdir, 'f.db')
    fake_img = os.path.join(tmpdir, 'fridge.jpg')
    with open(fake_img, 'wb') as fh:
        fh.write(b'\xff\xd8\xff\xe0 fake jpg')

    app = build_isolated_app(db_path)
    app.config['UPLOAD_FOLDER_HEALTH_REPORTS'] = tmpdir
    app.config['UPLOAD_FOLDER_FRIDGE_IMAGES'] = tmpdir

    real_upload_save = upload_routes.save_uploaded_file
    real_api_save = api_v1.save_uploaded_file
    real_upload_detect = upload_routes.detect_ingredients
    real_api_detect = api_v1.detect_ingredients

    upload_routes.save_uploaded_file = lambda file, folder: (fake_img, 'fridge.jpg')
    api_v1.save_uploaded_file = lambda file, folder: (fake_img, 'fridge.jpg')
    upload_routes.detect_ingredients = lambda path: _STATE['detections']
    api_v1.detect_ingredients = lambda path: _STATE['detections']

    client = app.test_client()

    def read_flashes():
        with client.session_transaction() as sess:
            return [list(x) for x in sess.get('_flashes', [])]

    def upload(detections, api=False, user_id=1):
        _STATE['detections'] = detections
        data = {'file': (BytesIO(b'\xff\xd8\xff\xe0'), 'fridge.jpg')}
        if api:
            with app.app_context():
                token = generate_jwt_token(user_id)
            return client.post(_API_UPLOAD, data=data,
                               content_type='multipart/form-data',
                               headers={'Authorization': f'Bearer {token}'})
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            # 隔离 app 的 /dashboard 是纯文本、不渲染模板，flash 不会被消费，
            # 会一直堆着 —— 每轮清一次，否则上一轮的消息串到这一轮
            sess.pop('_flashes', None)
        return client.post(_WEB_UPLOAD, data=data, content_type='multipart/form-data')

    def fridge_count(user_id=1):
        with app.app_context():
            return FridgeImage.query.filter_by(user_id=user_id).count()

    try:
        # 0 项：照片仍然要存下（不能做成上传失败）
        before = fridge_count()
        resp = upload([], api=False)
        flashes = read_flashes()
        after = fridge_count()

        check('网页 · 0 项返回 302（重定向到仪表盘，不是错误页）',
              resp.status_code == 302, str(resp.status_code))
        check('网页 · 0 项时照片仍然存下来了',
              after == before + 1, f'{before} -> {after}')
        check('网页 · 0 项时有一条 warning 提示',
              any(c == 'warning' for c, _ in flashes), str(flashes))
        check('网页 · 0 项时没有任何 success 提示',
              not any(c == 'success' for c, _ in flashes), str(flashes))
        check('网页 · 0 项提示里不说"检测成功"',
              all('成功' not in m for _, m in flashes), str(flashes))

        # 有结果：把数量和名字说出来
        upload(_three_items(), api=False)
        flashes = read_flashes()
        check('网页 · 3 项时是 success',
              any(c == 'success' for c, _ in flashes), str(flashes))
        check('网页 · 3 项的提示里有数量和名字',
              any('3 种' in m and '番茄' in m for _, m in flashes), str(flashes))

        # 小程序 / 鸿蒙通道
        resp = upload([], api=True)
        body = resp.get_json()
        check('API · 0 项仍返回 code=0（上传本身成功）',
              resp.status_code == 200 and body.get('code') == 0,
              str((resp.status_code, body.get('code'))))
        check('API · 0 项 message 不说"识别成功"',
              '识别成功' not in (body.get('message') or ''), str(body.get('message')))
        check('API · 0 项 message 说明没认出',
              '没认出' in (body.get('message') or ''), str(body.get('message')))
        check('API · 0 项 ingredients 是空数组',
              (body.get('data') or {}).get('ingredients') == [],
              str((body.get('data') or {}).get('ingredients')))

        resp = upload(_three_items(), api=True)
        body = resp.get_json()
        check('API · 3 项 message 是"食材识别成功"',
              body.get('message') == '食材识别成功', str(body.get('message')))
        check('API · 3 项 ingredients 有 3 条',
              len((body.get('data') or {}).get('ingredients') or []) == 3,
              str((body.get('data') or {}).get('ingredients')))

    finally:
        upload_routes.save_uploaded_file = real_upload_save
        api_v1.save_uploaded_file = real_api_save
        upload_routes.detect_ingredients = real_upload_detect
        api_v1.detect_ingredients = real_api_detect
        with app.app_context():
            db.session.remove()

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
