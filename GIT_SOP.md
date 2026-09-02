# NL2SQL Agent Git 使用 SOP

> 标准操作流程:首次上传、日常开发、版本回退、企业协作
> 仓库地址: `git@github.com:xuyi112/nl2sql-agent.git`(SSH)

---

## 一、环境准备(一次性)

### 1.1 检查 SSH 连接

```powershell
ssh -T git@github.com
# 成功: Hi xuyi112! You've successfully authenticated...
```

### 1.2 如果 SSH 失败(公钥丢失/换电脑)

```powershell
# 1. 生成新密钥(如果 ~/.ssh/id_ed25519 不存在)
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh" | Out-Null
ssh-keygen -t ed25519 -C "xuyi112" -f "$env:USERPROFILE\.ssh\id_ed25519" -N '""'

# 2. 查看公钥(复制输出内容)
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"

# 3. 浏览器打开 https://github.com/settings/ssh/new
#    Title 随便填,Key 粘贴公钥,Add SSH key
```

### 1.3 配置身份(新电脑才需要)

```powershell
git config --global user.name "xuyi112"
git config --global user.email "xuyi112@users.noreply.github.com"
```

---

## 二、首次上传(新仓库)

```powershell
cd f:\数据分析\nl2sql_agent

# 1. 初始化仓库
git init

# 2. 暂存所有文件(.gitignore 自动排除敏感文件)
git add .

# 3. 检查暂存内容(确认没有 .env / *.db / 个人文件)
git status

# 4. 提交
git commit -m "feat: 项目初始版本"

# 5. 关联远程仓库(SSH 方式)
git remote add origin git@github.com:xuyi112/nl2sql-agent.git

# 6. 主分支命名 + 推送
git branch -M main
git push -u origin main
```

### 上传前安全检查清单

| 检查项 | 命令 | 通过标准 |
|---|---|---|
| 无密钥文件 | `git status` | 无 `.env` |
| 无数据库 | `git status` | 无 `*.db` / `chroma_data/` |
| 无个人文件 | `git status` | 无 `*.md` 个人文档 |
| 示例文件无真实 Key | 打开 `.env.example` | 是占位符 `sk-你的真实APIKey填这里` |

---

## 三、日常开发流程(核心)

### 3.1 标准提交流程(每次修改后)

```powershell
cd f:\数据分析\nl2sql_agent

# 1. 查看改了什么
git status

# 2. 暂存 + 提交(提交信息格式见下方规范)
git add .
git commit -m "feat: 新增LLM图表分析"

# 3. 推送到 GitHub
git push
```

### 3.2 提交信息规范(Conventional Commits)

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feat:` | 新功能 | `feat: 新增缓存模块` |
| `fix:` | 修复 bug | `fix: 修复缓存失效问题` |
| `docs:` | 文档 | `docs: 更新 README` |
| `refactor:` | 重构(不改功能) | `refactor: 优化审核逻辑` |
| `perf:` | 性能优化 | `perf: 加快检索速度` |
| `test:` | 测试 | `test: 新增评测用例` |

### 3.3 查看历史

```powershell
git log --oneline          # 简洁版
git log --oneline -10      # 最近 10 条
git log --oneline --graph  # 图形化(看分支)
```

---

## 四、版本回退(不满意时)

### 4.1 安全回退(推荐,保留历史)

```powershell
# 1. 找到要回退到的版本
git log --oneline

# 2. 回退(生成一次"反向提交",历史保留,团队协作安全)
git revert <commit_id>
# 例: git revert f075638

# 3. 推送
git push
```

### 4.2 强制回退(危险,仅自己用)

```powershell
# 直接抹掉之后的提交,⚠️ 已推送的版本会与远程冲突
git reset --hard <commit_id>
git push --force
```

### 4.3 临时回到旧版本看看(不提交)

```powershell
git checkout <commit_id>   # 回到旧版本(游离状态,只读)
git checkout main          # 回到主分支
```

---

## 五、企业协作流程(多人开发)

### 5.1 分支策略(Git Flow 简化版)

```
main(稳定版,受保护,不能直接改)
  ├── feature/xxx(新功能)
  ├── fix/xxx(修复 bug)
  └── 流程:分支开发 → PR → 审查 → 合并
