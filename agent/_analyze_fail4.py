"""分析剩余失败:占比类 + 其他"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from graph import run_agent
from cache import clear_cache

clear_cache()

test_cases = [
    "各品类商品数量占比",
    "运动类商品有哪些",
    "价格最低的商品属于哪个品类",
]

for q in test_cases:
    print(f"\n{'='*50}")
    print(f"问题: {q}")
    result = run_agent(q, enable_auto_train=False)
    print(f"SQL: {result.get('generated_sql', '')[:200]}")
    print(f"审核: {'通过' if result.get('validation_passed') else '未通过'}")
    if result.get("result_rows"):
        print(f"结果: {result['result_rows'][:3]}")