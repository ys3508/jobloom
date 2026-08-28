# 技术规格：Career Direction Engine V2

状态：**设计规格，未实现。** 基线为 `9c5275e`（保守化后的 Career Hypothesis Generator V1）。
2026-08-28 经 Codex 复核；§8.1 描述的是当前已提交状态。
目标读者：实现者（Codex）与产品所有者。本文不改代码。

---

## 0. 范围与继承的不变量

V2 不推翻 V1，而是把 V1 的"预设 catalog → 对事实打分"降级为流水线中的**一个启发式阶段**，
并在其上下游补齐真正缺失的部分：证据的**自底向上聚类**、受控的 **Title Ontology**、
**真实市场对账**，以及**多轴不压平**的方向对象。

以下不变量由现有仓库强制，V2 必须原样继承，不得绕过：

| 不变量 | 现有强制点 |
|---|---|
| 事实不可发明；证据强度不可提升 | `evidence_matcher.EVIDENCE_ORDER`、claims manifest 校验 |
| 一切激活必须 `actor="user"` + 精确哈希 | `direction_core.approve_direction/approve_portfolio` |
| SearchDirection profile 不可变、按内容哈希 | `direction_core.register_direction` |
| 关键词组的**字段作用域属于代码**，不属于 profile | `direction_core.GROUP_FIELDS` 上方的注释 |
| JD 原文不得进入路由决策与事件表 | `direction_core.ROUTING_DENYLIST`、`record_routing` |
| 未确认材料只能产生 provisional，禁止采纳 | `career_direction_core.materialize_selection` |

**新增不变量（V2 提出）：**

- **N1**：任何面向用户的方向，其每个轴必须回溯到该轴自己的权威来源：证据轴走
  `direction → axis → facet_assignment → evidence_unit → fact_id → snapshot_sha256`；市场轴走
  `axis → market_requirement_profile → JobCard hashes`；意愿轴走 `axis → user-reviewed goals hash`；
  签证轴同时引用工作授权记录与市场 profile。任一所需来源断链即不得展示该轴的非 null 值。
- **N2**：分数**永不跨轴压平后覆盖原值**。排序可以用复合值，但复合值必须与其输入轴同时持久化，
  且携带 `ranking_explanation`。
- **N3**：模型可以**提出**候选（facet 标签、聚类命名、elicitation 问题措辞），
  **绝不可以**产生分数、绝不可以发明 title、绝不可以提升证据强度、绝不可以决定是否展示。
- **N4**：市场数据样本不足时**fail closed**——返回 `market_capacity: null` 与显式原因码，
  不得用默认值填充。

---

## 一、完整架构

### 1.1 流水线