```

### 5.2 完整协作流程

```powershell
# 1. 拉取最新代码
git pull

# 2. 建功能分支(不要直接在 main 上改)
git checkout -b feature/llm-insights

# 3. 开发 + 提交(可多次提交)
git add .
git commit -m "feat: 新增LLM图表分析"

# 4. 推送分支
git push origin feature/llm-insights

# 5. 浏览器打开 GitHub → 仓库 → Pull Requests → New PR
#    base: main ← compare: feature/llm-insights
#    填写描述 → Create Pull Request

# 6. 同事审查 → 通过后合并(网页上点 Merge)

# 7. 合并后删除本地分支 + 拉取最新
git checkout main
git pull
git branch -d feature/llm-insights
```

### 5.3 处理合并冲突

```powershell
# 合并时冲突 → 手动解决冲突文件(保留要的内容,删掉 <<<<<<< ======= >>>>>>> 标记)
# 解决后:
git add .
git commit -m "merge: 解决冲突"
git push
```

---

## 六、常见问题排查

| 问题 | 原因 | 解决 |
|---|---|---|
| `Failed to connect to github.com port 443` | HTTPS 被墙 | 用 SSH 地址:`git@github.com:xuyi112/nl2sql-agent.git` |
| `Permission denied (publickey)` | SSH key 未配置 | 见 1.2 重新配置 |
| `remote origin already exists` | 已关联过 | `git remote set-url origin <新地址>` |
| `push declined due to repository rule violations` | 提交含密钥 | 删除密钥文件 → 重新提交;历史含密钥则重建仓库(见七) |
| `Your branch is ahead of 'origin/main'` | 本地有未推送提交 | `git push` |
| `nothing to commit` | 没有改动 | 确认改过文件并 `git add` |
| 误提交了 `.env` | 忘记 gitignore | `git rm --cached .env` 重新提交;⚠️ Key 已泄露,去平台重置 |

---

## 七、安全规范(重要)

### 7.1 密钥保护

- ✅ `.env`(真实 API Key)已被 `.gitignore` 排除,永不提交
- ✅ `.env.example` 只放占位符:`sk-你的真实APIKey填这里`
- ⚠️ 如果 Key 曾出现在任何提交里(即使已删除),**立即去 DeepSeek 平台重置**

### 7.2 重置 API Key

1. 登录 https://platform.deepseek.com
2. API Keys → 删除旧 Key → 创建新 Key
3. 更新 `f:\数据分析\.env` 的 `DEEPSEEK_API_KEY`
4. 重启服务生效

### 7.3 重建干净仓库(历史含密钥时)

```powershell
# 远程仓库是空的(推送被拒)时:
Remove-Item -Recurse -Force .git   # 删除本地历史
git init                            # 重新初始化
git add .                           # 只暂存当前干净文件
git commit -m "feat: 初始版本"
git branch -M main
git remote add origin git@github.com:xuyi112/nl2sql-agent.git
git push -u origin main
```

---

## 八、速查卡(一页版)

```powershell
# 日常三连(改完代码)
git add . && git commit -m "feat: 说明" && git push

# 查看状态/历史
git status && git log --oneline

# 安全回退
git revert <commit_id> && git push

# 分支开发
git checkout -b feature/xxx
git push origin feature/xxx

# 拉取最新
git pull
```

---

## 九、简历描述(面试用)

> **项目版本管理与团队协作**:使用 Git 进行版本控制,采用 Git Flow 分支策略(main/feature/fix),通过 Pull Request 实现代码审查与团队协作,支持任意版本回退;配置 SSH 密钥认证与 GitHub Push Protection 安全机制,确保代码质量与可追溯性。