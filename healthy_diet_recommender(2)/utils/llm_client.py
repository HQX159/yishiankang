# utils/llm_client.py
"""LLM 客户端 — 文本/视觉大模型（OpenAI 兼容接口）+ 本地 Whisper ASR"""
import os
import base64
import json
import re
import time
import requests
from flask import current_app


class LLMClient:
    """大语言模型客户端，支持文本生成、图片食材识别和语音转文字"""

    def __init__(self, api_key=None, base_url=None, text_model=None,
                 vision_api_key=None, vision_base_url=None, vision_model=None):
        """
        初始化 LLM 客户端，优先使用传入参数，否则从 Flask 配置 / 环境变量读取。
        """
        try:
            self.api_key = api_key or current_app.config.get("SILICONFLOW_API_KEY")
            self.base_url = base_url or current_app.config.get("SILICONFLOW_BASE_URL")
            self.text_model = text_model or current_app.config.get("TEXT_MODEL_NAME")
            self.vision_api_key = vision_api_key or current_app.config.get("VISION_API_KEY")
            self.vision_base_url = vision_base_url or current_app.config.get("VISION_BASE_URL")
            self.vision_model = vision_model or current_app.config.get("VISION_MODEL_NAME")
        except RuntimeError:
            self.api_key = api_key or os.environ.get("SILICONFLOW_API_KEY", "")
            self.base_url = base_url or os.environ.get(
                "SILICONFLOW_BASE_URL", "https://api.deepseek.com/v1"
            )
            self.text_model = text_model or os.environ.get(
                "TEXT_MODEL_NAME", "deepseek-chat"
            )
            self.vision_api_key = vision_api_key or os.environ.get("VISION_API_KEY", "")
            self.vision_base_url = vision_base_url or os.environ.get(
                "VISION_BASE_URL", "https://api.siliconflow.cn/v1"
            )
            self.vision_model = vision_model or os.environ.get(
                "VISION_MODEL_NAME", "Qwen/Qwen2.5-VL-7B-Instruct"
            )

    def vision_available(self):
        """视觉模型是否已配置（有有效 API key）"""
        key = (self.vision_api_key or "").strip()
        return bool(key) and key != "your-api-key-here"

    # ------------------------------------------------------------------
    # 文本生成
    # ------------------------------------------------------------------
    def generate_text(self, prompt, system_prompt=None, temperature=0.7, max_tokens=2000, timeout=120):
        """
        调用 DeepSeek Chat API 生成文本。

        Args:
            prompt: 用户提示词
            system_prompt: 系统角色设定（可选，默认使用营养师人设）
            temperature: 温度参数 (0-2)
            max_tokens: 最大输出 token 数

        Returns:
            str: 模型生成的文本内容
        """
        if not system_prompt:
            system_prompt = (
                "你是一位专业的糖尿病营养师，擅长为中老年糖尿病患者设计日常控糖饮食。"
                "请根据用户提供的健康指标、现有食材和饮食需求，"
                "生成科学、安全、适合糖尿病患者日常食用的饮食建议和食谱。"
                "回答应详细、实用，包含具体的食材用量和烹饪步骤。"
            )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.text_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except requests.Timeout:
            raise RuntimeError("LLM API 请求超时，请稍后重试")
        except requests.RequestException as e:
            detail = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    detail = e.response.json()
                except Exception:
                    detail = e.response.text or str(e)
            raise RuntimeError(f"LLM API 调用失败: {detail}")

    # ------------------------------------------------------------------
    # 图片识别（冰箱食材）
    # ------------------------------------------------------------------
    def _vision_candidate_models(self):
        """候选视觉模型列表：主模型 + 备用模型，用于限流/故障自动切换。"""
        primary = str(self.vision_model or "").strip()
        models = [primary] if primary else []
        alt = ""
        try:
            alt = (current_app.config.get("VISION_MODEL_ALT") or "").strip()
        except RuntimeError:
            alt = (os.environ.get("VISION_MODEL_ALT") or "").strip()
        if not alt and "bigmodel.cn" in (self.vision_base_url or ""):
            # 智谱端点自动配对两个免费档（共用同一 Key，容量相互独立）
            if primary == "glm-4v-flash":
                alt = "glm-4.6v-flash"
            elif primary == "glm-4.6v-flash":
                alt = "glm-4v-flash"
        if alt and alt not in models:
            models.append(alt)
        return models

    def recognize_ingredients(self, image_path, max_items=30):
        """
        用视觉大模型识别冰箱照片中的食材（OpenAI 兼容 chat/completions）。

        多模型自动切换：主模型失败（429 限流/超时/解析失败）自动换备用模型，
        全部失败才抛 RuntimeError，由调用方（detect_ingredients）回退到 YOLO。

        Returns:
            list[dict]: [{"name": str, "confidence": float}]，[] = 模型明确判定无食材
        """
        if not self.vision_available():
            raise RuntimeError("视觉模型未配置（缺少 VISION_API_KEY）")
        if not os.path.exists(image_path):
            raise RuntimeError(f"图片不存在: {image_path}")

        ext = os.path.splitext(image_path)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        prompt = (
            "你是食材识别助手。请识别这张照片（通常是冰箱内部，可能有保鲜盒、密封袋、"
            "餐盘等容器）中可见的食物食材，例如猪肉、白菜、番茄、胡萝卜、西兰花、红烧肉等。"
            "只输出 JSON 数组，格式 [{\"name\": \"食材名\", \"confidence\": 0.9}]，"
            "用中文食材名，不要输出解释或其他文字；若没有可见食材则输出 []；最多 30 项。"
        )

        headers = {
            "Authorization": f"Bearer {self.vision_api_key}",
            "Content-Type": "application/json",
        }
        errors = []
        for model in self._vision_candidate_models():
            # glm-4v-flash 的 max_tokens 上限为 1024，其余视觉模型放宽到 4000
            max_tokens = 1024 if "4v-flash" in model else 4000
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ],
                    }
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            url = f"{self.vision_base_url.rstrip('/')}/chat/completions"
            content = None
            for attempt in range(3):
                # 429 限流：同模型退避重试（5s→10s）；超时重试 1 次；再失败才换下一个候选模型
                try:
                    response = requests.post(url, json=payload, headers=headers, timeout=90)
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"]
                    break
                except requests.Timeout:
                    if attempt == 0:
                        time.sleep(3)
                        continue
                    errors.append(f"{model}: 请求超时")
                    break
                except requests.RequestException as e:
                    resp = getattr(e, "response", None)
                    status = getattr(resp, "status_code", None)
                    detail = str(e)
                    if resp is not None:
                        try:
                            detail = resp.json()
                        except Exception:
                            detail = resp.text or str(e)
                    if status == 429 and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    errors.append(f"{model}: {detail}")
                    break
                except (KeyError, IndexError, ValueError) as e:
                    errors.append(f"{model}: 响应格式异常 {e}")
                    break
            if content is None:
                continue

            # 宽容提取 JSON 数组（模型可能加 markdown 围栏、说明文字，或被截断）
            text = content.strip()
            items = None
            # 1) 完整数组（贪婪匹配首 [ 到末 ]）
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, list):
                        if not parsed:
                            return []   # 模型明确判定：图中无食材
                        items = parsed
                except json.JSONDecodeError:
                    items = None
            # 2) 截断/畸形 JSON：逐条打捞完整的 {"name": ..., "confidence": ...} 对象
            if items is None:
                items = []
                for m in re.finditer(r"\{[^{}]*\}", text):
                    try:
                        obj = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and obj.get("name"):
                        items.append(obj)
            if not isinstance(items, list) or not items:
                errors.append(f"{model}: 无法解析食材 JSON: {text[:120]}")
                continue

            detections = []
            for item in items[:max_items]:
                if isinstance(item, str):
                    name, conf = item.strip(), 1.0
                elif isinstance(item, dict) and item.get("name"):
                    name = str(item["name"]).strip()
                    try:
                        conf = float(item.get("confidence", 1.0))
                    except (TypeError, ValueError):
                        conf = 1.0
                else:
                    continue
                if name:
                    detections.append({"name": name, "confidence": conf})
            if detections:
                return detections
            errors.append(f"{model}: 解析结果为空")
            continue

        raise RuntimeError("视觉模型识别失败（所有候选模型均不可用）: " + " | ".join(errors))

    # ------------------------------------------------------------------
    # 报告图片转录（OCR）—— 健康报告拍照件 / 扫描版 PDF 内嵌图
    # ------------------------------------------------------------------
    # ⚠️ 医学数据安全边界：这是**转录**，不是**解读**。prompt 必须死死摁住
    #    "只写你清晰看到的字、禁止推测数值"，看不清的强制标 [无法辨认]。
    #    转录结果最终会进 content_text 并被 report_parser 抽数值落库 ——
    #    模型一旦"脑补"一个血糖值，下游会当成真实检验结果存起来，这是安全事故。
    #    第二道防线在 report_parser：每条指标有 lo/hi 合理范围兜底。
    def transcribe_report_image(self, image_bytes=None, image_path=None, mime='image/jpeg'):
        """把一张报告图片转录成文字。

        Args:
            image_bytes: 图片原始字节（与 image_path 二选一，优先 bytes）
            image_path:  图片路径
            mime:        图片 MIME

        Returns:
            str: 转录文本（模型返回什么就返回什么，调用方再加工）

        Raises:
            RuntimeError: 未配置视觉模型，或所有候选模型都失败
        """
        if not self.vision_available():
            raise RuntimeError("视觉模型未配置（缺少 VISION_API_KEY）")
        if image_bytes is None:
            if not image_path or not os.path.exists(image_path):
                raise RuntimeError(f"图片不存在: {image_path}")
            ext = os.path.splitext(image_path)[1].lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            with open(image_path, "rb") as f:
                image_bytes = f.read()
        if not image_bytes:
            raise RuntimeError("图片内容为空")

        b64 = base64.b64encode(image_bytes).decode("ascii")

        prompt = (
            "你是病历/检验报告的文字转录助手。请逐字转录图片上的文字，"
            "保留指标名、数值、单位和参考区间，一行一行输出原文。\n"
            "严格要求：\n"
            "1. 只转录你**清晰看到**的文字，禁止推测、补全或编造任何数值；\n"
            "2. 看不清的地方原样写 [无法辨认]，不要猜；\n"
            "3. 不要解读、不要下诊断、不要给建议，只输出转录的原文；\n"
            "4. 不要输出任何说明性前缀或总结。"
        )

        headers = {
            "Authorization": f"Bearer {self.vision_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.vision_base_url.rstrip('/')}/chat/completions"

        errors = []
        for model in self._vision_candidate_models():
            # glm-4v-flash 的 max_tokens 上限 1024；检验单一页文字通常够用，
            # 真被截断也只是丢掉页尾几行，指标名+数值一般在开头就出现了。
            max_tokens = 1024 if "4v-flash" in model else 4000
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ],
                    }
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            for attempt in range(3):
                try:
                    response = requests.post(url, json=payload, headers=headers, timeout=90)
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"]
                    return (content or "").strip()
                except requests.Timeout:
                    if attempt == 0:
                        time.sleep(3)
                        continue
                    errors.append(f"{model}: 请求超时")
                    break
                except requests.RequestException as e:
                    resp = getattr(e, "response", None)
                    status = getattr(resp, "status_code", None)
                    detail = str(e)
                    if resp is not None:
                        try:
                            detail = resp.json()
                        except Exception:
                            detail = resp.text or str(e)
                    if status == 429 and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    errors.append(f"{model}: {detail}")
                    break
                except (KeyError, IndexError, ValueError) as e:
                    errors.append(f"{model}: 响应格式异常 {e}")
                    break

        raise RuntimeError("报告图片转录失败（所有候选模型均不可用）: " + " | ".join(errors))

    # ------------------------------------------------------------------
    # 语音转文字 (ASR)
    # ------------------------------------------------------------------
    def transcribe_audio(self, file_path):
        """
        将音频文件转写为文字。

        优先尝试本地 Whisper 模型；不可用时回退到 OpenAI Whisper API。

        Args:
            file_path: 音频文件路径

        Returns:
            str | None: 转写后的文字，失败时返回 None
        """
        # ---- 方案一：本地 Whisper ----
        try:
            import whisper

            model = whisper.load_model("base")
            result = model.transcribe(file_path, language="zh")
            return result["text"].strip()
        except ImportError:
            try:
                current_app.logger.warning("本地 whisper 未安装，尝试 API 转写")
            except RuntimeError:
                pass
        except Exception as exc:
            try:
                current_app.logger.warning(f"本地 Whisper 转写失败: {exc}")
            except RuntimeError:
                pass

        # ---- 方案二：OpenAI Whisper API ----
        try:
            asr_api_key = os.environ.get("OPENAI_API_KEY", self.api_key)
            asr_base_url = os.environ.get(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            )

            url = f"{asr_base_url}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {asr_api_key}"}

            with open(file_path, "rb") as f:
                response = requests.post(
                    url,
                    headers=headers,
                    files={"file": f},
                    data={"model": "whisper-1", "language": "zh"},
                    timeout=60,
                )
            response.raise_for_status()
            return response.json().get("text", "").strip()

        except Exception as exc:
            try:
                current_app.logger.error(f"语音转文字失败: {exc}")
            except RuntimeError:
                pass
            return None
