# Config 说明

## sources.yaml

这是脚本真实执行的信息源配置。

当前共 56 个来源。

字段：

- `id`：唯一 ID
- `name`：显示名称
- `category`：`market` / `policy` / `competitor` / `industry_news`
- `source_level`：
  - `S`：一手事实源
  - `A`：高质量分析源
  - `B`：线索源
- `method`：当前支持 `html_index` / `rss`
- `url`：列表页或 RSS
- `frequency`：当前作为说明字段，Actions 每天统一运行
- `priority`：1-5，同一事件优先保留高优先级来源
- `include_keywords`：列表页预筛关键词
- `enabled`：可选，设为 `false` 可临时停用

### 国内信息源

数据 / 官方：

- CADA
- CPCA
- CAAM
- 国家统计局
- 国家能源局
- 中国充电联盟
- 海关总署统计
- 国家及重点地方政府部门

国内媒体：

- 第一财经汽车
- 财联社汽车早报
- 界面新闻汽车
- 每日经济新闻汽车
- 经济观察网汽车
- 盖世汽车原创
- 汽车产经网
- 36氪汽车
- 新华汽车

## topics.yaml

定义“我们长期关心什么”。

v5 增加：

- 补能基础设施
- 地域市场
- 消费趋势

## companies.yaml

定义重点竞品与别名。

## calendar.yaml

定义节假、车展、技术展等确定性节点。
