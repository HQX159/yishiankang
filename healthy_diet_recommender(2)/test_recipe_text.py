# -*- coding: utf-8 -*-
"""自测：详情页文本清洗 + 营养信息分层展示

覆盖：
    1. strip_md：`**` 与行首编号洗净
    2. split_steps：`1. **备料**：…` → `备料：…`（改造前会显示成 `备料**：`）
       以及单行内联编号、无编号、空文本、正文里的小数不许被切开
    3. build_nutrition_block：四个数字 / 一句话 / 折叠细节 / 血糖提醒 / 免责声明
       各自的归属是否正确
    4. 边界：没有健康提示、认不出数字、原文为空
    5. 真实页面：详情页与社区详情页都不再出现 `**`，
       风险提醒**不在** `<details>` 里（藏起来等于没提示）

用法
    py test_recipe_text.py
"""
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

dbpath = os.path.join(tempfile.gettempdir(), 'ysak_recipe_text_test.db')
if os.path.exists(dbpath):
    os.remove(dbpath)
os.environ['DATABASE_URL'] = 'sqlite:///' + dbpath.replace('\\', '/')

sys.path.insert(0, '.')
from app import create_app                     # noqa: E402
from utils.database import db                  # noqa: E402
from models.user import User                   # noqa: E402
from models.recipe import Recipe               # noqa: E402
from models.community import CommunityPost     # noqa: E402
from utils.recipe_text import (                # noqa: E402
    strip_md, split_steps, build_nutrition_block,
)

app = create_app()
client = app.test_client()

passed = 0
failed = 0


def check(name, cond, extra=''):
    global passed, failed
    if cond:
        passed += 1
        print('  PASS  %s' % name)
    else:
        failed += 1
        print('  FAIL  %s   %s' % (name, extra))


# 取自线上真实食谱（id=36），保留模型原始的写法
RAW_NUTRITION = """热量约215 kcal | 蛋白质15.5 g | 碳水12 g | 脂肪11.5 g

💡 健康提示：1. **控糖角度**：本菜碳水仅约12g，主要来自番茄和黄瓜，升糖负荷低，适合作为夜间加餐，不会造成睡前血糖明显波动；鸡蛋提供优质蛋白，有助于增加饱腹感、减少夜间饥饿导致的加餐冲动。注意本菜**不含主食**，若睡前有明显饥饿感，可另配1小片全麦面包（约30g）并计入全日主食总量。
2. **血压角度**：全菜用盐控制在0.8g以内，以香醋、黑胡椒、番茄自身鲜味替代咸味，符合高血压低钠原则；番茄、黄瓜、生菜富含钾，有助于钠钾平衡。
3. **血脂角度**：用油仅3g且为橄榄油，鸡蛋虽含胆固醇但每日2个以内对多数人血脂影响有限，整体饱和脂肪极低；蘑菇膳食纤维有助于血脂管理。
4. **尿酸角度**：鸡蛋、番茄、黄瓜、蘑菇均为低嘌呤食材，适合高尿酸人群；建议夜宵后适量饮水，促进尿酸排泄。
5. **做法说明**：本菜为「烩+拌」组合，与汤配蒸菜的形态明显不同——热烩菜浓郁、冷拌菜爽脆，口感层次更丰富。
6. **进食建议**：夜宵建议在睡前1.5-2小时完成，进食后避免立即平卧。

【请留意】空腹血糖 8.9 mmol/L 偏高，建议结合医师意见调整饮食

———

本建议基于公开膳食指南的通用原则生成，不能替代医师的诊断与处方。如有不适请及时就医。"""


