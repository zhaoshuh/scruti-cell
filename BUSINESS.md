# Scrut-Cell 商业化评估与路线图

## 一、当前状态评估

**综合评分：7/10（技术验证完毕，产品化前夜）**

### 1.1 核心竞争力

| 能力 | 竞品现状 | 护城河强度 |
|------|---------|-----------|
| **LLM 生成数据集专属处理代码** | 无。GEOparse/Scanpy 全用固定规则 | ⭐⭐⭐⭐⭐ |
| **Neo4j 策略积累 + KB 指纹复用** | 类似 idea 存在但无 GEO 落地 | ⭐⭐⭐⭐ |
| **勘查→规划→执行三阶段智能 Agent** | 多数 ETL 工具无推理能力 | ⭐⭐⭐⭐ |
| **MCTS 组合搜索兜底** | 独有 | ⭐⭐⭐ |
| **文献参照增强**（架构就绪，网络未通） | 独有 | ⭐⭐⭐ |

### 1.2 技术债清单

| 严重程度 | 问题 | 修复时间 |
|---------|------|---------|
| 高 | 仅完整测试 1 个数据集（GSE334035），无批量基线 | 2-3 天 |
| 高 | 文献参照（PubMed→LLM 提取→修正规划）因网络未跑通 | 1 天 |
| 中 | Neo4j 启动偶尔锁文件残留需要手动清理 | 0.5 天 |
| 中 | 日志输出过于冗长（neo4j notifications 刷屏 CLI） | 1 小时 |
| 低 | SQLite/Neo4j 双后端部分查询字段不完全一致 | 1 天 |

### 1.3 已完成的技术栈

```
Layer 1: 规则管道 (parse)
  ✓ LangGraph 10 节点 DAG
  ✓ 6 种矩阵格式读取（.h5/.h5ad/.mtx/.csv/.rds/.loom）
  ✓ 77 条字段同义映射
  ✓ 4 维置信度验证
  ✓ Reflexion 三层自修正（KG→LLM→启发式）
  ✓ MCTS 12 策略模板 UCB1 搜索

Layer 2: 智能管道 (inspect → plan → execute)
  ✓ Decompressor 递归解压 + 族谱树
  ✓ Inspector 6 类文件指纹（含 barcode 分析）
  ✓ Reporter GseReport JSON
  ✓ StrategyMatcher 指纹提取 + KB 相似度匹配
  ✓ PlanGenerator LLM 规划生成（含 processing_code 代码生成）
  ✓ PlanValidator 6 条结构检查 + 3 轮 LLM 修正
  ✓ LiteratureEnricher PubMed→LLM 提取→规划修正
  ✓ PlanExecutor 规划执行 + 安全沙箱代码执行
  ✓ ExecutionMonitor 失败反省

Layer 3: KB 持久化
  ✓ SQLite 双后端
  ✓ Neo4j 双后端（MERGE upsert）
  ✓ success_count/fail_count 累计 + 排序复用
  ✓ 企业 license 自动检测切换

CLI: 6 个命令（parse/config/inspect/plan/execute/version）
```

---

## 二、商业化路线图

### 2.1 产品定位

**一句话：让任何生物学家都能一键把 GEO 数据集转为分析就绪的 AnnData。**

| 维度 | 定位 |
|------|------|
| 目标用户 | 计算生物学实验室、药企 R&D、单细胞分析平台 |
| 核心痛点 | GEO 数据标准化需数小时到数天人工 curation，且质量不稳定 |
| 价值主张 | 分钟级自动完成勘查+规划+执行，越用越准 |
| 定价模式 | 开源版免费（规则管道 + SQLite），企业版按数据集/年订阅（LLM + Neo4j + MCTS） |

### 2.2 阶段划分

```
┌────────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐
│  M0: 验证  │ → │  M1: 打磨  │ → │  M2: 包装  │ → │  M3: 推广  │
│  1-2 周    │    │  2-4 周    │    │  2-4 周    │    │  持续       │
└────────────┘    └────────────┘    └────────────┘    └────────────┘
```

---

## 三、M0: 验证（当前→2 周）

### 3.1 批量测试（P0）⭐⭐⭐⭐⭐

**目标：在 20 个不同结构的数据集上建立通过率和准确率基线。**

