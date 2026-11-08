# -*- coding: utf-8 -*-
"""
详情页文本清洗与「营养信息」结构化。

## 为什么需要这个模块

LLM 是按「给人看的 markdown」写正文的，它会顺手写：
    **烹饪步骤**
    1. **备料**：西红柿去皮切小块……

而模板是当**纯文本**渲染的（不用 markdown 引擎，也不想为了这点事引依赖）。
老的 `.strip(' -•·*1234567890.、）')` 只能削掉**两头**的字符：

    "1. **备料**：西红柿…"  →  "备料**：西红柿…"     ← 中间那个 ** 留在正文里

页面上就出现了「备料**：」这种怪东西。**只能在渲染前把 markdown 洗掉**，
而且必须洗旧数据 —— 数据库里已经存着的食谱正文是洗不回来的。

## 营养信息为什么要拆

模型按「控糖角度 / 血压角度 / 血脂角度 / 尿酸角度 / 做法说明 / 进食建议」
六段写，一段七八百字，全都挤在一个 `<div>` 里。对使用者来说那不是信息，是一面墙：
手机上要滑三屏，看完也记不住一句话。

拆成三层的依据是**用户到底想知道什么**：
    1. 这顿多少热量、多少碳水？—— 四个数字，一眼扫完（metrics）
    2. 这道菜对我血糖影响大吗？—— 一句话（highlight）
    3. 想深究的细节            —— 折起来，想看才点（points）

⚠️「【请留意】血糖提醒」**不许折起来**：那是风险提示，藏起来就失去了意义。
"""

import re

# ── 共用：markdown 洗白 ────────────────────────────────────────
# 行首的装饰：`- ` / `•` / `*` / `#` / `1.` / `1、` / `（1）`
_LEAD_DECOR = re.compile(
    r'^\s*(?:#{1,4}\s*|[-•·]\s+|[*]\s+|'
    r'\d{1,2}\s*[.、)]\s*|'
    r'[（(]\s*\d{1,2}\s*[)）]\s*)'
)


def strip_md(text):
    """洗掉 markdown 装饰，只留能直接显示的文字。

    - `**粗体**` / `__粗体__` → 去星号（正文里不加粗，靠模板的层级）
    - 行首的 `#`、`-`、`1.`、`（1）` → 去掉
    - 行首行尾的空白 → 去掉

    不处理代码块/链接 —— 食谱正文里不会出现，真出现了也不该在这里猜。
    """
    if not text:
        return ''
    lines = []
    for raw in str(text).split('\n'):
        line = raw.replace('**', '').replace('__', '')
        line = _LEAD_DECOR.sub('', line)
        lines.append(line.strip())
    return '\n'.join(lines).strip()


# ── 编号分隔（烹饪步骤 / 营养分点 共用）─────────────────────────
# 只认「数字 + . 或 、」且**数字后面紧跟中文/字母**的分隔，且前一个字符必须
# 是换行 / 空白 / 中文冒号句号，这样才不会被正文里的数字切开：
#   `睡前1.5-2小时`   → `1.` 后面是数字 5，不切
#   `体重0.3-0.4g`    → `0.` 前面是汉字、后面是数字，不切
_NUM_SPLIT = re.compile(
    r'(?:(?<=\n)|(?<=\s)|(?<=[：:；;。])|\A)'
    r'\d{1,2}\s*[.、)]\s*(?=[\u4e00-\u9fa5A-Za-z])'
)


def split_steps(instructions):
    """把 `Recipe.instructions` 拆成一条条干净的步骤。

    兼容三种存法：
    - 每行一步（`1. …\\n2. …`）
    - 全挤在一行（`1. … 2. …`）
    - 既有换行又有内联编号

    返回 list[str]，空文本返回 []（模板直接不渲染这一块）。
    """
    text = (instructions or '').strip()
    if not text:
        return []

    text = text.replace('**', '').replace('__', '')
    parts = _NUM_SPLIT.split(text)

    steps = []
    for part in parts:
        # 编号拆完还要按换行再拆一次：一段里可能是「1. 焯水\n2. 翻炒」，
        # 也可能完全没有编号、纯靠换行分步（老数据里很常见）
        for line in part.split('\n'):
            step = _LEAD_DECOR.sub('', line.strip()).strip()
            if step:
                steps.append(step)
    return steps


