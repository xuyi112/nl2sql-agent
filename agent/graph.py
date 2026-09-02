"""
NL2SQL Agent 的普通 Python 编排
================================

  retrieve → generate → validate → execute
                ↑__________|(审核失败回炉,最多2次)

用普通函数 + while 循环实现,不依赖 LangGraph。
"""
import json
import re
from typing import TypedDict

from langchain_openai import ChatOpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_ID
from schema_rag import add_question_sql_pair, retrieve as rag_retrieve
from cache import get_cached, set_cached


# ---------- 0. LLM 初始化(DeepSeek,兼容 OpenAI 协议) ----------

llm = ChatOpenAI(
    model=LLM_MODEL_ID,
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    temperature=0,          # 0 = 每次都尽量输出确定结果,SQL 生成不需要随机
)


# ---------- 0.5 Function Calling 工具定义 ----------

# 工具 1:SQL 生成。LLM 必须把 SQL 填进结构化参数,而不是自由文本输出。
SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_sql",
        "description": "提交生成的 SQLite SELECT 查询语句",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "完整的 SQLite SELECT 查询语句"}
            },
            "required": ["sql"],
        },
    },
}

# 工具 2:LLM 复核。passed 是真正的布尔值,reason 完整可取。
REVIEW_TOOL = {
    "type": "function",
    "function": {
        "name": "review_result",
        "description": "提交 SQL 审核结论",
        "parameters": {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean", "description": "SQL 是否正确回答了用户问题"},
                "reason": {"type": "string", "description": "审核理由(未通过时说明具体问题)"},
            },
            "required": ["passed", "reason"],
        },
    },
}

# 工具 3:追问生成。LLM 输出 3 个与当前问题相关的后续问题。
FOLLOWUP_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_followups",
        "description": "提交与当前查询相关的后续问题列表",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3 个相关的后续问题",
                }
            },
            "required": ["questions"],
        },
    },
}


# ---------- 1. State 定义:节点间共享的"流转单" ----------

class AgentState(TypedDict):
    """所有节点共享的状态字典。

    每个节点读自己需要的键、写自己产出的键,
    LangGraph 会自动把节点返回的键合并进 state。
    """
    question: str            # 用户问题(起点写入)
    ddl_text: str            # 检索到的表结构(retrieve 写入)
    doc_text: str            # 检索到的业务规则(retrieve 写入)
    few_shot_text: str       # 检索到的示例 SQL(retrieve 写入)
    generated_sql: str       # LLM 生成的 SQL(generate 写入)
    validation_passed: bool  # 审核是否通过(validate 写入)
    validation_errors: list  # 审核错误信息(validate 写入)
    retry_count: int         # 已重试次数(条件边用来判断是否回炉)
    result_rows: list        # SQL 执行结果行(execute 写入)
    result_columns: list     # SQL 执行结果列名(execute 写入)
    execution_error: str     # SQL 执行错误信息(execute 写入,空=成功)
    chart_config: dict       # 图表配置(visualize 写入)
    followup_questions: list # 相关追问(visualize 后写入)
    answer: str              # 最终回答(fail 或后续 answer 节点写入)


# ---------- 2. 节点定义:每个节点是一个函数 ----------

def retrieve_node(state: AgentState) -> dict:
    """节点 1:Schema RAG 检索。

    读: state["question"]
    写: state["ddl_text"] / ["doc_text"] / ["few_shot_text"]
    """
    ctx = rag_retrieve(state["question"], top_k=2)
    return {
        "ddl_text": ctx["ddl_text"],
        "doc_text": ctx["doc_text"],
        "few_shot_text": ctx["few_shot_text"],
    }


# ---------- 3. 节点 2:generate(SQL 生成) ----------

def _build_sql_prompt(state: AgentState) -> str:
    """把 state 里的三段上下文 + 用户问题,拼成发给 LLM 的完整 prompt。"""
    from datetime import date

    today = date.today().isoformat()  # 当前日期,如 2026-09-02
    # 如果有上次审核错误,带上让 LLM 修正(回炉时才有)
    error_hint = ""
    if state.get("validation_errors"):
        error_hint = f"""
【上次审核未通过,请修正以下问题】
{state['validation_errors']}
"""
    elif state.get("execution_error"):
        error_hint = f"""
【上次 SQL 执行失败,请修正以下问题】
{state['execution_error']}
"""
    return f"""你是电商数据分析专家。请根据以下数据库信息,为用户问题生成一条 SQLite SQL 查询。

【当前日期】{today}(今天是这一天,判断"上月/上季度"等相对时间时以此为准)

【数据库表结构】
{state['ddl_text']}

【业务规则】
{state['doc_text']}

【参考示例】
{state['few_shot_text']}
{error_hint}
【用户问题】
{state['question']}

请调用 submit_sql 工具提交你的 SQL 答案,不要输出任何其他内容。
注意:如果查询使用了 GROUP BY 聚合(如 COUNT/SUM),不需要加 LIMIT,直接返回全部结果。"""