```
┌─ S0 ─────────────────────────────────────────────────────────────────────┐
│ CandidateSnapshot (active, user-registered)                              │
│   └─ CandidateFacts[]  status ∈ {confirmed, locked}                      │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  确定性
┌─ S1 Evidence Normalization ──▼───────────────────────────────────────────┐
│ in : facts[]                                                             │
│ out: EvidenceUnit[]  (fact_id, surface_terms, source_strength, temporal) │
│ 持久化: evidence_units                                                    │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  规则 + (模型可提议 facet，规则复核)
┌─ S2 Facet Extraction ────────▼───────────────────────────────────────────┐
│ in : EvidenceUnit[]                                                      │
│ out: FacetAssignment[]  (unit_id, domain[], function[], seniority_band)  │
│ 持久化: evidence_facets                                                   │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  确定性
┌─ S3 Bottom-up Clustering ────▼───────────────────────────────────────────┐
│ in : FacetAssignment[]                                                   │
│ out: CareerHypothesis[0..30]  (证据充分时目标 10–30；不为凑数补候选)       │
│ 持久化: career_hypotheses                                                 │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  确定性（查表）
┌─ S4 Title Ontology Resolution ─▼─────────────────────────────────────────┐
│ in : CareerHypothesis[] + TitleOntology(version)                         │
│ out: OntologyBinding[]  (hypothesis → canonical_title[] + query 模板)     │
│ 持久化: hypothesis_ontology_bindings                                      │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  确定性（枚举 ontology）
┌─ S5 Top-down Market Candidates ▼─────────────────────────────────────────┐
│ in : TitleOntology + 用户地域/授权约束                                     │
│ out: MarketDirectionCandidate[]  (与候选人证据无关)                        │
│ 持久化: market_direction_candidates                                       │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  确定性 + MarketRequirementProfile
┌─ S6 Reconciliation ──────────▼───────────────────────────────────────────┐
│ in : CareerHypothesis[] × MarketDirectionCandidate[] × MRP               │
│ out: ReconciledDirection[]  (八轴；各轴按自身契约表达 null/unknown)        │
│ 持久化: reconciled_directions                                             │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  确定性（去重/收敛/分档）
┌─ S7 Convergence ─────────────▼───────────────────────────────────────────┐
│ in : ReconciledDirection[]                                               │
│ out: UserFacingDirection[0..6]（证据充分时目标 3–6）+ Question[0..5]      │
│ 持久化: direction_proposals（哈希锁定）                                    │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │  actor=user + 精确哈希
┌─ S8 Approval（现有路径，不改）▼──────────────────────────────────────────┐
│ materialize_selection → direction_core.register_direction               │
│                       → register_portfolio → approve_portfolio           │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.2 各阶段契约

| 阶段 | 输入 | 输出 | 持久化表 | 确定性规则 | 模型可参与部分 |
|---|---|---|---|---|---|
| S1 | `candidate_facts` 行 | `EvidenceUnit` | `evidence_units` | 全确定：分词、别名表、时间解析、强度直取 | 无 |
| S2 | `EvidenceUnit` | `FacetAssignment` | `evidence_facets` | facet 词表命中为主 | **可提议**未命中单元的 facet，须落回受控词表且写 `proposed_by:"model"` + 用户确认前不计入 verified |
| S3 | `FacetAssignment` | `CareerHypothesis` | `career_hypotheses` | 全确定：签名聚合、阈值、多样性计算 | **可提议**簇的人类可读命名（不影响任何计算） |
| S4 | Hypothesis + Ontology | `OntologyBinding` | `hypothesis_ontology_bindings` | 全确定：ontology 查表 | 无 |
| S5 | Ontology + 约束 | `MarketDirectionCandidate` | `market_direction_candidates` | 全确定：枚举 | 无 |
| S6 | Hypothesis × Market × MRP | `ReconciledDirection` | `reconciled_directions` | 全确定：八轴公式 | 无 |
| S7 | ReconciledDirection | `UserFacingDirection` | `direction_proposals` | 全确定：去重、分档、Top-N | **可提议** elicitation 问题的措辞（问题的**选取**由信息增益规则决定） |
| S8 | 用户选择 | SearchDirection/Portfolio | 现有表 | 全确定 | 无 |

**模型参与的三处，全部满足**：输出落在受控枚举内 → 规则校验 → 标记 `proposed_by` →
不影响任何分数 → 用户可见可否决。

---

## 二、Title Ontology

### 2.1 为什么必须有

现状：`career-direction-catalog.json` 的 `target_titles` 是**手写字符串数组**，
`direction_core.GROUP_FIELDS["target_titles"] = {"title"}` 只做 token 序列匹配。
后果有三：

1. `Computational Research Associate` 不在任何列表里 → 只能靠 discovery 关键词救回 review
   （已在真实岗位上观察到）。
2. `Research Analyst II` 与 `Research Analyst` 是两条独立字符串，无规范化关系。
3. 无法从方向**确定性地**生成搜索查询，只能靠人或模型现编。

### 2.2 数据结构

单独的版本化制品：`skills/jobloom/assets/title-ontology/<version>/ontology.json`

```json
{
  "schema_version": "0.1.0",
  "ontology_version": "2026.08.0",
  "sources": [
    {
      "source_id": "onet",
      "name": "O*NET 31.0 Database",
      "license": "CC BY 4.0",
      "url": "https://www.onetcenter.org/database.html",
      "version_url": "https://www.onetcenter.org/db_releases.html",
      "license_url": "https://www.onetcenter.org/license_db.html",
      "retrieved_at": "2026-08-28",
      "coverage": "US occupations, SOC-aligned"
    },
    {
      "source_id": "esco",
      "name": "ESCO v1.2.1",
      "license": "Commission Decision 2011/833/EU reuse terms; attribution required",
      "url": "https://esco.ec.europa.eu/",
      "version_url": "https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-versions",
      "license_url": "https://esco.ec.europa.eu/en/about-esco/faq?page=1",
      "retrieved_at": "2026-08-28",
      "coverage": "EU occupations and skills"
    },
    {
      "source_id": "jobloom",
      "name": "Jobloom curated",
      "license": "proprietary",
      "retrieved_at": "2026-08-28",
      "coverage": "healthcare / research / life-sciences analytics refinements"
    }
  ],
  "occupations": [
    {
      "occupation_id": "occ.research-data-analysis",
      "role_family": "analytics.research_data",
      "source_refs": [
        {"source_id": "jobloom", "code": "jobloom.research-data-analysis", "confidence": "curated"}
      ],
      "titles": [
        {
          "title_id": "title.research-data-analyst",
          "canonical_title": "Research Data Analyst",
          "function": "data_analysis",
          "domain_qualifiers": ["research"],
          "seniority": "individual_contributor",
          "seniority_rank": 2,
          "variants": [
            {"surface": "Research Data Analyst", "kind": "canonical"},
            {"surface": "Research Analyst II", "kind": "leveled", "level_token": "II"},
            {"surface": "Data Analyst, Research", "kind": "inverted"},
            {"surface": "Research Data Specialist", "kind": "synonym"}
          ],
          "requires_domain_guard": true,
          "domain_guard_terms": ["research", "study", "clinical", "academic", "public health"],
          "excluded_senses": [
            {
              "surface": "Research Analyst",
              "sense": "equity_research",
              "disambiguator_terms": ["equity", "sell-side", "buy-side", "securities", "coverage universe"],
              "action": "fail"
            },
            {
              "surface": "Research Analyst",
              "sense": "market_research",
              "disambiguator_terms": ["focus group", "survey panel", "brand tracking", "consumer insights"],
              "action": "review"
            }
          ],
          "adjacent_titles": [
            {"title_id": "title.clinical-research-data-analyst", "relation": "specialization", "distance": 1},
            {"title_id": "title.data-analyst", "relation": "generalization", "distance": 1},
            {"title_id": "title.biostatistician", "relation": "sibling", "distance": 2}
          ],
          "search_query_templates": [
            {"template_id": "exact", "pattern": "\"{canonical_title}\"", "engines": ["linkedin", "indeed"]},
            {"template_id": "domain_qualified", "pattern": "\"{canonical_title}\" {domain_guard}", "engines": ["linkedin", "indeed"]},
            {"template_id": "skill_discovery", "pattern": "{domain_guard} \"{skill_a}\" \"{skill_b}\" analyst", "engines": ["linkedin"]}
          ]
        }
      ]
    }
  ],
  "alias_index": [
    {
      "surface": "data analyst",
      "resolves_to": ["title.data-analyst", "title.research-data-analyst", "title.clinical-data-analyst"],
      "resolution": "ambiguous",
      "requires_domain_guard": true
    }
  ]
}
```

### 2.3 规范化算法（确定性）

```
normalize_title(raw_title) -> TitleResolution
  1. surface  := casefold(raw); 折叠多空格；去尾随标点
  2. 先执行非职级语义守卫：若职级词与 `SENIORITY_GUARD_FOLLOWERS` 中的后继词组成
       受保护短语（例如 `senior care`），则将该 token 标为 protected，不得剥离
  3. 仅剥离未受保护的 level token：正则
       ^(sr|senior|jr|junior)\b 或 \b(i|ii|iii|iv|v|1|2|3|4)$
       → level_token；剥离结果为 base_surface
  4. 在 alias_index 精确查 base_surface
       命中 resolution="unique"    → 绑定该 title_id
       命中 resolution="ambiguous" → 进入 5
       未命中                      → 进入 6
  5. 歧义消解：仅用 domain_guard_terms 在 **JobCard 结构化字段**中判定
       （title / summary / responsibilities / required_skills / employer）
       恰好一个 title 的 guard 命中 → 绑定
       零个或多个命中               → TitleResolution.status = "ambiguous"，方向不得自动匹配
  6. 未知 title：不猜测。status = "unmapped"，记录 surface 供 ontology 扩展审阅
  7. excluded_senses：若 disambiguator_terms 命中，按 action 返回 fail / review，
       **优先于**任何正向绑定
