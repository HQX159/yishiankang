# utils/rag.py
"""
RAG（检索增强生成）模块 — 关键词匹配 + LLM 食谱生成

采用轻量级关键词匹配替代 FAISS 向量检索：
- 知识库仅 7 条健康场景，无需 100MB 嵌入模型
- 毫秒级响应，无需等待模型下载/加载

场景取舍（2026-10-18 收敛）：本项目服务**糖尿病患者**，保留糖尿病 + 高血压 /
高血脂 / 痛风 / 肥胖 / 过敏 / 老年营养（都是糖友常见共病或安全红线）；
已下架「孕期营养」—— 妊娠期营养不属于中老年 2 型糖尿病的服务范围。
"""
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 控糖饮食知识库（关键词 → 饮食建议）
# ---------------------------------------------------------------------------
HEALTH_KNOWLEDGE = {
    "糖尿病": {
        "keywords": ["糖尿病", "血糖", "控糖", "胰岛素", "糖化", "空腹血糖", "餐后血糖",
                     "低GI", "升糖"],
        "advice": (
            "糖尿病患者应控制碳水化合物摄入，优先选择低GI食物（如燕麦、荞麦、全麦面包），"
            "每餐搭配足量蔬菜和优质蛋白质。避免含糖饮料、精制米面和甜点。"
            "推荐食材：苦瓜、秋葵、山药、洋葱、银耳、鸡胸肉、深海鱼。"
        ),
    },
    "高血压": {
        "keywords": ["高血压", "血压", "降压", "低盐", "低钠", "收缩压", "舒张压",
                     "心血管", "钠", "盐"],
        "advice": (
            "高血压患者应严格控制钠盐摄入（每日<5g），增加钾、钙、镁的摄入。"
            "多食新鲜蔬果、全谷物和低脂乳制品。避免腌制食品、加工肉类和高盐调味品。"
            "推荐食材：芹菜、菠菜、香蕉、紫菜、黑木耳、脱脂牛奶、藜麦。"
        ),
    },
    "高血脂": {
        "keywords": ["高血脂", "血脂", "胆固醇", "甘油三酯", "降脂", "低脂",
                     "饱和脂肪", "LDL", "HDL", "脂蛋白"],
        "advice": (
            "高血脂患者应限制饱和脂肪和反式脂肪摄入，增加不饱和脂肪酸（如橄榄油、坚果）。"
            "多摄入膳食纤维（每日25-30g），选择瘦肉和去皮禽肉。"
            "推荐食材：燕麦、山楂、深海鱼（三文鱼、鲭鱼）、豆制品、大蒜、洋葱。"
        ),
    },
    "过敏体质": {
        "keywords": ["过敏", "过敏原", "过敏史", "避敏", "过敏性", "荨麻疹",
                     "湿疹", "不耐受", "乳糖", "麸质"],
        "advice": (
            "过敏体质者需严格回避已知过敏原，谨慎尝试新食材。"
            "建议记录饮食日记以排查潜在过敏源。"
            "推荐低敏食材：大米、小米、鸡胸肉、瘦猪肉、白菜、胡萝卜、西兰花、苹果、梨。"
        ),
    },
    "痛风/高尿酸": {
        "keywords": ["痛风", "尿酸", "高尿酸", "嘌呤", "关节", "肾结石"],
        "advice": (
            "痛风患者应限制高嘌呤食物，每日饮水2000ml以上以促进尿酸排泄。"
            "避免动物内脏、浓肉汤、海鲜（贝类、虾蟹）、啤酒。"
            "推荐食材：鸡蛋、低脂奶制品、土豆、番茄、黄瓜、樱桃、薏米。"
        ),
    },
    "肥胖/减重": {
        "keywords": ["肥胖", "减重", "减肥", "超重", "BMI", "体重指数",
                     "体脂", "热量", "卡路里", "瘦身"],
        "advice": (
            "减重期应控制总热量摄入，制造每日300-500kcal的热量缺口。"
            "增加蛋白质摄入（每kg体重1.2-1.6g）以维持肌肉量，配合规律运动。"
            "推荐食材：鸡胸肉、鸡蛋、西兰花、燕麦、红薯、希腊酸奶、牛油果。"
        ),
    },
    "老年营养": {
        "keywords": ["老年", "高龄", "骨质疏松", "肌少", "衰老", "消化",
                     "钙", "维生素D"],
        "advice": (
            "老年人消化吸收功能下降，需选择易消化、高营养密度的食物。"
            "增加优质蛋白摄入以预防肌少症，注意钙和维生素D补充。"
            "推荐食材：鱼肉、鸡蛋羹、豆腐、南瓜、山药、牛奶、芝麻糊。"
        ),
    },
}