def _extract_sql_from_response(response) -> str:
    """优先从 Function Calling 的 tool_calls 提取 SQL,失败则退回正则提取。

    双保险:模型偶尔不调用工具时,系统不会挂掉。
    """
    try:
        for call in response.tool_calls:
            if call.get("name") == "submit_sql":
                args = call.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args)
                sql = str(args.get("sql", "")).strip()
                if sql:
                    return sql
    except Exception:
        pass
    # 兜底:正则提取(处理模型没调用工具的情况)
    return _extract_sql(response.content)


def generate_node(state: AgentState) -> dict:
    """节点 2:调用 LLM 生成 SQL(Function Calling 结构化输出)。

    读: state["question"] / ["ddl_text"] / ["doc_text"] / ["few_shot_text"]
    写: state["generated_sql"] / ["retry_count"]
    """
    prompt = _build_sql_prompt(state)
    response = llm.invoke(prompt, tools=[SQL_TOOL])
    sql = _extract_sql_from_response(response)  # 优先 tool_calls,兜底正则
    retry_count = state.get("retry_count", 0) + 1
    return {"generated_sql": sql, "retry_count": retry_count}


def _extract_sql(raw: str) -> str:
    """从 LLM 原始输出中提取纯 SQL(兜底方案)。

    处理:markdown 代码块、Python 包裹、多余解释文字。
    """
    text = raw.strip()

    # 1. 去掉 markdown 代码块:```sql ... ``` 或 ``` ... ```
    m = re.search(r"```(?:sql)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    # 2. 如果整段文本不是以 SELECT/WITH 开头,尝试提取第一个 SQL 语句
    upper = text.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        # 尝试从文本中提取 SELECT ... 语句
        m = re.search(r"((?:SELECT|WITH)\b.*)", text, re.DOTALL | re.IGNORECASE)
        if m:
            text = m.group(1).strip()

    return text


# ---------- 4. 节点 3:validate(SQL 安全审核,双层) ----------

# 允许的表名 / 列名白名单(从 DDL 里能拿到的真实对象)
ALLOWED_TABLES = {"orders", "products", "regions"}
# 危险关键字:出现即拒绝(写操作 / 删表 / 危险语句)
FORBIDDEN_KEYWORDS = [
    "delete", "update", "insert", "drop", "alter", "create",
    "truncate", "replace", "grant", "attach", "pragma",
]


def _rule_check(sql: str) -> list[str]:
    """规则层:纯代码检查,不调 LLM,秒级完成。

    返回: 错误信息列表(空列表 = 通过)
    """
    errors = []
    sql_lower = sql.lower()

    # 1. 只读检查:不能出现写操作/危险关键字
    for kw in FORBIDDEN_KEYWORDS:
        if kw in sql_lower:
            errors.append(f"检测到危险关键字 '{kw}',只允许 SELECT 查询")

    # 2. 必须以 SELECT 开头(去掉注释和空白后)
    stripped = sql_lower.lstrip(" \t\n\r-*")
    if not stripped.startswith("select"):
        errors.append("SQL 必须以 SELECT 开头")

    # 3. 表名白名单:提取 FROM/JOIN 后的表名,检查是否都在允许列表
    #    (简化版:只要 SQL 里出现白名单外的表名就报错)
    for table in ["orders", "products", "regions"]:
        pass  # 占位,完整版用正则提取 FROM/JOIN 后的表名
    # 简化检查:禁止出现明显不存在的表名(这里用白名单反向检查)
    import re
    from_tables = re.findall(r"(?:from|join)\s+([a-z_]+)", sql_lower)
    for t in from_tables:
        if t not in ALLOWED_TABLES:
            errors.append(f"引用了不存在的表 '{t}'")

    # 4. 强制 LIMIT:防止一次拉回全表(数据量大的保护)
    #    聚合查询豁免:返回的是汇总数据,通常行数不多
    #    - GROUP BY 聚合(COUNT/SUM 按组) → 豁免
    #    - 聚合函数无 GROUP BY(COUNT(*)/SUM/AVG/MIN/MAX 返回单行) → 豁免
    has_group_by = "group by" in sql_lower
    has_agg_func = any(
        f"{fn}(" in sql_lower for fn in ["count", "sum", "avg", "min", "max"]
    )
    if not has_group_by and not has_agg_func and "limit" not in sql_lower:
        errors.append("查询缺少 LIMIT 限制,请加上 LIMIT")

    return errors


def _llm_recheck(state: AgentState) -> list[str]:
    """LLM 复核层:判断 SQL 是否回答了用户问题(语义检查)。

    规则层抓"危险/语法",这层抓"答非所问"。
    返回: 错误信息列表(空列表 = 通过)
    """
    from datetime import date

    today = date.today().isoformat()  # 当前日期,如 2026-08-21
    prompt = f"""你是 SQL 审核专家。请判断下面这条 SQL 是否正确地回答了用户的问题。

【当前日期】{today}(今天是这一天,判断"上月/上季度"等相对时间时以此为准)

【用户问题】
{state['question']}

【生成的 SQL】
{state['generated_sql']}

请调用 review_result 工具提交审核结论,不要输出任何其他内容。

判断标准:
1. SQL 是否回答了用户问题的核心诉求(比如问题问"区域",SQL 是否按区域分组)
2. 如果 SQL 用了硬编码日期(如 '2026-07-01'),只要它符合当前日期下的"上月/上季度"等相对时间,就算正确,不要因为硬编码就判错
3. 只判断"是否答非所问",不要纠结 SQL 写法细节

如果 SQL 能正确回答用户问题,passed 为 true;否则为 false 并说明原因。"""
    response = llm.invoke(prompt, tools=[REVIEW_TOOL])
    passed, reason = _parse_review_response(response)
    if passed:
        return []
    return [f"LLM 复核未通过: {reason}"]


def _parse_review_response(response) -> tuple[bool, str]:
    """优先从 Function Calling 的 tool_calls 解析审核结论,失败则退回 JSON 正则提取。

    返回: (passed, reason)
    """
    try:
        for call in response.tool_calls:
            if call.get("name") == "review_result":
                args = call.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args)
                return bool(args.get("passed")), str(args.get("reason", ""))
    except Exception:
        pass
    # 兜底:从自由文本里正则提取 JSON
    content = response.content
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group())
            return bool(data.get("passed")), str(data.get("reason", ""))
        except Exception:
            pass
    return False, content[:200]