# ── 营养信息 ──────────────────────────────────────────────────
# 「【请留意】血糖提醒」由 routes 在落库时追加（见 recipe_routes._attach_risk_notice）。
# 它必须在折叠区之外、且在指标下面显眼的位置。
_NOTICE_RE = re.compile(r'【请留意】\s*([^\n]+)')
# 免责声明以 `———` 单独一行分隔（`\n\n———\n`）
_DISC_SEP = '———'
# 段落标签里这些后缀是废话，显示时削掉：控糖角度 → 控糖
_LABEL_SUFFIX = ('角度', '方面', '说明', '建议', '提醒', '要点', '提示', '注意')
# 前缀同理：针对高血脂 → 高血脂
_LABEL_PREFIX = ('针对', '关于', '对于')
# 判定「一句话」优先展示哪条：先控糖，再血糖/碳水，最后兜底第一条
_HIGHLIGHT_KEYS = ('控糖', '血糖', '碳水', '升糖')

_METRIC_ORDER = (
    ('kcal', '热量', 'kcal'),
    ('protein', '蛋白质', 'g'),
    ('carb', '碳水', 'g'),
    ('fat', '脂肪', 'g'),
)


def _fmt_num(v):
    """215.0 → '215'；15.5 → '15.5'。营养值不需要补零。"""
    if v is None:
        return None
    if abs(v - round(v)) < 0.05:
        return str(int(round(v)))
    return ('%.1f' % v).rstrip('0').rstrip('.')


def _short_label(label):
    """`控糖角度` → `控糖`；`做法说明` → `做法`；`针对高血脂` → `高血脂`。

    削完只剩一两个字（比如本来就叫「做法」）就保持原样，别削成空。
    """
    lab = (label or '').strip(' *：:')
    for pre in _LABEL_PREFIX:
        if lab.startswith(pre) and len(lab) > len(pre) + 1:
            lab = lab[len(pre):]
            break
    for suf in _LABEL_SUFFIX:
        if len(lab) > len(suf) + 1 and lab.endswith(suf):
            return lab[: -len(suf)]
    return lab


def _first_sentence(text, limit=64):
    """截到第一个句号/分号，太长再按字数硬截。

    目的：折叠区外只留一句话。模型爱用「A；B；C」堆三个分句，
    截在分号处既读得懂又不会一行占满屏。
    """
    text = (text or '').strip()
    if not text:
        return ''
    m = re.search(r'^.{8,}?[。；;]', text)
    cut = m.group(0)[:-1] if m else text
    if len(cut) > limit:
        cut = cut[:limit] + '…'
    return cut.rstrip('，,、 ')


def _split_points(body):
    """把「1. 控糖角度：… 2. 血压角度：…」拆成 [(标签, 正文), …]。

    优先按编号拆；没有编号就按换行拆；一行都没有就把整段当一条。
    模型偶尔写成「【控糖】…【血压】…」，也一并支持。
    """
    text = (body or '').strip()
    if not text:
        return []

    chunks = [c for c in _NUM_SPLIT.split(text) if c and c.strip()]
    if len(chunks) <= 1:
        # 没有编号：看换行
        lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
        chunks = lines if len(lines) > 1 else [text]

    # 单行里用【xx】分段的写法
    if len(chunks) == 1 and text.count('【') >= 2:
        parts = re.split(r'(?=【)', text)
        chunks = [p for p in parts if p.strip()]

    points = []
    for chunk in chunks:
        # `健康提示：- 本餐蛋白质…` 这种，短横线跟在冒号后面而不是行首，
        # strip_md 的行首清洗盖不到，这里再削一次
        chunk = _LEAD_DECOR.sub('', chunk.strip(' 　'))
        if not chunk:
            continue
        label, rest = '', chunk
        m = re.match(r'^【([^】]{2,10})】\s*(.*)$', chunk)
        if m:
            label, rest = m.group(1), m.group(2)
        else:
            m = re.match(r'^([^：:。，,\n]{2,12})[：:]\s*(.+)$', chunk)
            if m:
                label, rest = m.group(1), m.group(2)
        rest = rest.strip()
        if not rest:
            continue
        points.append({'label': _short_label(label) if label else '', 'text': rest})
    return points


