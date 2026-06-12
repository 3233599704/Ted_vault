# AI开发基础：Langgraph框架从入门到实战开发智能体\-附带完整可运行代码

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=NDM3ZGJhNGZiM2VhN2VhYWFlY2RiMjMyN2M2NDg4NTBfOGMwODU0ZTE1OWI0MDVjYjkzMzg5YmE0N2JjMWJmYzhfSUQ6NzU4MDE5OTU3MzQ3ODUyNTkwOF8xNzgxMjMxODc0OjE3ODEzMTgyNzRfVjM)

# LangGraph 实战教程：从小白到高手

> 一份让你真正学会用 LangGraph 构建 AI Agent 的实战指南
> 
> 

---

## 写在前面

如果你想用大语言模型（LLM）做点有意思的事情，比如做一个能自己查资料、能记住上下文、能调用各种工具的智能助手，那 LangGraph 绝对是你需要了解的东西。

这份教程不会堆砌一堆概念把你绕晕，而是用**真实可运行的代码**带你一步步搞懂这玩意儿到底怎么用。

直接可以运行的代码已经整理好了，需要的话找老师拿

---

## 第一章 LangGraph 是什么

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=YjE1NTFiN2VlOTI3OGU4ZmFiMDY1YjZjMWNiNzE4OGRfYTEwN2M0OGUwODQ1OTA2ZTVlNzNmMGIxNWJlNzYyZWZfSUQ6NzU4MDE5OTU3MzQ0OTE4MjE2MV8xNzgxMjMxODc1OjE3ODEzMTgyNzVfVjM)

### 一句话解释

**LangGraph 就是用来编排 AI 工作流的框架**，让你可以把复杂的 AI 任务拆成一个个小步骤，然后像搭积木一样把它们连起来。

### 为什么需要它？

假设你要做一个客服机器人：

1. 先要判断用户问的是技术问题还是销售问题

2. 技术问题交给技术 Agent 处理

3. 销售问题交给销售 Agent 处理

4. 如果答案质量不够好，还得循环改进

这种**有分支、有循环、有状态**的复杂流程，用普通代码写起来会很乱。LangGraph 就是专门解决这个问题的。

### LangGraph vs LangChain

很多人搞不清这俩的关系：

简单说：**LangChain 是做零件的，LangGraph 是用这些零件搭建复杂系统的**。

---

## 第二章 环境准备

### 安装依赖

```Bash
# 核心依赖
pip install langgraph langchain-core langchain-community

# 如果要用通义千问
pip install dashscope

# 如果要用 OpenAI
pip install langchain-openai
```

### 设置 API Key

```Python
import os

# 通义千问
os.environ["DASHSCOPE_API_KEY"] = "你的key"

# 或者 OpenAI
os.environ["OPENAI_API_KEY"] = "你的key"
```

---

## 第三章 核心概念

LangGraph 就三个核心概念，搞懂了这三个，其他都是组合拳。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MTYxZmNjNWMyZmNkZWE0ZmMyMTE4MjUxNDIzNDE2MDRfNDFjNTc1MWFkMmFmMDJiODNjODk0MGZlMDRlNDQ1MGNfSUQ6NzU4MDE5OTU3MDUzMDgzMTMwOV8xNzgxMjMxODc0OjE3ODEzMTgyNzRfVjM)

### State（状态）

State 就是一个**在所有节点间共享的数据结构**。你可以把它想象成一个"快递包裹"，每经过一个节点，就往里面加点东西或者修改点东西。

```Python
from typing_extensions import TypedDict

class MyState(TypedDict):
    count: int           # 计数器
    history: list[str]   # 历史记录
```

### Node（节点）

Node 就是**干活的**。每个节点是一个 Python 函数，接收当前状态，返回需要更新的字段。

```Python
def my_node(state: MyState):
    # 读取当前状态
    current_count = state["count"]
    
    # 返回要更新的字段（不用返回所有字段！）
    return {
        "count": current_count + 1,
        "history": state["history"] + ["执行了一次"]
    }
```

### Edge（边）

Edge 定义**节点之间怎么连接**，决定执行顺序。

```Python
# 普通边：A 执行完必定执行 B
workflow.add_edge("A", "B")

# 条件边：根据条件决定下一步
workflow.add_conditional_edges(
    "A",
    决策函数,
    {"条件1": "B", "条件2": "C"}
)
```

---

## 第四章 第一个程序

