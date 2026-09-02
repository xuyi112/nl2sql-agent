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
from schema_rag import get_collection

DB_PATH = Path(__file__).parent.parent / "data" / "ecommerce.db"

# ---------- 评测前清理:清空 auto_train 自动示例 ----------
# 多次评测会让自动示例堆积(最多100条),检索时可能命中不相关示例干扰 LLM,
# 评测场景应使用干净的人工示例知识库。

def _clear_auto_examples():
    """清空 auto_train 自动入库的示例(保留人工示例)。"""
    try:
        col = get_collection("question_sql")
        auto = col.get(where={"source": "auto"})
        if auto["ids"]:
            col.delete(ids=auto["ids"])
            print(f"🧹 已清空 {len(auto['ids'])} 条 auto_train 自动示例")
    except Exception as e:
        print(f"⚠️ 清理 auto 示例失败: {e}")

# ---------- 测试集:问题 + 期望 SQL ----------
# 覆盖:单表/多表 JOIN/聚合/日期过滤/TopN/分组/排序/条件过滤/组合场景
# 日期基准:当前 2026-09,上月=2026-08,上季度=2026-04~06
TEST_CASES = [
    # ========== 商品类(products 单表) ==========
    {
        "question": "最贵的5个商品",
        "expected_sql": """SELECT name, price FROM products ORDER BY price DESC LIMIT 5""",
    },
    {
        "question": "最便宜的3个商品",
        "expected_sql": """SELECT name, price FROM products ORDER BY price ASC LIMIT 3""",
    },
    {
        "question": "数码类商品的平均价格",
        "expected_sql": """SELECT AVG(price) AS avg_price FROM products WHERE category = '数码'""",
    },
    {
        "question": "各品类有多少个商品",
        "expected_sql": """SELECT category, COUNT(*) AS product_count FROM products GROUP BY category""",
    },
    {
        "question": "价格超过1000元的商品数量",
        "expected_sql": """SELECT COUNT(*) AS count FROM products WHERE price > 1000""",
    },
    {
        "question": "最贵的商品是什么",
        "expected_sql": """SELECT name, price FROM products ORDER BY price DESC LIMIT 1""",
    },
    {
        "question": "各品类的平均价格",
        "expected_sql": """SELECT category, AVG(price) AS avg_price FROM products GROUP BY category""",
    },
    {
        "question": "价格在100到500之间的商品",
        "expected_sql": """SELECT name, price FROM products WHERE price BETWEEN 100 AND 500 LIMIT 10""",
    },
    {
        "question": "所有商品的价格总和",
        "expected_sql": """SELECT SUM(price) AS total_price FROM products""",
    },
    {
        "question": "图书类最贵的商品",
        "expected_sql": """SELECT name, price FROM products WHERE category = '图书' ORDER BY price DESC LIMIT 1""",
    },
    {
        "question": "美妆类有多少个商品",
        "expected_sql": """SELECT COUNT(*) AS count FROM products WHERE category = '美妆'""",
    },
    {
        "question": "价格最低的商品属于哪个品类",
        "expected_sql": """SELECT category, name, price FROM products ORDER BY price ASC LIMIT 1""",
    },
    {
        "question": "食品类商品的平均价格",
        "expected_sql": """SELECT AVG(price) AS avg_price FROM products WHERE category = '食品'""",
    },
    {
        "question": "各品类价格最高的商品",
        "expected_sql": """SELECT category, MAX(price) AS max_price FROM products GROUP BY category""",
    },
    {
        "question": "运动类商品有哪些",
        "expected_sql": """SELECT name, price FROM products WHERE category = '运动' LIMIT 10""",
    },

    # ========== 订单类(orders 单表) ==========
    {
        "question": "总共有多少笔订单",
        "expected_sql": """SELECT COUNT(*) AS order_count FROM orders""",
    },
    {
        "question": "所有订单的总销售额",
        "expected_sql": """SELECT SUM(amount) AS total_sales FROM orders""",
    },
    {
        "question": "平均每笔订单金额",
        "expected_sql": """SELECT AVG(amount) AS avg_amount FROM orders""",
    },
    {
        "question": "最大的一笔订单金额",
        "expected_sql": """SELECT MAX(amount) AS max_amount FROM orders""",
    },
    {
        "question": "最小的一笔订单金额",
        "expected_sql": """SELECT MIN(amount) AS min_amount FROM orders""",
    },
    {
        "question": "8月份的订单数量",
        "expected_sql": """SELECT COUNT(*) AS order_count FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月1日有多少笔订单",
        "expected_sql": """SELECT COUNT(*) AS order_count FROM orders WHERE order_date = '2026-08-01'""",
    },
    {
        "question": "订单金额超过1000元的订单数",
        "expected_sql": """SELECT COUNT(*) AS count FROM orders WHERE amount > 1000""",
    },
    {
        "question": "平均每笔订单购买几件商品",
        "expected_sql": """SELECT AVG(quantity) AS avg_quantity FROM orders""",
    },
    {
        "question": "上个月的总销售额",
        "expected_sql": """SELECT SUM(amount) AS total_sales FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "上季度的订单数量",
        "expected_sql": """SELECT COUNT(*) AS order_count FROM orders WHERE order_date >= '2026-04-01' AND order_date <= '2026-06-30'""",
    },
    {
        "question": "2026年8月1日到8月7日的每日订单量",
        "expected_sql": """SELECT order_date, COUNT(*) AS order_count FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-07' GROUP BY order_date ORDER BY order_date""",
    },
    {
        "question": "购买数量为5的订单有多少笔",
        "expected_sql": """SELECT COUNT(*) AS count FROM orders WHERE quantity = 5""",
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

    # ========== 多表 JOIN ==========
    {
        "question": "全国各区域订单量",
        "expected_sql": """SELECT r.name, COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name
           ORDER BY order_count DESC""",
    },
    {
        "question": "各区域的销售额",
        "expected_sql": """SELECT r.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name
           ORDER BY sales DESC""",
    },
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
        "question": "各品类的总销售额",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.category
           ORDER BY sales DESC""",
    },
    {
        "question": "各地区的平均客单价",
        "expected_sql": """SELECT r.name, SUM(o.amount) / COUNT(*) AS avg_order_value
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name""",
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
    {
        "question": "销售额最高的前5个商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.name
           ORDER BY sales DESC
           LIMIT 5""",
    },
    {
        "question": "各品类的订单量",
        "expected_sql": """SELECT p.category, COUNT(*) AS order_count
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.category
           ORDER BY order_count DESC""",
    },
    {
        "question": "华东区的总销售额",
        "expected_sql": """SELECT SUM(o.amount) AS sales
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东'""",
    },
    {
        "question": "华北区有多少笔订单",
        "expected_sql": """SELECT COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华北'""",
    },
    {
        "question": "数码类商品的总销售额",
        "expected_sql": """SELECT SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE p.category = '数码'""",
    },
    {
        "question": "各区域销售额占比",
        "expected_sql": """SELECT r.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name
           ORDER BY sales DESC""",
    },
    {
        "question": "销售额最低的3个商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.name
           ORDER BY sales ASC
           LIMIT 3""",
    },
    {
        "question": "家电类商品有多少笔订单",
        "expected_sql": """SELECT COUNT(*) AS order_count
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE p.category = '家电'""",
    },
    {
        "question": "上季度各品类销售额占比",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-04-01'
             AND o.order_date <= '2026-06-30'
           GROUP BY p.category
           ORDER BY sales DESC""",
    },
    {
        "question": "运动类商品的销售额",
        "expected_sql": """SELECT SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE p.category = '运动'""",
    },
    {
        "question": "华东区各品类的销售额",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东'
           GROUP BY p.category
           ORDER BY sales DESC""",
    },
    {
        "question": "2026年8月各区域的销售额",
        "expected_sql": """SELECT r.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'
           GROUP BY r.name
           ORDER BY sales DESC""",
    },
    {
        "question": "上个月订单量最多的商品",
        "expected_sql": """SELECT p.name, COUNT(*) AS order_count
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'
           GROUP BY p.name
           ORDER BY order_count DESC
           LIMIT 1""",
    },
    {
        "question": "各品类的平均客单价",
        "expected_sql": """SELECT p.category, SUM(o.amount) / COUNT(*) AS avg_order_value
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.category""",
    },
    {
        "question": "销售额Top10商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.name
           ORDER BY sales DESC
           LIMIT 10""",
    },
    {
        "question": "华东区有多少笔订单",
        "expected_sql": """SELECT COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东'""",
    },
    {
        "question": "华南区上个月的销售额",
        "expected_sql": """SELECT SUM(o.amount) AS sales
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华南'
             AND o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'""",
    },
    {
        "question": "服饰类商品的平均价格",
        "expected_sql": """SELECT AVG(price) AS avg_price FROM products WHERE category = '服饰'""",
    },
    {
        "question": "上季度销售额最高的商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-04-01'
             AND o.order_date <= '2026-06-30'
           GROUP BY p.name
           ORDER BY sales DESC
           LIMIT 1""",
    },
    {
        "question": "各区域的平均订单金额",
        "expected_sql": """SELECT r.name, AVG(o.amount) AS avg_amount
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name""",
    },
    {
        "question": "家居类商品有多少个",
        "expected_sql": """SELECT COUNT(*) AS count FROM products WHERE category = '家居'""",
    },
    {
        "question": "上个月各区域的订单量",
        "expected_sql": """SELECT r.name, COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'
           GROUP BY r.name
           ORDER BY order_count DESC""",
    },
    {
        "question": "订单量最少的3个商品",
        "expected_sql": """SELECT p.name, COUNT(*) AS order_count
           FROM orders o
           JOIN products p ON o.product_id = p.id
           GROUP BY p.name
           ORDER BY order_count ASC
           LIMIT 3""",
    },

    # ========== 补充 41 条(凑满 100) ==========

    # --- 商品单表补充 ---
    {
        "question": "价格在500到1000之间的商品数量",
        "expected_sql": """SELECT COUNT(*) AS count FROM products WHERE price BETWEEN 500 AND 1000""",
    },
    {
        "question": "价格超过2000的商品有哪些",
        "expected_sql": """SELECT name, price FROM products WHERE price > 2000 ORDER BY price DESC LIMIT 10""",
    },
    {
        "question": "价格低于50的商品",
        "expected_sql": """SELECT name, price FROM products WHERE price < 50 ORDER BY price ASC LIMIT 10""",
    },
    {
        "question": "各品类商品数量占比",
        "expected_sql": """SELECT category, COUNT(*) AS product_count FROM products GROUP BY category ORDER BY product_count DESC""",
    },
    {
        "question": "数码类最便宜的商品",
        "expected_sql": """SELECT name, price FROM products WHERE category = '数码' ORDER BY price ASC LIMIT 1""",
    },
    {
        "question": "家电类最贵的商品",
        "expected_sql": """SELECT name, price FROM products WHERE category = '家电' ORDER BY price DESC LIMIT 1""",
    },
    {
        "question": "价格第二贵的商品",
        "expected_sql": """SELECT name, price FROM products ORDER BY price DESC LIMIT 1 OFFSET 1""",
    },
    {
        "question": "图书类有多少个商品",
        "expected_sql": """SELECT COUNT(*) AS count FROM products WHERE category = '图书'""",
    },
    {
        "question": "美妆类最贵的商品",
        "expected_sql": """SELECT name, price FROM products WHERE category = '美妆' ORDER BY price DESC LIMIT 1""",
    },
    {
        "question": "价格在1000元以上的商品平均价格",
        "expected_sql": """SELECT AVG(price) AS avg_price FROM products WHERE price > 1000""",
    },

    # --- 订单单表补充 ---
    {
        "question": "2026年8月的总订单金额",
        "expected_sql": """SELECT SUM(amount) AS total_sales FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月1日的订单金额",
        "expected_sql": """SELECT SUM(amount) AS total_sales FROM orders WHERE order_date = '2026-08-01'""",
    },
    {
        "question": "2026年8月有多少天有订单",
        "expected_sql": """SELECT COUNT(DISTINCT order_date) AS days FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "金额在500到1000之间的订单数",
        "expected_sql": """SELECT COUNT(*) AS count FROM orders WHERE amount BETWEEN 500 AND 1000""",
    },
    {
        "question": "购买数量为1的订单总金额",
        "expected_sql": """SELECT SUM(amount) AS total_sales FROM orders WHERE quantity = 1""",
    },
    {
        "question": "2026年8月平均每笔订单金额",
        "expected_sql": """SELECT AVG(amount) AS avg_amount FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月最大订单金额",
        "expected_sql": """SELECT MAX(amount) AS max_amount FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月最小订单金额",
        "expected_sql": """SELECT MIN(amount) AS min_amount FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月购买数量总和",
        "expected_sql": """SELECT SUM(quantity) AS total_quantity FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月订单金额超过500的订单数",
        "expected_sql": """SELECT COUNT(*) AS count FROM orders WHERE order_date >= '2026-08-01' AND order_date <= '2026-08-31' AND amount > 500""",
    },

    # --- 多表 JOIN 补充 ---
    {
        "question": "各区域2026年8月的销售额",
        "expected_sql": """SELECT r.name, SUM(o.amount) AS sales
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY r.name ORDER BY sales DESC""",
    },
    {
        "question": "各品类2026年8月的销售额",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY sales DESC""",
    },
    {
        "question": "华东区2026年8月的订单量",
        "expected_sql": """SELECT COUNT(*) AS order_count
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东' AND o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'""",
    },
    {
        "question": "华北区销售额最高的商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华北'
           GROUP BY p.name ORDER BY sales DESC LIMIT 1""",
    },
    {
        "question": "各区域2026年8月的平均客单价",
        "expected_sql": """SELECT r.name, SUM(o.amount) / COUNT(*) AS avg_order_value
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY r.name""",
    },
    {
        "question": "2026年8月各品类的订单量",
        "expected_sql": """SELECT p.category, COUNT(*) AS order_count
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY order_count DESC""",
    },
    {
        "question": "华南区2026年8月销售额最高的商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华南' AND o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.name ORDER BY sales DESC LIMIT 1""",
    },
    {
        "question": "各区域2026年8月的订单量",
        "expected_sql": """SELECT r.name, COUNT(*) AS order_count
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY r.name ORDER BY order_count DESC""",
    },
    {
        "question": "2026年8月销售额最高的品类",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY sales DESC LIMIT 1""",
    },
    {
        "question": "华东区2026年8月各品类的销售额",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东' AND o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY sales DESC""",
    },
    {
        "question": "2026年8月订单量最多的商品",
        "expected_sql": """SELECT p.name, COUNT(*) AS order_count
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.name ORDER BY order_count DESC LIMIT 1""",
    },
    {
        "question": "各区域2026年8月的订单金额占比",
        "expected_sql": """SELECT r.name, SUM(o.amount) AS sales
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY r.name ORDER BY sales DESC""",
    },
    {
        "question": "2026年8月各品类的平均客单价",
        "expected_sql": """SELECT p.category, SUM(o.amount) / COUNT(*) AS avg_order_value
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category""",
    },
    {
        "question": "华东区2026年8月销售额最高的商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东' AND o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.name ORDER BY sales DESC LIMIT 1""",
    },
    {
        "question": "2026年8月各区域订单量占比",
        "expected_sql": """SELECT r.name, COUNT(*) AS order_count
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY r.name ORDER BY order_count DESC""",
    },
    {
        "question": "2026年8月销售额最低的品类",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY sales ASC LIMIT 1""",
    },
    {
        "question": "华北区2026年8月的销售额",
        "expected_sql": """SELECT SUM(o.amount) AS sales
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华北' AND o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'""",
    },
    {
        "question": "2026年8月各品类销售额Top5",
        "expected_sql": """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY sales DESC LIMIT 5""",
    },
    {
        "question": "华东区2026年8月订单量最多的商品",
        "expected_sql": """SELECT p.name, COUNT(*) AS order_count
           FROM orders o JOIN products p ON o.product_id = p.id JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东' AND o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.name ORDER BY order_count DESC LIMIT 1""",
    },
    {
        "question": "2026年8月各区域销售额Top3",
        "expected_sql": """SELECT r.name, SUM(o.amount) AS sales
           FROM orders o JOIN regions r ON o.region_id = r.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY r.name ORDER BY sales DESC LIMIT 3""",
    },
    {
        "question": "2026年8月各品类订单量Top3",
        "expected_sql": """SELECT p.category, COUNT(*) AS order_count
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.category ORDER BY order_count DESC LIMIT 3""",
    },
    {
        "question": "2026年8月销售额最高的商品",
        "expected_sql": """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-08-01' AND o.order_date <= '2026-08-31'
           GROUP BY p.name ORDER BY sales DESC LIMIT 1""",
    },
]


