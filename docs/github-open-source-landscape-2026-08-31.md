# Jobloom 相关 GitHub 开源项目调研

更新时间：2026-08-31（America/New_York）。Star、最近推送和开放事项会持续变化；本报告的数字是本次调研快照。

## 0. 调研口径

本报告以 Jobloom 当前定位为基准：本地优先、事实受约束、证据可追溯、答案有 scope/expiration、材料 hash 锁定、Fill-Only、提交前强制复核、确认提交证据和不可变归档。它不是“每天尽量多投”的海投机器人。

维护状态主要依据 GitHub `pushed_at`，不是 README 更新时间。表中的“开放事项”优先使用仓库页面；只有 GitHub API 数据时，它可能包含 open issues 与 open PR 的合计，因此只作为维护负担信号，不直接等同 bug 数。

许可证判断：

- MIT、Apache-2.0、Unlicense：通常可复用代码，但必须履行各自声明和归属要求。
- AGPL-3.0：可以研究和内部运行；若把修改版作为网络服务提供，通常需要开放对应源代码。合入 Jobloom 前必须先确定许可证策略。
- `NOASSERTION` / 无 LICENSE：默认不可复制代码，只能观察公开行为和独立重写设计。
- “来源于真实页面的语义 fixture”不等于真实 DOM，也不等于当前 ATS 已通过 live acceptance。

## 1. 总览结论

### 可复用资产与只借设计必须分开