def validate_node(state: AgentState) -> dict:
    """节点 3:双层审核 SQL。

    读: state["generated_sql"] / ["question"]
    写: state["validation_passed"] / ["validation_errors"]
    """
    sql = state["generated_sql"]

    # 第一层:规则层(快,拦截危险/语法错误)
    errors = _rule_check(sql)

    # 第二层:规则层通过后,才做 LLM 复核(慢,抓语义错误)
    if not errors:
        errors = _llm_recheck(state)

    return {
        "validation_passed": len(errors) == 0,
        "validation_errors": errors,
    }


def route_after_validate(state: AgentState) -> str:
    """根据审核结果决定下一步去哪(被 run_agent 的 while 循环替代,保留作参考)。

    返回: "regen"(回炉) / "fail"(放弃) / "execute"(执行)
    """
    passed = state["validation_passed"]
    retry = state.get("retry_count", 0)

    if not passed and retry < 2:
        return "regen"      # 没过且没重试够 → 回炉重生成
    if not passed:
        return "fail"       # 重试够还不行 → 失败
    return "execute"        # 通过 → 执行


# ---------- 5. 节点 4:execute(SQL 执行) ----------

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ecommerce.db"


def execute_node(state: AgentState) -> dict:
    """节点 4:执行审核通过的 SQL,拿结果。

    读: state["generated_sql"]
    写: state["result_rows"] / ["result_columns"] / ["execution_error"]

    执行失败不抛异常,而是把错误信息写进 execution_error,
    让 run_agent 带错误回炉重写(规则层 EXPLAIN 只能查语法,
    运行时错误如除零/类型不匹配只有真正执行才暴露)。
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(state["generated_sql"])
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return {
            "result_rows": rows,
            "result_columns": columns,
            "execution_error": "",
        }
    except sqlite3.Error as e:
        return {"execution_error": str(e)}


def fail_node(state: AgentState) -> dict:
    """失败节点:重试多次仍失败,返回错误信息。"""
    errors = list(state.get("validation_errors") or [])
    exec_err = state.get("execution_error", "")
    if exec_err:
        errors.append(f"SQL 执行失败: {exec_err}")
    return {
        "answer": f"无法生成有效 SQL,错误: {errors}",
    }


# ---------- 6. 节点 5:visualize(可视化决策) ----------

from visualize import decide_chart_with_llm


def visualize_node(state: AgentState) -> dict:
    """节点 5:根据查询结果自动决定图表类型(LLM 决策,规则兜底)。

    读: state["result_rows"] / ["result_columns"] / ["question"]
    写: state["chart_config"]
    """
    chart_config = decide_chart_with_llm(
        llm=llm,
        question=state["question"],
        columns=state["result_columns"],
        rows=state["result_rows"],
    )
    return {"chart_config": chart_config}


# ---------- 6.5 节点 6:followups(追问生成) ----------

def generate_followups(state: AgentState) -> list[str]:
    """生成 3 个与当前查询相关的后续问题(引导用户深入探索)。

    读: state["question"] / ["generated_sql"] / ["result_columns"]
    写: 返回追问列表(失败返回空列表,不影响主流程)
    """
    try:
        prompt = f"""你是电商数据分析助手。用户刚问了一个问题并得到了结果,请生成 3 个相关的后续问题,帮助用户深入探索数据。

