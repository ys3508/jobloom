# 开源生态调研：与 Jobloom 相关的项目

调研日期 2026-08-31。用途：给其他 agent 做 brainstorming 的输入材料，不是实施计划。
本文件不授权任何代码引入、不授权提交申请、不授权接触真实雇主表单。

## 方法与可信度声明

- **Star / push 时间 / open issues / license / 语言 / fork**：全部来自 GitHub REST API
  `/repos/{owner}/{repo}`，抓取于 2026-08-31。这些是**实测值**。
- **功能描述、是否自动提交、是否需要付费 key**：来自各项目 README 或仓库文件树。凡是只有
  README 单方面声明、我没有读到对应代码的，下面标注 `[README 声明，未验证]`。
- **文件树**：对 `neonwatty/job-apply-plugin` 和 `santifer/career-ops` 拉取了完整
  `git/trees?recursive=1` 并读了具体 fixture 文件，属于**实测**。
- 没有 clone、没有运行任何项目、没有安装任何依赖。部署难度一栏是**从依赖清单推断的**，
  不是跑通的结论。

## 判断这些项目时用的 Jobloom 判据

后面每个项目都按这六条打分，因为这是 Jobloom 与大多数同类项目真正分叉的地方：

| 判据 | Jobloom 的立场 |
| --- | --- |
| **不自动提交** | Fill-Only，`stop_before_submit` fail closed，submit counter 必须为零 |
| **不编造** | 简历/答题内容必须能追溯到 CandidateFact / EvidenceUnit，缺证据就报缺口 |
| **日志无值** | 事件、stdout、快照里不出现候选人值、答案值、JD 全文 |
| **安全路径确定性** | 浏览器执行、hash 校验、状态机、可信度判断不许有 LLM |
| **证据有出处与时效** | 事实带来源与新鲜度，过期要求重新确认 |
| **先测量后建** | 任何 key / filter / adapter / 阈值之前先跑便宜的测量 |

## 一个对本次调研有约束力的实测数据

今天的 review queue（`.jobloom/review-queue-20260831.json`，105 个 opening）按
`apply_url` 域名聚合：

| ATS | 数量 | 占比 |
| --- | ---: | ---: |
| jobs.lever.co | 71 | 68% |
| job-boards.greenhouse.io | 19 | 18% |
| jobs.ashbyhq.com | 14 | 13% |
| jobs.smartrecruiters.com | 1 | 1% |
| **Workday** | **0** | **0%** |
| LinkedIn / Indeed / Glassdoor | 0 | 0% |

**这条数据决定了哪些项目对我们有用**：任何以 LinkedIn Easy Apply 或 Indeed/Glassdoor 聚合
为核心的项目（AIHawk、ApplyPilot、JobSpy），它们最花力气的那部分对 Jobloom 当前队列的
覆盖率是 **0%**。任何覆盖 Lever/Greenhouse/Ashby 的项目，覆盖率是 **99%**。

---

# 第 1 类 · 可直接复用的完整项目

## 1.1 neonwatty/job-apply-plugin — **最高优先级**

- **链接**：https://github.com/neonwatty/job-apply-plugin
- **Star**：96 ｜ fork 22
- **维护状态**：最后 push **2026-09-01**（今天），open issues **1**，创建于 2026-01-05。
  小而新，作者在持续推。风险是巴士系数=1。
- **部署难度**：**低**。`claude plugin marketplace add neonwatty/job-apply-plugin` +
  `claude plugin install`。依赖 Node.js + Python 3，Playwright 只在 Codex 集成路径用。
  **不需要任何 API key**（用 Claude Code / Codex 自带的浏览器能力）。
- **技术栈**：Python（主）+ JavaScript，Claude Code / Codex plugin 形态，Claude in Chrome
  或 Codex Browser 驱动可见浏览器。MIT。