| 类型 | 项目 | 证据与限制 | 对 Jobloom 的建议 |
| --- | --- | --- | --- |
| **可 vendor 的测试资产** | [neonwatty/job-apply-plugin](https://github.com/neonwatty/job-apply-plugin) | MIT；活跃；三套有 approval/provenance/hash 的语义 fixture | 有限复制 fixture、许可证和来源记录；本地复核；不引入其事实权威或把 replay 当 live DOM |
| **可评估的成熟参考实现** | [Gsync/jobsync](https://github.com/Gsync/jobsync) | MIT；2024 年创建；约 967 Star/172 forks；活跃 | 优先评估 UI/MCP 边界；若没有窄模块接口则只借信息架构 |
| **只借设计/分类法** | [DaveVoyles/resume-builder](https://github.com/DaveVoyles/resume-builder) | MIT，但约 10 Star/1 fork、年轻个人仓库 | 读 evidence ledger 与 interview packet；不作为运行时依赖 |
| **只借设计/分类法** | [vesaias/JobNavigator](https://github.com/vesaias/JobNavigator) | MIT，但约 18 Star/9 forks、年轻个人仓库 | 借 Kanban、source health、H-1B signal schema；不引入 iframe header stripping 或整套服务 |
| **只借设计/分类法** | [vitaecontext/vitaecontext](https://github.com/vitaecontext/vitaecontext) | MIT，但约 73 Star/9 forks、年轻项目 | 借 Career Context export/import schema；Jobloom 自己实现并保留 hash/approval |
| **只借工作流** | [santifer/career-ops](https://github.com/santifer/career-ops) | MIT、活跃、社区大；核心判断仍在模型/Markdown | 借 level strategy、positioning consistency、story/company/comp workflow，不进确定性内核 |
| **只借 UI 观念** | [srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher) | Apache-2.0、活跃；一体化 Web 应用而非稳定单点库 | 借 heatmap、parse preview、多 provider UX，不引入 match percentage |
| **研究材料，非依赖** | [browser-use/browser-use](https://github.com/browser-use/browser-use) | MIT 且成熟，但价值核心是模型驱动浏览器，依赖面与 Jobloom 执行目标不匹配 | 观察控件语义和失败案例；执行层直接使用 Playwright |
| **研究预览** | [scottgal/lucidRESUME](https://github.com/scottgal/lucidRESUME) | Unlicense，但约 6 Star，作者明确标 research preview | 借 provenance/drift 分类法，不成为依赖 |

### 没有找到完全替代 Jobloom 的项目

外部项目通常覆盖其中一段：搜索、评分、简历改写、表单填写或 tracker。没有看到另一个项目同时提供 Jobloom 现有的：

- CandidateFact 与不可变 CandidateSnapshot；
- direct / related / transferable / unsupported 且禁止证据升级；
- AnswerEntry scope、expiration、事件级联失效；
- 四种移民/赞助问题严格分离；
- standing authorization 与答案时效分离；
- 材料物理 hash 锁、全表 pre-submit review、正向提交证据；
- 提交时复制实际文件并验证 archive manifest；
- 确定性优先、模型成本分层和 outcome denominator。

---

# 第一类：可直接复用或独立运行的完整项目

## 1.1 career-ops

- 项目：[santifer/career-ops](https://github.com/santifer/career-ops)；约 **69,632 Star**。
- 维护：2026-08-31 有代码推送；约 213 open issues、157 open PR，维护非常活跃但变更面巨大。
- 部署：简单到中等。`npx @santifer/career-ops init`；Node.js/npm。生成 PDF 需 Playwright Chromium。可接多种 coding CLI，本地/免费模型路线存在，但完整体验仍有模型成本。
- 技术栈：JavaScript/Node.js、Markdown/YAML 数据、Playwright、CLI/TUI、Agent skills、插件系统。
- 可复用模块：
  - `providers/*.mjs` 的 ATS/数据源 provider 分层；
  - A–H 岗位评估报告结构；
  - company deep research、contact discovery、interview STAR+R story bank；
  - application artifacts、reply classification、follow-up cadence、funnel/pattern analysis；
  - 多 CLI skill 包装、doctor、升级和插件 registry。
- Jobloom 尚缺且值得加：面试故事库、公司研究、岗位真实性/ghost-job 信号、联系人草稿、回复分类、offer/谈判辅助、跨 CLI 安装器。
- 理念冲突：
  - 依赖 Agent 的整体推理和提示词判断，缺少 Jobloom 数据库级证据/时效/授权强制门；
  - 1–5 综合评分与 Jobloom “gap taxonomy 而非面试概率/单分数”不同；
  - 简历 keyword injection 风险高于 Jobloom 的 claim manifest；
  - 项目说不自动提交，与 Jobloom Fill-Only一致，但并不等于每个路径都有机械 submit gate。
- 结论：适合独立体验和借鉴外围工作流；不建议作为 Jobloom 内核依赖。

## 1.2 JobNavigator

- 项目：[vesaias/JobNavigator](https://github.com/vesaias/JobNavigator)；**18 Star**。
- 维护：最近推送 2026-08-23；0 个 API 开放事项，项目新且单维护者风险较高。
- 部署：中高。Docker Compose 最快；FastAPI、React、PostgreSQL、Playwright、Caddy/nginx、Chrome MV3 扩展。云模型可 BYOK，也支持 Ollama；Gmail/Telegram/LinkedIn 集成另需配置。
- 技术栈：Python 3.12、FastAPI、SQLAlchemy、APScheduler、React 18、Tailwind、Vite、PostgreSQL 16、Playwright、Manifest V3。
- 可复用模块：
  - Greenhouse/Lever/Ashby 等 ATS source adapters；
  - URL hash、tracking 参数清洗、content hash 去重；
  - Chrome 扩展的页面捕获和 application autofill UI 信息架构（不包括剥离
    `X-Frame-Options` / CSP 的实现）；
  - persona + reusable Q&A bank；
  - application Kanban 与状态历史；
  - source health、定时任务、Telegram digest、Gmail outcome monitor；
  - H-1B LCA/拒绝赞助信号。
- Jobloom 尚缺且值得加：图形化 review queue、source health dashboard、可视化状态历史、结果邮件建议、H-1B employer evidence、通知和备份。
- 理念冲突：AI 直接生成 first-person 表单答案，未显示 Jobloom 等级的事实、scope 和 expiration 强制验证；iframe header stripping 扩展扩大浏览器权限；百分比 AI score 不应进入 Jobloom 决策核心。
- 结论：MIT，但年轻且社区验证有限；只借设计并由 Jobloom 独立实现。禁止引入剥离
  frame security headers 的代码。接入时必须让 Jobloom core 成为唯一字段和值的权威。

## 1.3 JobSync

- 项目：[Gsync/jobsync](https://github.com/Gsync/jobsync)；**967 Star**。
- 维护：2026-08-31 有代码推送；API 记录 23 个开放事项，活跃。
- 部署：中等。Docker/self-host；Next.js 应用和本地数据。AI 可接 Ollama，云模型需 key。
- 技术栈：TypeScript、Next.js/React、Docker、PDF/DOCX 解析、MCP、Greenhouse/Lever 自动发现。
- 可复用模块：
  - application tracker、任务/活动/计时、统计 dashboard；
  - 简历 PDF/DOCX 分区解析后逐段 accept/skip；
  - resume management 和 PDF templates；
  - Greenhouse/Lever scheduled discovery；
  - Question Bank；
  - MCP server，在用户批准下从 Agent 添加申请与问题。
- Jobloom 尚缺且值得加：MCP 边界、用户友好的 onboarding、分区确认、任务提醒、tracker UI、resume preview。
- 理念冲突：AI match score 是中心功能；没有看到 Jobloom 式 freshness、material lock、pre-submit review 和 provenance 等级。导入接受一段不等于确认其中每条事实。
- 结论：MIT，最适合作为 Jobloom UI/MCP 交互参考。

## 1.4 JobSentinel

- 项目：[cboyd0319/JobSentinel](https://github.com/cboyd0319/JobSentinel)；**23 Star**。
- 维护：最近推送 2026-08-12；API 记录 16 个开放事项。开发验证矩阵较完整，但项目仍早期。
- 部署：下载桌面 release 可能简单；源码构建较难，需要固定 Node/npm/Rust、Tauri 平台依赖和长验证链。AI 并非所有核心功能必需。
- 技术栈：Tauri、Rust、TypeScript/React、Vite、Tailwind、本地桌面存储。
- 可复用模块：
  - posting freshness、repost、弱来源、疑似 scam/ghost-job 信号；
  - salary floor、written/verbal offer、总包、通勤/搬迁/deadline；
  - restricted source 的用户打开/本地导入边界；
  - safe support report；
  - 本地桌面壳和 responsible-AI/privacy 文档。
- Jobloom 尚缺且值得加：PostingTrustRecord、offer/谈判对象、薪资底线、支持报告、桌面安装和回滚。
- 理念冲突：大部分理念相容；需防止“ghost job”变成不透明概率或自动拒绝。Jobloom 应保留 evidence-bearing reason code 和 `unknown`。
- 结论：MIT，风险、薪资和桌面 UX 值得重点借鉴。

## 1.5 SimplyApply

- 项目：[artbyjazi/simply-apply](https://github.com/artbyjazi/simply-apply)；**39 Star**。
- 维护：最近代码推送 2026-07-22；2 个开放事项。很新，Docker 路径作者明确尚未验证，长期维护未证明。
- 部署：原生中等，需要 Python 3.12、FastAPI、Node 20/Next.js；Docker Compose 提供但未验证。解析/改写需要 Anthropic、OpenAI-compatible、Ollama 或 LM Studio。
- 技术栈：Python/FastAPI、Next.js、SQLite、JSON Resume、ReportLab、DOCX。
- 可复用模块：
  - no-fabrication whitelist：雇主、职位、学校、日期、数字、技能必须来自基础简历；
  - 违规反馈后重试一次，仍失败则回退原简历；
  - JSON Resume 中间格式；
  - 单页 PDF 和 ATS-safe DOCX 双输出、渲染降级。
- Jobloom 尚缺且值得加：渲染失败降级、单页可读性测试、portable JSON Resume export、guardrail violation UI。
- 理念冲突：证据边界只到“基础简历出现过”，而 Jobloom 认为一页简历不是全部职业事实，也不保证该事实仍有效；AGPL 与 Jobloom 当前许可证策略可能冲突。
- 结论：设计高度相关；在未决定 AGPL 前只借鉴，勿复制实现。

## 1.6 job-apply-plugin

- 项目：[neonwatty/job-apply-plugin](https://github.com/neonwatty/job-apply-plugin)；**96 Star**。
- 维护：GitHub UTC 时间 2026-09-01 有推送（纽约仍为 2026-08-31）；1 个 API 开放事项，活跃。
- 部署：简单。作为 Codex/Claude Code plugin 安装；需要可见 Browser/Chrome 集成。无需固定云 API key，但 Agent 主机本身可能有使用成本。
- 技术栈：Python 存储 helper、JavaScript QA/replay、Codex/Claude skills、浏览器插件接口、JSON/JSONL 本地存储。
- 可复用模块：
  - `job-apply`、`answer-memory`、`job-search`、`job-preferences` skill 分层；
  - confirmed/inferred/missing/sensitive 答案状态和 separate remember consent；
  - session 仅保存 answer references；
  - Lever/Greenhouse/Ashby semantic replay fixtures、approval、provenance、SHA-256、`finalActionActivations: 0`；
  - QA privacy scanner 和 final-action tripwire；
  - Codex/Claude 双宿主打包。
- Jobloom 尚缺且值得加：真实来源压缩后的语义 fixture pipeline、fixture approval/provenance、跨 Agent plugin packaging、敏感答案“使用同意”和“记忆同意”分离。
- 理念冲突：
  - 上游 broad `authorization.sponsorship*` kind 不能直接映射 Jobloom 四个独立问题；
  - README 明示当前真实 ATS flows 多数未验证；semantic replay 无 DOM，不能证明 live fill；
  - plaintext profile/answer 文件的事实约束弱于 Jobloom registry 和级联失效。
- 结论：MIT，当前最值得有限度直接复用的项目；只 vendor 必要语义 fixture/许可证，worker 和安全权威保留 Jobloom 自己实现。

## 1.7 MR.Jobs

- 项目：[humancto/mr-jobs](https://github.com/humancto/mr-jobs)；**4 Star**。
- 维护：最近代码推送 2026-04-06；0 个 API 开放事项。半年内无新代码，活跃度偏低。
- 部署：中高。FastAPI/local dashboard、SQLite、Playwright、python-jobspy、模型 provider；完整功能需要浏览器和可选模型 key。
- 技术栈：Python、FastAPI、SQLite、Playwright、python-jobspy、Web dashboard。
- 可复用模块：Greenhouse/Lever adapter、JobSpy source、RSS/HN source、scoring/tailor/form-analysis/email-classification 的组件路由、follow-up/ghost detection。
- Jobloom 尚缺且值得加：provider-independent component router、follow-up reminders、inbox classification proposal。
- 理念冲突：80+ 等阈值触发 tailoring、0–100 AI fit 分数和 generic AI form filler 都比 Jobloom 更宽松；“基于简历”仍不能机械证明无编造。
- 结论：MIT，可读实现；维护信号不足，不建议成为依赖。

## 1.8 AIHawk

- 项目：[feder-cr/Jobs_Applier_AI_Agent_AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk)；约 **30,294 Star**。
- 维护：2026-08-19 有推送；约 28 个开放事项。仓库历史上有归档/迁移和第三方 provider 移除，生态状态需谨慎确认。
- 部署：高。Python、Chrome/Selenium、YAML secrets/config、LLM provider；真实平台自动化易受反机器人和页面漂移影响。
- 技术栈：Python、Selenium/WebDriver、LangChain/LLM、YAML、PDF/cover-letter generation。
- 可复用模块：配置分层、浏览器初始化、resume/cover builder facade、provider/plugin 架构历史、异常处理和运行指南。
- Jobloom 尚缺且值得加：adapter failure telemetry、浏览器兼容诊断、运行环境 doctor。
- 理念冲突：核心目标是规模化自动申请；可能自动提交；历史 issue 明确出现经验/薪资/notice period 填错；规避检测/批量申请与 Jobloom Fill-Only、平台边界和事实约束直接冲突。AGPL 也限制代码合入。
- 结论：用作“哪些风险会出现”的反面研究样本，不作为实现基底。

## 1.9 notiapply

- 项目：[akshatvasisht/notiapply](https://github.com/akshatvasisht/notiapply)；**0 Star**。
- 维护：最近推送 2026-06-08；1 个开放事项。项目非常新，缺乏社区验证。
- 部署：高。Tauri、Playwright sidecar、Python scrapers、PostgreSQL、LaTeX 和浏览器扩展。
- 技术栈：TypeScript/Tauri、Python、Playwright、PostgreSQL、LaTeX。
- 可复用模块：per-job sidecar isolation、shadow DOM traversal、IPC contract、Tiered sources、LaTeX resume pipeline、Kanban。
- Jobloom 尚缺且值得加：worker 进程隔离、单申请失败不拖垮队列、IPC schema、运行级 fault containment。
- 理念冲突：以 autonomous pipeline 和 submission 为目标；使用 fingerprint spoofing；LLM 改写后直接进入自动化链，与 Jobloom 安全/平台政策边界冲突。
- 结论：只借鉴隔离和 IPC 设计；不要复用规避检测或自动提交路径。

---

# 第二类：可二次开发的单点组件

## 2.1 JobSpy：岗位聚合

- 项目：[speedyapply/JobSpy](https://github.com/speedyapply/JobSpy)；**4,203 Star**。
- 维护：最近推送 2026-02-18；API 记录 62 个开放事项。社区大，但当前代码更新频率需观察。
- 部署：低到中。Python package；部分站点无需 key，但不同 job board 的限制、代理和反爬风险不同。
- 技术栈：Python、HTTP/scraping、Pandas 数据输出。
- 可复用模块：多站点统一 schema、location/compensation/job-type normalization、结果 dataframe、source-specific adapters。
- Jobloom 可增加：可插拔 aggregator source、source reliability/health、统一外部 JobCard import。
- 冲突：聚合站抓取和批量采集可能违反 Jobloom 当前“官方 ATS API 或用户自己浏览”的边界；结果质量、重复和过期不可直接信任。
- 建议：MIT，但先只做可选 provider；默认仍优先官方 ATS API。

## 2.2 Resume Matcher：JD/简历分析 UI

- 项目：[srbhr/Resume-Matcher](https://github.com/srbhr/Resume-Matcher)；**28,294 Star**。
- 维护：2026-08-31 有推送；仓库页面约 25 open issues、41 open PR，活跃。
- 部署：Docker 较简单；源码需 Python 3.13、Node 22、uv。Ollama 可零 API 费；云 provider 需 key。
- 技术栈：FastAPI、Python、LiteLLM、Next.js 16、React 19、TypeScript、TinyDB、Playwright PDF。
- 可复用模块：provider abstraction、JD keyword/section extraction、resume diff UI、PDF preview、Docker single-port packaging、Ollama path。
- Jobloom 可增加：requirement-to-evidence heatmap、before/after claim diff、用户可解释的 gap UI、多 provider 配置。
- 冲突：ATS match percentage 和关键词优化容易奖励 padding；模型建议没有 Jobloom 的 claim manifest 时不能直接落简历。
- 建议：Apache-2.0，可借 UI/infra；匹配结果必须转为 Jobloom gap taxonomy。

## 2.3 lucidRESUME：多源履历与证据 ledger

- 项目：[scottgal/lucidRESUME](https://github.com/scottgal/lucidRESUME)；**6 Star**。
- 维护：最近推送 2026-08-18；0 开放事项。明确是 research preview。
- 部署：中高。.NET/C# 桌面/CLI、ONNX、本地文档渲染；无需云 API 才能做基础解析/embedding。
- 技术栈：C#/.NET、ONNX MiniLM、OpenXML、QuestPDF、YAML rules。
- 可复用模块：PDF/DOCX/LinkedIn export 合并、source tracking、skill→job→date→bullet provenance、非重叠年限、template learning、resume drift、JD extraction/explain。
- Jobloom 可增加：多源证据导入与冲突队列、技能实际使用年限、版本 drift 报告、LinkedIn export importer。
- 冲突：embedding cosine 自动合并可能错误合并不同事实；Jobloom 应把它作为候选聚类并要求用户确认。
- 建议：Unlicense；设计价值高，但先验证研究预览的正确性。

## 2.4 ResumeAgent：可组合简历技能

- 项目：[ApplyU-ai/ResumeAgent](https://github.com/ApplyU-ai/ResumeAgent)；**6 Star**。
- 维护：最近推送 2026-02-22；0 开放事项，维护偏低。
- 部署：中高。TypeScript、Docker、LaTeX、Tesseract、LLM BYOK。
- 技术栈：TypeScript、Agent skills、LaTeX、Tesseract.js、PDF/DOCX、Docker。
- 可复用模块：schema-driven resume parser、per-block tailoring/diff、JD heatmap、LaTeX live preview、CJK/OCR、多模板。
- Jobloom 可增加：block/element/phrase 三级 evidence heatmap、CJK 渲染、OCR 输入、per-block approval。
- 冲突：所谓 zero-fabrication 主要依赖 prompt；“gap bridging/quantification”可能越过证据边界。
- 建议：Apache-2.0；只让模型产生候选变换，Jobloom verifier 决定是否可用。

## 2.5 dreamjobs skill-extractor：技能抽取

- 项目：[dreamjobs-tech/skill-extractor](https://github.com/dreamjobs-tech/skill-extractor)；**0 Star**。
- 维护：最近推送 2026-07-08；0 开放事项。新项目，尚无社区验证。
- 部署：中等；预训练 gazetteer + MiniLM/MLP/ONNX，多语言实现。基础运行不必付费 API key。
- 技术栈：Python、JavaScript、Ruby、Rust、MiniLM、ONNX、NER/gazetteer。
- 可复用模块：跨语言一致输出、技能 gazetteer、上下文分类器、模型测试语料。
- Jobloom 可增加：Capability ontology 离线候选提取、多语言一致性和 corpus calibration。
- 冲突：其 73% F1 意味着不能直接生成 CandidateFact 或硬过滤；英语训练上下文限制明显。
- 建议：MIT；只作为候选 extractor，所有命中仍需 Jobloom pattern/golden/user review。

## 2.6 ESCO Skill Extractor：标准化技能与职业

- 项目：[KonstantinosPetrakis/esco-skill-extractor](https://github.com/KonstantinosPetrakis/esco-skill-extractor)；**31 Star**。
- 维护：最近推送 2025-06-12；0 开放事项，当前维护偏低。
- 部署：中等。Python、sentence-transformer/embedding 模型；本地运行，无必需付费 key。
- 技术栈：Python、ESCO/ISCO、Sentence Transformers、cosine similarity。
- 可复用模块：文本→ESCO skill、文本→ISCO occupation、canonical ID 和标准职业关系。
- Jobloom 可增加：外部 taxonomy 映射层、跨语言 canonical skill ID、职业方向市场对齐。
- 冲突：embedding 相似不能升级证据；ESCO 的标准职业分类也不能覆盖用户自己定义的方向。
- 建议：MIT；作为可选 external taxonomy，不成为 CandidateFact 来源。

## 2.7 Browser Use：通用浏览器执行

- 项目：[browser-use/browser-use](https://github.com/browser-use/browser-use)；约 **111,875 Star**。
- 维护：GitHub UTC 2026-09-01 有推送；仓库页面约 114 open issues、278 open PR，极活跃。
- 部署：中等。Python 3.11+、Chromium；可接真实 Chrome、local/cloud browser。Agent 模式通常需 Browser Use 或第三方模型 key。
- 技术栈：Python、Chromium/CDP、LLM agents、CLI、cloud/browser profiles。
- 可复用模块：持久浏览器 session、element state、文件上传、browser profile、可见执行、agent history。
- Jobloom 可增加：标准化浏览器 executor 接口、浏览器健康检查、可见/可暂停 session、artifact capture 的红acted debug 模式。
- 冲突：通用 Agent 可以自行导航、点击和解释页面；直接使用会让 LLM 同时拥有“决定值”和“执行动作”两种权力，违反 Jobloom 最小执行面。Cloud 模式还改变隐私边界。
- 建议：不进入 Jobloom 运行时。去掉模型决策后，剩余需求由直接 Playwright 实现更小、
  更易审计，也避免引入庞大依赖和攻击面。只借其页面观察语义、测试案例和故障分类。

## 2.8 OpenResume：本地简历解析/渲染

- 项目：[xitanggg/open-resume](https://github.com/xitanggg/open-resume)；**8,874 Star**。
- 维护：最近代码推送 2024-10-29；143 个 API 开放事项，代码维护已明显放缓。
- 部署：低。浏览器/Next.js，强调客户端解析和 PDF 生成，无必需 API key。
- 技术栈：TypeScript、React/Next.js、Redux、PDF.js、客户端 PDF renderer/parser。
- 可复用模块：纯前端 resume builder、PDF text reconstruction、ATS readability、JSON resume state、打印布局。
- Jobloom 可增加：无需上传服务器的可视编辑、ATS parse preview、resume JSON editor。
- 冲突：parser 输出只是候选结构，不是已确认事实；AGPL 不宜直接合入未定许可证的 Jobloom。
- 建议：研究 UX/算法；代码复用需先解决 AGPL。

## 2.9 JSON Resume CLI：可移植简历格式

- 项目：[jsonresume/resume-cli](https://github.com/jsonresume/resume-cli)；**4,719 Star**。
- 维护：最近推送 2026-06-12，但仓库已 archived；6 个 API 开放事项。
- 部署：低。Node CLI、本地 JSON 和主题；通常不需要 API key。
- 技术栈：JavaScript/Node、JSON Schema、HTML/PDF themes。
- 可复用模块：JSON Resume import/export、schema validation、theme renderer、portable resume ecosystem。
- Jobloom 可增加：CandidateSnapshot/ResumeVersion 的脱敏 JSON Resume export、主题 adapter、schema migration。
- 冲突：JSON Resume schema 没有 Jobloom 证据 ID、strength、claim manifest、expiry；不能成为事实权威。
- 建议：MIT 但 archived；只做 compatibility adapter，不依赖 CLI 本身。

---

# 第三类：理念相近、实现路径不同

## 3.1 evidence-backed resume-builder

- 项目：[DaveVoyles/resume-builder](https://github.com/DaveVoyles/resume-builder)；**10 Star**。
- 维护：最近推送 2026-08-22；0 开放事项，活跃但规模小。
- 部署：中等。Node/terminal agent、DOCX 渲染；无需固定模型 key，任意能读写仓库的 Agent 可驱动。
- 技术栈：JavaScript、JSON/Markdown evidence ledger、DOCX、HTML tracker。
- 可复用模块：每个数字声明对 evidence ledger 的硬阻断、private-by-default、tracker、tailor lifecycle、interview study guide。
- Jobloom 可增加：人类可读 evidence ledger view、面试 defensibility packet、claim failure explanation。
- 理念冲突：证据存放和流程主要靠文件/Agent discipline，缺少 Jobloom 数据库状态机、答案时效和授权级联。
- 深问题启发：它不只问“关键词是否匹配”，还问“面试时能否为每条声明举证”，与 Jobloom 的职业定位方向高度一致。

## 3.2 VitaeContext

- 项目：[vitaecontext/vitaecontext](https://github.com/vitaecontext/vitaecontext)；**73 Star**。
- 维护：最近推送 2026-08-30；1 个开放事项，活跃。
- 部署：简单。`npx` 安装，支持 Codex、Claude Code、Gemini 等 provider；无必需付费 API key。
- 技术栈：JavaScript/Node、Markdown career context、跨 provider skills/installers。
- 可复用模块：统一 career-context schema、validate/install/export、多 Agent provider adapter、proof-of-work/context modules。
- Jobloom 可增加：可移植 Career Evidence Packet、不同 Agent 的只读上下文视图、公开/私有字段分层。
- 理念冲突：可移植 Markdown context 比 Jobloom registry 更容易被手工改写；缺少 hash、approval、expiration。
- 深问题启发：把职业身份作为长期、可复用的 Agent context，而不是每次从一页简历重新推断。

## 3.3 Career Manager

- 项目：[muggl3mind/career-manager](https://github.com/muggl3mind/career-manager)；**24 Star**。
- 维护：最近推送 2026-04-16；0 开放事项；9 commits 左右，个人原型。
- 部署：简单到中等。Claude Code + Python requirements；README 甚至建议 skip permissions，安全上不应照搬。
- 技术栈：Python、Claude skills、CSV/Markdown、静态 dashboard。
- 可复用模块：onboarding interview、自动提出 3–8 career paths、criteria/background/search config、company research、pipeline health check。
- Jobloom 可增加：职业方向 onboarding UX、career hypotheses 的并列比较、company dossier、pipeline health report。
- 理念冲突：Agent 直接派生路径和配置，缺少 Jobloom user-only materialization/hash approval；`--dangerously-skip-permissions` 不可接受。
- 深问题启发：先定义职业路径和目标公司，再决定岗位，而不是被每个 JD 临时牵引。

## 3.4 Appliable

- 项目：[roysahar11/appliable](https://github.com/roysahar11/appliable)；**7 Star**。
- 维护：最近推送 2026-04-05；0 开放事项，低频。
- 部署：中高。Claude Code skills/agents、浏览器搜索、WhatsApp 通知和简历 PDF pipeline；外部服务配置较多。
- 技术栈：JavaScript/skills、Agent orchestration、browser search、WhatsApp、PDF。
- 可复用模块：深度 profile interview、多方向 base resumes、两阶段 relevance scan、daily report、批量 agent orchestration、手机审批。
- Jobloom 可增加：异步 daily digest、移动端 review、方向级 base resume、两阶段廉价筛选。
- 理念冲突：批量并行定制由 Agent 自审，缺少 Jobloom claim-by-claim deterministic gate；WhatsApp 传输材料改变隐私边界。
- 深问题启发：把“我是谁、我想去哪”建模为长期上下文，并主动对不合理的目标方向提出反证。

## 3.5 Job Search Superpower

- 项目：[MikeBengtson/job-search-superpower](https://github.com/MikeBengtson/job-search-superpower)；**2 Star**。
- 维护：最近推送 2026-05-26；0 开放事项。规模小。
- 部署：极低，单 prompt/skill 文件；无必需 API key，但使用者的 LLM 服务可能收费。
- 技术栈：Prompt/Markdown/YAML export。
- 可复用模块：21 类 role-family tuning、职业策略问卷、title integrity、career changer/founder returner 等 persona、structured export packet。
- Jobloom 可增加：persona/transition-specific onboarding、role-family heuristic library、策略 export、title-integrity checks。
- 理念冲突：GitHub 未明确识别标准许可证；规则由 prompt 执行，不是确定性代码；行业趋势近似值不能作为市场事实。
- 深问题启发：对同一履历按 career transition 类型提出不同的定位问题，而不是只重排关键词。

## 3.6 Proficiently Claude Skills

- 项目：[proficientlyjobs/proficiently-claude-skills](https://github.com/proficientlyjobs/proficiently-claude-skills)；**354 Star**。
- 维护：最后代码推送 2026-03-12；4 个 API 开放事项。仓库活动仍有更新，但代码半年未推，需观察。
- 部署：简单但宿主受限。Claude Code/Cowork + Chrome 扩展；Telegram/network 功能另需配置。
- 技术栈：Claude plugin/skills、Markdown profile、scripts、Chrome integration、Telegram。
- 可复用模块：setup/job-search/tailor/cover-letter/network-scan/apply 的 skill 分解、共享 prerequisites、ATS patterns、priority hierarchy、per-job folder。
- Jobloom 可增加：清晰的 skill capability 分包、network scan、Telegram/mobile入口、用户数据目录规范。
- 理念冲突：Claude 浏览器直接执行和简历改写，缺少 Jobloom backend gate；profile.md/application-data.md 的 freshness/authorization 边界更弱。
- 深问题启发：一个职业系统应拆成可组合的长期能力，而不是单一“大投递”命令。

---

# 第四部分：Jobloom 新功能候选池

以下不是把外部项目功能全部搬进来，而是按 Jobloom 原则重新设计。

## P0：完成真实但受限的 Fill-Only 闭环

1. **PDF application-material gate**：扩展名、magic bytes、kind、hash 四重验证；resume 和 cover letter 同等处理。
2. **版本化 worker protocol**：session/page/package hash、过期、origin allowlist、结果只含 observed hash、原子导入、不可重放。
3. **ATS semantic replay corpus**：引入带 MIT attribution 的 Lever/Greenhouse/Ashby `kind/role/provenance/approval`；生成 Jobloom 本地交互 fixture。
4. **Worker 进程隔离**：参考 notiapply 的 per-job sidecar，但不复用 submit 或 stealth；每次只允许一个 package/一页。
5. **生产 adapter 顺序由真实队列驱动**：当前样本 Lever 68%、Greenhouse 18%、Ashby 13%、SmartRecruiters 1%、Workday 0%。先前三个；每个 adapter 必须有 supervised live acceptance。
6. **Final-action tripwire**：不仅检查 label/type，还拦截 form submit side effect；测试必须证明 activation count 为零。

## P1：Career Evidence Bank 的用户体验

1. **多源导入与冲突队列**：resume、LinkedIn export、项目文档、旧简历只生成 proposed facts；逐条确认后进入 CandidateFact。
2. **Evidence Ledger UI**：`claim → fact/evidence → source → confidence → transformation`，支持面试时快速回溯。
3. **Resume drift report**：比较两个 ResumeVersion，指出新增/删除/数值变化/证据丢失，不只做文本 diff。
4. **Requirement heatmap**：block/element/phrase 展示 direct/related/transferable/unsupported；拒绝单一 ATS 百分比。
5. **Portable Career Evidence Packet**：参考 VitaeContext/JSON Resume，但导出分公开版、Agent 只读版、完整私有版；不泄露 answer values。
6. **多语言 capability mapping**：ESCO/离线 extractor 仅提出 alias 候选；golden tests 和用户批准后才能进入 ontology。

## P1：岗位质量与投入决策

1. **PostingTrustRecord**：官方源、时间、repost、canonical/apply domain、关闭后聚合副本、payment/identity/off-platform signals；只给 reason codes，不给 ghost-job 概率。
2. **Source health dashboard**：上次成功、失败原因、字段缺失率、最近 schema drift、API/页面来源区分。
3. **Salary floor / total-comp / commute / relocation**：作为用户偏好和 offer object，不混入能力证据。
4. **投入价值视图**：Current Fit、Career Value、Real Gaps、Posting Trust、Required User Time 分开，不能压成一个分数。
5. **Cross-city presentation groups**：只做 UI 分组，每个 opening 保留独立 job ID/URL，绝不合并。

## P1：Dashboard 与审批队列

1. review queue 必须使用 evidence coverage 排序，不直接使用已知反相关的 raw `ranking_score`；
2. application Kanban 展示真实状态机，不允许拖拽绕过 transition guard；
3. Answer freshness、authorization、resume approval、material lock、pre-submit review 单独显示；
4. job decision、submitted-confirmed、backend submitted 三个 rung 分开；
5. safe support report 默认只含 schema version、counts、hash prefixes、reason codes 和环境版本。

## P2：面试、联系与结果

1. **Interview Story Bank**：Situation/Task/Action/Result/Reflection 每句话引用 EvidenceUnit；缺失部分留空让用户补。
2. **Defensibility packet**：每个定制 bullet 对应面试追问、证据和不可声称的边界。
3. **Company dossier**：官方来源、近期事件、产品/团队、风险和需要验证的问题；外部事实有抓取时间。
4. **Network scan**：只导入用户提供 CSV；公司匹配；消息只生成草稿、不自动打开或发送。
5. **Outcome classification**：先处理用户导入的 `.eml`；存 hash/outcome code，不存正文；用户确认后再变更状态。
6. **Follow-up cadence**：只创建提醒/草稿；从未确认提交的申请不生成 follow-up。
7. **Offer/negotiation object**：记录 advertised/verbal/written compensation、deadline、条款审阅问题；不提供法律判断。

## P3：运营、可移植性和成本

1. Codex/Claude 等 provider adapter，共享同一 Jobloom core，而不是复制业务规则到各 skill；
2. MCP 只暴露 value-free query 和受审批 mutation；
3. local Ollama/廉价模型作为可选 rung，所有 deterministic gates 不依赖模型；
4. adapter failure telemetry 仅保存 reason codes、版本和结构 hash；
5. backup/restore、schema migrations、doctor、desktop packaging、升级回滚；
6. 模型预算、source 请求预算、用户时间预算分别统计。

---

# 功能冲突矩阵

| 外部常见做法 | 为什么有吸引力 | Jobloom 风险 | Jobloom 兼容实现 |
| --- | --- | --- | --- |
| ATS/fit 百分比 | 易排序、易展示 | 伪精确，掩盖硬缺口 | eligibility + gap taxonomy + action |
| 关键词注入 | 快速提高表面覆盖 | padding、虚构、证据升级 | 只允许已确认事实的重排/压缩/澄清 |
| LLM 直接填开放题 | 覆盖任意表单 | 第一人称虚构、scope 错误 | AnswerLibrary exact/reviewed forms；未知即暂停 |
| 自动 Submit | 省用户时间 | 法律承诺、重复申请、不确定状态 | Fill-Only + pre-submit review + 用户最终动作 |
| 自动保存所有答案 | 下次更快 | 敏感信息长期化、答案过期 | 使用同意与记忆同意分离；scope/expiration |
| embedding 自动合并履历 | 去重方便 | 错误合并不同事实 | 只生成候选冲突组，用户确认 |
| 通用 browser agent | 适配所有页面 | 模型同时决定和执行 | 确定性 package + 最小直接 Playwright executor + hash verify |
| 批量抓 LinkedIn/Indeed | 岗位更多 | 平台条款、封号、低可信重复 | 官方 ATS API + 用户自己打开的 browser assist |
| ghost-job 概率 | 看起来直观 | 不可验证、误伤 | posting-trust reason codes + unknown |
| skip-permissions/stealth | 自动化顺滑 | 扩大权限、规避平台控制 | 最小权限、可见浏览器、用户 gesture、无绕过 |

# 建议给其他 Agents 的 Brainstorming 问题

1. 哪些外部模块能通过适配层接入，而无需复制 Jobloom 的事实与状态规则？
2. 如何证明一个 ATS adapter “当前可用”？semantic replay、DOM fixture、supervised live acceptance 各自证明什么？
3. PostingTrustRecord 应保存哪些原始证据，哪些只能保存 hash/source timestamp？
4. Career Evidence Packet 如何在可移植和不泄露隐私之间分层？
5. Interview Story Bank 的每个叙述句应如何绑定 EvidenceUnit，如何处理用户口述的新证据？
6. Dashboard 哪些 mutation 可以开放，哪些必须继续走 CLI/core 的 user actor 审批？
7. 如何评估复用代码的许可证边界，尤其 AGPL、无许可证和真实页面衍生 fixture？
8. 如何量化新功能是否真正降低“每次面试所需时间/成本”，而不是只增加申请数？
9. 哪些外部项目的失败案例应变成 Jobloom regression test？
10. 当外部项目 README 声称“never fabricate/never submit”时，需要哪些代码级证据才能相信？

# 数据来源

- 各项目 GitHub repository、README、LICENSE 和 GitHub 官方 repository metadata API，访问于 2026-08-31。
- Star 和时间为动态数据；复核时可打开每个项目链接及 `https://api.github.com/repos/<owner>/<repo>`。
- 本报告没有执行任何外部项目，也没有用真实候选人资料或真实申请页面做 live acceptance。