# ── 1) strip_md ────────────────────────────────────────────
print('=== 1) markdown 洗白 ===')
check('去掉加粗星号', strip_md('**备料**：切块') == '备料：切块')
check('去掉行首编号', strip_md('1. 焯水') == '焯水')
check('去掉中文顿号编号', strip_md('2、翻炒') == '翻炒')
check('去掉行首短横', strip_md('- 黄瓜 200g') == '黄瓜 200g')
check('去掉标题井号', strip_md('## 烹饪步骤') == '烹饪步骤')
check('真实段落不再有星号', '**' not in strip_md(RAW_NUTRITION))
check('空串安全', strip_md('') == '' and strip_md(None) == '')
check('正文里的星号全部清掉',
      '**' not in strip_md('**A** 与 **B** 都要'))


# ── 2) split_steps ────────────────────────────────────────
print('=== 2) 烹饪步骤 ===')
steps = split_steps("1. **备料**：西红柿去皮切小块\n"
                    "2. **焯蘑菇**：水开后焯30秒\n"
                    "3. **淋蛋液**：静置10秒")
check('拆出三步', len(steps) == 3, steps)
check('不再出现「备料**：」这种残留', all('**' not in s for s in steps), steps)
check('步骤名保留下来（备料：…）', steps[0].startswith('备料'), steps[0])
check('编号没混进正文', all(not s[0].isdigit() for s in steps), steps)

one_line = split_steps('1. 焯水 2. 翻炒 3. 装盘')
check('单行内联编号也能拆', len(one_line) == 3, one_line)

plain = split_steps('把菜洗净\n切成段')
check('没有编号按行拆', plain == ['把菜洗净', '切成段'], plain)

check('空文本返回空列表', split_steps('') == [] and split_steps(None) == [])

num = split_steps('1. 夜宵建议在睡前1.5-2小时完成 2. 进食后别立即平卧')
check('正文里的小数不会被当成编号切开',
      len(num) == 2 and '1.5-2小时' in num[0], num)


# ── 3) build_nutrition_block ──────────────────────────────
print('=== 3) 营养信息分层 ===')
blk = build_nutrition_block(RAW_NUTRITION)

check('四个指标都拿到了', len(blk['metrics']) == 4, blk['metrics'])
check('指标值正确',
      [m['value'] for m in blk['metrics']] == ['215 kcal', '15.5 g', '12 g', '11.5 g'],
      blk['metrics'])
check('指标标签是中文',
      [m['label'] for m in blk['metrics']] == ['热量', '蛋白质', '碳水', '脂肪'],
      blk['metrics'])

lead = blk['highlight']
check('门面句挑的是控糖角度', lead and lead['label'] == '控糖', lead)
check('门面句只有一句，不是整段',
      lead and len(lead['text']) <= 70 and '血压角度' not in lead['text'], lead and lead['text'])
check('门面句讲清了碳水与升糖', '碳水' in lead['text'] and '升糖' in lead['text'], lead['text'])

labels = [p['label'] for p in blk['points']]
check('其余五条进折叠区', len(blk['points']) == 5, labels)
check('折叠区标签去掉了「角度」二字',
      '血压' in labels and '血脂' in labels and '尿酸' in labels, labels)
check('门面句不在折叠区重复', lead['label'] not in labels, labels)

check('血糖提醒单独拎出来（不折叠）', '空腹血糖 8.9' in blk['notice'], blk['notice'])
check('血糖提醒里没有【请留意】这层壳', '【请留意】' not in blk['notice'], blk['notice'])
check('免责声明单独拿到', blk['disclaimer'].startswith('本建议基于'), blk['disclaimer'])
check('免责声明不在正文里重复', '不能替代医师' not in ' '.join(p['text'] for p in blk['points']))
check('全块没有残留星号',
      '**' not in str(blk))

# ── 4) 边界 ───────────────────────────────────────────────
print('=== 4) 边界情况 ===')
none_blk = build_nutrition_block('')
check('原文为空返回 None', none_blk is None)
check('None 也返回 None', build_nutrition_block(None) is None)