- **可复用的功能点（具体到文件）**：
  - `qa/fixtures/lever-application-2026-08-v1/{fixture,provenance,approval}.json`
    ——同样的三件套还有 `greenhouse-single-page-2026-08-v1`、`ashby-application-2026-08-v1`、
    `linkedin-easy-apply-screening-2026-08-v1`。这是**从真实申请页录制、脱敏后编译**的表单结构，
    带 `sourceRecordingSha256`、`fixtureSha256`、`approvedBy` / `approvedAt`。
  - fixture 里的 `oracle.finalActionActivations: 0` —— 与 Jobloom 要的 submit counter 为零
    是同一个断言。
  - 语义 `kind` 分类法：`contact.email` / `contact.location` / `authorization.work_authorized` /
    `authorization.sponsorship_status` / `source.discovery_radio` / `resume.file` …
    控件用 `role`（textbox / combobox / radiogroup / file）而不是 CSS selector 描述，
    天然抗 selector drift。
  - `qa/scenarios/{lever,greenhouse,ashby}-complete-profile/{profile,expected}.json` +
    `synthetic-resume.pdf`：合成 profile、期望结果、合成 PDF，可作为回归测试的现成骨架。
- **它做了但 Jobloom 没有、值得加的**：
  - "录制 → 脱敏编译 → 人工 approve → promote 成 fixture" 这条**取证流水线**本身。
    Jobloom 现在没有任何机制把一个真实 ATS 页面变成可复现的测试资产。
  - 把 fixture 的批准动作独立成 `approval.json`（谁、何时批的），而不是隐含在 commit 里。
- **与 Jobloom 理念的冲突**：**几乎没有**。README 明确
  `"Never submits applications - Stops at final review"`，profile 存本地
  `~/.job-apply/`，无 telemetry。唯一需要注意的两点：
  1. profile.json / answers.json 是**明文**存放的，Jobloom 的 0600 + 值不落日志要求更严；
  2. 它自己承认除 Lever/Ashby 外的 guided workflow `"unverified and may drift"`
     ——不要把它的 Workday/Rippling 路径当成已验证能力。
- **给其他 agent 的问题**：MIT fixture 引入 Jobloom 需要 attribution 与一次人工核对，
  是否接受？还是坚持自造合成 fixture（代价：测不出真实结构）？

## 1.2 santifer/career-ops — **理念最接近的大项目**

- **链接**：https://github.com/santifer/career-ops
- **Star**：**69,633** ｜ fork 13,173
- **维护状态**：最后 push **2026-08-31**（今天），open issues **371**（issue 多是热度的副作用，
  不必然是失修），创建于 2026-04-04 —— 五个月涨到 7 万星。
- **部署难度**：**很低**。`npx @santifer/career-ops init`。它本质是一堆 Markdown 指令文件 +
  少量 `.mjs`，跑在 Claude Code / Codex / OpenCode / Gemini 里。**不强制付费 key**，
  可接 OpenRouter 免费模型、Ollama 本地端点或 Gemini 免费额度。
- **技术栈**：JavaScript（`.mjs`）+ Markdown 指令文件 + YAML 配置。数据存本地
  Markdown / YAML / TSV，不用数据库。MIT。
- **可复用的功能点（具体到模块）**：
  - `providers/{lever,greenhouse,ashby,smartrecruiters,workday}.mjs` —— 各 ATS 公开 API 的
    board 扫描器。Jobloom 已有 `ats_sources.py` / `ats_source_probe.py`，**不是替换，是覆盖度对账**：
    它预置了 100+ 公司的 portal 配置和 45+ 检索式。
  - `providers/_profile-keywords.mjs`、`profile-language.mjs` —— 把个人定位转成检索/匹配语汇。
  - `config/profile.example.yml`、`modes/_profile.template.md` —— profile schema 可以对照
    Jobloom 的 candidate facts 看有没有漏掉的维度。
  - `test-fixtures/upgrade/state-v1.16 → v1.18` —— **状态升级迁移测试**的做法：整份用户状态
    目录做 fixture，跑升级，比对 `expected.json`。Jobloom 有 schema 但没有这种整状态迁移测试。
