# Auto PR Radar v5

一个面向汽车品牌传播团队的每日公开信息监测脚本。

目标：每天自动回答四件事：

1. 今天汽车行业发生了什么值得知道的事
2. 哪些数据、政策、竞品动作正在出现变化
3. 哪些变化可能发展成传播选题
4. 哪些信息需要继续补数据、找案例、做深度策划

当前版本不抓：小红书、抖音等社媒；KOL；公司内部信息。

## v5 新增

- 飞书自定义机器人自动推送
- DeepSeek 默认配置更新
- 国内财经与汽车行业信息源
- 国家统计局、国家能源局、中国充电联盟、海关统计等数据源
- 前一天重复信息过滤
- LLM event_key 事件级二次去重
- 每日信息源健康检查
- 72 小时新鲜度过滤
- 首页健康检查结果复用，减少重复请求
- 修复 URL 去重误删 query id 的问题
- delivered 只记录真正进入日报/飞书的内容
- 飞书发送失败时不推进 seen/delivered 状态，下一次可重试
- LLM 从逐条请求改成批量请求
- GitHub Actions 增加自动测试
- `04 政策 / 行业变量` 现在同时包含 policy 和 industry_news

## 运行流程

```text
GitHub Actions 每天 07:37
        ↓
信息源健康检查
        ↓
抓取公开信息
        ↓
时间新鲜度过滤
        ↓
同日跨来源去重
        ↓
检查前一天是否已经发过
        ↓
节点 / 数据 / 洞察评分
        ↓
DeepSeek 批量分析
        ↓
event_key 事件级二次去重
        ↓
生成 Markdown 日报
        ↓
推送飞书 Bot
        ↓
成功后写入 seen / delivered
        ↓
自动 commit 回 GitHub
```

## 当前执行信息源

`config/sources.yaml` 是脚本真实读取的执行配置。

当前共 **56 个来源**：

- 市场 / 数据：6
- 政策 / 监管：18
- 竞品官方：19
- 行业新闻：13

信息源等级：

- S 级事实源：43
- A 级分析源：9
- B 级线索源：4

### 市场与数据

已配置：

- 中国汽车流通协会 CADA
- 乘联分会 CPCA
- 中国汽车工业协会 CAAM
- 国家统计局
- 中国充电联盟 EVCIPA
- 海关总署统计

重点抓：

- 总销量、零售、批发
- 新能源渗透率
- 纯电 / 插混 / 增程结构
- 库存
- 出口
- 汽车类消费
- 充换电基础设施

### 国家与地方政策

国家层面：

- 中国政府网
- 工信部
- 发改委
- 商务部
- 财政部
- 国家税务总局
- 市场监管总局
- 交通运输部
- 国家能源局

地方：

- 北京
- 上海
- 广东
- 深圳
- 浙江
- 江苏
- 福建
- 四川
- 重庆

### 中国竞品官方

- 理想
- 小鹏
- 小米
- 比亚迪
- 吉利 / 银河 / 极氪
- 零跑
- 赛力斯 / 问界
- 阿维塔
- 岚图

### 海外竞品官方

- Tesla
- BMW
- Mercedes-Benz
- Audi
- Volkswagen
- Porsche
- Volvo
- Toyota
- GM
- Ford

### 国内行业媒体

v5 新增 / 强化：

- 第一财经汽车
- 财联社汽车早报
- 界面新闻汽车
- 每日经济新闻汽车
- 经济观察网汽车
- 盖世汽车原创
- 汽车产经网
- 36氪汽车
- 新华汽车

用途分级：

- A 级：用于发现行业结构、经营、技术、竞争变化
- B 级：用于发现线索，重要事实继续回查 S 级来源

### 海外行业媒体

- Reuters Autos & Transportation
- Financial Times Automobiles
- TechCrunch Transportation
- The Verge Transportation

## DeepSeek 配置

v5 对 DeepSeek 做了默认配置。

最少只需要在 GitHub 设置：

```text
DEEPSEEK_API_KEY
```

脚本默认：

```text
base_url = https://api.deepseek.com
model = deepseek-v4-flash
```

也兼容旧的通用配置：

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

如果 `LLM_MODEL` 未设置，会使用 `deepseek-v4-flash`。

