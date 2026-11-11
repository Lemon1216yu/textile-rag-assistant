# 织知 · 纺织企业内部知识助手

初次体验请先打开 [项目与使用说明（HTML）](使用说明.html)：系统介绍、建库和问答流程、操作步骤及演示数据说明。


**企业工作空间入口：http://127.0.0.1:8766**。PowerShell 执行 `./start-enterprise.ps1`，本地初始账号见 `tmp/enterprise-access.txt`。

新版位于 `enterprise/` + `frontend/`，提供独立登录、部门权限、React 工作台、文档上传/审核/版本替换、异步索引、引用溯源、三层记忆、人工工单及审计。生产部署使用 MySQL、Redis、Milvus、百炼 Qwen / Embedding / Rerank、Nginx 与 Docker；未配置模型的本机模式明确显示原文摘录。

## 先跑通一条真实 PDF 问答

第一次启动前，将 `.env.enterprise.example` 复制为 `.env.enterprise`，添加 `LOCAL_RAG_MODELS=1`，再执行 `.\start-enterprise.ps1`。首次会下载两份公开 PDF 和经过校验的量化模型。打开工作台问“丝绸制丝废水主要有哪些污染物？”，点击回答下的「依据与过程」，可以核对召回数量、精排分数和出版机构的 PDF 原页。未配置生成模型时，回答明确标为原文摘录。

```text
离线：审核前上传 → 解析与分段 → BGE 编码、BM25 倒排索引 → 审核发布
在线：问题改写 → 部门与版本过滤 → 关键词及向量召回 → RRF → MMR
      → 去重 → 结合标题的精排 → 低证据过滤（本地模型）→ 带页码的引用
```

在已初始化的本地开发库执行 `python -m scripts.evaluate_enterprise`，同一题集会分别运行纯 BM25 和完整检索，生成 `tmp/retrieval-evaluation.json`。题集包含部门角色、面料条件、目标正文或物理页及无答案问题；脚本只读，不创建对话，不调用外部模型。它测量的是**检索**，不评估生成回答、查询改写或全流程业务准确率。默认本地证据阈值 `LOCAL_EVIDENCE_MIN=0.08` 是开发起点，云端精排不套用这个分值。上线前请用企业独立标注集重新校验。

- [离线 RAG 部署和资料说明](docs/离线RAG运行说明.md)
- [固定评测问题](examples/retrieval_cases.json) · [本机逐题评测结果](docs/retrieval-evaluation.json)
- [企业版部署、架构与上线手册](docs/企业版部署与验收.md)
- [企业版测试结果与待验收项](docs/企业版验收记录.md)
- [界面配置模型：对话、Embedding、Rerank](docs/模型配置设置.md)
- [真实基础设施联调结果](docs/infrastructure-validation.json)

## 轻量版
旧版接口位于 `app/` 和 `static/`，使用端口 8765；企业工作台位于 `enterprise/` 和 `frontend/`，使用端口 8766。两者的数据库、身份机制与功能范围不同。建议从企业工作台开始。

## 验证
运行 `python -m pytest -q` 和 `npm --prefix frontend run build`。固定题集只反映当前小规模模拟语料的检索表现，不代表生产准确率。真实业务上线还需部门权限验收、业务标注评测、安全检查、容量验证与备份恢复演练。
