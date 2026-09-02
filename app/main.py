"""
FastAPI 后端:NL2SQL Agent 的 HTTP 接口
========================================
把 LangGraph 图包成 REST API,前端或 Postman 发请求即可使用。

启动方式:
  cd nl2sql_agent/app
  uvicorn main:app --reload --port 8000

打开文档:
  http://localhost:8000/docs
"""
import sys
from pathlib import Path

# 把 agent 目录加入 Python 路径(这样能 import graph/visualize 等模块)
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from graph import run_agent

# ---------- 1. 初始化 ----------

app = FastAPI(title="NL2SQL Agent API", version="0.1.0")

# CORS:允许浏览器 HTML 页面调用 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 HTML 静态文件
@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ---------- 2. 请求/响应模型 ----------

class QueryRequest(BaseModel):
    """请求体:用户问题"""
    question: str


class QueryResponse(BaseModel):
    """响应体:完整结果"""
    question: str
    sql: str
    validation_passed: bool
    validation_errors: list
    result_columns: list
    result_rows: list
    chart: dict
    followup_questions: list = []


# ---------- 3. 路由 ----------

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """NL2SQL 查询接口:自然语言 → SQL → 结果 → 图表配置"""
    result = run_agent(req.question)
    return QueryResponse(
        question=req.question,
        sql=result.get("generated_sql", ""),
        validation_passed=result.get("validation_passed", False),
        validation_errors=result.get("validation_errors", []),
        result_columns=result.get("result_columns", []),
        result_rows=result.get("result_rows", []),
        chart=result.get("chart_config", {}),
        followup_questions=result.get("followup_questions", []),
    )
