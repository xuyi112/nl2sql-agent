"""
配置模块:从 .env 读取 DeepSeek API 配置。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env 候选位置(按优先级):
#   1. 本项目目录 nl2sql_agent/.env
#   2. 工作区根目录 .env(与 HelloAgents 项目共用)
_env_candidates = [
    Path(__file__).parent.parent / ".env",            # nl2sql_agent/.env
    Path(__file__).parent.parent.parent / ".env",     # f:\数据分析\.env
]
for env_path in _env_candidates:
    if env_path.exists():
        load_dotenv(env_path)
        print(f"📄 已加载环境变量: {env_path}")
        break

LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "deepseek-chat")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "base_url_not_set")
