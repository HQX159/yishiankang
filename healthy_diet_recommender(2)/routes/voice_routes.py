# routes/voice_routes.py
from flask import Blueprint, request, jsonify, session, current_app
from utils.llm_client import LLMClient
from models.recipe import Recipe
from models.fridge_image import FridgeImage
from models.health_report import HealthReport
from models.ingredient import Ingredient
from utils.database import db
from utils.rag import generate_recipe_with_rag
import os

voice_bp = Blueprint('voice', __name__)


def check_user_logged_in():
    """未登录守卫：**这个接口永远回 401 JSON，不跳登录页**。

    调用方是上传音频的 fetch / 鸿蒙端，拿到 401 才好处理；它没有"页面"形态，
    所以不转用 `utils.auth.login_guard()`（那里会按 Accept 判页面）。
    """
    user_id = session.get('user_id')
    if not user_id:
        return None, jsonify({'message': '未登录'}), 401
    return user_id, None, 200


@voice_bp.route('/voice_command', methods=['POST'])
def voice_command():
    user_id, error_response, status_code = check_user_logged_in()
    if error_response:
        return error_response, status_code

    if 'audio' not in request.files:
        return jsonify({'message': '没有音频文件'}), 400

    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({'message': '没有选择音频文件'}), 400

    # 保存上传的音频文件
    audio_path = os.path.join('static', 'uploads', 'audio')
    os.makedirs(audio_path, exist_ok=True)
    file_path = os.path.join(audio_path, audio_file.filename)
    audio_file.save(file_path)

    # 使用ASR模型转录音频
    llm_client = LLMClient()
    user_query = llm_client.transcribe_audio(file_path)

    if not user_query:
        return jsonify({'message': '语音转文字失败'}), 500

    # 获取用户最新的健康报告
    latest_health_report = HealthReport.query.filter_by(user_id=user_id) \
        .order_by(HealthReport.uploaded_at.desc()).first()

    # 获取用户最新的冰箱食材
    latest_fridge_image = FridgeImage.query.filter_by(user_id=user_id) \
        .order_by(FridgeImage.uploaded_at.desc()).first()

    # 构建健康报告文本
    health_report_text = ""
    if latest_health_report and latest_health_report.content_text:
        health_report_text = latest_health_report.content_text[:500]  # 限制长度

    # 构建食材文本
    ingredients_text = ""
    if latest_fridge_image:
        # Query ingredients associated with the latest fridge image
        fridge_ingredients = Ingredient.query.filter_by(fridge_image_id=latest_fridge_image.id).all()
        if fridge_ingredients:
            ingredients = [ing.name for ing in fridge_ingredients]
            ingredients_text = ", ".join(ingredients)

    # 使用RAG生成食谱
    try:
        recipe_content = generate_recipe_with_rag(
            health_report_text,
            ingredients_text,
            user_query
        )

        # 使用统一的解析函数
        from routes.recipe_routes import _parse_recipe_content
        title, ingredients, instructions, nutrition_info, health_tip = _parse_recipe_content(recipe_content)

        # 保存生成的食谱到数据库
        recipe = Recipe(
            user_id=user_id,
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            nutrition_info=nutrition_info
        )

        db.session.add(recipe)
        db.session.commit()

        return jsonify({
            'message': '语音指令处理成功',
            'recipe': recipe.to_dict(),
            'transcribed_text': user_query
        }), 201

    except Exception as e:
        current_app.logger.error(f"语音处理失败: {str(e)}")
        return jsonify({'message': f'语音处理失败: {str(e)}'}), 500