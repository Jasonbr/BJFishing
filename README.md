# BJFishing — 北京钓鱼分析 MCP Server

> 北京钓鱼智能分析与策略推荐系统。基于 Open-Meteo 气象数据 + Astral 天文计算 + 北京本地化合规规则，为钓友提供鱼情评分、策略建议和合规检查。

## 功能概览

| 子命令 | 说明 | 示例 |
|--------|------|------|
| `collect` | 采集钓点环境数据 | `python cli.py collect --spot-name "密云水库"` |
| `analyze` | 综合分析鱼情评分 | `python cli.py analyze --spot-name "温榆河" --water-type river` |
| `report` | 生成策略报告（JSON / Qwen / Ollama） | `python cli.py report --spot-name "温榆河" --output-mode json` |
| `submit` | 提交渔获反馈（触发自学习调权） | `python cli.py submit --spot-name "温榆河" --species crucian_carp --rating 4 --score 0.72` |
| `tune` | 查看调权状态 / 重算 / 导出 | `python cli.py tune --recompute` |

## 快速开始

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 采集数据
python cli.py collect --spot-name "密云水库"

# 分析鱼情
python cli.py analyze --spot-name "温榆河顺义段" --water-type river --species-id crucian_carp

# 生成报告
python cli.py report --spot-name "温榆河顺义段" --output-mode json

# 提交渔获反馈
python cli.py submit --spot-name "温榆河" --species crucian_carp --rating 4 --score 0.72

# 查看调权状态
python cli.py tune
```

### Docker 部署

```bash
docker build -t bjfishing .
docker run --rm -p 8000:8000 bjfishing
# 或使用 docker-compose
docker-compose up
```

### MCP Server 模式

```bash
python -m services.server
```

## 架构

```
┌─────────────────────────────────────────────────┐
│                  CLI / MCP Server                │
│              cli.py  /  services/server.py       │
├─────────────────────────────────────────────────┤
│  tools/         │  collect.py  analyze.py       │
│                 │  report.py    submit_catch.py │
├─────────────────────────────────────────────────┤
│  engine/        │  season    water_temp          │
│                 │  oxygen    pressure             │
│                 │  temperature  solunar           │
│                 │  wind      precipitation        │
│                 │  weights                        │
├─────────────────────────────────────────────────┤
│  compliance/    │  gate.py (禁渔期+饮用水源+渔具)  │
│  strategy/      │  position bait time_windows risk│
│  feedback/      │  storage.py  tuning.py          │
├─────────────────────────────────────────────────┤
│  services/      │  geocode  weather  astronomy   │
│  knowledge/     │  beijing_waters  compliance     │
│  config.py     │  BJ_TZ / settings / paths       │
└─────────────────────────────────────────────────┘
```

## 评分模型

### 综合评分公式

```
fishing_score = Σ(score_i × weight_i) + feedback_adjustment
```

### 默认权重

| 维度 | 权重 | 说明 |
|------|------|------|
| pressure | 0.25 | 气压（最关键因子） |
| temperature | 0.20 | 水温/气温适配 |
| solunar | 0.15 | 月相/日出日落 |
| wind | 0.10 | 风速/风向 |
| precipitation | 0.10 | 降水影响 |
| season | 0.10 | 季节系数 |
| water | 0.05 | 水域类型 |

权重随季节动态调整（`engine/weights.py`），反馈调权范围 ±0.10（`FEEDBACK_ADJUSTMENT_MAX`）。

### 校准常量

#### 溶氧模型 (`engine/oxygen.py`)

| 常量 | 值 | 说明 |
|------|----|------|
| `_O2_CONST` | 14.6 | 0℃ 时饱和溶氧 (mg/L) |
| `_O2_TEMP_COEFF` | 0.4 | 一次项系数 |
| `_O2_TEMP_SQUARED` | 0.008 | 二次项系数 |
| `_O2_EXCELLENT` | 8.0 | 溶氧充足阈值 |
| `_O2_GOOD` | 6.0 | 溶氧适中阈值 |

饱和溶氧公式: `sat_o2 = 14.6 - 0.4×T + 0.008×T²`

#### 水温估算 (`engine/water_temp.py`)

| 常量 | 值 | 说明 |
|------|----|------|
| `_BASE_COEFF` | 0.75 | 气温→水温转换系数 |
| `_BASE_OFFSET` | 3.5 | 基础偏移 (℃) |

公式: `water_temp = 0.75 × air_temp_avg + 3.5 + water_type_adjustment`

#### 气压评分 (`engine/pressure.py`)

| 常量 | 值 | 说明 |
|------|----|------|
| `_PRESSURE_NORMAL_MIN` | 1000.0 | 正常气压下限 (hPa) |
| `_PRESSURE_NORMAL_MAX` | 1025.0 | 正常气压上限 (hPa) |

#### 反馈调权 (`feedback/tuning.py`)

| 常量 | 值 | 说明 |
|------|----|------|
| `FEEDBACK_ADJUSTMENT_MAX` | 0.10 | 最大调权幅度 (±10%) |
| train_ratio | 0.8 | 训练集比例 (80/20 split) |

调权算法: `adjustment = clip(mean(rating_norm - fishing_score), ±0.10)`

## 合规规则

### 禁渔期

- **天然水域**（河流/水库）: 4 月 1 日 ~ 7 月 31 日
- **黑坑**（商业钓场）: 全年开放

### 饮用水源保护区（全年禁钓）

- 密云水库 (`miyun_reservoir`)
- 怀柔水库 (`huairou_reservoir`)

### 禁用渔具渔法

| 渔具 | 严重程度 | 处罚 |
|------|----------|------|
| 电鱼 | 刑事 | 追究刑事责任 |
| 毒鱼 | 刑事 | 追究刑事责任 |
| 炸鱼 | 刑事 | 追究刑事责任 |
| 刺网 | 行政 | 罚款 1000-5000 元 |
| 地笼 | 行政 | 罚款 500-2000 元 |
| 多钩长线 | 行政 | 罚款 500-2000 元 |

## 开发

### 测试

```bash
# 运行全部测试（含覆盖率）
python -m pytest

