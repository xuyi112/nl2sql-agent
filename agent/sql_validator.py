"""
SQL 安全审核模块(第 1 层:规则审核)
===================================
纯代码检查,零 LLM 成本,负责拦截"危险/明显错误"的 SQL:

  ① 只读检查:拒绝 DELETE / UPDATE / INSERT / DROP / ALTER 等写操作
  ② 语法检查:SQL 必须能被 sqlite3 解析
  ③ 表名/列名白名单:只能引用数据库中真实存在的表和列
"""
import re
import sqlite3
from pathlib import Path

# 数据库路径(和 schema_rag.py 一致):agent/ 的上一级 data/ecommerce.db
DB_PATH = Path(__file__).parent.parent / "data" / "ecommerce.db"

# 危险操作关键词:命中任何一个 → 直接拒绝
# 用正则 \b 词边界,避免误伤 "UPDATE" 出现在注释/字符串里(简单版)
DANGEROUS_KEYWORDS = [
    r"\bDELETE\b", r"\bUPDATE\b", r"\bINSERT\b", r"\bDROP\b",
    r"\bALTER\b", r"\bCREATE\b", r"\bTRUNCATE\b", r"\bREPLACE\b",
    r"\bGRANT\b", r"\bATTACH\b", r"\bPRAGMA\b",
]


def _get_known_names() -> tuple[set, set]:
    """从数据库读取所有真实的表名和列名,用于白名单检查。

    返回: (表名集合, 列名集合)
    """
    tables, columns = set(), set()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = {row[0] for row in cur.fetchall()}
        for table in tables:
            cur = conn.execute(f'PRAGMA table_info("{table}")')
            columns.update(row[1] for row in cur.fetchall())
    finally:
        conn.close()
    return tables, columns


def validate_sql_rules(sql: str) -> dict:
    """规则审核:检查 SQL 是否安全、合法、可执行。

    参数: LLM 生成的 SQL 文本
    返回: {
        "passed": bool,          # 是否通过
        "errors": list[str],     # 错误信息(空 = 通过)
    }
    """
    errors = []



    # ---------- 检查 1:只读检查 ----------
    for pattern in DANGEROUS_KEYWORDS:
        if re.search(pattern, sql, re.IGNORECASE):
            errors.append(f"检测到危险操作 {pattern.strip('\\b')},系统只允许 SELECT 查询")
            # 一旦发现危险操作,直接返回(不再继续检查,避免浪费)
            return {"passed": False, "errors": errors}

    # ---------- 检查 2:语法检查 ----------
    try:
        conn = sqlite3.connect(DB_PATH)
        # EXPLAIN 不真正执行查询,只让数据库"解析"SQL,语法错会抛异常
        conn.execute(f"EXPLAIN {sql}")
        conn.close()
    except sqlite3.Error as e:
        errors.append(f"SQL 语法错误: {e}")
        return {"passed": False, "errors": errors}

    # ---------- 检查 3:表名/列名白名单 ----------
    tables, columns = _get_known_names()
    # 粗粒度检查:提取 SQL 中出现的所有"疑似表名"(FROM/JOIN 后面跟的词)
    table_refs = re.findall(r"(?:FROM|JOIN)\s+([a-zA-Z_]\w*)", sql, re.IGNORECASE)
    for t in table_refs:
        if t not in tables:
            errors.append(f"表 {t} 不存在,请参考表结构中的表名")

    # 粗粒度检查:提取所有"疑似列名"(SELECT/GROUP BY/ORDER BY/WHERE 后)
    # 注意:这是简化版,不处理别名/子查询,但足够拦截常见幻觉列名
    col_refs = re.findall(r"([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)", sql)
    for table, col in col_refs:
        if col not in columns:
            errors.append(f"列 {table}.{col} 不存在,请参考表结构中的列名")

    if errors:
        return {"passed": False, "errors": errors}
    return {"passed": True, "errors": []}


if __name__ == "__main__":
    # 自测:验证审核功能
    test_cases = [
        # (描述, SQL, 期望结果)
        ("正常查询", "SELECT * FROM orders LIMIT 5", True),
        ("危险操作", "DELETE FROM orders", False),
        ("语法错误", "SELECT FROM orders", False),
        ("不存在的表", "SELECT * FROM users", False),
        ("不存在的列", "SELECT amount, fake_col FROM orders", False),
    ]
    print("=" * 60)
    print("规则审核自测")
    print("=" * 60)
    for desc, sql, expected in test_cases:
        result = validate_sql_rules(sql)
        mark = "✅" if result["passed"] == expected else "❌"
        print(f"{mark} [{desc}] passed={result['passed']} (期望 {expected})")
        if result["errors"]:
            print(f"   错误: {result['errors']}")