## 飞书 Bot 配置

进入：

```text
GitHub Repository
→ Settings
→ Secrets and variables
→ Actions
```

新增 Repository Secrets：

```text
FEISHU_WEBHOOK_URL
```

如果你的飞书自定义机器人开启了“签名校验”，再增加：

```text
FEISHU_SIGNING_SECRET
```

可选 Repository Variable：

```text
FEISHU_SEND_EMPTY=true
```

默认 `false`，当天没有值得推送的新信息时不会发空消息。

### 安全

不要把以下信息直接写进代码或 YAML：

- DeepSeek API Key
- 飞书 Webhook URL
- 飞书签名 Secret

全部放 GitHub Actions Secrets。

## 前一天重复检测

v5 有三层去重。

### 1. seen

完全相同文章不重复抓取。

### 2. 昨日标题 / URL 去重

会检查：

- 去除追踪参数后的 URL
- 标题相似度
- 中文双字 ngram 重叠
- 标题中的日期、月份、季度

例如：

```text
宝马中国二季度销量下滑，纯电车型承压
宝马中国Q2销量下滑：纯电车型继续承压
```

会判断为同一事件。

但：

```text
财联社汽车早报 8月12日
财联社汽车早报 8月13日
```

不会误判为重复。

### 3. LLM event_key

DeepSeek 会对重点信息输出：

```text
主体 | 事件 / 变化 | 时间周期
```

即使第二天媒体换了一套标题，只要 event_key 与昨天一致，也会过滤。

## 信息新鲜度

默认：

```yaml
lookback_hours: 72
```

抓到明确发布日期且超过 72 小时的内容会过滤。

对于列表页无法识别日期的信息，每个来源默认最多抓 2 条，避免项目第一次运行时把大量历史文章都推送出来。

## 健康检查

每天生成：

```text
data/health/latest.json
reports/health/YYYY-MM-DD.md
```

检查：

- HTTP
- robots.txt
- 候选链接数量
- 关键词命中
- 最新可见日期
- 执行耗时
- 失败原因

状态：

```text
✅ OK
⚠️ Empty
🚫 Robots blocked
❌ HTTP failed
🧩 Need adapter
```

## 输出

日报：

```text
reports/daily/YYYY-MM-DD.md
```

结构：

```text
临近节点

01 今日必须知道
02 市场变化
03 竞品动作
04 政策 / 行业变量
05 潜在传播选题
```

飞书会发送更精简的版本，每个区最多保留 3 条，避免信息过载。

## GitHub Actions

工作流：

```text
.github/workflows/daily.yml
```

默认每天北京时间约 07:37 运行。

也可以进入：

```text
Actions
→ Daily Auto PR Radar
→ Run workflow
```

手动测试。

## 本地运行

```bash
python -m venv .venv
```

macOS / Linux：

```bash
source .venv/bin/activate
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

安装依赖：

```bash
pip install -r requirements.txt
```

跑测试：

```bash
python -m unittest discover -s tests -v
```

只跑健康检查：

```bash
python src/main.py --healthcheck-only
```

不调用 DeepSeek：

```bash
python src/main.py --no-llm
```

完整运行：

```bash
python src/main.py
```

## 配置文件

```text
config/
├── sources.yaml
├── topics.yaml
├── companies.yaml
└── calendar.yaml
```

其中：

- `sources.yaml`：去哪里抓
- `topics.yaml`：长期关心什么
- `companies.yaml`：重点看谁
- `calendar.yaml`：什么时候一定要看

## 重要边界

脚本不会：

- 绕过登录
- 绕过验证码
- 绕过 robots.txt
- 绕过付费墙
- 抓取小红书 / 抖音等社媒
- 抓取 KOL
- 抓取公司内部系统

部分站点依赖 JavaScript 或会调整页面结构，所以“已经配置”不等于永久稳定。健康检查就是为了持续发现失效源。

## 推荐 Secrets

必需：

```text
DEEPSEEK_API_KEY
FEISHU_WEBHOOK_URL
```

可选：

```text
FEISHU_SIGNING_SECRET
LLM_BASE_URL
LLM_MODEL
LLM_API_KEY
```

可选 Variables：

```text
FEISHU_SEND_EMPTY
```