```

### 2.4 防"正文出现即误判为职位名"

现有 `direction_core` 已经把 `target_titles` 的字段作用域锁死在 `title`
（`GROUP_FIELDS`），正文里的 title 只会触发 `contextual_title_reference_only`。
**V2 必须保持这条，并额外要求：**

- ontology 解析只读 `job.title`；`summary`/`responsibilities` 仅用于**消歧**，
  不得用于**绑定**。
- 归并到规格层的一条规则：`bind_from ⊆ {title}`，`disambiguate_from ⊆ ROUTING_FIELDS`，
  两个集合在代码常量中分开定义，禁止从 ontology JSON 配置。（与 `GROUP_FIELDS` 同理由：
  数据不得给自己扩权。）

### 2.5 确定性查询生成

```
generate_queries(direction) -> Query[]
  for title in direction.bound_titles (按 seniority_rank 升序, title_id 字典序):
    for template in title.search_query_templates (按 template_id 字典序):
      slots := {canonical_title, domain_guard := title.domain_guard_terms[0],
                skill_a, skill_b := direction 的 top-2 evidence-backed skill terms}
      if 任一 slot 缺失 → 跳过该 template（不填默认值）
      yield Query(query_id = sha256(pattern|slots)[:16], text = render(template, slots),
                  engine = template.engines[i], provenance = {title_id, template_id, ontology_version})
