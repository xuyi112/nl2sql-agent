"""
可视化节点:SQL 结果 → 图表配置
==================================
根据查询结果自动决定画什么图,返回前端可渲染的 JSON 配置。

决策方式(双层):
  1. LLM 决策:根据用户问题 + 结果特征,从 bar/line/pie/table 中选(更懂语义)
  2. 规则兜底:LLM 失败/非法输出时,退回纯规则判断

规则判断逻辑:
  2 列(名称+数值) + 列名含日期 → 折线图
  2 列(名称+数值)              → 柱状图
  其他                         → 表格(兜底)
"""
import json
import re
from datetime import datetime


# ---------- LLM 图表决策工具定义 ----------

CHART_TOOL = {
    "type": "function",
    "function": {
        "name": "choose_chart",
        "description": "选择最适合展示查询结果的图表类型",
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "table"],
                    "description": "bar=柱状图(分类对比), line=折线图(时间趋势), pie=饼图(占比分布), table=表格(多列/复杂数据)",
                },
                "title": {"type": "string", "description": "图表标题"},
                "x_label": {"type": "string", "description": "X轴标签(表格时忽略)"},
                "y_label": {"type": "string", "description": "Y轴标签(表格时忽略)"},
            },
            "required": ["chart_type", "title"],
        },
    },
}


def decide_chart(
    columns: list[str],
    rows: list[tuple],
) -> dict:
    """规则版:根据结果特征,自动决定图表类型并生成配置(LLM 决策的兜底)。

    返回: {
        "chart_type": "bar" / "line" / "pie" / "table",
        "title": "...",
        "x_label": "...",
        "y_label": "...",
        "labels": [...],     # X 轴数据
        "values": [...],     # Y 轴数据
    }
    """
    # 边界情况:空结果 → 表格
    if not rows or not columns:
        return _table_config(columns, rows)

    # 2 列(名称+数值):可能是柱状图或折线图
    if len(columns) == 2:
        return _two_col_config(columns, rows)

    # 其他情况 → 表格(兜底)
    return _table_config(columns, rows)


def decide_chart_with_llm(llm, question: str, columns: list[str], rows: list[tuple]) -> dict:
    """LLM 版:根据用户问题 + 结果特征选图表类型,失败/非法输出退回规则版。

    读: 用户问题(语义,如"占比"→饼图) + 结果列/行
    写: 图表配置(与规则版同结构)
    """
    # 边界情况:空结果 → 表格(不用调 LLM)
    if not rows or not columns:
        return _table_config(columns, rows)

    try:
        prompt = _build_chart_prompt(question, columns, rows)
        response = llm.invoke(prompt, tools=[CHART_TOOL])
        decision = _parse_chart_response(response)
        if decision:
            return _build_config_from_decision(decision, columns, rows)
    except Exception as e:
        print(f"⚠️ LLM 图表决策失败,退回规则版: {e}")

    # 兜底:规则版
    return decide_chart(columns, rows)


def _build_chart_prompt(question: str, columns: list[str], rows: list[tuple]) -> str:
    """构造图表决策 prompt:问题 + 结果摘要(不全量给数据,省 token)。"""
    sample = rows[:5]  # 只给前 5 行示例
    return f"""你是数据可视化专家。请根据用户问题和查询结果,选择最合适的图表类型。

【用户问题】
{question}

【查询结果】
列: {columns}
行数: {len(rows)}
前5行示例: {sample}

选择规则:
- bar(柱状图): 分类对比(如各区域销售额)
- line(折线图): 时间趋势(如每日订单量)
- pie(饼图): 占比分布(如各品类销售额占比,类别通常少于8个)
- table(表格): 多列数据或无法用图表达

请调用 choose_chart 工具提交你的选择。"""


def _parse_chart_response(response) -> dict | None:
    """从 Function Calling 的 tool_calls 解析图表决策,失败返回 None。"""
    try:
        for call in response.tool_calls:
            if call.get("name") == "choose_chart":
                args = call.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args)
                chart_type = args.get("chart_type")
                if chart_type in ("bar", "line", "pie", "table"):
                    return {
                        "chart_type": chart_type,
                        "title": args.get("title", "查询结果"),
                        "x_label": args.get("x_label", ""),
                        "y_label": args.get("y_label", ""),
                    }
    except Exception:
        pass
    return None


def _build_config_from_decision(decision: dict, columns: list[str], rows: list[tuple]) -> dict:
    """把 LLM 的决策转成标准图表配置(校验数据可用性)。"""
    chart_type = decision["chart_type"]

    # 表格:直接返回
    if chart_type == "table":
        return _table_config(columns, rows)

    # 图表需要 2 列(名称+数值),否则退回表格
    if len(columns) < 2:
        return _table_config(columns, rows)

    labels = [str(row[0]) for row in rows]
    values = [row[1] for row in rows]

    # 数值列必须全是数字,否则退回表格
    if not all(_is_numeric(v) for v in values):
        return _table_config(columns, rows)

    title = decision.get("title") or f"{columns[1]} 按 {columns[0]} 分布"

    if chart_type == "pie":
        return {
            "chart_type": "pie",
            "title": title,
            "labels": labels,
            "values": values,
        }

    if chart_type == "line":
        return {
            "chart_type": "line",
            "title": title,
            "x_label": decision.get("x_label") or columns[0],
            "y_label": decision.get("y_label") or columns[1],
            "labels": labels,
            "values": values,
        }

    # bar(默认)
    return {
        "chart_type": "bar",
        "title": title,
        "x_label": decision.get("x_label") or columns[0],
        "y_label": decision.get("y_label") or columns[1],
        "labels": labels,
        "values": values,
    }


def _is_date_column(col_name: str) -> bool:
    """判断列名是否是"日期/时间"类型。"""
    date_keywords = ["date", "time", "month", "year", "day", "日期", "时间", "月"]
    return any(kw in col_name.lower() for kw in date_keywords)


def _is_numeric(value) -> bool:
    """判断值是否是数字。"""
    return isinstance(value, (int, float))


def _two_col_config(columns: list[str], rows: list[tuple]) -> dict:
    """处理 2 列数据(名称+数值)。"""
    labels = [str(row[0]) for row in rows]
    values = [row[1] for row in rows]

    # 如果数值列全是数字,才画图
    if not all(_is_numeric(v) for v in values):
        return _table_config(columns, rows)

    # 判断是折线图还是柱状图
    col0, col1 = columns[0], columns[1]
    if _is_date_column(col0) or _is_date_column(col1):
        # 日期在前 → 折线图;日期在后 → 交换后折线图
        if _is_date_column(col0):
            return {
                "chart_type": "line",
                "title": f"{col1} 按 {col0} 趋势",
                "x_label": col0,
                "y_label": col1,
                "labels": labels,
                "values": values,
            }
        else:
            return {
                "chart_type": "line",
                "title": f"{col0} 按 {col1} 趋势",
                "x_label": col1,
                "y_label": col0,
                "labels": [str(row[1]) for row in rows],
                "values": [row[0] for row in rows],
            }

    # 默认:柱状图
    return {
        "chart_type": "bar",
        "title": f"{col1} 按 {col0} 分布",
        "x_label": col0,
        "y_label": col1,
        "labels": labels,
        "values": values,
    }


def _table_config(columns: list[str], rows: list[tuple]) -> dict:
    """兜底:返回表格配置。"""
    return {
        "chart_type": "table",
        "title": "查询结果",
        "columns": columns,
        "rows": [list(row) for row in rows],
    }
