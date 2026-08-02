# Scrut-Cell Preprocessing Agent — deepagents Architecture

> 基于 `deepagents` (LangGraph) 多 Agent 架构的完整 Mermaid 流程图

```mermaid
flowchart TB
    subgraph CLI["CLI Entry"]
        A["scruticell preprocess GSE_ID"]
    end

    subgraph AgentCreation["Agent Creation (agent.py)"]
        B["create_preprocessing_agent()"]
        B --> B1["ChatOpenAI(model=deepseek-chat, temp=0.0)"]
        B --> B2["create_deep_agent()"]
        B --> B3["Sub-Agents: file-prep + classifier (+ evidence)"]
        B --> B4["Tools: organize_output"]
        B --> B5["Backend: StateBackend"]
        B --> B6["System Prompt: MAIN_AGENT_SYSTEM_PROMPT"]
    end

    subgraph MainAgent["Main Orchestrator Agent"]
        C["task: Preprocess GEO dataset GSE_ID"]
        C --> C1{{"Review FileContextReport"}}
        C1 --> C2{{"Consistent signals?"}}
        C2 -->|"Yes"| C3["Delegate to classifier"]
        C2 -->|"No (conflict)"| C4["Delegate to evidence"]
        C4 --> C5["Delegate to classifier"]
        C3 --> C6["Delegate to classifier"]
        C5 --> C6
        C6 --> C7["Call organize_output"]
        C7 --> C8["Return result"]
    end

    subgraph FilePrep["File-Prep Sub-Agent (file_prep.py)"]
        D["System Prompt: FILE_PREP_SYSTEM_PROMPT"]
        D --> D1["Tool: download_gse(gse_id)"]
        D1 --> D2["Tool: decompress_files(download_dir)"]
        D2 --> D3["Tool: inspect_files(download_dir, soft_file)"]
    end

    subgraph InspectFiles["inspect_files() Internal (inspect_tool.py)"]
        E["Scan download_dir rglob"]
        E --> E1["Parse SOFT file"]
        E1 --> E1a["gse_metadata → gse_info"]
        E1 --> E1b["gsm_entries → raw_gsm"]
        E1 --> E1c["extract_gsm_metadata() → characteristics map"]

        E --> E2["For each file: Inspector.inspect_file()"]
        E2 --> E2a["FileSummary (n_rows/n_cols/columns/barcodes...)"]
        E2 --> E2b{"L5: Is noise file?\n(.pdf/.docx/.png/.tar.gz/...)"}
        E2b -->|"Yes"| E2c["Skip from signals, keep raw"]
        E2b -->|"No"| E2d["Build FileSignals"]

        E2d --> E2d1["extract_metadata_signals()\n(molecule, strategy, title, keywords)"]
        E2d --> E2d2["extract_filename_signals()\n(companion files, 10x format, keywords)"]
        E2d --> E2d3["extract_content_signals()\n(feature_id_type, barcode_format, columns)"]
        E2d1 --> E2e["FileSignals (ancestry, file_type, metadata, filename, content)"]

        E2e --> E3["L1: build_field_value_maps()"]
        E3 --> E3a["FieldValueMap per field (unique values → GSM IDs)"]

        E2e --> E4["L2+L3: group_file_signals()"]
        E4 --> E4a["Group by signal_fingerprint()"]
        E4a --> E4b["per_file_gsm_id (ancestry → GSM ID extraction)"]
        E4b --> E4c["ancestry_pattern ({GSM}/barcodes.tsv.gz template)"]

        E4c --> E5["For each group: build_content_preview()"]
        E5 --> E5a["FileContentPreview\n(columns, feature_samples, barcodes, raw_lines)"]

        E4c --> E6["For each group: build_file_meta()"]
        E6 --> E6a["FileMeta\n(filename tokens, column headers, barcode prefixes)"]

        E6a --> E7["For each group: compute_attribution()"]
        E7 --> E7a["L0: ancestry path GSM extraction"]
        E7 --> E7b["L1: SOFT supplementary_file_N match"]
        E7 --> E7c["Signals: filename/column/prefix token extraction"]
        E7a --> E7d["AttributionResult (candidates + signals + source)"]
    end

    subgraph MetadataExtraction["Metadata Extraction (inspect_tool.py inline)"]
        F["build_extraction_context(raw_gsm, gse_info)"]
        F --> F1["_serialize_gsm_entries()\n(all SOFT values → plain strings)"]
        F1 --> F2a["gse_global\n(title, organism, experiment_type,\n summary, overall_design) — raw"]
        F1 --> F2b["gse_protocols\n(treatment_protocol_ch1 → [{value, gsm_ids}]\n extraction_protocol_ch1 → [{value, gsm_ids}])"]
        F1 --> F2c["per_gsm_text_fields\n(title, description, source_name,\n characteristics_ch1 → [{value, gsm_ids}])"]

        F2a --> F3["LLM: ChatOpenAI.invoke()"]
        F2b --> F3
        F2c --> F3
        F3 --> F3a["Prompt: METADATA_EXTRACTION_PROMPT"]
        F3a --> G["Output A: experiment_flowchart\n(Mermaid TD — full experimental pipeline)"]
        F3a --> H["Output B: extract_metadata() code\n(Python function — per-GSM extraction)"]

        H --> H1["Sandbox Execution"]
        H1 --> H1a["extraction_sandbox.run_extraction_metadata()"]
        H1a --> H1b["For each GSM: exec(code, sandbox, locals)\n→ sandbox has re + get_char() + builtins"]
        H1b --> H1c["extract_metadata(serialized_gsm[GSM_ID])\n→ {organism, tissue, age, sex, genotype, ...}"]
        H1c --> H1d["Merge extracted fields into field_value_maps\n(union with existing characteristics-based maps)"]
    end

    subgraph Evidence["Evidence Sub-Agent (evidence.py) — Conditional"]
        I["Trigger: metadata signals conflict"]
        I --> I1["Tool: search_gse_papers(gse_id, organism, keywords)"]
        I1 --> I2["Read PubMed abstracts"]
        I2 --> I3{"Resolved?"}
        I3 -->|"Yes"| I4["Return EvidenceReport"]
        I3 -->|"No"| I5["Tool: read_paper_methods(pmid)"]
        I5 --> I6["Download PDF → PyMuPDF → \nchunking → sentence-transformers → \nTop-3 cosine retrieval → LLM RAG"]
        I6 --> I7["Return EvidenceReport\n(source, pmids, findings, excerpts)"]
    end

    subgraph Classifier["Classifier Sub-Agent (classifier.py)"]
        J["System Prompt: build_classifier_prompt()"]
        J --> J1["Load skills/modality_knowledge/SKILL.md\n→ {MODALITY_KNOWLEDGE} placeholder"]
        J1 --> J2["Zero tools — pure LLM reasoning"]

        J2 --> J3["Input: FileContextReport"]
        J3 --> J3a["experiment_flowchart (Mermaid — modality confirmation)"]
        J3 --> J3b["enriched field_value_maps (sample grouping)"]
        J3 --> J3c["file_signal_groups + per_file_gsm_id (file ownership)"]
        J3 --> J3d["content_preview (modality cross-check)"]

        J3a --> J4["Classification Decisions"]
        J4 --> J4a["1. File→Sample Assignment (1:1)\nPriority: per_file_gsm_id\n> attribution.candidates > token signals\n> inferred composite key"]
        J4a --> J4b["2. Shared File Detection\n(same file used by multiple GSM)"]
        J4b --> J4c["3. Demux File Detection\n(column/row/pre-per_sample_key splitting)"]
        J4c --> J4d["4. Sample Grouping\n(field_value_maps value differences)"]
        J4d --> J4e["5. Modality Detection\n(flowchart + SKILL.md + content_preview)"]

        J4e --> J5["Output: Compact JSON"]
        J5 --> J5a["sample_groups[]\n({gsm_ids, sample_key, key_source,\n file_prefix, modalities})"]
        J5 --> J5b["shared_file_groups[]\n({ancestry, role, ref_by_gsm})"]
        J5 --> J5c["unclassified[]"]
    end

    subgraph Organize["organize_output Tool (organize_tool.py)"]
        K["Input: classification_json + download_dir"]
        K --> K1["Build dir_name → gsm_ids mapping"]
        K1 --> K2a{"key_source == 'inferred_composite'?"}
        K2a -->|"Yes"| K2b["dir_name = sample_key\n(all GSM share one dir)"]
        K2a -->|"No"| K2c["dir_name = gsm_id\n(one dir per GSM)"]

        K2b --> K3["For each resolved group"]
        K2c --> K3
        K3 --> K3a["Create: preprocessed/{dir_name}/"]
        K3a --> K3b["For each modality:"]
        K3b --> K3c{"LLM provided files?"}
        K3c -->|"Yes"| K3d["Use LLM's files list"]
        K3c -->|"No"| K3e["_scan_gsm_files()"]
        K3e --> K3e1["Walk download_dir (cached rglob)"]
        K3e1 --> K3e2["Match: GSM ID in ancestry\nOR file_prefix in ancestry"]
        K3e2 --> K3e3["_is_noise() filter (modality-aware:\nimages OK for spatial)"]
        K3e3 --> K3e4["_assign_role()\n(filename pattern → matrix/barcodes/features/...)"] 

        K3d --> K4["For each file entry:"]
        K3e4 --> K4
        K4 --> K4a{"Demux file?"}
        K4a -->|"Yes"| K4b["_handle_demux() — TODO stub"]
        K4a -->|"No"| K4c["_link_file_entry()"]
        K4c --> K4c1["Resolve source path"]
        K4c1 --> K4c2["os.link() hard link (fallback: shutil.copy2)"]
        K4c2 --> K4d["Write files.json + metadata.json"]

        K3 --> K5["Handle shared_file_groups"]
        K5 --> K5a["Build gsm_id → dir_name map"]
        K5a --> K5b["For each ref_by_gsm:\nhard link shared file into dir_name/modality/"]

        K5b --> K6["Write manifest.json"]
        K4d --> K6
        K6 --> K6a["{sample_count, group_count,\n sample_groups (logical),\n total_files_linked}"]
    end

    subgraph Cleanup["Post-Processing (cli.py)"]
        L{"--keep-data flag?"}
        L -->|"Default (no)"| L1["_cleanup_gse_output()"]
        L1 --> L1a["shutil.rmtree(download/)"]
        L1 --> L1b["unlink *.h5ad"]
        L1a --> L1c["Keep preprocessed/ (hard links survive)"]
        L1b --> L1c
        L -->|"Yes"| L2["Keep everything"]
    end

    %% Main flow connections
    A --> AgentCreation
    AgentCreation --> MainAgent
    MainAgent --> FilePrep
    FilePrep --> InspectFiles
    InspectFiles --> MetadataExtraction

    MetadataExtraction -->|"merge"| Report["FileContextReport\n+ experiment_flowchart\n+ enriched field_value_maps\n+ file_signal_groups\n+ per_file_gsm_id\n+ content_preview"]["FileContextReport Build"]

    Report --> MainAgent
    MainAgent --> Evidence
    Evidence --> Classifier
    Report --> Classifier
    Classifier --> Organize
    Organize --> Cleanup

    %% Styles
    style CLI fill:#e1f5fe,stroke:#0277bd
    style AgentCreation fill:#f3e5f5,stroke:#7b1fa2
    style MainAgent fill:#fff9c4,stroke:#f9a825
    style FilePrep fill:#e8f5e9,stroke:#2e7d32
    style InspectFiles fill:#fff3e0,stroke:#e65100
    style MetadataExtraction fill:#fce4ec,stroke:#c62828
    style Evidence fill:#e0f7fa,stroke:#00695c
    style Classifier fill:#ede7f6,stroke:#4527a0
    style Organize fill:#f1f8e9,stroke:#558b2f
    style Cleanup fill:#fafafa,stroke:#616161
    style Report fill:#c8e6c9,stroke:#1b5e20
```