# 类型检查
python -m mypy .

# Lint
python -m ruff check .
```

### 覆盖率要求

- engine: > 90%
- tools: > 80%
- services: > 70%
- 总覆盖率: > 80%

### 项目结构

```
BJFishing/
├── cli.py              # CLI 入口（5 子命令）
├── config.py           # 配置管理（pydantic-settings）
├── logging_config.py   # 结构化日志
├── compliance/
│   └── gate.py         # 合规前置拦截
├── engine/
│   ├── season.py       # 季节判断
│   ├── water_temp.py   # 水温估算
│   ├── oxygen.py       # 溶氧推算
│   ├── pressure.py     # 气压评分
│   ├── temperature.py  # 温度评分
│   ├── solunar.py      # 月相评分
│   ├── wind.py         # 风况评分
│   ├── precipitation.py# 降水评分
│   └── weights.py      # 动态权重
├── strategy/
│   ├── position.py     # 钓位推荐
│   ├── bait.py         # 饵料推荐
│   ├── time_windows.py# 时间窗口
│   └── risk.py         # 风险评估
├── tools/
│   ├── collect.py      # 数据采集
│   ├── analyze.py      # 综合分析
│   ├── report.py       # 报告生成
│   └── submit_catch.py # 渔获提交
├── feedback/
│   ├── storage.py      # SQLite 存储
│   └── tuning.py       # 自学习调权
├── services/
│   ├── geocode.py      # 地理编码
│   ├── weather.py      # 气象服务
│   ├── astronomy.py    # 天文计算
│   └── server.py       # MCP Server
├── knowledge/
│   ├── beijing_waters.yaml  # 北京水域
│   ├── compliance_2026.yaml # 合规规则
│   ├── season_model.yaml    # 四季模型
│   └── species_temp.yaml    # 鱼种适温
└── tests/
    ├── test_engine.py       # 引擎测试
    ├── test_compliance.py   # 合规测试
    ├── test_services.py     # 服务测试
    ├── test_integration.py  # 集成测试
    └── test_feedback.py     # 反馈测试
```

## 免责声明

**本系统生成的鱼情分析、评分和策略建议仅供参考，不构成任何专业建议。** 钓鱼活动请遵守《中华人民共和国渔业法》及北京市相关法律法规，注意人身安全，不在禁渔期、禁渔区、饮用水源保护区垂钓。系统不对钓获结果做任何保证，用户需自行承担钓鱼活动的全部风险。

## License

MIT
