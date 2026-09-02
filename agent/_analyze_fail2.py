"""分析失败案例:最便宜的3个商品"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from graph import run_agent
from cache import clear_cache

clear_cache()

test_cases = [
    "最便宜的3个商品",
    "数码类商品的平均价格",
    "各品类的平均价格",
]

for q in test_cases:
    print(f"\n{'='*50}")
    print(f"问题: {q}")
    result = run_agent(q, enable_auto_train=False)
    print(f"SQL: {result.get('generated_sql', '')[:150]}")
    print(f"审核: {'通过' if result.get('validation_passed') else '未通过'}")
    if result.get("validation_errors"):
        print(f"错误: {result['validation_errors']}")
    if result.get("result_rows"):
        print(f"结果: {result['result_rows'][:3]}")