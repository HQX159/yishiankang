# routes/tts_routes.py
from flask import Blueprint, request, jsonify, session, current_app, url_for, send_file
from utils.tts import synthesize_speech
from utils.database import db

tts_bp = Blueprint('tts', __name__)


@tts_bp.route('/recipes/<int:recipe_id>/audio', methods=['GET'])
def recipe_audio(recipe_id):
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code
    from models.recipe import Recipe
    from utils.web_tts import audio_for_recipe
    recipe = Recipe.query.filter_by(id=recipe_id, user_id=user_id).first()
    if recipe is None:
        return jsonify(message='食谱不存在'), 404
    try:
        audio_path = audio_for_recipe(recipe, current_app.instance_path)
        response = send_file(audio_path, mimetype='audio/mpeg', conditional=True,
                             download_name='recipe.mp3', max_age=0)
        response.headers['Cache-Control'] = 'private, no-cache'
        return response
    except Exception:
        current_app.logger.exception('Recipe audio generation failed: recipe=%s', recipe_id)
        return jsonify(message='语音暂时无法生成，请稍后重试'), 503


def check_user_logged_in():
    """未登录守卫：**这个接口永远回 401 JSON，不跳登录页**。

    调用方是 fetch（`Accept: */*`）和小程序/鸿蒙，拿到 401 才好处理；
    它没有"页面"形态，所以不转用 `utils.auth.login_guard()`（那里会按 Accept 判页面）。
    网页 MP3 播放端点和旧合成接口均采用这个守卫。
    """
    user_id = session.get('user_id')
    if not user_id:
        return None, jsonify({'message': '未登录'}), 401
    return user_id, None, 200


@tts_bp.route('/text_to_speech', methods=['POST'])  # 修正路由，移除/api/tts前缀
def text_to_speech():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    data = request.get_json()
    text = data.get('text')

    if not text:
        return jsonify({'message': '请提供需要转换的文本'}), 400

    audio_path = synthesize_speech(text)
    if not audio_path:
        return jsonify({'message': '语音合成失败'}), 500

    # 生成正确的音频URL
    audio_url = '/' + audio_path

    return jsonify({
        'message': '语音合成成功',
        'audio_url': audio_url
    }), 200