def build_nutrition_block(raw):
    """`Recipe.nutrition_info` 原文 → 模板直接可用的结构。

    Returns:
        None（原文为空）或 dict：
            metrics    -- [{'label': '碳水', 'value': '12 g'}, …]，缺项就不出现
            highlight  -- {'label': '控糖', 'text': '一句话'} 或 None
            points     -- [{'label', 'text'}, …] 折叠区里的细节（不含 highlight）
            notice     -- '【请留意】…' 的内容（血糖偏高提醒），不折叠
            disclaimer -- 免责声明
            fallback   -- 原文洗白后仍无处安放时的兜底文本（正常情况下是空串）
    """
    if not raw or not str(raw).strip():
        return None

    text = strip_md(raw)

    # 1) 免责声明：从 `———` 处切开
    if _DISC_SEP in text:
        body, _, disclaimer = text.partition(_DISC_SEP)
    else:
        body, disclaimer = text, ''
    body = body.strip()
    disclaimer = disclaimer.strip()

    # 2) 血糖提醒：单独拎出来，模板里放在指标下面、不折叠
    notice = ''
    m = _NOTICE_RE.search(body)
    if m:
        notice = m.group(1).strip()
        body = _NOTICE_RE.sub('', body).strip()

    # 3) 指标行：健康提示之前的这段才是「数字」，之后的都是话
    head, rest = body, ''
    for mark in ('健康提示', '💡', '提示：'):
        idx = body.find(mark)
        if idx > 0:
            head, rest = body[:idx], body[idx:]
            break
    else:
        # 没有「健康提示」字样：只有出现 kcal 的那一行算指标，其余仍当正文
        lines = body.split('\n')
        num_lines = [ln for ln in lines if 'kcal' in ln or '千卡' in ln]
        if num_lines:
            head = num_lines[0]
            rest = '\n'.join(ln for ln in lines if ln is not head)
        else:
            head, rest = '', body

    metrics = []
    if head.strip():
        try:
            from utils.nutrition_parser import parse_nutrition
            parsed = parse_nutrition(head) or {}
        except Exception:
            parsed = {}
        for key, label, unit in _METRIC_ORDER:
            val = _fmt_num(parsed.get(key))
            if val:
                metrics.append({'label': label, 'value': '%s %s' % (val, unit)})
        if not metrics:
            # 认不出数字（模型写成「- 总热量：约 450-500 kcal」之类）→ 原文照显示
            metrics = []

    # rest 去掉「💡 健康提示：」这层壳
    rest = re.sub(r'📊|💡', '', rest)
    rest = re.sub(r'^\s*健康提示\s*[：:]?', '', rest)
    rest = re.sub(r'健康提示\s*[：:]', '', rest).strip()

    points = _split_points(rest)

    # 4) 挑一句当门面
    highlight = None
    if points:
        pick = None
        for key in _HIGHLIGHT_KEYS:
            pick = next((p for p in points if key in p['label']), None)
            if pick:
                break
        pick = pick or points[0]
        highlight = {
            'label': pick['label'] or '提示',
            'text': _first_sentence(pick['text']),
        }
        points = [p for p in points if p is not pick]

    # 5) 指标为空、也没有分点 → 至少把开头一句亮出来，别整块空白
    fallback = ''
    if not metrics and not highlight:
        fallback = _first_sentence(body)
        points = []

    return {
        'metrics': metrics,
        'highlight': highlight,
        'points': points,
        'notice': notice,
        'disclaimer': disclaimer,
        'fallback': fallback,
    }
