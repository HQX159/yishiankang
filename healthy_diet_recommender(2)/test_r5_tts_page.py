# -*- coding: utf-8 -*-
"""详情页语音朗读改造的端到端自测（不需要启动服务器）。

做法：用 Flask test_client 直接渲染一道已有食谱的详情页，
检查页面里到底是旧链路还是新链路，并核对渲染出来的 HTML 结构。
不监听端口、不改数据库，测完就走。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                     # noqa: E402
from models.recipe import Recipe               # noqa: E402


def main():
    app = create_app()
    passed = 0
    total = 0

    def check(name, cond, detail=''):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print('[PASS] ' + name)
        else:
            print('[FAIL] ' + name + ('   ' + str(detail) if detail else ''))

    with app.app_context():
        recipe = Recipe.query.order_by(Recipe.id.desc()).first()
        if recipe is None:
            print('库里没有任何食谱，先造一条再测（本脚本不造数据）')
            return 1
        recipe_id, uid, title = recipe.id, recipe.user_id, recipe.title

    print('测试食谱：id=%s  title=%s  user=%s' % (recipe_id, title, uid))

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid

    # 注意路由是 blueprint('/api/recipes') + '/recipes/<id>' —— 两段 recipes。
    # 只写 /api/recipes/<id> 会命中 get_recipe_api()（返回 JSON），页面相关断言全都会假失败。
    resp = client.get('/api/recipes/recipes/%s' % recipe_id)
    check('详情页 200', resp.status_code == 200, resp.status_code)
    html = resp.get_data(as_text=True)

    check('直接播放 MP3', 'id="ttsAudio"' in html and 'controls' in html)
    check('音频地址带食谱 ID', '/api/tts/recipes/%s/audio' % recipe_id in html)
    check('已加载音频脚本', 'js/recipe-audio.js' in html)
    check('移除系统朗读依赖', 'speechSynthesis' not in html)
    check('有语速选择', 'id="ttsRate"' in html)
    check('有停止按钮', 'id="ttsStop"' in html)
    check('有播放进度条', 'tts-track-fill' in html)
    check('错误信息可被读屏识别', 'role="status"' in html)

    # ---- 页面其它按钮没被这次改动带坏 ----
    check('收藏按钮还在', 'id="favBtn"' in html and "getElementById('favBtn')" in html)
    check('换一道按钮还在', 'id="regenBtn"' in html)
    page_script = next((s for s in re.findall(r'<script>([\s\S]*?)</script>', html)
                        if '// ---- 收藏按钮' in s), '')
    check('页面 IIFE 闭合', len(re.findall(r'(?m)^\(function\(\) \{', page_script)) == 1
          and len(re.findall(r'(?m)^\}\)\(\);', page_script)) == 1)

    # ---- 静态样式 ----
    css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'static', 'css', 'style.css')
    with open(css_path, encoding='utf-8') as f:
        css = f.read()
    for cls in ('.tts-rate-select', '.tts-track', '.tts-track-fill', '.tts-stop'):
        check('样式已进 style.css：' + cls, cls in css)

    print('-' * 60)
    print('通过 %d/%d' % (passed, total))
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