- **它做了但 Jobloom 没有、值得加的（这一条是本次调研最大的收获来源）**：
  - **A–H 结构化评估报告 + 全局 1–5 评分**：把"这个岗位值不值得投"变成有固定章节的报告，
    而不是一个分数。
  - **level strategy**（该按什么级别定位自己）—— 正好是你问的"针对某岗位如何定位自己"。
  - **positioning consistency check**：跨简历 / LinkedIn / 面试叙事的一致性检查。
    Jobloom 有 claims manifest，但没有"跨载体一致性"这个检查维度。
  - **comp research**（薪酬调研）与 `salary-observations.tsv` —— Jobloom 完全没有薪酬轴。
  - **`contacto` 模式**：识别 hiring manager 并起草 LinkedIn 外联草稿（draft-only）——
    对应 Jobloom 的 Task 12 network scan。
  - **`deep` 模式**：6 轴公司结构化调研。对应 Jobloom Task 9 的 posting trust，但方向是
    "这家公司值不值得去"而不是"这条岗位是不是假的"。
  - **`outcome` 模式**：记录结果并归档产物；`data/follow-ups.md` 跟进机制。
  - **`training` / `project` 模式**：评估该学什么课、该做什么项目来补缺口——
    Jobloom 现在只会报告 evidence gap，不会给补齐路径。
  - **STAR+Reflection story bank**，且明确"没有出处的数字不许用"。
- **与 Jobloom 理念的冲突**：
  - **不冲突的部分**：README 明确 `"The system never submits an application — you always
    have the final call"`、`"never sends, submits, or clicks anything"`；拒绝使用 story bank 里
    没有出处的数字。这两条与 Jobloom 高度一致。
  - **冲突的部分**：它的核心是 Markdown 指令 + LLM 判断，**评分和评估在模型里**；
    Jobloom 的 routing / trust / 状态机要求确定性、可复现、可审计。它的 1–5 分不可复现，
    Jobloom 的 `ranking_score` 至少可以被 `ranking_score_impact.py` 审计。
    → **借理念，不借实现**。
  - 它对 CV 是"tailor / keyword mirroring"，靠 FAQ 提醒用户自己检查幻觉；Jobloom 是
    claims manifest 硬门。这是强度差别，不是方向差别。

## 1.3 Gsync/jobsync — 追踪与看板

- **链接**：https://github.com/Gsync/jobsync
- **Star**：967 ｜ fork 172
- **维护状态**：最后 push **2026-08-31**（今天），open issues 23，创建于 2024-05-21。活跃且有年头。
- **部署难度**：**中**。`docker compose up`。需要 Docker；AI 功能可**全本地 Ollama**，
  也可接 OpenAI/Gemini/DeepSeek/OpenRouter，**不强制付费 key**。
- **技术栈**：Next.js + React + Shadcn UI + Tailwind，SQLite + Prisma，Nivo 图表，
  Tiptap 富文本，Vercel AI-SDK。MIT。
- **可复用的功能点**：
  - **Prisma schema**：application / resume version / task / question bank 的关系建模，
    可以直接对照 Jobloom 的 SQLite schema 找缺口。
  - **Nivo 图表 + 监控 dashboard 的视图划分**（活动量、成功率、待办）——
    对应 codex 计划的 Task 8，这是现成的信息架构参考。
  - **简历导入的 review card 模式**：AI 抽取结构化数据后，**每个 section 出一张卡片让人确认后才入库**。
    这与 Jobloom 的"人工批准后才 approve"是同一个思路的 UI 实现。
  - Question Bank（面试题库）。
- **它做了但 Jobloom 没有、值得加的**：
  - 时间/活动记录（task logging）与"接下来该做什么"的待办视图——Jobloom 只有状态机，没有节奏。
  - 成功率/漏斗的**图形**呈现。Jobloom 有 funnel 分子分母的概念但没有视图。
- **与 Jobloom 理念的冲突**：
  - 只追踪，不自动投递 —— 不冲突。
  - 但它是 **Next.js 全栈 Web 应用**，Jobloom 是 CLI + skill + 本地私有目录。
    Task 8 要的是 loopback + per-run token + 只读路由，**不要为了这个引入 Next.js/Prisma 栈**。
    → **借视图设计，不借技术栈**。

## 1.4 feder-cr/Jobs_Applier_AI_Agent_AIHawk — 反面参照

- **链接**：https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk
- **Star**：**30,294** ｜ fork 4,645
- **维护状态**：最后 push 2026-08-19，open issues 28。仓库说明中提到
  `"due to copyright considerations, we have removed all third-party provider plugins"`。