让我们写一个最简单的计数器程序，理解状态是怎么流转的。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MTcwODZjNGJlNTY4ZDM4YmZlMDNkN2ZkMzczNDJhY2JfYjhlMGIzMzY5YjQwMzYxOWQ5MjBjNmU1NGZmNmY4NjFfSUQ6NzU4MDE5OTU3MzQ4MjY3MTA3MF8xNzgxMjMxODc0OjE3ODEzMTgyNzRfVjM)

### 完整代码

```Python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

# 1. 定义状态结构
class CounterState(TypedDict):
    count: int
    history: list[str]

# 2. 定义节点函数
def increment_node(state: CounterState):
    """计数器 +1"""
    new_count = state["count"] + 1
    message = f"count: {state['count']} → {new_count}"
    
    return {
        "count": new_count,
        "history": state["history"] + [message]
    }

def double_node(state: CounterState):
    """计数器翻倍"""
    new_count = state["count"] * 2
    message = f"count: {state['count']} → {new_count}"
    
    return {
        "count": new_count,
        "history": state["history"] + [message]
    }

def report_node(state: CounterState):
    """报告结果"""
    return {"history": state["history"] + [f"最终结果: {state['count']}"]}

# 3. 创建图
workflow = StateGraph(CounterState)

# 4. 添加节点
workflow.add_node("increment", increment_node)
workflow.add_node("double", double_node)
workflow.add_node("report", report_node)

# 5. 连接节点
workflow.add_edge(START, "increment")
workflow.add_edge("increment", "double")
workflow.add_edge("double", "report")
workflow.add_edge("report", END)

# 6. 编译
app = workflow.compile()

# 7. 运行
result = app.invoke({
    "count": 5,
    "history": ["开始执行"]
})

print("执行历史:")
for step in result["history"]:
    print(f"  - {step}")
print(f"\n最终计数: {result['count']}")
```

### 运行结果

```Plain Text
执行历史:
  - 开始执行
  - count: 5 → 6
  - count: 6 → 12
  - 最终结果: 12

最终计数: 12
```

### 关键点

1. **只返回需要更新的字段**：节点不用返回完整的 state，只返回变化的部分

2. **状态自动传递**：每个节点执行完，更新后的状态自动传给下一个节点

3. **必须编译**：调用 `compile()` 后才能运行

---

## 第五章 Reducer 机制

这是很多人一开始搞不明白的点：**为什么有时候状态会被覆盖，有时候会累加？**

答案就是 **Reducer**。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MzY4Zjc2NjM0Mzc0YjkxNzA3MTM2ZDM2ZTY2MTljODFfZTdjNmNlZDQ3Yjk5YzcwMTg0MmY0ZTBlZWY4ZTc0NjhfSUQ6NzU4MDE5OTU2OTMyMzQ0NTIwMl8xNzgxMjMxODc0OjE3ODEzMTgyNzRfVjM)

### 问题：状态被覆盖

默认情况下，每个节点返回的值会**覆盖**之前的值：

```Python
class State(TypedDict):
    score: int  # 普通定义

# Node1 返回 {"score": 10}  → state["score"] = 10
# Node2 返回 {"score": 20}  → state["score"] = 20 (覆盖了!)
# 最终 score = 20
```

### 解决方案：使用 Reducer

```Python
from typing import Annotated
import operator

class State(TypedDict):
    score: Annotated[int, operator.add]  # 使用 operator.add 作为 reducer

# Node1 返回 {"score": 10}  → state["score"] = 0 + 10 = 10
# Node2 返回 {"score": 20}  → state["score"] = 10 + 20 = 30
# 最终 score = 30 (累加!)
```

### 实际例子：游戏得分

```Python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from typing import Annotated
import operator

class ScoreState(TypedDict):
    player_name: str  # 普通字段，会被覆盖
    score: Annotated[int, operator.add]  # 累加
    actions: Annotated[list[str], operator.add]  # 列表也能累加

def level1_node(state):
    return {"score": 10, "actions": ["通关第一关"]}

def level2_node(state):
    return {"score": 20, "actions": ["通关第二关"]}

def level3_node(state):
    return {"score": 30, "actions": ["通关第三关"]}

# 构建图...
workflow = StateGraph(ScoreState)
workflow.add_node("level1", level1_node)
workflow.add_node("level2", level2_node)
workflow.add_node("level3", level3_node)
workflow.add_edge(START, "level1")
workflow.add_edge("level1", "level2")
workflow.add_edge("level2", "level3")
workflow.add_edge("level3", END)

app = workflow.compile()
result = app.invoke({"player_name": "小明", "score": 0, "actions": []})

print(f"玩家: {result['player_name']}")
print(f"总分: {result['score']}")  # 10+20+30=60
print(f"完成动作: {result['actions']}")
```

