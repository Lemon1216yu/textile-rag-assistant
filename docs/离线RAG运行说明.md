# 纺织 PDF 知识库

当前开发工作台使用离线 CPU 模型，不需要将企业文档传到外网。下载步骤可以在独立联网机器执行。将校验过的 `data/models` 和 `data/public-pdfs` 拷入内网，后续解析、索引、查询均能在内网进行。权重和 PDF 不进 Git；下载地址、版本和摘要在 `scripts/download_rag_assets.py` 与 `examples/public_sources.json`。

## 从代码启动

Windows 开发环境：复制 `.env.enterprise.example` 为 `.env.enterprise`（已有配置不要覆盖），添加 `LOCAL_RAG_MODELS=1`，运行 `.\start-enterprise.ps1`。首次安装 Python、前端依赖和下载权重需要网络；已准备环境可以直接分别执行 `python -m enterprise.cli worker` 与 `python -m uvicorn enterprise.main:app --host 127.0.0.1 --port 8766`，无需再次联网。请使用项目虚拟环境。已有资料升级时，先运行 `python -m enterprise.cli rebuild-bm25`。新机首次导入开发参考库用 `python -m scripts.import_public_rag`（完整启动脚本已包含）。生成模型另行在管理员设置里配置。

## 如何得到一条引用

1. 独立 worker 校验文件 SHA-256，按 PDF 物理页提取文字。以章节和段落为边界，长段按最多 400 字符切分，窗口重叠 56 字符，不跨页。页面没有足够文字时整份文档进入失败队列，需 OCR 或处理空页后重试。复杂合并表格、图片和图纸不能保证还原结构，应人工核对原页。
2. 标题、章节与正文送入 BGE-small-zh-v1.5（量化 ONNX，归一化的 512 维 CLS 向量）；保存向量和来源元数据。同时对文本分词，写入 FTS5 磁盘倒排索引。普通上传经过审核、发布后方可被员工检索。演示导入命令仅用于开发数据库中的固定校验清单。
3. 查询补全面料编号和纺织同义词；配置对话模型后也可使用路由器的改写。BM25 与向量分别召回；两路名次按 RRF（k=60）合并。MMR 选择最多 16 个相关且多样的切片，同文档内按规范化正文去重。最后 BGE-reranker-base 对问题和每段原文联合编码，按分数取最多 6 条。每条结果携带版本、页码及排序分数。
4. 当前本机用 SQLite 保存向量，对有权访问的小语料做精确余弦检索。企业部署配置 Milvus 时，worker 同时写入集合，查询改用 Milvus 并限制可访问文档 ID；SQL 再核验发布状态和部门。切换已有库：确认集合维度、编码器与存量向量相同，先运行 `python -m enterprise.cli rebuild-vectors` 再切流。不同模型即使同维度也必须重新编码。多个实例应共用资料卷和倒排索引；这里的单机 SQLite 索引未做跨主机高可用验证。
5. 未配置对话模型时显示检索原文及引用；只有配置生成模型后才生成自然语言回答。外部资料不能直接代替企业内部规程或批次质量判定。

## 本地实测

两份公开资料合计 29 页、101 个切片。询问丝绸制丝废水的污染物，首条命中 HJ 1177—2021 的 PDF 第 6 页（印刷页第 3 页）；询问棉织物掉色和耐洗色牢度的关系，首条命中 ISP1001 第 5 页。两条均经过真实向量化和精排。它们是链路验收样例，不能据此推算业务准确率、容量或生产延迟。

Linux 镜像默认使用 HTTP 模型服务。要使用内网 CPU 模型，构建时传入 `--build-arg LOCAL_MODELS=1`，给 API 和 worker 同时设置 `LOCAL_RAG_MODELS=1` 并将权重目录只读挂载到 `/opt/zhizhi/data/models`。此镜像选项本次未在 Linux 上重新构建验收。