```

**模型不参与查询串生成。** 查询是 ontology × 模板 × 证据支撑技能的纯函数，
可复现、可 diff、可回归测试。

### 2.6 七个易误判 edge case

| # | 场景 | 期望行为 |
|---|---|---|
| E1 | `Research Analyst` 在投行 JD 中（equity research） | `excluded_senses.equity_research` 命中 → **fail**，不进池 |
| E2 | `Research Analyst II` | 剥离 level token `II` → 绑定 `title.research-data-analyst`；`seniority_rank` 由 level 提升一档，若超出用户 band → `seniority_outside_portfolio` |
| E3 | `Data Analyst`（无任何领域词） | `alias_index` 标 ambiguous + `requires_domain_guard` → 无 guard 命中则 `ambiguous`，只能进 review，永不 auto-match |
| E4 | JD 标题 `Senior Care Coordinator` | 现有 `SENIORITY_GUard_FOLLOWERS["senior"] ∋ "care"` 必须继续生效——`senior` 是领域词不是职级。ontology 的 level 剥离**必须在**该守卫之后执行 |
| E5 | JD 正文写 "you will partner with our Clinical Data Analyst team"，标题是 `Office Coordinator` | 绑定只读 title → 不绑定；正文命中只产生 `contextual_title_reference_only`（现有行为，必须保留） |
| E6 | `Biostatistician I` vs `Biostatistics Analyst` | 两者均绑到 `occ.biostatistics`，但 `title_id` 不同；去重发生在 **occupation × facet 签名**层，不在字符串层 |
| E7 | `Clinical Data Manager`（CDM，EDC/CTMS 岗）vs `Clinical Data Analyst` | 不同 `function`（`data_operations` vs `data_analysis`）；`excluded_senses` 不适用，靠 function facet 区分，且 `warning_keywords` 命中 EDC/CTMS 时按义务降级 |

---

## 三、Evidence Coverage 与 Direction Object

### 3.1 EvidenceUnit（S1 产物）

```json
{
  "unit_id": "eu-0007",
  "fact_id": "fact-example-0001",
  "snapshot_sha256": "aaaaaaaa…",
  "source_strength": "direct",
  "surface_terms": ["r", "pipeline", "shell", "research-data"],
  "temporal": {"start": "2024-01", "end": "2024-12", "months": 12, "recency_months": 20},
  "source_kind": "experience_claim",
  "locked": false
}
```

`source_strength` 与 `fact_id` 是**事实**，直取自 `candidate_facts`，**任何下游不得修改**。
但它只表示该事实本身的证据等级，不表示它对所有能力或方向都有同等支持力。

S2 必须另外产生有类型的关系强度：

```json
{
  "unit_id": "eu-0007",
  "facet_kind": "capability",
  "facet_value": "linux_shell_scripting",
  "relation_strength": "direct",
  "source_strength": "direct",
  "rule_id": "R-FACET-014",
  "assigned_by": "controlled_rule"
}
```

硬约束：`relation_strength <= source_strength`。同一事实对不同能力可以有不同的
`relation_strength`；例如课程证书作为文档事实可以是 direct，但它对“生产环境 Python 工作经验”
最多只能是 strongly_related。该关系只能由受控规则或用户确认建立，不能按事实类型批量推断。

### 3.2 Direction Object

```json
{
  "direction_id": "dir-…",
  "proposal_id": "…",
  "ontology_version": "2026.08.0",
  "snapshot_sha256": "aaaaaaaa…",

  "identity": {
    "occupation_id": "occ.research-data-analysis",
    "bound_title_ids": ["title.research-data-analyst", "title.clinical-research-data-analyst"],
    "facet_signature": {"domain": ["clinical", "public_health"],
                        "function": ["data_analysis", "data_management"],
                        "seniority_band": "ic_2"}
  },

  "evidence": {
    "by_strength": {
      "direct":            [{"unit_id": "eu-0007", "fact_id": "fact-example-0001", "facet": "r_programming", "relation_strength": "direct"}],
      "strongly_related":  [],
      "transferable":      [],
      "mention_only":      []
    },
    "unsupported_core_signals": ["gene-environment interaction"],
    "supporting_fact_ids": ["fact-example-0001", "fact-example-0002"],
    "provenance": [{"axis": "evidence_fit", "facet_assignments": ["eu-0007:capability:r_programming"], "rule_id": "R-EF-01"}],
    "diversity": {
      "distinct_employers": 3, "distinct_time_spans": 3,
      "max_single_fact_share": 0.21, "distinct_functions": 2
    },
    "coverage": {
      "domain":    {"required": 3, "covered": 3},
      "function":  {"required": 2, "covered": 2},
      "seniority": {"band": "ic_2", "candidate_band": "ic_2", "delta": 0}
    },
    "market_requirement_coverage": {
      "profile_id": "mrp-…", "required_terms": 14, "covered": 9,
      "uncovered_required": ["GWAS", "UK Biobank"], "sample_size": 41
    }
  },

  "claim_boundaries": {
    "allowed":   [{"capability_id": "cap.r-data-pipeline", "fact_ids": ["fact-example-0001"], "max_relation_strength": "direct"}],
    "forbidden": [{"capability_id": "cap.uk-biobank", "reason": "no_supporting_fact"}]
  },

  "axes": {
    "evidence_fit":        {"value": 84,  "unit": "0-100", "inputs": ["evidence.by_strength", "coverage"], "rule_id": "R-EF-01"},
    "career_distance":     {"value": 1,   "unit": "facet_hops", "inputs": ["facet_signature", "candidate_current_facets"]},
    "narrative_coherence": {"value": 0.72,"unit": "0-1", "inputs": ["diversity", "temporal"]},
    "market_capacity":     {"value": null,"unit": "postings_per_window", "null_reason": "market_sample_below_minimum"},
    "accessibility":       {"value": null,"unit": "0-100", "null_reason": "market_profile_unavailable"},
    "user_intent":         {"value": null,"unit": "0-100", "null_reason": "career_goals_not_supplied"},
    "career_growth":       {"value": null,"unit": "0-100", "null_reason": "career_goals_not_supplied"},
    "visa_compatibility":  {"value": "unknown", "unit": "enum", "inputs": ["candidate.work_authorization", "mrp.sponsorship_distribution"]}
  },

  "readiness": "ready_now",
  "ranking": {
    "method": "lexicographic_v1",
    "key": ["evidence_fit", "market_capacity", "user_intent"],
    "explanation": "evidence_fit=84 高于同档；market_capacity 缺失，按 null_last 排在有值项之后"
  }
}
```

### 3.3 字段分类

| 类别 | 字段 | 规则 |
|---|---|---|
| **事实**（直取，不可改） | `fact_id`、`source_strength`、`snapshot_sha256`、`locked`、`temporal` | 任何阶段改写即为缺陷 |
| **受控关系** | `facet_assignment.relation_strength`、`rule_id`、`assigned_by` | 必须 `<= source_strength`；模型提议在用户确认前不参与 verified 计算 |
| **计算结果**（确定性函数） | 全部 `axes.*`、`diversity`、`coverage`、`readiness`、`ranking` | 必须可由输入重算复现；须带 `rule_id` |
| **用户输入** | `career_goals`（desired/avoid/priorities）、方向选择、weights、facet 确认 | 缺失时相关轴为 `null` + `null_reason`，**禁止填默认值** |
| **模型提议 + 规则校验** | 未命中单元的 facet 标签、簇命名、elicitation 问题措辞 | 落受控枚举、写 `proposed_by:"model"`、不参与计算、用户可否决 |

### 3.4 八轴定义

| 轴 | 定义 | 输入 | 值域 | null 语义 |
|---|---|---|---|---|
| `evidence_fit` | 候选人证据对该方向**核心信号**的加权覆盖 | EvidenceUnit + 强度因子 | 0–100 | 永不为 null（无证据即 0） |
| `career_distance` | 当前 facet 签名到目标签名的跳数 | facet 图 | 0–5 整数 | 无当前签名时 null |
| `narrative_coherence` | 证据能否讲成一条连贯故事（时间连续性 + 雇主分散度 + 功能一致性） | diversity + temporal | 0–1 | 支撑单元 < 3 时 null |
| `market_capacity` | 时窗内该方向的真实岗位供给量 | MarketRequirementProfile | 计数 | **样本不足即 null** |
| `accessibility` | 市场硬要求中候选人已覆盖的比例 | MRP.required_terms × evidence | 0–100 | MRP 缺失即 null |
| `user_intent` | 用户明示意愿的对齐度 | career_goals | 0–100 | **未提供目标即 null（含所有列表为空）** |
| `career_growth` | 该方向对用户长期目标的推进程度 | career_goals.skills_to_build + 目标角色距离 | 0–100 | 同上 |
| `visa_compatibility` | 该方向的雇主群体对 sponsorship 的支持分布 | MRP.sponsorship_distribution × candidate.work_authorization | enum: `supported` / `mixed` / `hostile` / `unknown` | 无 MRP 即 `unknown` |

**null 传播规则**：任一轴为 null，其 `null_reason` 必须是受控码，
且必须出现在方向的 `review_reasons` 中。

---

## 四、双向生成与对账（确定性伪代码）

### 4.1 自底向上：生成 10–30 个 hypothesis

```
def bottom_up_hypotheses(units, facets, cfg):
    # 1. 按 facet 签名分桶（domain × function × seniority_band）
    buckets = defaultdict(list)
    for u in units:
        f = facets[u.unit_id]
        for d in f.domain or [NULL_DOMAIN]:
            for fn in f.function or [NULL_FUNCTION]:
                buckets[(d, fn, f.seniority_band)].append(u)

    # 2. 逐级放宽：精确签名 → 忽略 seniority → 忽略 domain
    #    每一级都保留，后续去重阶段再收敛
    generalized = {}
    for (d, fn, s), us in buckets.items():
        generalized[(d, fn, s)]        = us
        generalized[(d, fn, ANY)]     += us
        generalized[(ANY_DOM, fn, s)] += us

    # 3. 阈值过滤（确定性）
    out = []
    for sig, us in generalized.items():
        if len(us) < cfg.MIN_UNITS:                 continue   # 默认 3
        if distinct_facts(us) < cfg.MIN_FACTS:      continue   # 默认 3
        if distinct_employers(us) < cfg.MIN_SOURCES and
           max_single_fact_share(us) > cfg.MAX_SHARE:  continue  # 默认 2 / 0.6
        out.append(Hypothesis(signature=sig, units=us,
                              diversity=diversity(us)))

    # 4. 排序后最多保留 MAX_HYP=30；证据不足时允许返回 0–9 个，不设硬下限
    out.sort(key=lambda h: (-weighted_strength(h), h.signature))
    return out[:cfg.MAX_HYP]
```

`weighted_strength(h) = Σ STRENGTH_FACTOR[facet_assignment.relation_strength] × recency_decay(u)`，
`STRENGTH_FACTOR` 复用 `evidence_matcher.EVIDENCE_ORDER` 的序，不新造一套。

### 4.2 自顶向下：市场候选

```
def top_down_candidates(ontology, constraints):
    out = []
    for occ in ontology.occupations:
        for title in occ.titles:
            if title.seniority_rank > constraints.max_seniority_rank: continue
            if occ.role_family in constraints.excluded_families:      continue
            out.append(MarketCandidate(
                occupation_id=occ.occupation_id, title_id=title.title_id,
                queries=generate_queries(title, constraints)))
    return sorted(out, key=lambda c: (c.occupation_id, c.title_id))