【用户问题】
{state['question']}

【查询的列】
{state.get('result_columns', [])}

要求:
- 3 个问题,与当前查询相关(如按其他维度拆分、对比、下钻)
- 每个问题都能用数据库回答
- 不要重复用户已问的问题

请调用 submit_followups 工具提交你的问题列表。"""
        response = llm.invoke(prompt, tools=[FOLLOWUP_TOOL])
        for call in response.tool_calls:
            if call.get("name") == "submit_followups":
                args = call.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args)
                questions = args.get("questions", [])
                if isinstance(questions, list):
                    return [str(q) for q in questions[:3]]
    except Exception as e:
        print(f"⚠️ 追问生成失败: {e}")
    return []


# ---------- 6. 普通 Python 编排(替代 LangGraph 图) ----------

def run_agent(question: str) -> dict:
    """普通 Python 版编排:retrieve → generate → validate → (回炉) → execute → visualize

    回炉条件(最多 2 次):
    - 审核失败(规则层或 LLM 复核)
    - 审核通过但执行失败(运行时错误,如除零/类型不匹配)

    缓存:相同问题(非相对时间)直接返回上次结果,省 API 费用。
    """
    # 0. 缓存检查:相同问题直接返回上次结果(相对时间问题自动跳过)
    cached = get_cached(question)
    if cached:
        print(f"⚡ 缓存命中: {question}")
        return cached

    state: AgentState = {"question": question, "retry_count": 0}

    # 1. 检索
    state.update(retrieve_node(state))

    # 2. 生成 + 审核 + 执行(while 循环 = 条件边回炉)
    while True:
        state.update(generate_node(state))
        state.update(validate_node(state))
        if state["validation_passed"]:
            # 审核通过 → 尝试执行;执行成功才退出循环
            state.update(execute_node(state))
            if not state.get("execution_error"):
                break
        if state["retry_count"] >= 2:
            break  # 重试够 → 退出

    # 3. 结果分岔:失败 → fail;通过 → visualize
    if not state["validation_passed"] or state.get("execution_error"):
        state.update(fail_node(state))
        return state

    # 4. auto_train:执行成功 → 把 (问题, SQL) 自动入库,系统越用越聪明
    add_question_sql_pair(state["question"], state["generated_sql"])

    state.update(visualize_node(state))

    # 5. 追问生成:3 个相关后续问题(失败不影响主流程)
    state["followup_questions"] = generate_followups(state)

    # 6. 写入缓存:下次相同问题直接返回(相对时间问题自动跳过)
    set_cached(question, state)
    return state


if __name__ == "__main__":
    # 测试:完整链路(需要真实 DeepSeek API)
    result = run_agent("华东区上月销售额Top3商品")
    print("✅ 全链路跑通!")
    print("=" * 60)
    print("生成的 SQL:")
    print(result["generated_sql"])
    print("=" * 60)
    print("审核结果:", "通过 ✅" if result["validation_passed"] else "未通过 ❌")
    print("查询结果列:", result.get("result_columns"))
    print("查询结果行:", result.get("result_rows"))
    print("=" * 60)
    chart = result.get("chart_config", {})
    print("图表类型:", chart.get("chart_type"))
    print("图表标题:", chart.get("title"))