数据集选择应覆盖：

| 类别 | 数量 | 示例特征 |
|------|------|---------|
| 单样本单矩阵 | 5 | 标准 10X h5/h5ad 格式 |
| 多样本多矩阵 | 5 | 每个 GSM 独立矩阵文件 |
| 复合字段元信息 | 3 | 含 `Male_Control_72yr` 类字段 |
| 嵌套压缩包 | 2 | `.tar.gz` 内含 `.h5` 文件 |
| 非标准矩阵格式 | 3 | CSV/RDS/Loom 格式 |
| 多物种 | 2 | 人类 + 小鼠 |

**测试策略：**

```bash
# 自动化批量脚本
for gse in GSE001 GSE002 ... GSE020; do
    scruticell inspect $gse -o ./batch/$gse
    scruticell plan $gse --report ./batch/${gse}_report.json -o ./batch/$gse
    scruticell execute $gse \
        --plan-file ./batch/${gse}_plan.json \
        --report-file ./batch/${gse}_report.json \
        -o ./batch/$gse
done
```

**产出一份报告：**
- 总通过率（execute 成功的比例）
- 平均置信度
- 平均处理时间
- KB 命中率（复用策略的比例）
- 与手工 curation 的一致性评分

### 3.2 修复文献参照（P0）

```bash
# 测试 PubMed 连通性
python -c "from scruticell.literature.pubmed import search_pubmed; print(search_pubmed('ceria nanoparticles APOE4 Alzheimer scRNA-seq'))"

# 如果仍不通，检查：
# 1. 防火墙 / 代理设置（NCBI E-utilities 端口 443）
# 2. 是否被限流（设置 NCBI_API_KEY）
# 3. 使用 HTTPS 而非 FTP（已修复）
```

### 3.3 清理日志噪音

Neo4j notification 刷屏问题，在 `KnowledgeGraph._run()` 方法中抑制不必要的 notification 日志：

```python
# neo4j 5.x 可以在连接时设置通知抑制级别
def connect(self):
    self._driver = GraphDatabase.driver(
        self.uri, auth=(self.user, self.password),
        notifications_min_severity="OFF",  # or "INFORMATION"
    )
```

---

## 四、M1: 打磨（2-4 周）

### 4.1 Neo4j 容器化（P1）

消除用户侧的 Java 安装 + 下载 + 解压 + 改密码 + license 五步操作：

```yaml
# docker-compose.yml
version: '3.8'
services:
  neo4j:
    image: neo4j:5.26-community
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/scruticell
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs

volumes:
  neo4j_data:
  neo4j_logs:
```

用户只需：`docker compose up -d`

### 4.2 Web 界面 MVP（P1）

用 **Gradio** 或 **Streamlit** 做一个最小可用的 Web 界面：

```
┌─────────────────────────────────────────┐
│  Scrut-Cell                              │
│                                         │
│  GSE Accession: [GSE334035    ] [提交]   │
│                                         │
│  模式: ○ 规则管道  ● 智能管道           │
│        ○ 快速跳过文献                    │
│                                         │
│  ┌─ 输出 ────────────────────────────┐  │
│  │                                    │  │
│  │  进度: inspect ✓ plan ✓ execute... │  │
│  │  矩阵: 3 files, 54,198 cells       │  │
│  │  基因: 38,584 genes                │  │
│  │  批次: 3 batches                   │  │
│  │  置信度: 100%                      │  │
│  │                                    │  │
│  │  [下载 .h5ad]  [下载 report.json]  │  │
│  └────────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

建议用 Streamlit（生态更成熟，社区更大）：

```python
# app.py - ~100 行
import streamlit as st
from scruticell.intelligence import Reporter, PlanOrchestrator, PlanExecutor

st.title("Scrut-Cell")
gse_id = st.text_input("GSE Accession", "GSE334035")

if st.button("Run"):
    with st.spinner("Inspecting..."):
        # ... run pipeline ...
        pass
    st.success(f"Done: 54198 cells, 38584 genes, 3 batches")
    st.download_button("Download .h5ad", data, "output.h5ad")