# ---------------------------------------------------------------------------
# 关键词匹配 + Prompt 构建
# ---------------------------------------------------------------------------
def build_enhanced_prompt(health_report_text, ingredients_text, user_query,
                          purpose=None, flavor=None, meal_time=None):
    """
    基于关键词匹配构建增强 prompt（毫秒级响应，无需模型加载）。

    工作原理：
    1. 将健康报告 + 用户查询拼接，扫描是否含有关键词（糖尿病、高血压…）
    2. 命中则注入对应的饮食建议知识
    3. 用户在生成页选的「目的 / 口味 / 用餐时间」作为硬要求拼进 prompt（R4）
    4. 组装成 LLM 可直接使用的增强 prompt

    Args:
        health_report_text: 健康报告 OCR 文本
        ingredients_text: 冰箱食材列表（逗号分隔）
        user_query: 用户的口语化需求
        purpose: 饮食目的 key（见 utils/recipe_options.py），可选
        flavor: 饮食口味 key，可选
        meal_time: 用餐时间 key，可选

    Returns:
        str: 注入知识后的完整 LLM prompt
    """
    from utils.recipe_options import build_prompt_clause, purpose_knowledge_condition

    combined = ((health_report_text or "") + " " + (user_query or "")).lower()

    matched = []

    # 用户明确选了「控糖 / 降压 / 控脂减重」，直接注入对应知识 ——
    # 不再依赖那几个字是否恰好出现在报告或输入框里。
    pinned = purpose_knowledge_condition(purpose)
    if pinned and pinned in HEALTH_KNOWLEDGE:
        matched.append(f"【{pinned}】{HEALTH_KNOWLEDGE[pinned]['advice']}")

    for condition, data in HEALTH_KNOWLEDGE.items():
        if condition == pinned:
            continue                # 上面已经加过，别重复
        for kw in data["keywords"]:
            if kw.lower() in combined:
                matched.append(f"【{condition}】{data['advice']}")
                break  # 每个场景只添加一次

    # 组装
    sections = []

    if health_report_text:
        sections.append(f"## 用户健康报告摘要\n{health_report_text[:500]}")

    if ingredients_text:
        sections.append(f"## 冰箱现有食材\n{ingredients_text}")

    if matched:
        sections.append("## 相关控糖饮食知识\n" + "\n\n".join(matched))

    # R4 三维度：放在「用户需求」之前 —— 结构化选项比自由文本优先级更高
    sections.append(build_prompt_clause(purpose, flavor, meal_time))

    if user_query:
        sections.append(f"## 用户需求\n{user_query}")

    prompt = (
        "\n\n".join(sections)
        + "\n\n请根据以上信息，生成一份适合这位用户、做法具体、口味好吃的个性化食谱。"
        "严格按以下格式输出：\n\n"
        "**菜品名称**：一个吸引人的菜名\n\n"
        "**所需食材**：\n- 食材1（用量）\n- 食材2（用量）\n\n"
        "**烹饪步骤**：\n1. 步骤1\n2. 步骤2\n\n"
        "**营养信息**：热量约xxx kcal | 蛋白质xxx g | 碳水xxx g | 脂肪xxx g\n\n"
        # 改造前写的是「针对用户健康状况的特别说明」，模型理解成写论文：
        # 控糖/血压/血脂/尿酸/做法/进食各来一段，六段七八百字。
        # 详情页把它整段铺出来就是一面墙，糖友根本不看。
        # 现在明确限死字数和不许分点 —— 细节该进「控糖提示」那套结构化卡片，
        # 不该塞进健康提示里重复一遍。
        "**健康提示**：最多 2 句，每句不超过 40 字，只说这道菜对血糖的影响、"
        "以及吃的时候要注意什么。不要分点、不要加小标题、不要加粗、"
        "不要重复营养信息里已经写过的数字。"
    )

    return prompt


# ---------------------------------------------------------------------------
# 完整食谱生成管线
# ---------------------------------------------------------------------------
def generate_recipe_with_rag(health_report_text, ingredients_text, user_query,
                             purpose=None, flavor=None, meal_time=None):
    """
    完整的食谱生成管线：增强 prompt → 调用 LLM → 返回食谱文本。

    这是路由层应该直接调用的函数，封装了 RAG + LLM 的全流程。

    Args:
        health_report_text: 健康报告 OCR 文本
        ingredients_text: 冰箱食材列表
        user_query: 用户需求
        purpose / flavor / meal_time: R4 三维度（见 utils/recipe_options.py），可选；
                                     不传或传非法值时按默认档走，行为同改造前。

    Returns:
        str: LLM 生成的完整食谱文本
    """
    from utils.llm_client import LLMClient

    # 步骤1：构建增强 prompt
    prompt = build_enhanced_prompt(health_report_text, ingredients_text, user_query,
                                   purpose=purpose, flavor=flavor, meal_time=meal_time)
    logger.info(f"增强 prompt 已构建，长度 {len(prompt)} 字符")

    # 步骤2：调用 LLM 生成食谱
    llm = LLMClient()
    recipe = llm.generate_text(
        prompt,
        system_prompt=(
            "你是一位资深营养师和烹饪大师，擅长根据用户的健康状况和现有食材，"
            "设计出既健康又美味的个性化食谱。请严格按要求的格式输出，"
            "所有建议需科学有据、安全可行。"
        ),
        temperature=0.7,
        max_tokens=1500,
    )

    return recipe
