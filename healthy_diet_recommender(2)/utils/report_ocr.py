# utils/report_ocr.py
"""健康报告文字提取 —— PDF / 图片统一入口。

解决什么
    「上传健康报告」以前只认**带文字层的 PDF**：
      - 图片（png/jpg）：走 pytesseract，本机没装 tesseract → 必抛 TesseractNotFoundError；
      - 扫描版 PDF：PyPDF2 抽不出字 → content_text 为空。
    医院给患者的报告大量是手机拍照 / 扫描件，这条路不通等于"图片全废"。

现在怎么走（按优先级）
    PDF：
      1. PyPDF2 抽文字层 —— 有字（≥ MIN_PDF_TEXT_CHARS）直接用，最快最准；
      2. 没字 → 大概率扫描件 → 用 PyPDF2 抽每页内嵌大图，交给视觉大模型逐张转录；
      3. 抽不出图 / 转录失败 → 返回空文本，由页面空态文案解释（不会瞎猜）。
    图片：
      1. 视觉大模型转录（复用 LLMClient 的多模型退避 + 429 重试，与冰箱识别同一套）；
      2. 视觉模型没配 key → 回退 pytesseract（装了 tesseract 才可能成功）；
      3. 都不行 → 返回空文本。

安全边界（医学场景，不能含糊）
    - prompt 明令「只转录清晰可见的文字，禁止推测编造数值」；
    - 视觉转录的文本末尾**追加一行来源标注**，页面"原文"区展示时用户知道这是模型转录的，
      不是原始电子文本 —— 诚实标注，避免用户把 OCR 结果当原件；
    - 下游 report_parser 的合理范围（lo/hi）会再兜一层离谱值；
      risk_guard 全文扫描照常工作，与本模块无关。

依赖约定
    PyPDF2 / PIL 都在**函数内** import：一是这些库只在被调用时才需要；
    二是自测脚本通过 sys.modules['PyPDF2'] 注入假模块（见 test_upload_metrics_inline.py），
    函数内 import 恰好从 sys.modules 取，桩才能生效 —— 改成模块级 import 会弄坏那些自测。

用法
    from utils.report_ocr import extract_report_text
    text, method = extract_report_text(filepath, filename)
    # method ∈ {'pdf-text', 'pdf-vision', 'image-vision', 'image-tesseract', 'none'}
"""

import os

# 「这份 PDF 到底有没有文字层」的判据 —— 判定错了会走错整条链：
#   有文字层却判成扫描件 → 白白去调视觉模型（慢、烧额度），或干脆读不出字；
#   扫描件却判成有文字层 → 用户报告明明拍得清楚却读不到指标。
#
# 为什么是这两个条件（而不是"字数 ≥ 某个大阈值"）：
#   1. MIN_PDF_TEXT_CHARS 只要求 15 —— 实测一份真实胃镜报告正文才 28 个字
#      （test_upload_metrics_inline.py 的 _NO_NUMBER_TEXT）。阈值设 30 会把这种
#      正常的短报告误判成扫描件，直接读不出字 —— 这是踩过的坑。
#   2. 只要命中任一指标名（血糖/血压/血脂…），无论多短都算"有文字层" ——
#      文字层里有指标名，恰恰就是我们要抽的东西。
#   扫描件被误放进来的典型特征是完全空串，不是"短"，所以 15 这个下限够用。
MIN_PDF_TEXT_CHARS = 15

# 视觉转录的单图长边上限（px）。手机拍照动辄 4000×3000、好几 MB，
# 视觉 API 限 base64 体积且传输慢；压到 2000px 对检验单文字清晰度足够。
MAX_IMAGE_EDGE = 2000

# 扫描 PDF 最多处理前几页 —— 检验单通常 1~3 页，多页报告截断处理，防超时。
MAX_PDF_OCR_PAGES = 4

