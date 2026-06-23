# langgraph_cs —— LangGraph 客服 Agent 骨架（阶段 2）

用 **LangGraph** 重写的最小可跑客服 Agent，作为秋招简历项目的起点。
参照本仓库 EchoMind（手写 anthropic SDK）的架构，但改用 LangGraph 的原生抽象，重点是**讲得清每一行**。

## 这张图在做什么

```
START → intent(意图识别) → agent(按意图应答) → END
                                  ↑
                  编译时挂 MemorySaver(checkpointer)，靠 thread_id 记住多轮上下文
```

## 怎么跑

```bash
# 1. 装依赖（可复用仓库根的 .venv）
pip install -r langgraph_cs/requirements.txt

# 2. 配 key
cp langgraph_cs/.env.example langgraph_cs/.env
#   编辑 .env，填入 DEEPSEEK_API_KEY

# 3. 从仓库根目录运行（注意是 -m 模块方式，保证 import 路径正确）
python -m langgraph_cs.main
```

试试多轮记忆：先说「我叫小明」，再问「我叫什么名字」，看它是否记得 —— 记得就说明 checkpointer 生效了。

## 文件导览（对照 EchoMind 学习）

| 文件 | 作用 | 对照 EchoMind |
|---|---|---|
| `state.py` | 图的状态 `CSState`（TypedDict + `add_messages`） | 一堆 Request/Result dataclass |
| `nodes/intent.py` | 意图识别节点（教学版单路） | `core/intent_recognizer.py`（三路融合） |
| `nodes/agent.py` | 按意图选 system prompt 应答 | `agents/agent_orchestrator.py` 各 Agent |
| `graph.py` | 组装 StateGraph + 挂 checkpointer | Orchestrator 的 run 编排 + redis 会话 |
| `main.py` | CLI 入口 | `api/main.py` 的 `_cli()` |

## 下一步（路线图）

- **阶段 3**：`add_edge` 换成 `add_conditional_edges`，按意图路由到多个真正的 Agent 节点 + 失败降级
- **阶段 1/RAG**：在 agent 前加 `rag_node`，用 LangChain Retriever 检索知识库，做朴素 vs rerank 的指标对比
- **阶段 4**：`interrupt` 实现 human-in-the-loop（低置信度转人工）；接 LangSmith 自动评测
- **持久化**：`MemorySaver` → `SqliteSaver`/`RedisSaver`