## 关键设计要点

### 信号收集 vs 判决分离
| 层 | 位置 | 职责 |
|----|------|------|
| **Python 信号收集** | `context/` (attribution, metadata, filenames, contents, signal_grouper, extraction_context) | L0 ancestry 提取、L1 SOFT ref 匹配、文件名/列头/内容 token 提取、信号指纹分组、紧凑上下文构建 |
| **LLM 判决** | classifier sub-agent, metadata_extractor prompt | 文件→样本归属、共享/demux 文件检测、复合 key 推断、样本分组、模态判定、流程图生成、extract_metadata() 代码生成 |

### 数据流契约
| 阶段 | 输入 | 输出 |
|------|------|------|
| SOFT → metadata_extractor | 序列化后的三层紧凑 JSON | flowchart + extract_metadata() code |
| sandbox → field_value_maps | extract_metadata(code, gsm_dict) | per-GSM 结构化 JSON → 合并到 field_value_maps |
| FileContextReport → classifier | experiment_flowchart + enriched fields + file signals | sample_groups JSON |
| classifier → organize_output | classification JSON + download_dir | preprocessed/ directory tree |

### 子 Agent 架构
| Agent | 工具 | 模型 | 触发 |
|-------|------|------|------|
| file-prep | download_gse, decompress_files, inspect_files | deepseek-chat | 始终 |
| metadata-extractor | 无 (纯 LLM) | deepseek-chat | 始终 (inspect_files 内联) |
| evidence | search_gse_papers, read_paper_methods | deepseek-chat | 条件 (metadata 信号冲突) |
| classifier | 无 (纯 LLM) | deepseek-chat | 始终 |