# 视觉转录文本末尾追加的来源标注。
# ⚠️ 不以 ']' 结尾：latest_report_source() 靠 "[...]" 且 len<40 判定"解析失败的占位符"，
#    这行标注要是长成那样，会被误判成占位符而不展示。
VISION_SOURCE_NOTE = "\n\n（上文由视觉模型自图片转录，可能存在误差，请以原件为准）"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _pdf_text(filepath):
    """抽 PDF 文字层。返回 (text, ok)：ok=False 表示打开/解析炸了（区别于"没字"）。

    ⚠️ 每页之间（含末页）保留一个换行 —— 与重构前 upload_routes/api_v1 里的
    `content_text += page.extract_text() + "\\n"` 完全一致。页面上"共 N 字"的显示
    依赖这个长度，改成 '\n'.join 会少一个字，属于无意义的展示口径漂移。
    """
    import PyPDF2                                   # 函数内 import，理由见文件头
    try:
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            return ''.join((page.extract_text() or '') + '\n' for page in reader.pages), True
    except Exception as exc:                        # noqa: BLE001
        print(f"[report_ocr] PDF 读取失败 {filepath}: {exc}")
        return '', False


def _pdf_embedded_images(filepath):
    """抽扫描版 PDF 每页的内嵌图片，返回图片字节列表。

    只取"大图"（扫描件一页一张整图）；矢量小碎片（logo、装饰线）按数据量过滤。
    最多 MAX_PDF_OCR_PAGES 页 —— 检验单用不上更多，防大 PDF 把视觉模型额度烧光。
    """
    import PyPDF2
    try:
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            blobs = []
            for page in reader.pages[:MAX_PDF_OCR_PAGES]:
                try:
                    images = list(page.images)
                except Exception:                   # noqa: BLE001 —— 单页没有/抽不出图，跳过
                    continue
                for img in images:
                    data = img.data
                    if data and len(data) >= 20000:  # <20KB 的当装饰图丢掉
                        blobs.append(bytes(data))
            return blobs
    except Exception as exc:                        # noqa: BLE001
        print(f"[report_ocr] PDF 内嵌图抽取失败 {filepath}: {exc}")
        return []


def _shrink_to_jpeg(data):
    """把图片字节压到适合视觉 API 的体积：长边 ≤ MAX_IMAGE_EDGE 的 JPEG。

    返回 (jpeg_bytes, 'image/jpeg')；图片本身打不开/转不动 → 返回 (None, None)，
    调用方按"这张图处理不了"跳过，不让一张坏图挂掉整份报告。
    """
    import io
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        # PNG 带 alpha 通道时转 JPEG 必须先铺白底，否则透明区域变黑、文字看不清
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        w, h = img.size
        if max(w, h) > MAX_IMAGE_EDGE:
            scale = MAX_IMAGE_EDGE / max(w, h)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=85)
        return out.getvalue(), 'image/jpeg'
    except Exception as exc:                        # noqa: BLE001
        print(f"[report_ocr] 图片预处理失败: {exc}")
        return None, None


def _looks_like_text_layer(text):
    """这份 PDF 抽出来的字，算不算"真的有文字层"？"""
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) >= MIN_PDF_TEXT_CHARS:
        return True
    # 很短但命中指标名 —— 那就是实质内容，按有文字层处理
    try:
        from utils.report_parser import detect_metric_names
        return bool(detect_metric_names(stripped))
    except Exception:                               # noqa: BLE001
        return False


def _vision_transcribe(jpeg_bytes):
    """视觉大模型转录一张图。返回文本（可能为空串）；视觉模型没配 → 抛 RuntimeError。"""
    from utils.llm_client import LLMClient
    client = LLMClient()
    return client.transcribe_report_image(image_bytes=jpeg_bytes, mime='image/jpeg')


def _vision_available():
    try:
        from utils.llm_client import LLMClient
        return LLMClient().vision_available()
    except Exception:                               # noqa: BLE001
        return False