- **部署难度**：中高（Playwright + LLM provider 配置）。`[README 声明，未验证]`
- **技术栈**：Python + Playwright。**AGPL-3.0**。
- **可复用**：**建议不复用**。AGPL 对 Jobloom 有传染风险；且它的核心能力（LinkedIn
  批量投递）对我们队列覆盖率 0%。
- **与 Jobloom 理念的冲突（这是列它的原因）**：
  - 官方描述即 `"auto-apply with a tailored resume and cover letter for each posting"`
    —— **自动提交**，与 Fill-Only 直接对立。
  - 媒体报道其"一次投 2,843 个岗位"的用法 —— 这正是 Jobloom 用证据门槛和逐条批准要避免的模式。
  - LLM 生成投递内容 + 无 claims manifest —— 编造风险由用户自担。
- **给其他 agent 的价值**：它 30k 星说明"批量自动投"是市场主流需求。Jobloom 选了相反的路，
  这个选择的代价（慢）和收益（可辩护）应该被明确记录，而不是默认。

## 1.5 Pickle-Pixel/ApplyPilot — 更强的反面参照

- **链接**：https://github.com/Pickle-Pixel/ApplyPilot
- **Star**：1,533 ｜ fork 555
- **维护状态**：最后 push **2026-03-08**（近 6 个月未更新），open issues **71**。
  **偏向停更**，issue 积压。
- **部署难度**：中。`pip install applypilot` + 处理 jobspy 依赖冲突 + `applypilot init`；
  需要 Python 3.11+ / Node 18+ / Chrome / Claude Code CLI。
  **需要 Gemini API key**（免费额度够用）；CAPTCHA 需付费 CapSolver key。
- **技术栈**：Python + Playwright + Claude Code CLI + Gemini。**AGPL-3.0**。
- **可复用的功能点（概念层面）**：
  - 表单字段分类 + JD 抽取的三段降级：**JSON-LD → CSS selector → AI 抽取**。
    这个"先结构化、再选择器、最后才用模型"的降级顺序与 Jobloom
    "最贵的那一级不许放在最便宜的问题下面"是同一条原则，值得抄。
  - 跨 board 按 URL 去重。（注意：Jobloom 已经measured 过跨城去重不可做，见 known-liabilities。）
  - `resume_facts` 保留不变、只重排不新增的 tailoring 约束 —— 弱化版的 claims manifest。
- **与 Jobloom 理念的冲突（严重）**：
  - **自动提交**：`applypilot apply` 会 `"fills fields, uploads documents, answers questions,
    and submits"`。
  - **LLM 现场生成筛选题答案**，README 未记录该阶段的防编造机制。
  - **集成 CAPTCHA 破解服务（CapSolver）**。Jobloom 把 CAPTCHA 列为**强制暂停**事件。
    这是一条不可调和的红线，也是我们不应引入其任何执行路径代码的理由。
  - AGPL-3.0。
  - 覆盖 Indeed/LinkedIn/Glassdoor/ZipRecruiter + 48 个 Workday portal —— 对我们队列覆盖 0%。

---

# 第 2 类 · 可二次开发的单点组件

## 2.1 Skyvern-AI/skyvern — 表单执行引擎

- **链接**：https://github.com/Skyvern-AI/skyvern
- **Star**：22,893 ｜ fork 2,152 ｜ open issues 220 ｜ 最后 push **2026-09-01**（今天）
- **部署难度**：中高。Python 3.11–3.13、FastAPI、SQLAlchemy + Alembic、Playwright。
  需要 LLM provider key（付费）才能发挥其 AI 能力。
- **技术栈**：Python / FastAPI / Playwright。**AGPL-3.0**。
- **可复用的功能点**：
  - 其**混合模式**设计值得借：稳定步骤走 Playwright 确定性 selector，
    只在页面可能变化处退回 AI；支持 pure-selector / pure-NL / selector-with-AI-fallback
    三种模式挂在同一个 page object 上。
  - 它在 write 类任务（填表、登录、下载）的基准上表现最好，说明这条路可行。
- **与 Jobloom 理念的冲突**：
  - **AGPL-3.0** —— 直接引入会传染，基本排除。
  - 其卖点是"不依赖预设 XPath，由模型现场判断" —— 与 Jobloom
    "浏览器执行路径不许有 LLM"**正面冲突**。模型决定点哪里，就无法做确定性 hash 校验和重放审计。
  - → **只借"确定性优先、AI 兜底"的分层思想，不引入代码。**