only_num = build_nutrition_block('热量约 320 kcal | 蛋白质 28 g | 碳水 8 g | 脂肪 9 g')
check('只有数字也能出指标', len(only_num['metrics']) == 4, only_num['metrics'])
check('没有健康提示时门面与折叠区都为空',
      only_num['highlight'] is None and only_num['points'] == [])

prose = build_nutrition_block('这道菜很适合糖友，碳水低，蛋白质够。吃的时候别配白粥。')
check('纯叙述没有数字 → 指标为空', prose['metrics'] == [])
check('纯叙述也能给出一句话', bool(prose['highlight'] or prose['fallback']),
      prose['highlight'] or prose['fallback'])
check('纯叙述不会异常拆成很多条', len(prose['points']) <= 1, prose['points'])

no_notice = build_nutrition_block(
    '热量约 240 kcal | 蛋白质 28 g | 碳水 8 g | 脂肪 9 g\n\n'
    '💡 健康提示：低碳水，适合控糖加餐')
check('没有风险提醒时 notice 是空串', no_notice['notice'] == '')
check('单句健康提示直接当门面', no_notice['highlight']['label'] == '提示',
      no_notice['highlight'])
check('单句健康提示没有折叠区', no_notice['points'] == [])


# ── 5) 真实页面 ───────────────────────────────────────────
print('=== 5) 页面渲染 ===')
client.post('/api/user/register', data={'username': 'txt_a', 'password': 'abc123'})
client.post('/api/user/login', data={'identifier': 'txt_a', 'password': 'abc123'})
with app.app_context():
    uid = User.query.filter_by(username='txt_a').first().id
    r = Recipe(user_id=uid, title='番茄蘑菇烩蛋',
               ingredients='西红柿 200g\n蘑菇 100g\n鸡蛋 2个',
               instructions="1. **备料**：西红柿去皮切小块\n2. **焯蘑菇**：焯30秒\n"
                            "3. **淋蛋液**：静置10秒",
               nutrition_info=RAW_NUTRITION)
    db.session.add(r)
    db.session.commit()
    rid = r.id

html = client.get('/api/recipes/recipes/%d' % rid).get_data(as_text=True)
check('详情页不再出现裸星号', '**' not in html)
check('步骤名正常显示（备料：…）', '备料：西红柿去皮' in html, html[:0])
check('四个营养数字都在页面上', all(x in html for x in ('215 kcal', '15.5 g', '12 g')))

check('细节收进了折叠区', '<details class="nut-more">' in html)
check('折叠区标题报了条数', '还有 5 条饮食说明' in html)
check('血糖提醒在折叠区之前',
      html.find('nut-notice') < html.find('nut-more') if 'nut-notice' in html else False)
check('血糖提醒没有被折叠',
      # 原来的写法是「第一个 <details> 之前必须有血糖提醒」—— 太依赖折叠块的先后顺序。
      # R5 第 4 项在营养区**之前**又加了一个 <details>（升糖依据），这条就假失败了。
      # 真正的语义是：把所有折叠内容整个拿掉，血糖提醒还在页面上。
      '空腹血糖 8.9' in re.sub(r'<details[\s\S]*?</details>', '', html))
check('免责声明在页面底部', '不能替代医师的诊断与处方' in html)
check('一句门面话在页面上', '升糖负荷低' in html)

# 旧的整段铺法已经拆掉：不该再有把换行替换成 <br> 的大段
check('不再是一整段 <br> 墙', 'nutrition-content' not in html)

with app.app_context():
    post = CommunityPost(recipe_id=rid, user_id=uid, note='控糖夜宵')
    db.session.add(post)
    db.session.commit()
    pid = post.id

chtml = client.get('/api/community/post/%d' % pid).get_data(as_text=True)
check('社区详情页同样洗净星号', '**' not in chtml)
check('社区详情页也有折叠区', 'nut-more' in chtml)
check('社区详情页也有血糖提醒', 'nut-notice' in chtml)


print('\n%d/%d 通过' % (passed, passed + failed))
sys.exit(0 if failed == 0 else 1)
