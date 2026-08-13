# 直接上传到 GitHub

这个文件夹已经是仓库根目录结构，**不要再套一层文件夹**。

## 方法一：GitHub 网页上传

1. GitHub 新建一个空 Repository
2. 进入仓库
3. 点击 `Add file` → `Upload files`
4. 解压本项目
5. 将解压后的**全部内容**拖进 GitHub
6. 确认 `.github/` 文件夹也上传成功
7. Commit changes

上传后仓库根目录应该能直接看到：

```text
.github/
config/
data/
reports/
src/
tests/
.gitignore
.env.example
README.md
CHANGELOG.md
requirements.txt
```

## 配置 Secrets

进入：

```text
Repository
→ Settings
→ Secrets and variables
→ Actions
```

### Repository secrets

至少新增：

```text
DEEPSEEK_API_KEY
FEISHU_WEBHOOK_URL
```

如果飞书 Bot 开启了签名校验，再新增：

```text
FEISHU_SIGNING_SECRET
```

可选：

```text
LLM_BASE_URL
LLM_MODEL
LLM_API_KEY
```

### Repository variables

可选：

```text
FEISHU_SEND_EMPTY
```

建议值：

```text
false
```

## 第一次测试

上传并配置 Secret 后：

```text
Actions
→ Daily Auto PR Radar
→ Run workflow
```

等待执行完成。

重点检查：

```text
reports/health/YYYY-MM-DD.md
reports/daily/YYYY-MM-DD.md
```

同时确认飞书群是否收到 Radar。

## 注意

- 不要把 DeepSeek Key 写进代码
- 不要把飞书 Webhook URL 写进代码
- 不要提交 `.env`
- `.env.example` 只是字段模板，没有真实密钥
