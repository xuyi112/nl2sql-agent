"""
结果缓存模块:相同问题直接返回上次结果,省 API 费用
====================================================
设计要点:
  1. 规范化 key:问题文本去空白/小写 → MD5,相同问题命中同一缓存
  2. 相对时间问题不缓存:"上月/上季度/今天"等答案随时间变化,
     缓存会返回过期数据(9月问"上月"缓存了8月,10月再问应返回9月)
  3. 数据版本检测:缓存条目存数据版本号(PRAGMA data_version),
     读取时比对——数据变了(增/删/改)版本号变 → 缓存自动失效
  4. TTL 过期:缓存有效期 1 小时,超时自动失效
  5. 内存缓存:单进程够用(生产多进程可换 Redis,接口不变)
"""
import hashlib
import re
import sqlite3
import time
from pathlib import Path

# 数据库路径(和 schema_rag.py 一致)
DB_PATH = Path(__file__).parent.parent / "data" / "ecommerce.db"

# 相对时间关键词:命中任何一个 → 不缓存(答案随时间变化)
RELATIVE_TIME_KEYWORDS = [
    "上月", "上季度", "本月", "本季度", "今天", "昨天", "前天",
    "最近", "近7天", "近30天", "近一周", "近一月", "同比", "环比",
]

# 缓存有效期(秒):1 小时
CACHE_TTL = 3600

# 内存缓存:{key: {"ts": 写入时间, "data_version": 数据版本, "result": 结果}}
_cache: dict[str, dict] = {}


def _get_data_version() -> str:
    """数据版本号:数据指纹(覆盖全部 3 张表)。

    任何数据变更(增/删/改)都会改变指纹:
    - orders: COUNT + MAX(order_date) + SUM(amount)
    - products: COUNT + MAX(price)
    - regions: COUNT

    注:不用 PRAGMA data_version——它是连接级缓存,跨连接不可靠
    (新连接读到的值可能滞后,Windows 上尤其明显)。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        orders = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(order_date), ''), "
            "COALESCE(SUM(amount), 0) FROM orders"
        ).fetchone()
        products = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(price), 0) FROM products"
        ).fetchone()
        regions = conn.execute("SELECT COUNT(*) FROM regions").fetchone()
        return (
            f"o:{orders[0]}_{orders[1]}_{orders[2]}"
            f"|p:{products[0]}_{products[1]}"
            f"|r:{regions[0]}"
        )
    finally:
        conn.close()


def _make_key(question: str) -> str:
    """规范化问题文本,生成缓存 key。

    删除所有空白 + 小写 → MD5。
    这样"华东区 上月 销售额"和"华东区上月销售额"命中同一缓存。
    """
    normalized = re.sub(r"\s+", "", question.strip().lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _is_relative_time(question: str) -> bool:
    """判断问题是否含相对时间词(答案随时间变化,不缓存)。"""
    return any(kw in question for kw in RELATIVE_TIME_KEYWORDS)


def get_cached(question: str) -> dict | None:
    """取缓存:命中 + 数据版本一致 + 未过期 → 返回结果,否则 None。

    数据版本检测:缓存里存的版本号 vs 当前版本号,
    不一致说明数据已变更 → 缓存失效(返回 None,重新生成)。
    """
    if _is_relative_time(question):
        return None
    key = _make_key(question)
    entry = _cache.get(key)
    if not entry:
        return None
    # 数据版本检测:数据变了 → 缓存失效
    if entry["data_version"] != _get_data_version():
        return None
    # TTL 检测:过期 → 缓存失效
    if time.time() - entry["ts"] >= CACHE_TTL:
        return None
    return entry["result"]


def set_cached(question: str, result: dict) -> None:
    """写入缓存:存当前数据版本号(相对时间问题跳过)。"""
    if _is_relative_time(question):
        return
    key = _make_key(question)
    _cache[key] = {
        "ts": time.time(),
        "data_version": _get_data_version(),
        "result": result,
    }


def clear_cache() -> None:
    """清空缓存(测试/调试用)。"""
    _cache.clear()