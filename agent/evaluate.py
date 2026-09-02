"""
NL2SQL 评测脚本
===============
用一组测试问题 + 期望 SQL,跑一遍完整链路,统计准确率。

评测方式(两种结合):
  1. 结果对比:执行生成的 SQL 和期望 SQL,比较查询结果是否一致(语义正确)
  2. SQL 对比:比较生成的 SQL 与期望 SQL 的文本相似度(写法正确)

用法:
  cd nl2sql_agent/agent
  python evaluate.py

输出:
  每个问题的通过/失败 + 原因
  总体准确率统计
"""
import sys
import time
from pathlib import Path

# 把 agent 目录加入路径(和 app/main.py 一致)
sys.path.insert(0, str(Path(__file__).parent))

import sqlite3
from graph import run_agent

DB_PATH = Path(__file__).parent.parent / "data" / "ecommerce.db"

# ---------- 测试集:问题 + 期望 SQL ----------
# 覆盖:单表/多表 JOIN/聚合/日期过滤/TopN/分组
TEST_CASES = [
    {
        "question": "华东区上月销售额Top3商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东'
             AND o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'
           GROUP BY p.name
           ORDER BY sales DESC
           LIMIT 3""",
    },
    {
        "question": "全国各区域订单量",
        "expected_sql": """SELECT r.name, COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name
           ORDER BY order_count DESC""",
    },
    {
        "question": "上季度各品类销售额",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-04-01'
             AND o.order_date <= '2026-06-30'
           GROUP BY p.category
           ORDER BY sales DESC""",
    },
    {
        "question": "最贵的5个商品",
        "expected_sql": """SELECT name, price FROM products ORDER BY price DESC LIMIT 5""",
    },
    {
        "question": "各地区的平均客单价",
        "expected_sql": """SELECT r.name, SUM(o.amount) / COUNT(*) AS avg_order_value
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name""",
    },
    {
        "question": "上个月每天的订单量趋势",
        "expected_sql": """SELECT o.order_date, COUNT(*) AS order_count
           FROM orders o
           WHERE o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'
           GROUP BY o.order_date
           ORDER BY o.order_date""",
    },
    {
        "question": "数码类商品的平均价格",
        "expected_sql": """SELECT AVG(price) AS avg_price FROM products WHERE category = '数码'""",
    },
    {
        "question": "订单量最多的前3个地区",
        "expected_sql": """SELECT r.name, COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name
           ORDER BY order_count DESC
           LIMIT 3""",
    },
]


# ---------- 评测工具 ----------

def _execute_sql(sql: str) -> list:
    """执行 SQL,返回结果行(排序后,用于对比)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        # 排序:结果顺序不影响正确性(ORDER BY 差异不算错)
        return sorted(rows, key=str)
    finally:
        conn.close()


def _normalize_sql(sql: str) -> str:
    """SQL 规范化:去空白/大小写,用于文本对比。"""
    return " ".join(sql.lower().split())


def evaluate() -> dict:
    """跑完整评测,返回统计结果。"""
    results = []
    total = len(TEST_CASES)
    passed = 0

    print("=" * 70)
    print("NL2SQL 评测开始")
    print("=" * 70)

    for i, case in enumerate(TEST_CASES, 1):
        question = case["question"]
        expected_sql = case["expected_sql"]

        print(f"\n[{i}/{total}] {question}")
        start = time.time()

        # 1. 跑完整链路
        result = run_agent(question)
        elapsed = time.time() - start
        generated_sql = result.get("generated_sql", "")

        # 2. 检查是否通过审核并执行成功
        if not result.get("validation_passed"):
            print(f"  ❌ 审核未通过: {result.get('validation_errors')}")
            results.append({"question": question, "passed": False, "reason": "审核未通过"})
            continue
        if result.get("execution_error"):
            print(f"  ❌ 执行失败: {result.get('execution_error')}")
            results.append({"question": question, "passed": False, "reason": "执行失败"})
            continue

        # 3. 结果对比(语义正确性)
        try:
            expected_rows = _execute_sql(expected_sql)
            actual_rows = _execute_sql(generated_sql)
            result_match = expected_rows == actual_rows
        except Exception as e:
            result_match = False
            print(f"  ⚠️ 对比执行异常: {e}")

        # 4. SQL 文本对比(写法正确性)
        sql_match = _normalize_sql(generated_sql) == _normalize_sql(expected_sql)

        # 5. 判定:结果一致 = 通过(语义对);结果不一致但 SQL 文本一致 = 通过
        is_passed = result_match or sql_match
        if is_passed:
            passed += 1
            print(f"  ✅ 通过 (耗时 {elapsed:.1f}s)")
            if not result_match:
                print(f"     (结果不一致但 SQL 文本一致)")
        else:
            print(f"  ❌ 结果不一致 (耗时 {elapsed:.1f}s)")
            print(f"     期望: {expected_sql[:100]}...")
            print(f"     生成: {generated_sql[:100]}...")

        results.append({
            "question": question,
            "passed": is_passed,
            "reason": "" if is_passed else "结果不一致",
            "generated_sql": generated_sql,
            "elapsed": elapsed,
        })

    # ---------- 汇总 ----------
    print("\n" + "=" * 70)
    print("评测结果汇总")
    print("=" * 70)
    print(f"总题数: {total}")
    print(f"通过: {passed}")
    print(f"失败: {total - passed}")
    print(f"准确率: {passed / total * 100:.1f}%")
    print(f"平均耗时: {sum(r['elapsed'] for r in results) / total:.1f}s")

    # 失败明细
    failed = [r for r in results if not r["passed"]]
    if failed:
        print("\n失败明细:")
        for r in failed:
            print(f"  ❌ {r['question']}: {r['reason']}")

    return {
        "total": total,
        "passed": passed,
        "accuracy": passed / total,
        "results": results,
    }


if __name__ == "__main__":
    evaluate()