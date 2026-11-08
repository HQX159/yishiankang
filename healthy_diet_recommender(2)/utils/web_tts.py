"""Generate real Mandarin MP3 audio without a server sound device."""
import asyncio
import hashlib
import logging
import os
from pathlib import Path
import tempfile
import threading

logger = logging.getLogger(__name__)
_slots = threading.BoundedSemaphore(2)
VOICE = 'zh-CN-XiaoxiaoNeural'


def recipe_text(recipe):
    from utils.recipe_text import split_steps
    from utils.diabetes_advice import build_advice
    advice = build_advice(recipe.ingredients, recipe.servings)
    paragraphs = ['菜名，' + recipe.title + '。']
    if advice:
        paragraphs.append('控糖提示：' + advice['tier_label'] + '。' + advice['portion_text'])
        if advice.get('high_names'):
            paragraphs.append('升糖较快的食材：' + '，'.join(advice['high_names']) + '。')
    paragraphs.append('所需食材：' + (recipe.ingredients or '').replace('\n', '，'))
    for i, step in enumerate(split_steps(recipe.instructions), 1):
        paragraphs.append('第%d步，%s' % (i, step))
    return '\n'.join(paragraphs)


def audio_for_recipe(recipe, instance_path):
    text = recipe_text(recipe)
    if not text.strip() or len(text) > 12000:
        raise ValueError('食谱内容为空或过长')
    key = hashlib.sha256((VOICE + '\n' + text).encode('utf-8')).hexdigest()
    directory = Path(instance_path) / 'tts'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (key + '.mp3')
    if target.is_file() and target.stat().st_size > 1024:
        return target
    if not _slots.acquire(timeout=2):
        raise RuntimeError('语音服务繁忙，请稍后重试')
    temp_path = None
    try:
        if target.is_file() and target.stat().st_size > 1024:
            return target
        import edge_tts
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as f:
            temp_path = f.name

        async def generate():
            await asyncio.wait_for(
                edge_tts.Communicate(text, VOICE, connect_timeout=10, receive_timeout=20)
                .save(temp_path), timeout=45)

        asyncio.run(generate())
        with open(temp_path, 'rb') as f:
            header = f.read(3)
        if os.path.getsize(temp_path) <= 1024 or not (
                header == b'ID3' or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224)):
            raise RuntimeError('语音服务没有返回有效音频')
        os.replace(temp_path, target)
        return target
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        _slots.release()