## 2.2 browser-use/browser-use — 浏览器 agent 底座

- **链接**：https://github.com/browser-use/browser-use
- **Star**：**111,875** ｜ fork 12,285 ｜ open issues 383 ｜ 最后 push **2026-09-01**
- **部署难度**：中。Python + Playwright，需 LLM key。
- **技术栈**：Python。**MIT**（许可证友好）。
- **可复用**：它把页面转成 agent 可读的结构化表示这一层，可以对照 Jobloom 的
  `form-page-observation.template.json` 看有没有漏掉的控件语义。
- **与 Jobloom 理念的冲突**：同 Skyvern——它的定位是让模型自主操作浏览器，
  Jobloom 要的是"模型不参与执行"。**当参照，不当依赖。**

## 2.3 speedyapply/JobSpy — 消费型 board 抓取

- **链接**：https://github.com/speedyapply/JobSpy
- **Star**：4,203 ｜ fork 833 ｜ open issues 62 ｜ 最后 push **2026-02-18**（6 个月未更新）
- **部署难度**：低（`pip install`），但**抓取端点会无预警失效**，这是这类项目的通病。
- **技术栈**：Python。MIT。
- **可复用**：LinkedIn / Indeed / Glassdoor / Google / ZipRecruiter 的抓取。
- **判断**：**对 Jobloom 当前队列覆盖率 0%**，且 Jobloom 已经选择走官方 ATS board
  （17 个已注册源）而不是消费型聚合站。**建议不引入**，除非将来明确要扩展到聚合站——
  那时也应先测量聚合站上有多少岗位是我们 ATS 源上没有的。

## 2.4 简历解析类（pyresparser / pyresume / open-resume）

