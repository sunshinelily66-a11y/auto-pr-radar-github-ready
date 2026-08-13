# CHANGELOG

## v5

### 功能
- 接入飞书自定义机器人 webhook
- 支持飞书签名校验
- DeepSeek 默认模型更新为 `deepseek-v4-flash`
- DeepSeek 批量分析，减少 API 调用次数
- 增加国内财经、汽车媒体和基础设施数据源
- 增加 event_key 事件级昨日去重
- 增加 72 小时新鲜度过滤

### 修复
- 修复 canonical URL 直接删除全部 query 参数导致不同文章可能被误合并的问题
- 修复 `delivered` 把全部抓取内容当成“已发送”的问题
- 修复政策 / 行业变量区未包含 industry_news 的问题
- 修复 `Radar(root=...)` 仍使用全局 ROOT 路径的问题
- 修复健康检查后 crawl 再次请求同一首页的问题
- 防止“汽车早报 8月12日 / 8月13日”等固定栏目被标题相似度误去重

### 稳定性
- 网络请求增加有限重试
- 飞书发送失败不推进状态，下一次可以重试
- GitHub Actions 增加 unittest
- 增加 workflow concurrency，避免每日任务重叠