def _tesseract_fallback(filepath):
    """视觉模型不可用时的兜底：本地 tesseract。装了才可能成功。"""
    try:
        from PIL import Image
        import pytesseract
        return pytesseract.image_to_string(Image.open(filepath), lang='chi_sim')
    except Exception as exc:                        # noqa: BLE001
        print(f"[report_ocr] tesseract 兜底失败（多半未安装）: {exc}")
        return ''


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def extract_report_text(filepath, filename):
    """提取健康报告文字。永不抛异常 —— 上传主流程不能被解析失败挂掉。

    Returns:
        (text, method)
        text   : 提取到的文字（可能为空串）。视觉转录的文本带来源标注行。
        method : 'pdf-text'         PDF 文字层（最准）
                 'pdf-vision'       扫描版 PDF → 内嵌图视觉转录
                 'image-vision'     图片 → 视觉转录
                 'image-tesseract'  图片 → 本地 tesseract（视觉模型没配时的兜底）
                 'none'             什么都没读出来
    """
    if not filepath or not os.path.exists(filepath):
        return '', 'none'

    lower = (filename or '').lower()

    # ---- PDF：先文字层，没有再视觉 ----
    if lower.endswith('.pdf'):
        text, ok = _pdf_text(filepath)
        if ok and _looks_like_text_layer(text):
            return text, 'pdf-text'
        if not ok:
            # 打开就炸（损坏/加密）—— 视觉路线也救不了（内嵌图同样抽不出），直接认输
            return '', 'none'
        # 有文字层但字太少 → 疑似扫描件，走内嵌图
        blobs = _pdf_embedded_images(filepath)
        if not blobs:
            return '', 'none'
        if not _vision_available():
            # 没配视觉模型 + 没装 tesseract —— 扫描 PDF 无解，如实返回空
            return '', 'none'
        parts = []
        for blob in blobs:
            jpeg, _ = _shrink_to_jpeg(blob)
            if not jpeg:
                continue
            try:
                parts.append(_vision_transcribe(jpeg))
            except Exception as exc:                # noqa: BLE001 —— 单页失败不连坐
                print(f"[report_ocr] PDF 内嵌图转录失败: {exc}")
        text = '\n'.join(p for p in parts if p and p.strip())
        if not text.strip():
            return '', 'none'
        return text + VISION_SOURCE_NOTE, 'pdf-vision'

    # ---- 图片：优先视觉模型，没配 key 回退 tesseract ----
    if lower.endswith(('.png', '.jpg', '.jpeg')):
        with open(filepath, 'rb') as f:
            raw = f.read()
        jpeg, _ = _shrink_to_jpeg(raw)
        if jpeg and _vision_available():
            try:
                text = _vision_transcribe(jpeg)
                if text and text.strip():
                    return text + VISION_SOURCE_NOTE, 'image-vision'
                return '', 'none'                   # 模型跑了但没读到字，如实返回
            except Exception as exc:                # noqa: BLE001
                print(f"[report_ocr] 视觉转录失败，准备走 tesseract 兜底: {exc}")
        text = _tesseract_fallback(filepath)
        if text and text.strip():
            return text, 'image-tesseract'
        return '', 'none'

    # 不认识的后缀（上游 allowed_file 已拦，这里只是防御）
    return '', 'none'