### 常用 Reducer

---

## 第六章 条件分支

真实的应用不可能一条路走到黑，肯定需要根据情况走不同的路。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MmVhOTgxNWRlYWMwNmQ1ZmE3OGY1M2IyODZkMGUyOTlfNzJhMWUxZTk4ZTA2NjRkY2QxM2VhMTE1Nzc0ZWY5ZTJfSUQ6NzU4MDE5OTU3MzQ4MjcyMDIyMl8xNzgxMjMxODc1OjE3ODEzMTgyNzVfVjM)

### 关键函数：add\_conditional\_edges

```Python
workflow.add_conditional_edges(
    "source_node",    # 从哪个节点出发
    routing_function, # 用什么函数决定路由
    {                 # 路由映射表
        "result1": "target_node1",
        "result2": "target_node2",
    }
)
```

### 实际例子：智能客服路由

```Python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class CustomerState(TypedDict):
    question: str
    category: str
    answer: str

# 分类节点
def classify_question(state: CustomerState):
    question = state["question"].lower()
    
    if "退款" in question or "退货" in question:
        category = "refund"
    elif "物流" in question or "配送" in question:
        category = "shipping"
    elif "产品" in question or "使用" in question:
        category = "product"
    else:
        category = "general"
    
    return {"category": category}

# 处理节点
def handle_refund(state):
    return {"answer": "退款专员：请提供订单号，3个工作日内处理。"}

def handle_shipping(state):
    return {"answer": "物流客服：您的包裹正在配送中。"}

def handle_product(state):
    return {"answer": "产品顾问：请查看说明书第3页。"}

def handle_general(state):
    return {"answer": "客服：感谢咨询，还有其他问题吗？"}

# 路由函数
def route_question(state: CustomerState) -> str:
    return state["category"]

# 构建图
workflow = StateGraph(CustomerState)

workflow.add_node("classify", classify_question)
workflow.add_node("refund", handle_refund)
workflow.add_node("shipping", handle_shipping)
workflow.add_node("product", handle_product)
workflow.add_node("general", handle_general)

workflow.add_edge(START, "classify")

# 关键：条件路由
workflow.add_conditional_edges(
    "classify",
    route_question,
    {
        "refund": "refund",
        "shipping": "shipping",
        "product": "product",
        "general": "general"
    }
)

# 所有分支都到 END
workflow.add_edge("refund", END)
workflow.add_edge("shipping", END)
workflow.add_edge("product", END)
workflow.add_edge("general", END)

app = workflow.compile()

# 测试
test_questions = [
    "我想申请退款",
    "我的快递到哪了？",
    "这个产品怎么使用？",
]

for q in test_questions:
    result = app.invoke({"question": q, "category": "", "answer": ""})
    print(f"问题: {q}")
    print(f"回答: {result['answer']}\n")
```

---

## 第七章 循环流程

有时候一次处理不够好，需要反复迭代优化。LangGraph 天然支持循环。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=MDM4ZTI4OTkxZGE1OTViZjlmZjM5NTZiZTljM2Q4ZWVfYmQzOTczNzExMzM1ODM0NmQzZjRkM2IxNGNjZWM2ODlfSUQ6NzU4MDE5OTU2OTQyMzkxMTg2Nl8xNzgxMjMxODc1OjE3ODEzMTgyNzVfVjM)

### 关键点

1. **条件边可以指回前面的节点**：形成循环

2. **必须有退出条件**：否则会无限循环

3. **建议设置最大迭代次数**：兜底保护

### 实际例子：答案优化器