# ---------- 评测工具 ----------

def _execute_sql(sql: str) -> tuple[list, list]:
    """执行 SQL,返回 (列名, 结果行)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        return columns, rows
    finally:
        conn.close()


def _rows_to_dicts(columns: list, rows: list) -> list[dict]:
    """把 (列名, 行) 转成 dict 列表,用于语义对比(忽略列顺序)。"""
    return [dict(zip(columns, row)) for row in rows]


def _compare_semantic(expected_cols, expected_rows, actual_cols, actual_rows) -> bool:
    """语义对比:忽略列名和列顺序,只比较值。

    规则:期望的每一行,其所有值都能在生成的某一行中找到(子集匹配)。
    例:期望 (name, price) 生成 (id, name, price) → 生成行包含期望值 → 通过
    例:期望 (order_count, 50000) 生成 (total_orders, 50000) → 值相同 → 通过
    值统一转字符串,避免 int/str 混合类型比较报错。
    """
    def _norm_row(row):
        return set(str(v) for v in row)

    actual_norm = [_norm_row(row) for row in actual_rows]
    for e_row in expected_rows:
        e_set = _norm_row(e_row)
        # 期望行的所有值必须能在某个生成行中找到
        if not any(e_set.issubset(a_set) for a_set in actual_norm):
            return False
    return True


def _normalize_sql(sql: str) -> str:
    """SQL 规范化:去空白/大小写,用于文本对比。"""
    return " ".join(sql.lower().split())


def evaluate() -> dict:
    """跑完整评测,返回统计结果。"""
    # 评测前清空 auto 示例,避免污染
    _clear_auto_examples()

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

        # 1. 跑完整链路(评测时关闭 auto_train,避免污染知识库)
        result = run_agent(question, enable_auto_train=False)
        elapsed = time.time() - start
        generated_sql = result.get("generated_sql", "")

        # 2. 检查是否通过审核并执行成功
        if not result.get("validation_passed"):
            print(f"  ❌ 审核未通过: {result.get('validation_errors')}")
            results.append({"question": question, "passed": False, "reason": "审核未通过", "elapsed": elapsed})
            continue
        if result.get("execution_error"):
            print(f"  ❌ 执行失败: {result.get('execution_error')}")
            results.append({"question": question, "passed": False, "reason": "执行失败", "elapsed": elapsed})
            continue

        # 3. 结果对比(语义正确性,忽略列顺序)
        try:
            expected_cols, expected_rows = _execute_sql(expected_sql)
            actual_cols, actual_rows = _execute_sql(generated_sql)
            result_match = _compare_semantic(expected_cols, expected_rows, actual_cols, actual_rows)
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