# ---------------------------------------------------------------------------
# 自测：python -m utils.report_ocr（需要包上下文，不起 Flask、不连库）
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import tempfile

    passed, total = 0, 0

    def check(name, cond, detail=''):
        global passed, total
        total += 1
        mark = '[PASS]' if cond else '[FAIL]'
        print(f'{mark} {name}' + ('' if cond else f'   {detail}'))
        if cond:
            passed += 1

    print('=' * 78)
    print('report_ocr 自测（视觉模型调用全部用桩，不联网）')
    print('=' * 78)

    tmp = tempfile.mkdtemp(prefix='report_ocr_test_')

    # 1) 常量约定：来源标注不能被 latest_report_source 误判成"失败占位符"
    note = VISION_SOURCE_NOTE.strip()
    check('来源标注不以 ] 结尾', not (note.startswith('[') and note.endswith(']') and len(note) < 40),
          note)

    # 2) 不存在的文件 → ('', 'none')，不抛
    text, method = extract_report_text(os.path.join(tmp, 'ghost.pdf'), 'ghost.pdf')
    check('文件不存在 → none 不抛', text == '' and method == 'none', f'{method}/{text!r}')

    # 3) 不认识的后缀 → none
    text, method = extract_report_text(__file__, 'report.docx')
    check('未知后缀 → none', text == '' and method == 'none', method)

    # 4) 图片分支：桩掉视觉模型（可用 + 返回固定文本），验证走视觉且带来源标注
    import utils.report_ocr as ro

    class _FakeClient:
        def __init__(self, *a, **k):
            pass
        def vision_available(self):
            return True
        def transcribe_report_image(self, image_bytes=None, mime=None, **k):
            # 顺手验证传进来的图确实是压缩过的 JPEG
            assert image_bytes[:2] == b'\xff\xd8', '不是 JPEG'
            assert len(image_bytes) < 2 * 1024 * 1024, '图太大没压缩'
            return '空腹血糖 8.9 mmol/L 糖化血红蛋白 8.2 %'

    # report_ocr 的视觉调用在函数内 `from utils.llm_client import LLMClient`，
    # 桩要打在 utils.llm_client 模块上才能生效（模块属性查不到函数内 import 的来源）
    import utils.llm_client as llm_mod
    real_cls = llm_mod.LLMClient
    llm_mod.LLMClient = _FakeClient
    try:
        from PIL import Image
        img_path = os.path.join(tmp, 'report.png')
        Image.new('RGB', (3000, 2000), (255, 255, 255)).save(img_path, 'PNG')
        text, method = extract_report_text(img_path, 'report.png')
        check('图片 → image-vision', method == 'image-vision', method)
        check('视觉转录带来源标注', VISION_SOURCE_NOTE.strip() in text, repr(text[-60:]))
        check('转录正文在前', text.startswith('空腹血糖'), repr(text[:20]))

        # 5) 视觉模型没配 key → 回退 tesseract；tesseract 未装 → 如实 none
        class _NoVisionClient:
            def __init__(self, *a, **k):
                pass
            def vision_available(self):
                return False
        llm_mod.LLMClient = _NoVisionClient
        text, method = extract_report_text(img_path, 'report.png')
        # 本机没装 tesseract → none；装了则 image-tesseract。两种都算通过，只要不抛
        check('视觉不可用时优雅降级', method in ('image-tesseract', 'none')
              and isinstance(text, str), f'{method}/{text!r}')

        # 6) 真实图片能被 _shrink_to_jpeg 压小（3000px → ≤2000px）
        with open(img_path, 'rb') as f:
            jpeg, mime = _shrink_to_jpeg(f.read())
        from PIL import Image as _I
        w, h = _I.open(__import__('io').BytesIO(jpeg)).size
        check('压缩后长边 ≤ 2000', max(w, h) <= MAX_IMAGE_EDGE, f'{w}x{h}')
        check('压缩输出 JPEG', mime == 'image/jpeg', str(mime))

        # 7) RGBA 透明底铺白（不炸、能转）
        rgba_path = os.path.join(tmp, 'rgba.png')
        _I.new('RGBA', (800, 600), (255, 0, 0, 0)).save(rgba_path, 'PNG')
        jpeg2, _ = _shrink_to_jpeg(open(rgba_path, 'rb').read())
        check('RGBA 转 JPEG 不炸', jpeg2 is not None and len(jpeg2) > 0)
    finally:
        llm_mod.LLMClient = real_cls

    # 8) PDF 分支（假 PyPDF2 走 sys.modules，跟 test_upload_metrics_inline 同款）
    import types, sys
    _SCAN_PAGE_TEXT = '第1页'        # 扫描件页眉那种零星文字
    _SHORT_REPORT = ('患者黄某 男 18岁 检查日期2025-06-17 内镜提示：慢性胃炎 建议随访'
                     ' 建议清淡饮食注意休息避免辛辣')   # 约 45 字，短但正常
    fake = types.ModuleType('PyPDF2')

    class _Page:
        def __init__(self, text):
            self._t = text
        def extract_text(self):
            return self._t

    class _Reader:
        def __init__(self, f):
            self.pages = [_Page('空腹血糖 8.9 mmol/L 糖化血红蛋白 8.2 %' * 2)]

    fake.PdfReader = _Reader
    pdf_path = os.path.join(tmp, 'r.pdf')
    open(pdf_path, 'wb').write(b'%PDF-1.4 fake')
    real_mod = sys.modules.get('PyPDF2')
    sys.modules['PyPDF2'] = fake
    try:
        text, method = extract_report_text(pdf_path, 'r.pdf')
        check('PDF 有文字层 → pdf-text', method == 'pdf-text' and '空腹血糖' in text, method)

        # ⚠️ 回归：真实短报告（28 字，无指标名）不能被当成扫描件丢掉。
        #    阈值曾设 30，把这种正常报告判成"没文字层"，整条链读不出字。
        class _ShortReader:
            def __init__(self, f):
                self.pages = [_Page(_SHORT_REPORT)]
        fake.PdfReader = _ShortReader
        text, method = extract_report_text(pdf_path, 'r.pdf')
        check('短报告（%d 字）仍走文字层' % len(_SHORT_REPORT),
              method == 'pdf-text' and '慢性胃炎' in text, f'{method}/{text!r}')

        # 极短但含指标名 → 也算有文字层
        class _TinyReader:
            def __init__(self, f):
                self.pages = [_Page('空腹血糖 8.9')]
        fake.PdfReader = _TinyReader
        text, method = extract_report_text(pdf_path, 'r.pdf')
        check('极短但含指标名 → 文字层', method == 'pdf-text', f'{method}/{text!r}')

        # 扫描件页眉（第1页）无指标名且太短 → 判定为没有文字层，去走视觉
        class _ScanReader:
            def __init__(self, f):
                self.pages = [_Page(_SCAN_PAGE_TEXT)]
        fake.PdfReader = _ScanReader
        text, method = extract_report_text(pdf_path, 'r.pdf')
        check('扫描件页眉无内嵌图 → none（不猜）', text == '' and method == 'none',
              f'{method}/{text!r}')

        # PDF 打开炸 → none（ok=False 不走视觉）
        class _BoomReader:
            def __init__(self, f):
                raise ValueError('corrupt')
        fake.PdfReader = _BoomReader
        text, method = extract_report_text(pdf_path, 'r.pdf')
        check('损坏 PDF → none 不抛', text == '' and method == 'none', method)

        # 扫描 PDF 带内嵌大图 → pdf-vision（桩视觉可用）
        class _Img:
            def __init__(self, data):
                self.data = data
        class _ImgPage:
            def extract_text(self):
                return '第1页'
            @property
            def images(self):
                buf = __import__('io').BytesIO()
                _I.new('RGB', (1200, 1600), (250, 250, 250)).save(buf, 'JPEG')
                return [_Img(buf.getvalue())]
        class _ScanImgReader:
            def __init__(self, f):
                self.pages = [_ImgPage()]
        fake.PdfReader = _ScanImgReader
        llm_mod.LLMClient = _FakeClient
        try:
            text, method = extract_report_text(pdf_path, 'r.pdf')
            check('扫描 PDF → pdf-vision 且带标注',
                  method == 'pdf-vision' and VISION_SOURCE_NOTE.strip() in text
                  and '空腹血糖' in text, f'{method}/{text!r}')
        finally:
            llm_mod.LLMClient = real_cls
    finally:
        if real_mod is not None:
            sys.modules['PyPDF2'] = real_mod
        else:
            sys.modules.pop('PyPDF2', None)

    print('-' * 78)
    print(f'通过 {passed}/{total}')
    import sys as _s
    _s.exit(0 if passed == total else 1)