```Python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from typing import Annotated
import operator

class ImprovementState(TypedDict):
    question: str
    answer: str
    quality_score: float
    iteration: Annotated[int, operator.add]
    max_iterations: int

def generate_answer(state: ImprovementState):
    """生成或改进答案"""
    iteration = state.get("iteration", 0)
    
    # 模拟答案逐步改进
    if iteration == 0:
        answer = "简单回答：这是一个基础答案。"
        score = 0.5
    elif iteration == 1:
        answer = "改进回答：这是一个更详细的答案，包含更多信息。"
        score = 0.7
    else:
        answer = "完善回答：经过仔细思考的完整答案，包含背景和建议。"
        score = 0.9
    
    print(f"第{iteration + 1}次生成 (质量: {score})")
    
    return {
        "answer": answer,
        "quality_score": score,
        "iteration": 1  # 每次+1（因为有 reducer）
    }

def check_quality(state):
    """检查质量，不更新状态"""
    return state

def should_continue(state: ImprovementState) -> str:
    """决定是继续还是结束"""
    if state["quality_score"] >= 0.8:
        return "done"
    elif state["iteration"] >= state["max_iterations"]:
        return "done"
    else:
        return "improve"

# 构建带循环的图
workflow = StateGraph(ImprovementState)

workflow.add_node("generate", generate_answer)
workflow.add_node("check", check_quality)

workflow.add_edge(START, "generate")
workflow.add_edge("generate", "check")

# 关键：条件边可以循环回去
workflow.add_conditional_edges(
    "check",
    should_continue,
    {
        "improve": "generate",  # 循环
        "done": END             # 结束
    }
)

app = workflow.compile()

result = app.invoke({
    "question": "什么是人工智能？",
    "answer": "",
    "quality_score": 0.0,
    "iteration": 0,
    "max_iterations": 5
})

print(f"\n最终答案: {result['answer']}")
print(f"最终质量: {result['quality_score']}")
print(f"迭代次数: {result['iteration']}")
```

---

## 第八章 聊天机器人

做聊天机器人最头疼的是**消息历史管理**。LangGraph 提供了专门的工具来解决这个问题。

### 核心：MessagesState 和 add\_messages

```Python
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage

# 方式1：继承 MessagesState
class ChatState(MessagesState):
    user_name: str
    conversation_count: int

# 方式2：手动定义
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_name: str
```

`add_messages` 是一个智能 Reducer，它会：

- 自动追加新消息

- 处理消息 ID 去重

- 保持消息顺序

### 完整聊天机器人

```Python
import os
from langgraph.graph import StateGraph, START, END, MessagesState
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_community.llms import Tongyi

os.environ["DASHSCOPE_API_KEY"] = "your-key"

llm = Tongyi(model="qwen-plus", temperature=0.7)

class ChatState(MessagesState):
    user_name: str
    conversation_count: int

def chatbot_node(state: ChatState):
    """聊天节点"""
    # 构建系统提示
    system_prompt = f"""你是一个友好的AI助手。
用户名: {state.get('user_name', '用户')}
当前是第 {state.get('conversation_count', 0)} 轮对话。"""
    
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = llm.invoke(messages)
    
    return {
        "messages": [AIMessage(content=response)],
        "conversation_count": state.get("conversation_count", 0) + 1
    }

# 构建图
workflow = StateGraph(ChatState)
workflow.add_node("chatbot", chatbot_node)
workflow.add_edge(START, "chatbot")
workflow.add_edge("chatbot", END)
app = workflow.compile()

# 多轮对话
state = {"messages": [], "user_name": "小明", "conversation_count": 0}

conversations = [
    "你好，我是小明",
    "我刚才说我叫什么？",
    "帮我推荐一本Python书"
]

for user_input in conversations:
    print(f"用户: {user_input}")
    state["messages"].append(HumanMessage(content=user_input))
    result = app.invoke(state)
    state = result
    print(f"助手: {result['messages'][-1].content}\n")
```

---

## 第九章 Agent 工具调用

Agent 和普通聊天机器人的区别是：**Agent 能使用工具**。

### 定义工具

使用 `@tool` 装饰器：

```Python
from langchain_core.tools import tool

@tool
def search_web(query: str) -> str:
    """搜索网络信息
    
    Args:
        query: 搜索关键词
    """
    # 实际实现
    return f"搜索 '{query}' 的结果：..."

@tool
def calculator(expression: str) -> str:
    """计算数学表达式
    
    Args:
        expression: 数学表达式，如 "2+2"
    """
    try:
        result = eval(expression)
        return f"计算结果: {expression} = {result}"
    except:
        return "计算错误"

@tool
def get_weather(city: str) -> str:
    """查询城市天气
    
    Args:
        city: 城市名称
    """
    weather_data = {
        "北京": "晴天，15-25度",
        "上海": "多云，18-28度",
    }
    return weather_data.get(city, f"{city}天气信息不可用")
```

### 工具定义要点

1. **必须有 docstring**：描述工具用途，AI 靠这个判断什么时候用