```

**与候选人证据完全无关**——这是"市场存在什么"，不是"我适合什么"。

### 4.3 求交汇

```
def reconcile(hypotheses, candidates, mrp_index):
    pairs = []
    for h in hypotheses:
        for c in candidates:
            overlap = facet_overlap(h.signature, ontology.facets_of(c.title_id))
            if overlap.score < cfg.MIN_FACET_OVERLAP:   continue   # 默认 0.5
            mrp = mrp_index.get(c.title_id, constraints.region)
            pairs.append(ReconciledDirection(
                hypothesis=h, candidate=c,
                evidence_fit      = evidence_fit(h, ontology.core_signals(c.title_id)),
                career_distance   = facet_hops(candidate_current_signature, h.signature),
                narrative_coherence = coherence(h),
                market_capacity   = mrp.capacity if mrp and mrp.sufficient else None,
                accessibility     = accessibility(h, mrp) if mrp and mrp.sufficient else None,
                user_intent       = intent(h, c, goals) if goals_non_empty else None,
                career_growth     = growth(h, c, goals) if goals_non_empty else None,
                visa_compatibility= visa(mrp, candidate.work_authorization) if mrp else "unknown"))
    return pairs
```

**没有交汇的两侧都要保留并显式命名**，不得静默丢弃：

- hypothesis 无市场对应 → `no_market_direction`（证据有、市场无）
- 市场候选无 hypothesis 支撑 → `no_candidate_evidence`（市场有、证据无，可作为 Build Toward）

### 4.4 合并近似方向

```
def dedupe(directions):
    # 相似度 = 三项加权，全部确定性
    def sim(a, b):
        return (0.5 * jaccard(a.facet_signature, b.facet_signature)
              + 0.3 * (1.0 if a.occupation_id == b.occupation_id else 0.0)
              + 0.2 * jaccard(set(a.supporting_fact_ids), set(b.supporting_fact_ids)))

    groups = union_find(directions, predicate=lambda a, b: sim(a, b) >= cfg.MERGE_THRESHOLD)  # 0.75
    merged = []
    for g in groups:
        keep = max(g, key=lambda d: (d.evidence_fit, -d.career_distance, d.direction_id))
        keep.merged_from = sorted(d.direction_id for d in g if d is not keep)
        keep.bound_title_ids = sorted(set(chain(*[d.bound_title_ids for d in g])))
        merged.append(keep)
    return merged
```

**关键**：合并发生在 **occupation × facet 签名**层，不在 title 字符串层。
`Biostatistician` 与 `Biostatistics Analyst` 会被合并为一个方向的两个 title，
而不是两个"方向"。

### 4.5 证据充分性闸门

```
def evidence_gate(d, cfg):
    reasons = []
    if len(d.supporting_fact_ids) < cfg.MIN_FACTS:              reasons.append("insufficient_fact_count")
    if d.diversity.distinct_employers < cfg.MIN_EMPLOYERS:      reasons.append("insufficient_evidence_diversity")
    if d.diversity.max_single_fact_share > cfg.MAX_SHARE:       reasons.append("single_fact_dominates")
    if d.evidence.by_strength["direct"] == [] and \
       d.evidence.by_strength["strongly_related"] == []:        reasons.append("no_direct_or_strong_evidence")
    return reasons          # 非空 → readiness 不得高于 "build_toward"
```

### 4.6 Readiness 分档（不是分数）

```
readiness(d):
  if evidence_gate(d):                                   return "unsupported"
  if d.market_capacity is None:                          return "unverified_market"
  if d.evidence_fit >= 75 and d.accessibility >= 70
       and d.career_distance <= 1:                       return "ready_now"
  if d.evidence_fit >= 60 and d.career_distance <= 2:    return "near_term"
  if d.evidence_fit >= 35:                               return "build_toward"
  return "stretch"
```

阈值全部进 `config`，可回归测试，不允许模型调节。

### 4.7 收敛到 3–6 个"彼此不同"的方向

```
def converge(directions, cfg):
    pool = [d for d in directions if d.readiness != "unsupported"]
    pool.sort(key=rank_key)                       # 见 4.9
    selected = []
    for d in pool:
        if len(selected) >= cfg.MAX_SHOWN: break  # 6
        # 多样性约束：新方向必须在 facet 或 occupation 上与已选项有实质差异
        if any(sim(d, s) >= cfg.DIVERSITY_THRESHOLD for s in selected):  # 0.6
            continue
        selected.append(d)
    if len(selected) < cfg.MIN_SHOWN:             # 3
        # 不硬凑：返回实际数量 + 原因码，并触发 elicitation
        return selected, ["fewer_directions_than_target"]
    return selected, []
```

**宁可返回 2 个也不凑 3 个。** 这条必须写成验收测试。

### 4.8 Elicitation：最少问题、最大信息增益

```
def elicitation_questions(directions, cfg):
    # 候选问题池（受控模板，非模型自由生成）
    candidates = []
    for d in directions:
        for sig in d.evidence.unsupported_core_signals:
            candidates.append(Q(kind="evidence_probe", target=sig,
                                affected=[x.direction_id for x in directions
                                          if sig in x.evidence.unsupported_core_signals]))
    if goals is None or goals_all_empty:
        candidates.append(Q(kind="intent_probe", target="career_goals",
                            affected=[d.direction_id for d in directions]))
    for d in directions:
        if d.axes.visa_compatibility == "unknown":
            candidates.append(Q(kind="constraint_probe", target="sponsorship"))

    # 信息增益 = 该问题能改变多少方向的 readiness 档位
    def gain(q):  return len({d for d in q.affected
                              if would_change_readiness(d, q)})
    candidates.sort(key=lambda q: (-gain(q), q.kind, q.target))
    # 去冗余：已被前一问题覆盖的方向不再重复计入
    return greedy_set_cover(candidates, cfg.MAX_QUESTIONS)   # 默认 5