| 项目 | Star | 最后 push | License | 判断 |
| --- | ---: | --- | --- | --- |
| [OmkarPathak/pyresparser](https://github.com/OmkarPathak/pyresparser) | 959 | 2023-09-13 | GPL-3.0 | **失修 3 年 + GPL，排除** |
| [xitanggg/open-resume](https://github.com/xitanggg/open-resume) | 8,874 | 2024-10-29 | AGPL-3.0 | 失修近 2 年 + AGPL，排除 |
| [wespiper/pyresume](https://github.com/wespiper/pyresume) | 5 | 2025-08-03 | MIT | 太小、无社区，不值得依赖 |

**结论**：这一类**不需要引入**。Jobloom 已有 `extract_candidate_facts.py` /
`fact_structure.py` / `evidence_units.py`，而且 Jobloom 的要求（事实要带出处、要能被
claims manifest 引用）比这些库的输出结构更严格。它们解析出的是扁平字段，喂不进 Jobloom 的证据模型。

## 2.5 srbhr/Resume-Matcher — 匹配打分

- **链接**：https://github.com/srbhr/Resume-Matcher
- **Star**：28,294 ｜ fork 5,008 ｜ open issues 59 ｜ 最后 push **2026-08-31**（今天）｜ **Apache-2.0**（友好）
- **部署难度**：中。Python 3.13+ FastAPI + Node 22+ Next.js + TinyDB，或单容器 Docker。
  可用 **Ollama 本地免费**，也可接付费 key。
- **技术栈**：FastAPI / LiteLLM / Next.js 16 / React 19 / TinyDB / Playwright（导 PDF）。
- **可复用的功能点**：
  - JD 关键词抽取 + 简历-JD 相似度打分的**思路**；用 LiteLLM 抽象多 provider 的做法。
  - 用 headless Chromium 导出 PDF 的路径（Jobloom 的 PDF 生成目前是痛点）。
- **它做了但 Jobloom 没有、值得加的**：
  - **"简历对 ATS 是否友好"这个维度**：Jobloom 检查的是"claims 是否有证据"，
    完全没有检查"这份 PDF 被 ATS 解析后还剩什么"。
    → 值得加一个**确定性**的 ATS 可解析性检查（文本层是否存在、是否有多栏/表格/图片文字），
    这与 `artifact_integrity_audit.py` 只读 PDF 的现状是自然衔接。
- **与 Jobloom 理念的冲突**：
  - 它会**生成和改写简历内容**，README 未记录任何防幻觉机制 —— 与 claims manifest 冲突。
  - **没有可独立调用的库/CLI**，是一体化 Web 应用，**不可作为组件引入**。
  - → 只借"ATS 可解析性"这一个观念，代码不用。

---

# 第 3 类 · 理念相近、实现不同（"如何针对某岗位定位自己"）

## 3.1 noamseg/interview-coach-skill — **对 Task 11 最有价值**

- **链接**：https://github.com/noamseg/interview-coach-skill
- **Star**：2,063 ｜ fork 342 ｜ open issues 14 ｜ 最后 push **2026-05-29**（3 个月未更新，但形态稳定）
- **部署难度**：**极低**。`git clone` + `mv SKILL.md CLAUDE.md`。
  **无外部依赖、无 API key**，只要有 Claude Code 之类的文件系统访问环境。MIT。
- **技术栈**：纯 Markdown skill（无代码语言），状态存单个 `coaching_state.md`。
- **可复用的功能点**：
  - **4 级 fit scoring：Strong Fit / Workable / Stretch / Gap** —— 把"我这段经历能不能答这个问题"
    做成有序等级而不是布尔。Jobloom 的 evidence coverage 可以直接借这个分级语汇。
  - **6 个 JD 解码视角**：重复频率、顺序与强调、required vs nice-to-have、动词选择、
    言外之意、缺口。这是"针对某岗位如何定位自己"的**可操作分解**，
    比 Jobloom 现在的关键词/direction 匹配更接近人的判断方式。
  - **story 元数据**：STAR 全文 + "earned secrets"（只有真做过的人才知道的细节）+ 强度评分 +
    **last-used 日期以监控重复使用**。`earned secrets` 这个字段是很好的**真实性代理**——
    编造的故事写不出 earned secret。
  - **来源分层：verified / general knowledge / unknown**，以及**时效衰减：陈旧数据被标记，
    而不是被默默采信**。这两条几乎是 Jobloom freshness 机制的镜像。
  - **校准机制**：3 次以上真实面试后，比对练习分与真实结果的漂移。
- **它做了但 Jobloom 没有、值得加的**：
  - `apply` 命令在遇到没有答案的题时**标记 gap 而不是编造** —— 与 AnswerLibrary 的
    "unknown question 必须暂停"是同一条规则，但它还多了一步：把 gap 累积成待补清单。
  - 故事**重复使用监控**（同一个故事在多少家公司用过）。
  - 面试后 debrief 与结果回灌。
- **与 Jobloom 理念的冲突**：
  - 几乎没有。唯一的结构性差异：它把全部状态放在**一个 Markdown 文件**里，
    没有 hash、没有不可变记录、没有审批。Jobloom 的 StoryEvidence 需要引用不可变的
    CandidateFact / EvidenceUnit ID。→ **借分类法和字段设计，存储层自己做。**
- **给其他 agent 的问题**：`earned secrets` 这个字段能不能落到 Jobloom 的证据模型里？
  它是"真实性"的强信号，但它不是可验证的事实，放进 claims manifest 会不会污染证据层？

## 3.2 santifer/career-ops 的定位相关模式（见 1.2）

`level strategy`、`positioning consistency check`、`profile-language.mjs`、
`_profile-keywords.mjs` —— 这几处是它回答"如何定位自己"的具体载体，值得单独读代码。

## 3.3 amruthpillai/reactive-resume — 隐私优先的简历工具

- **链接**：https://github.com/amruthpillai/reactive-resume
- **Star**：42,019 ｜ fork 4,672 ｜ open issues 117 ｜ 最后 push 2026-08-28 ｜ **MIT**
- **部署难度**：中（自托管全栈，Docker）。
- **技术栈**：TypeScript / React。
- **对 Jobloom 的意义**：**不是功能来源，是理念参照**——它是这个领域里
  "隐私优先、完全自托管、数据不出本机"做得最成功的项目（4.2 万星证明这个定位有市场）。
  它的简历 **schema 设计**和多模板渲染值得在做 PDF 生成时对照。
- **冲突**：它是简历**排版**工具，不管证据、不管投递。与 Jobloom 无直接重叠。

---

# 第 4 类 · Jobloom 没考虑到、值得补的功能

按"证据是否支持"和"是否违反现有边界"排序。**这一节是给 brainstorm 用的候选清单，不是待办。**

## 4.1 强烈建议考虑

| # | 功能 | 来源 | 为什么 Jobloom 需要 | 风险 / 边界 |
| --- | --- | --- | --- | --- |
| A | **ATS 可解析性检查** | Resume-Matcher | 现在只保证"claims 有证据"，不保证"PDF 被 ATS 解析后还剩内容"。一份多栏排版的 PDF 可能证据完美但解析后是乱码。 | 必须**确定性**实现（文本层提取 + 结构检查），不许用模型评分。可挂在 `artifact_integrity_audit.py` 下。 |
| B | **薪酬轴** | career-ops `salary-observations.tsv` | 队列里 105 个 opening 完全没有薪酬维度。投递决策缺一整个轴。 | 只记录**岗位页上写明的**薪酬区间，缺失就是缺失，**不许推断、不许用外部估算**。这正是 known-liabilities 里"没有信号就报缺口"的规则。 |
| C | **STAR story bank 的分类法** | interview-coach-skill | Task 11 已在计划里，但计划没有定义 fit 分级、question family、重复使用监控、earned secret。 | 存储必须引用不可变 fact ID；不许从 story 反向生成 fact。 |
| D | **gap → 补齐路径** | career-ops `training` / `project` | Jobloom 会说"这个方向证据不足"，但不会说"补什么能变成足"。 | 建议只做**确定性**的缺口枚举 + 用户自填，不做课程推荐。 |
| E | **录制→脱敏→审批→fixture 的取证流水线** | job-apply-plugin | 这是让 ATS 适配器可维护的**唯一**办法。适配器会漂移；没有录制机制，每次漂移都要手工重来。 | 录制产物含真实页面，必须脱敏后才能入库；`approval.json` 记录谁批的。 |

## 4.2 值得讨论

| # | 功能 | 来源 | 说明 |
| --- | --- | --- | --- |
| F | **positioning 一致性检查** | career-ops | 简历 / LinkedIn / 面试叙事三处的自我定位是否互相矛盾。Jobloom 有 claims manifest 但只管单份简历内部。 |
| G | **跟进节奏 / 待办视图** | jobsync, career-ops `follow-ups.md` | Jobloom 有状态机但没有"今天该做什么"。数据库显示 1 个 application 卡在 `ready_to_fill` 三天——这正是缺节奏的症状。 |
| H | **JD 抽取的三级降级** | ApplyPilot | JSON-LD → CSS selector → 模型。Jobloom 的 `posting_sections.py` 可以先加 JSON-LD 这一级（很多 ATS 页面有 `JobPosting` 结构化数据，免费且确定性）。 |
| I | **公司深度调研 6 轴** | career-ops `deep` | 与 Task 9 posting trust 互补：trust 问"这条岗位是不是假的"，deep 问"这家公司值不值得去"。 |
| J | **面试后 debrief 与结果回灌** | interview-coach-skill, career-ops `outcome` | Jobloom 有 `outcome_core.py`，但没有把面试过程的信息回灌到 story bank / direction 的机制。 |
| K | **整状态目录的升级迁移测试** | career-ops `test-fixtures/upgrade/state-v1.16` | Jobloom 的 `.jobloom/` 是长期演进的私有状态，目前没有跨版本迁移测试。 |

## 4.3 明确建议不做

| 功能 | 出现在 | 不做的原因 |
| --- | --- | --- |
| 自动提交 / 批量投递 | AIHawk, ApplyPilot | 与 Fill-Only 核心对立 |
| CAPTCHA 破解（CapSolver 等） | ApplyPilot | Jobloom 把 CAPTCHA 定义为强制暂停；破解 bot 检测是红线 |
| 模型驱动的表单执行 | Skyvern, browser-use | 执行路径有 LLM 就无法做确定性 hash 校验与重放审计 |
| LinkedIn / Indeed 聚合抓取 | JobSpy, AIHawk, ApplyPilot | 对当前队列覆盖率 0%；且多数涉及登录态抓取 |
| LLM 生成简历正文 | Resume-Matcher, resume-lm | 与 claims manifest 冲突 |
| 跨城/跨 board 去重合并 | ApplyPilot | Jobloom 已 measured 证明任何阈值都会先合并掉真岗位（见 known-liabilities） |

---

# 汇总表

| 项目 | ★ | 最后 push | issues | License | 部署 | 需付费 key | 自动提交 | 会编造 | 对 Jobloom 的用法 |
| --- | ---: | --- | ---: | --- | --- | --- | --- | --- | --- |
| [job-apply-plugin](https://github.com/neonwatty/job-apply-plugin) | 96 | 2026-09-01 | 1 | MIT | 低 | 否 | **否** | 否 | **直接复用 fixture + kind 分类法** |
| [career-ops](https://github.com/santifer/career-ops) | 69.6k | 2026-08-31 | 371 | MIT | 低 | 否 | **否** | 有出处约束 | **借功能清单与理念** |
| [interview-coach-skill](https://github.com/noamseg/interview-coach-skill) | 2.1k | 2026-05-29 | 14 | MIT | 极低 | 否 | 不适用 | **明确拒绝** | **借 story bank 分类法** |
| [jobsync](https://github.com/Gsync/jobsync) | 967 | 2026-08-31 | 23 | MIT | 中 | 否 | 否 | 会生成简历 | 借 dashboard 视图设计 |
| [Resume-Matcher](https://github.com/srbhr/Resume-Matcher) | 28.3k | 2026-08-31 | 59 | Apache-2.0 | 中 | 否(可 Ollama) | 否 | **会** | 只借 ATS 可解析性观念 |
| [reactive-resume](https://github.com/amruthpillai/reactive-resume) | 42.0k | 2026-08-28 | 117 | MIT | 中 | 否 | 否 | 否 | 隐私架构参照 |
| [skyvern](https://github.com/Skyvern-AI/skyvern) | 22.9k | 2026-09-01 | 220 | **AGPL** | 中高 | 是 | 可 | — | 只借分层思想 |
| [browser-use](https://github.com/browser-use/browser-use) | 111.9k | 2026-09-01 | 383 | MIT | 中 | 是 | 可 | — | 观察层参照 |
| [AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk) | 30.3k | 2026-08-19 | 28 | **AGPL** | 中高 | 是 | **是** | **是** | 反面参照 |
| [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) | 1.5k | 2026-03-08 | 71 | **AGPL** | 中 | 是 | **是** | 部分 | 反面参照 + 借降级思路 |
| [JobSpy](https://github.com/speedyapply/JobSpy) | 4.2k | 2026-02-18 | 62 | MIT | 低 | 否 | 否 | 否 | 暂不引入 |
| [open-resume](https://github.com/xitanggg/open-resume) | 8.9k | 2024-10-29 | 143 | **AGPL** | — | — | — | — | 失修，排除 |
| [pyresparser](https://github.com/OmkarPathak/pyresparser) | 959 | 2023-09-13 | 45 | **GPL** | — | — | — | — | 失修，排除 |

---

# 留给 brainstorm 的开放问题

1. **许可证策略**：MIT / Apache 可用，AGPL / GPL 一律排除吗？还是允许"读了但重写"？
   后者需要一条明确的界线，否则无法向 reviewer 证明没有抄。
2. **是否接受引入 job-apply-plugin 的三份 MIT fixture**（Lever / Greenhouse / Ashby），
   还是坚持自造合成 fixture？前者测的是真实结构，后者只测协议外壳。
3. **`earned secrets` 该放在证据层还是叙事层？** 它是真实性的强信号，但不是可验证事实。
4. **薪酬轴（B）与 posting trust（Task 9）是不是同一件事的两面？**
   "写明薪酬"既是决策信息，也是 career-ops 和多数 trust 模型里的可信度信号。
5. **career-ops 有 7 万星、覆盖面几乎是 Jobloom 的超集，且理念不冲突。
   那么 Jobloom 存在的理由是什么？** 我的答案是：确定性、可审计、证据硬门——
   career-ops 的评分在模型里、不可复现；Jobloom 的每一条都要能被重放和审计。
   **这个答案应该被明确写进 README，否则它只是隐含假设。**
6. **本文件全部结论基于"没有 clone、没有运行"。**
   在真正引入任何代码之前，至少要把 job-apply-plugin 跑起来验证一次。