2. **参数要有类型注解**：让 AI 知道怎么传参

3. **Args 部分详细说明**：每个参数是干什么的

---

## 第十章 ReAct Agent

ReAct = Reasoning \+ Acting，是目前最流行的 Agent 架构。

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=ZGVkMmYxYmNiZWQwYmM4MjlhY2JkNzk4YmU1OGMyOWJfNDM2M2I3ZjY2ZDVjNGUwNzgwNTRiM2YxMWVhZTk1ZDdfSUQ6NzU4MDE5OTU3MzM0NDI5MTgwMF8xNzgxMjMxODc1OjE3ODEzMTgyNzVfVjM)

### 工作流程

1. **Thought**：AI 思考需要做什么

2. **Action**：选择并调用工具

3. **Observation**：观察工具返回结果

4. **重复**：直到任务完成

### 使用预置组件

LangGraph 提供了现成的组件：

```Python
from langgraph.prebuilt import create_react_agent, ToolNode
from langchain_community.chat_models import ChatTongyi
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage

@tool
def add(a: float, b: float) -> float:
    """两数相加"""
    return a + b

@tool
def multiply(a: float, b: float) -> float:
    """两数相乘"""
    return a * b

# 创建模型
llm = ChatTongyi(model="qwen-plus", temperature=0.7)

# 创建 ReAct Agent
agent = create_react_agent(
    model=llm,
    tools=[add, multiply],
    messages_modifier=SystemMessage(content="你是一个数学计算助手。")
)

# 使用
result = agent.invoke({
    "messages": [{"role": "user", "content": "计算 (10 + 5) × 3"}]
})
print(result["messages"][-1].content)
```

### 手动实现 ReAct Agent

如果需要完全控制，可以手动实现：

```Python
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing import Annotated, Literal
from typing_extensions import TypedDict

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    iterations: Annotated[int, operator.add]

# 工具定义...
tools = [python_executor, text_analyzer]
tool_node = ToolNode(tools)

# 模型绑定工具
llm_with_tools = llm.bind_tools(tools)

def call_model(state: AgentState):
    """调用模型决策"""
    messages = state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response], "iterations": 1}

def should_continue(state) -> Literal["tools", "__end__"]:
    """判断是否需要调用工具"""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "__end__"

# 构建图
workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, {
    "tools": "tools",
    "__end__": END
})
workflow.add_edge("tools", "agent")

app = workflow.compile()
```

---

## 第十一章 多Agent协作

当任务复杂时，一个 Agent 搞不定，需要多个专业 Agent 配合。

### Supervisor 模式

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=OGQyZDNmMjI4Mzc1YjhlMzI4ODFkZWJhNTY2ODZjOTZfZWVhMTU5MjFiNWI2YWFjMDBjNWI3NTQxZmVhZWQ4OTVfSUQ6NzU4MDE5OTU3MzQ3ODU1ODY3Nl8xNzgxMjMxODc0OjE3ODEzMTgyNzRfVjM)

核心思想：**一个 Supervisor 负责调度，多个专业 Agent 负责执行**。

### 完整实现