```

模型只可改写问题的**自然语言措辞**；`kind` / `target` / 选取顺序全部由规则决定。

### 4.9 排序：保留原轴 + 解释

**禁止** V1 那种 `overall_score = fit×p1 + value×p2` 单值压平。改为：

```
rank_key(d) = (
   READINESS_ORDER[d.readiness],        # 先按档
   -null_last(d.axes.user_intent),      # 用户明示意愿优先（null 排后）
   -d.evidence_fit,
   -null_last(d.axes.market_capacity),
   d.direction_id                       # 稳定 tie-break
)
```

- 每个方向持久化 `ranking.method`、`ranking.key`、`ranking.explanation`。
- 用户可切换排序主键（`by_evidence` / `by_intent` / `by_market`），
  **切换只改展示顺序，不改任何轴的值**。
- 若产品确实需要一个单值，必须命名为 `display_rank_score` 并与八轴同时存储，
  且在 schema 上标注 `derived: true`、`not_authoritative: true`。

---

## 五、市场验证接口

### 5.1 MarketRequirementProfile (MRP)

```json
{
  "schema_version": "0.1.0",
  "profile_id": "mrp-research-data-analyst-us-ne-2026q3",
  "title_id": "title.research-data-analyst",
  "region": {"country": "US", "metro": "Boston-MA"},
  "seniority_band": "ic_2",
  "window": {"from": "2026-06-01", "to": "2026-08-31", "days": 92},
  "sample": {
    "postings_collected": 63,
    "postings_after_dedupe": 41,
    "distinct_employers": 27,
    "max_employer_share": 0.17,
    "sufficient": true,
    "insufficient_reasons": []
  },
  "required_terms": [
    {"term": "R", "employer_support": 0.71, "posting_support": 0.78, "obligation": "required"},
    {"term": "SQL", "employer_support": 0.55, "posting_support": 0.61, "obligation": "required"}
  ],
  "preferred_terms": [{"term": "REDCap", "employer_support": 0.33, "posting_support": 0.29}],
  "sponsorship_distribution": {"supports": 0.12, "does_not_support": 0.05, "unknown": 0.83},
  "salary": {"currency": "USD", "p25": 68000, "p50": 78000, "p75": 92000, "reported_share": 0.44},
  "provenance": {
    "sources": [{"name": "linkedin", "collected_at": "2026-08-31T12:00:00Z", "postings": 38}],
    "collector_version": "1.0.0",
    "job_card_sha256_list_sha256": "…"
  }
}
```

### 5.2 聚合规则（确定性）

1. **最低样本量**：`postings_after_dedupe >= 25` **且** `distinct_employers >= 10`。
   任一不满足 → `sufficient: false`，`market_capacity`/`accessibility` 返回 `null`。
2. **时间窗口**：默认 90 天滚动。窗口内不足则**不得**自动放宽——放宽必须显式传参
   并记入 `window.widened_from`。
3. **去重**：`canonical_url` 或 employer requisition ID 相同则直接去重；否则仅当
   `(employer_normalized, title_normalized, location_normalized, description_fingerprint)`
   均相同才视作重复，保留最新一条。同一雇主、title、location 的不同 requisition
   不得仅凭三元组被删除。
4. **单雇主污染防护**：term 的 required/preferred 入选**始终**按
   **employer_support**（出现该词的不同雇主占比）计算；`posting_support` 只作诊断展示，
   永不参与 term 入选。`max_employer_share > 0.30` 时另标
   `single_employer_risk: true`，提醒该 profile 的容量统计仍可能受单一雇主影响。
   > 这正是"把某一家公司的技术栈误认为方向核心要求"的防线：
   > Epic/Clarity/Caboodle 在一家医院系统的 20 条 JD 里都出现 ≠ 这是该方向的核心要求。
5. **term 入选门槛**：`employer_support >= 0.30` 才进 `required_terms`；
   `0.15 ≤ employer_support < 0.30` 进 `preferred_terms`；低于 0.15 丢弃。
6. **分层**：region × seniority_band 各自独立成 profile，**不得跨层合并**。
7. **obligation** 直接沿用 `direction_core.MANDATORY_OBLIGATION_FIELDS` 的判定：
   只有出现在 `required_skills` / `required_certifications` 才算 required，
   散文不算（与现有 `warning_keywords` 语义一致）。
8. **原文不落库**：MRP 只存词项与统计量，JD 原文不进任何表（沿用 `ROUTING_DENYLIST` 精神）。

### 5.3 采集接口

```
collect_market_profile(title_id, region, seniority_band, window) -> MRP
  前置：所有 JD 必须先经 ingest → JobCard（结构化），不得直接对原文做词频
  失败模式：采集器不可用 / 样本不足 / 全部来自单一雇主 → 返回 sufficient=false + 原因码
```

采集器只能接入获授权的 API、用户明确提供的数据或条款允许的来源，并保存来源、许可/使用条款
版本与采集时间。查询模板描述的是搜索意图，不构成对 LinkedIn、Indeed 或其他站点进行自动抓取的授权。

---

## 六、迁移方案

### 6.1 `career_direction_core.py` 逐符号处置

| 现有符号 | 处置 | 去向 / 理由 |
|---|---|---|
| `validate_catalog` / `CATALOG_KEYS` / `ARCHETYPE_KEYS` | **降级为 provisional heuristic** | 移到 `career_heuristic_catalog.py`。V2 中 catalog 只用于**冷启动**（ontology 未覆盖时）与 provisional 提案，不参与 verified 排序 |
| `validate_goals` / `GOAL_KEYS` | **保留 + 修复** | 移到 `career_intent.py`。**必须修**：所有列表为空时等同 `goals=None`（见危险问题 #1） |
| `_usable_facts` | **保留、重命名** `select_scoring_facts` | 移到 `evidence_units.py` |
| `_signal_match` | **删除** | 将 `evidence_matcher` 泛化为唯一的 typed relation resolver；Job requirement 与 Career facet 均调用同一解析内核，但传入不同的受控 relation context，避免第三套匹配实现 |
| `_text_matches` | **删除** | 双向子集过松（见危险问题 #2）。由 `title_ontology.resolve` + facet 图替代 |
| `_career_value` | **拆分** | → `axes.user_intent` + `axes.career_growth` 两个独立轴，不再合成一个数 |
| `_criteria` | **保留** | 移到 `direction_projection.py`，逻辑不变（从 CandidateProfile 投影出 criteria） |
| `_score` | **拆分** | → `evidence_fit.py`（轴）+ `direction_projection.py`（生成 profile）。**`overall_score` 删除** |
| `_weights` | **保留、重命名** `suggest_portfolio_weights` | 明确它只是**建议**，用户必须覆盖 |
| `generate_proposal` | **拆分** | 编排逻辑移到 `career_direction_pipeline.py`；S1–S7 各自成模块 |
| `propose_candidate` / `propose_material` | **保留** | 作为 CLI 入口；内部改调 pipeline |
| `materialize_selection` | **保留，不动** | 这是当前最正确的部分：哈希校验 + actor=user + 拒绝 provisional |
| `STRENGTH_FACTORS` | **保留、移位** | 移到 `evidence_matcher.py`，与 `EVIDENCE_ORDER` 同处，避免两处定义强度语义 |

### 6.2 新模块边界

```
skills/jobloom/scripts/
  evidence_units.py            S1  事实 → EvidenceUnit
  facet_taxonomy.py            S2  受控 domain/function/seniority 词表 + 分配
  career_hypothesis.py         S3  自底向上聚类
  title_ontology.py            S4  ontology 加载、规范化、消歧、查询生成
  market_profile.py            S5/S6 MRP 聚合与查询（采集器另放 collectors/）
  direction_axes.py            S6  八轴计算，每个函数一个 rule_id
  direction_projection.py      S6  ReconciledDirection → SearchDirection profile
  career_direction_pipeline.py S7  编排、去重、收敛、elicitation
  career_intent.py             —   career_goals 校验与意图轴
  career_heuristic_catalog.py  —   V1 catalog，降级为冷启动路径
