# utils/yolo_detector.py
"""
食材检测模块：视觉大模型优先（中文食材），YOLOv8 兜底。
在 Render 等轻量环境（无 torch/ultralytics）下会自动降级。

权重来源：config.YOLO_MODEL_PATH（默认 models/yolo/best.pt，自训 8 类食材模型）。
"""
import logging
import os
import sys

logger = logging.getLogger(__name__)

_YOLO_AVAILABLE = False
_model = None
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_weight_path():
    """解析权重路径：优先 config.YOLO_MODEL_PATH，失败时回退项目内默认位置。
    相对路径一律按项目根目录解析，不依赖进程工作目录。"""
    try:
        from config import Config
        raw = Config.YOLO_MODEL_PATH
    except Exception:
        raw = None
    if not raw:
        raw = os.path.join('models', 'yolo', 'best.pt')
    return raw if os.path.isabs(raw) else os.path.join(_PROJECT_ROOT, raw)

try:
    import torch
    from ultralytics import YOLO

    # 强制覆盖 torch.load，关闭 weights_only 安全检查
    original_load = torch.load

    def patched_load(file_path, map_location="cpu"):
        return original_load(file_path, map_location=map_location, weights_only=False)

    torch.load = patched_load

    # 受限环境（如 DSH 沙箱）：ultralytics 的 get_cpu_info 通过 Popen 管道读取 CPU 信息，
    # 管道被拒绝（PermissionError WinError 5）会导致推理静默失败。
    # 这里加安全回退：失败时返回静态字符串，正常环境（含 Linux 部署）不受影响。
    try:
        from ultralytics.utils import torch_utils as _torch_utils

        _orig_get_cpu_info = _torch_utils.get_cpu_info

        def _safe_get_cpu_info(*args, **kwargs):
            try:
                return _orig_get_cpu_info(*args, **kwargs)
            except Exception:
                return "unknown CPU"

        _torch_utils.get_cpu_info = _safe_get_cpu_info
    except Exception:
        pass

    _WEIGHT_PATH = _resolve_weight_path()
    if not os.path.exists(_WEIGHT_PATH):
        raise FileNotFoundError(f"权重文件不存在: {_WEIGHT_PATH}")
    _model = YOLO(_WEIGHT_PATH, task="detect")
    _YOLO_AVAILABLE = True
    _names = getattr(_model, "names", None) or {}
    logger.info("YOLOv8 模型加载成功: %s（%d 类）", _WEIGHT_PATH, len(_names))
except Exception as e:
    logger.warning(f"YOLOv8 不可用（轻量部署模式）: {e}")


def _yolo_detect(image_path):
    """YOLOv8 检测（自训食材模型，类别名即中文食材名，仅作兜底）。"""
    if not _YOLO_AVAILABLE or _model is None:
        logger.info("YOLO 不可用，跳过食材检测")
        return []

    try:
        results = _model(image_path)
        detections = []
        result = results[0]
        boxes = result.boxes
        names = result.names

        if boxes is not None:
            for box in boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                ingredient_name = names[class_id]
                detections.append({
                    'name': ingredient_name,
                    'confidence': confidence
                })

        return detections
    except Exception as e:
        logger.error(f"YOLO 食材检测失败: {e}")
        return []


def detect_ingredients(image_path):
    """
    识别图片中的食材：视觉大模型优先（中文食材名），失败时自动回退 YOLOv8。
    返回 [{"name": str, "confidence": float}]。
    """
    # ---- 首选：视觉大模型 ----
    try:
        from utils.llm_client import LLMClient

        detections = LLMClient().recognize_ingredients(image_path)
        logger.info(f"视觉模型识别出 {len(detections)} 种食材: "
                    f"{[d['name'] for d in detections][:10]}")
        return detections
    except Exception as e:
        logger.warning(f"视觉模型识别不可用/失败，回退 YOLO: {e}")

    # ---- 兜底：YOLOv8 ----
    return _yolo_detect(image_path)


if __name__ == "__main__":
    image_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not image_path:
        print("用法: python -m utils.yolo_detector <图片路径>")
        sys.exit(1)
    print(detect_ingredients(image_path))