```Python
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool
from typing import Annotated, Literal, Sequence
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

# 1. 定义状态
class SupervisorState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    next: str  # 下一个执行的 Agent

# 2. 定义各种工具
@tool
def check_system_status(component: str) -> str:
    """检查系统组件状态"""
    status = {"database": "正常", "server": "负载高", "api": "正常"}
    return status.get(component, "未知")

@tool
def get_product_info(product_name: str) -> str:
    """获取产品信息"""
    products = {
        "基础版": "99元/月，基础功能",
        "专业版": "299元/月，高级功能",
    }
    return products.get(product_name, "产品不存在")

@tool
def query_invoice(order_id: str) -> str:
    """查询发票"""
    invoices = {"ORD001": "已开具", "ORD002": "处理中"}
    return invoices.get(order_id, "订单不存在")

# 3. 定义专业 Agent 节点
def tech_agent_node(state):
    """技术支持 Agent"""
    last_message = state["messages"][-1].content
    
    if "系统" in last_message or "服务器" in last_message:
        result = check_system_status.invoke({"component": "server"})
        response = f"【技术支持】系统状态: {result}"
    else:
        response = "【技术支持】请描述您遇到的技术问题。"
    
    return {"messages": [AIMessage(content=response)]}

def sales_agent_node(state):
    """销售 Agent"""
    last_message = state["messages"][-1].content
    
    for product in ["基础版", "专业版"]:
        if product in last_message:
            info = get_product_info.invoke({"product_name": product})
            return {"messages": [AIMessage(content=f"【销售顾问】{info}")]}
    
    return {"messages": [AIMessage(content="【销售顾问】我们有基础版和专业版，需要了解哪个？")]}

def billing_agent_node(state):
    """账单 Agent"""
    return {"messages": [AIMessage(content="【账单专员】请提供订单号，我来查询。")]}

# 4. Supervisor 节点
def supervisor_node(state):
    """Supervisor：决定下一步"""
    last_message = state["messages"][-1].content
    
    tech_keywords = ["错误", "bug", "崩溃", "系统", "服务器"]
    sales_keywords = ["价格", "购买", "产品", "套餐"]
    billing_keywords = ["发票", "支付", "退款", "账单"]
    
    if any(kw in last_message for kw in tech_keywords):
        next_agent = "tech_support"
    elif any(kw in last_message for kw in sales_keywords):
        next_agent = "sales"
    elif any(kw in last_message for kw in billing_keywords):
        next_agent = "billing"
    else:
        next_agent = "sales"  # 默认销售
    
    return {"next": next_agent}

# 5. 路由函数
def route_after_supervisor(state) -> Literal["tech_support", "sales", "billing"]:
    return state["next"]

# 6. 构建图
workflow = StateGraph(SupervisorState)

workflow.add_node("supervisor", supervisor_node)
workflow.add_node("tech_support", tech_agent_node)
workflow.add_node("sales", sales_agent_node)
workflow.add_node("billing", billing_agent_node)

workflow.add_edge(START, "supervisor")
workflow.add_conditional_edges(
    "supervisor",
    route_after_supervisor,
    {
        "tech_support": "tech_support",
        "sales": "sales",
        "billing": "billing",
    }
)
workflow.add_edge("tech_support", END)
workflow.add_edge("sales", END)
workflow.add_edge("billing", END)

app = workflow.compile()

# 7. 测试
test_cases = [
    "我遇到500错误，系统无法访问",
    "专业版多少钱？",
    "我需要查发票",
]

for query in test_cases:
    print(f"用户: {query}")
    result = app.invoke({"messages": [HumanMessage(content=query)], "next": ""})
    for msg in result["messages"]:
        if isinstance(msg, AIMessage):
            print(msg.content)
    print()
```

---

## 第十二章 实战项目

### 智能客服系统架构

把前面学的所有东西整合起来，做一个完整的智能客服系统：

1. **Supervisor** 分析问题类型

2. **技术支持 Agent** 处理技术问题

3. **销售顾问 Agent** 处理产品咨询

4. **账单专员 Agent** 处理财务问题

5. 每个 Agent 都有自己的**专业工具**

6. 支持**多轮对话**和**历史记忆**

完整代码见附带的 `langgraph_supervisor_1.py` 文件。

---

## 第十三章 进阶话题

### 持久化和记忆

使用 `checkpointer` 保存对话状态：

```Python
from langgraph.checkpoint.memory import MemorySaver

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)

# 使用 thread_id 管理不同会话
config = {"configurable": {"thread_id": "user_123"}}
result = app.invoke(state, config=config)
```

### 流式输出

实现打字机效果：

```Python
# 方式1：使用 streaming=True 的模型
llm = Tongyi(model="qwen-plus", streaming=True)

for chunk in llm.stream(messages):
    print(chunk, end="", flush=True)

# 方式2：使用 app.stream()
for event in app.stream(initial_state):
    print(event)
```

### 子图（Subgraph）

复杂系统可以拆成多个子图：

```Python
# 定义子图
sub_workflow = StateGraph(SubState)
# ... 添加节点和边
sub_graph = sub_workflow.compile()

# 在主图中使用子图作为节点
main_workflow.add_node("sub_process", sub_graph)
```

### Human\-in\-the\-Loop

让人类参与决策：

```Python
# 设置断点
app = workflow.compile(
    checkpointer=memory,
    interrupt_before=["sensitive_action"]
)

# 运行到断点会暂停
result = app.invoke(state, config)

# 人工审核后继续
app.invoke(None, config)  # 继续执行
```

### 调试技巧

```Python
# 可视化图结构
print(app.get_graph().draw_mermaid())

# 打印执行过程
for event in app.stream(state, stream_mode="debug"):
    print(event)
```

---