```

`career_direction_core.py` 保留为 **CLI 门面**（薄封装），不再承载算法。

### 6.3 新表

```sql
evidence_units(unit_id PK, snapshot_sha256, fact_id, source_strength, surface_terms_json,
               temporal_json, source_kind, created_at)
evidence_facets(unit_id, facet_kind, facet_value, relation_strength, rule_id,
                assigned_by, confirmed_by_user,
                PRIMARY KEY(unit_id, facet_kind, facet_value))
career_hypotheses(hypothesis_id PK, snapshot_sha256, signature_json, unit_ids_json,
                  diversity_json, created_at)
title_ontology_versions(ontology_version PK, sources_json, content_sha256, loaded_at)
market_requirement_profiles(profile_id PK, title_id, region_json, seniority_band,
                            window_json, sample_json, terms_json, provenance_json,
                            sufficient, collected_at)
direction_proposals(proposal_id PK, snapshot_sha256, ontology_version, mode,
                    content_json, content_sha256, created_at)
direction_proposal_axes(proposal_id, direction_id, axis, value_json, null_reason,
                        rule_id, PRIMARY KEY(proposal_id, direction_id, axis))
```

**不改动**任何现有表。`search_directions` / `search_portfolios` 的审批边界原样保留。

### 6.4 实施顺序

1. 两个可并行的基础模块：
   - `evidence_units.py` + `facet_taxonomy.py`（S1/S2）——无外部依赖，可独立测试
   - `title_ontology.py`（S4）——含规范化与消歧，是 top-down 路径的地基
2. `career_hypothesis.py`（S3）——依赖证据基础模块
3. `direction_axes.py`（S6，先实现不依赖市场的四轴）
4. `career_direction_pipeline.py`（S7）——串起 1–3，此时已可替换 V1 的 verified 路径
5. `market_profile.py` + 采集器（S5）——最后，因为它需要真实 JD 积累

**1–4 完成即可上线**，市场轴以 `null` + `market_profile_unavailable` 呈现，符合 N4。

---

## 七、验收测试（26 条）

### provenance 与证据完整性

1. 每个展示方向的每条 `axes.*` 都能回溯到该轴的权威来源；证据轴回溯到
   `facet_assignment → unit_id → fact_id → snapshot_sha256`，市场/意愿/签证轴分别回溯到
   MRP JobCard hashes、goals hash、授权记录；所需来源断链即失败。
2. `transferable` 证据在任何路径下都不会出现在 `by_strength.direct` 中。
3. 方向对象里不含任何 fact 的 `value` 原文（只有 ID 与 matched_term）。
4. 同一 `fact_id` 支撑多个方向时，`source_strength` 必须一致；各 facet 的
   `relation_strength` 可以不同，但永远不得超过 `source_strength`，且必须携带 rule_id。
5. `snapshot_sha256` 与生成时的活动快照不一致 → 提案标记 `stale`，拒绝 materialize。

### Title 歧义

6. `Research Analyst` + `equity / sell-side` → `fail`，不进池。
7. `Research Analyst II` → 绑定 `title.research-data-analyst`，`level_token="II"` 被记录。
8. `Data Analyst` 无领域词 → `ambiguous`，只能 review，永不 auto-match。
9. `Senior Care Coordinator` → `senior` 不被当作职级（现有守卫必须继续通过）。
10. JD 正文出现 `Clinical Data Analyst` 而标题不是 → 不绑定，只产生 `contextual_title_reference_only`。

### 市场与证据的四个象限

11. 有证据、无市场 → 方向保留并标 `no_market_direction`，`market_capacity: null`。
12. 有市场、无证据 → 方向可出现在 `build_toward`，`evidence_fit` 必须为 0 且 `readiness != ready_now`。
13. 市场样本 < 25 条或雇主 < 10 家 → `sufficient: false`，`market_capacity` 与 `accessibility` 均为 `null`，且 `review_reasons` 含原因码。
14. term 入选始终按 employer_support 计算；单一雇主占比 > 0.30 时另标
    `single_employer_risk: true`。构造 20 条同一医院含 Epic、其余至少 9 家雇主均不含 Epic
    的 JD，`Epic` 不得进入 `required_terms`。

### 用户意图

15. 未提供 `career_goals` → `user_intent` 与 `career_growth` 均为 `null` + `career_goals_not_supplied`。
16. **提供了 `career_goals` 但所有列表为空** → 行为与未提供**完全一致**；作为当前
    V1 已修行为的回归保护，不得重新产生默认 50。
17. 用户在 `avoid_roles` 中明确排除的方向，即使 `evidence_fit` 最高，也不得进入 `ready_now`，且必须标 `user_excluded`。
18. 切换排序主键只改顺序，所有 `axes.*` 的值逐字节不变。

### 收敛与多样性

19. 证据只够 2 个方向时，返回 2 个 + `fewer_directions_than_target`，**不得**用低证据方向凑到 3。
20. `Biostatistician` 与 `Biostatistics Analyst` 合并为一个方向的两个 title，不得作为两个方向展示。
21. 任意两个展示方向的 `sim()` < `DIVERSITY_THRESHOLD`。
22. 单条事实支撑占比 > `MAX_SHARE` 的方向，`readiness` 不得高于 `build_toward`。

### 变更与版本

23. 上传新材料但产生的 EvidenceUnit 集合与既有一致（无 material change）→ **不触发**重排，提案哈希不变。
24. `ontology_version` 变化 → 旧提案标 `ontology_superseded`，`materialize_selection` 拒绝；用户必须重新审阅。
25. `materialize_selection` 收到与 `content_sha256` 不符的提案 → 拒绝（现有行为，回归保护）。
26. provisional 提案在任何 actor 下都不可 materialize（现有行为，回归保护）。

---

## 八、结论

### 8.1 V1 最危险的 5 个设计问题

> **基线说明**：以下针对当前提交 `9c5275e`。V1 的保守化修复已经提交。
> 规格本身不因这些修复而改变，
> 因为它们都是**在 heuristic 层打补丁**，没有触及"无 ontology、无市场、单值压平"的结构问题。

**1. 空 `career_goals` 会凭空产生 `career_value = 50`。** ✅ 已修
`validate_goals` 现在在所有列表为空时返回 `None`，实测 `career_value` 全为 `null`
且 `career_goals_not_supplied` 正确出现。**修复正确。**

**2. `overall_score` 把证据、市场、意图压平成一个数并用于排序与分档。** ❌ 仍存在
`tier` 仍由 `overall` 阈值决定，原始轴仍被一个数覆盖。V1 已加
`score_status: "provisional_heuristic"` 与 `decision_grade: false` ——
这是**诚实的自我标注，不是修复**。本规格第三、四章要求的分轴保存仍待实现。

**3. `_text_matches` 双向子集过松。** ✅ V1 已保守收紧，但**摆到了另一个极端**
现在是 `tokens(needle) == tokens(value)` 完全相等。副作用：目标写
`"Healthcare Data Analyst"` 只能匹配字面相同的 title，写 `"healthcare data"`
则一个都匹配不上。这正是本规格要求用 **Title Ontology + facet 图**替代字符串比较的原因——
松和紧都是同一个错误的两面。

**4. Current Fit 实质是关键词覆盖率。** ⚠️ 已显著缓解，未根治
V1 新增 `FACT_TYPE_STRENGTH_CAPS`（summary / skill / resume_claim 封顶 `mention_only`，
provisional 模式整体封顶 `transferable`），confidence 追加"核心信号必须全部 strong"与
"仅 verified 模式可为 high"。实测 verified 路径从 **6 个 high / 5 个 primary**
降到 **2 个 high / 3 个 primary**，区分度明显改善。
但根因未变：强度仍由**事实类型批量推断**，而非该事实与该能力的真实关系。
真正的解仍是本规格 S1/S2 的 EvidenceUnit + facet，而非更多的封顶表。

**5. `_signal_match` 是仓库里第三套技能匹配实现。** ❌ 仍存在，且已扩大
V1 为它新增了 `_effective_strength` 与 `source_evidence` 归并逻辑。
仓库现在有三处各自决定"什么算证据、算多强"：`evidence_matcher.match_requirement`、
`career_direction_core._signal_match`、以及 `direction_core` 中的信号扫描。
同一规则多处实现是这个项目的复发性缺陷（attestation gate、技能匹配已各出过一次事故）。

> 附带问题：`pypdf` 未声明依赖 —— ✅ 已修，仓库已有 `requirements.txt`
> （`pypdf>=6.0,<7.0`）并在 CI 加了安装步骤。

### 8.2 最先应该实现的 3 个模块

1. **并行建设两组基础模块**：
   - `title_ontology.py`（S4）——是确定性查询、title 消歧与 top-down 去重的地基；
   - `evidence_units.py` + `facet_taxonomy.py`（S1/S2）——把事实变成可聚类、可溯源的
     单元，是修复危险问题 #4 的正解。
   二者没有相互依赖，不应人为串行；后续对账层同时依赖两者。
2. **`career_hypothesis.py` + ontology reconciliation（S3/S4 对账）** —— 生成并交汇
   bottom-up 与 top-down 候选，产出带 provenance 的方向假设。
3. **`direction_axes.py`（S6 的非市场四轴）** —— 立刻消除 `overall_score` 压平，
   把 `evidence_fit` / `career_distance` / `narrative_coherence` / `user_intent`
   分开存储。此模块可在无市场数据时独立上线。

市场采集（S5）**排在最后**：它需要真实 JD 积累，且在样本不足时按 N4 返回 null 即可。

### 8.3 已固化的产品规则

1. **概述词不等于直接经验**：简历概述句里单独出现一个领域词，最多形成
   `mention_only` / contextual 关系；只有同一事实内存在具体行动、对象或产出，且受控规则
   能把它绑定到该能力时，才可形成 `direct`。用户对具体经历的一次确认可持久化复用，
   不得在每个岗位上重复询问。
2. **方向数量宁缺毋滥**：证据只支持 2 个方向时展示 2 个，并给出
   `fewer_directions_than_target`；不得降低证据门槛凑够 3 个。

### 8.4 仍需产品所有者拍板的决定

1. **Career Value 的最低输入要求**：要不要强制用户至少填 `desired_roles` 或
   `skills_to_build` 之一，否则整个意图轴为 null？
2. **市场样本门槛**：25 条 / 10 家雇主是否合适？门槛越高越可信、越容易 null。
3. **Ontology 的来源与许可**：是否采用 O*NET（数据库为 CC BY 4.0，需署名）与
   ESCO（适用 Commission Decision 2011/833/EU 的复用与署名要求）？
   自建部分的维护责任归谁？版本更新频率？
4. **用户明确排除的高证据方向如何呈现**：完全隐藏，还是展示但标 `user_excluded`？
   （本规格默认后者——隐藏等于静默丢弃，与项目原则冲突。）
5. **排序默认主键**：默认按 `readiness` 还是按 `user_intent`？
6. **`career_distance` 的度量单位**：facet 跳数是否足够，还是需要引入"预计准备时间"？
