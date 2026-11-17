# 刺与泡沫 · 关系演练场

一个「先别讲道理，先练一遍」的沟通演练 Web 应用。你选一个高难度关系场景，和 AI 扮演的角色对话；系统实时判断你的表达策略、对方的防御档位，并在右栏给出沟通风格诊断和一句可以直接发过去的话。

## 现在能做什么

- **4 个场景**：适老化改造、谈钱、谈加薪、挚友冷战。
- **角色回弹**：对方台词来自按防御档位整理的台词库，不靠模型自由发挥。
- **策略识别**：每轮标出「要求 / 解释 / 共情 / 回避」。
- **5 档防御**：分数与台词强绑定，5 分是暴怒反击，1 分是卸下防备。
- **沟通私教**：右侧显示情绪状态、沟通风格诊断、防御曲线和「一键抄作业」。
- **主动干预**：连续三句高防御时，教练会插话叫停，并给一句可执行的改写。
- **复盘报告**：防御走势、策略分布、关键软化句、跨会话画像。
- **本地存储**：SQLite 保存每一场演练，刷新/重启后可以继续回看。

## 运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
streamlit run app.py
```

默认地址：<http://127.0.0.1:8501>

小抄默认会调用一次模型做润色；如果只想走本地规则（断网/演示兜底），在 `.env` 里设置：

```ini
SAY_COPY_MODEL=0
```

## 测试

```powershell
$env:PYTHONUTF8=1
python scripts/selftest.py
python scripts/test_scenes.py
python scripts/test_anchor_authority.py
python scripts/test_db.py
python scripts/test_bestie.py
python scripts/test_hints.py
python scripts/test_case_queue.py
python scripts/test_app.py
```

`test_app.py` 会走真实模型接口，需要一个可用的 `DEEPSEEK_API_KEY`。如果只想跑离线测试，跳过它即可。

## 目录

```text
app.py             Streamlit 页面与交互
agent.py           一轮对话：选锚点 → 约束档位 → 返回固定台词
rules.py           防御分因果约束
scenes.py          4 个场景、台词库、AnchorLine
models.py          Turn / Session 数据模型
db.py              SQLite 持久化与迁移
charts.py          防御曲线与标注
hints.py           L1 规则小抄 + L2 模型润色
coach.py           教练点评
insight.py         跨会话画像
theme.py           页面样式
assets/            角色头像
content/           台词 JSON
docs/              PLAN / PRD / 文档页
scripts/           测试与构建脚本
```

## 隐私

- `.env`、`data/`（SQLite、报告、截图、日志）已在 `.gitignore` 中排除。
- 仓库里只有代码、台词、文档和头像，不包含真实对话记录或 API Key。
