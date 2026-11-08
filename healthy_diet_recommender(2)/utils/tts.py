# utils/tts.py
"""语音合成模块 — 在无音频设备环境下返回 None"""
import os
import uuid
import logging

logger = logging.getLogger(__name__)

_ENGINE = None

try:
    import pyttsx3
    _ENGINE = pyttsx3
    logger.info("pyttsx3 语音引擎加载成功")
except Exception as e:
    logger.warning(f"pyttsx3 不可用: {e}")


def synthesize_speech(text):
    """将文本转换为语音，失败返回 None"""
    if _ENGINE is None:
        logger.warning("语音引擎不可用，跳过 TTS")
        return None

    try:
        from flask import current_app
        audio_dir = os.path.join(current_app.root_path, 'static', 'audio')
        os.makedirs(audio_dir, exist_ok=True)

        filename = f"{uuid.uuid4()}.mp3"
        file_path = os.path.join(audio_dir, filename)

        engine = _ENGINE.init()
        voices = engine.getProperty('voices')
        if voices:
            engine.setProperty('voice', voices[0].id)
        engine.setProperty('rate', 150)
        engine.setProperty('volume', 0.9)
        engine.save_to_file(text, file_path)
        engine.runAndWait()

        return os.path.join('static', 'audio', filename)
    except Exception as e:
        logger.error(f"语音合成失败: {e}")
        return None