```

### 4.3 License 发放线上化

简单做法：一个 `scruticell license generate <user_id>` 命令生成 key，用户自己设环境变量。后续可以做一个注册页面发邮件。

```bash
scruticell license generate my-lab-name
# → my-lab-name-a1b2c3d4-ENT-SCRUTICELL
```

### 4.4 错误恢复能力增强

- `--reflect` 模式应该在执行失败时**自动开启**，不需要用户主动传参
- Reflexion 的 `_heuristic_strategy()` 可以增加一些常见错误的处理模板
- `processing_code` 执行失败时，应该返回更友好的错误信息给 LLM 反省

---

## 五、M2: 包装（2-4 周）

### 5.1 Benchmark 报告

选 20 个手工 curated 的数据集，做对比：

| 指标 | Scrut-Cell | 手工 curation | 提升 |
|------|-----------|--------------|------|
| 平均处理时间 | 5 分钟 | 2 小时 | 24x |
| 元信息字段覆盖率 | 85% | 75% | +13% |
| 批次标注准确率 | 95% | 90% | +5% |
| 基因 ID 统一率 | 99% | 95% | +4% |

> 这是向客户/投资者展示时最有说服力的一页。

### 5.2 文档

- **用户手册**：覆盖 6 个 CLI 命令 + Web 界面 + 常见问题
- **开发文档**：Intelligence 层 API + 添加新文件格式 + 扩展 Inspector
- **案例研究**：选 3 个不同复杂度的 GSE，展示从输入到输出全过程

### 5.3 视频 Demo

5 分钟录屏：
1. 粘贴 GSE 编号 → 点击运行
2. 展示 inspect report 的结构化信息
3. 展示 LLM 生成的 processing_code（复合字段分割）
4. 展示最终 .h5ad 的 obs/var 结构
5. 展示 KB 匹配复用（第二次跑同结构数据集是秒级的）

### 5.4 开源社区准备

- pip install 一键安装
- GitHub Actions CI/CD
- Docker 镜像
- PyPI 发布

---

## 六、M3: 推广（持续）

### 6.1 渠道

| 渠道 | 方式 | 预期效果 |
|------|------|---------|
| bioRxiv / 论文 | 提交 Scrut-Cell 方法论文 | 学术引用和信用背书 |
| 单细胞会议 | 海报/闪电演讲（Single Cell Genomics, RECOMB） | 精准触达目标用户 |
| Twitter/X | 发布 Demo 视频 + 对比图 | 病毒传播 |
| GitHub | 开源版 + 文档 + 示例 | 自然增长 |
| 生物信息社群 | Biostars, seqanswers 回答问题 | SEO 长尾 |

### 6.2 盈利路径

| 路径 | 模式 | 时间线 |
|------|------|--------|
| **开源版** | 免费，MIT 协议 | 现在 |
| **企业版** | 按数据集/年订阅（$99-$999/月） | M2 完成后 |
| **私有部署** | 制药公司内网部署 + 定制 | 有客户后 |
| **SaaS** | 按查询量计费 | M3 后 |

### 6.3 竞品对标

| 产品 | 规则管道 | LLM 规划 | KB 复用 | 定制代码生成 |
|------|---------|---------|--------|------------|
| GEOparse | ✓ | ✗ | ✗ | ✗ |
| Scanpy | ✓ | ✗ | ✗ | ✗ |
| GEO2R | ✓ | ✗ | ✗ | ✗ |
| GEOexplorer | ✓ | ✗ | ✗ | ✗ |
| **Scrut-Cell** | ✓ | ✓ | ✓ | ✓ |

---

## 七、立即行动清单

按优先级排序：

- [ ] **1. 批量测试 20 个数据集**（建立基线数据）
- [ ] **2. 修复文献参照网络问题**（完善 plan 自检闭环）
- [ ] **3. 清理 Neo4j 日志噪音**（改善可读性）
- [ ] **4. Neo4j Docker 部署方案**（降低安装门槛）
- [ ] **5. Streamlit Web 界面**（降低使用门槛）
- [ ] **6. `scruticell license generate` 命令**（发放工具）
- [ ] **7. Benchmark 报告**（商业洽谈资料）
- [ ] **8. 5 分钟视频 Demo**（传播材料）
- [ ] **9. GitHub README 精修 + PyPI 发布**（开源基础建设）

---

> 评估日期：2026-06-25 | 当前版本：v0.1.0 | 下一步：M0 批量测试
