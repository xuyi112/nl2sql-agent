"""
Schema RAG:表结构向量化检索
============================
第 3 步的核心模块,职责:

1. 从 SQLite 提取三张表的 DDL(建表语句)
2. 拼接人工维护的字段中文注释(业务知识)
3. 把 DDL / 业务文档 / 问题-SQL 对 向量化存入 ChromaDB
4. 用户提问时按语义相似度检索相关知识
5. auto_train:执行成功的 (问题, SQL) 自动入库,系统越用越聪明
"""
import hashlib
import sqlite3
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

# 数据库路径:agent/ 的上一级目录 data/ecommerce.db
DB_PATH = Path(__file__).parent.parent / "data" / "ecommerce.db"

# ChromaDB 持久化目录(向量库存这里)
CHROMA_DIR = Path(__file__).parent.parent / "chroma_data"

# 本地嵌入模型:免费、离线可用(中文场景可换 BAAI/bge-small-zh-v1.5)
EMBED_MODEL = "all-MiniLM-L6-v2"

# 字段中文注释:人工维护的"业务知识"
# 结构:{表名: {列名: 中文说明}}
FIELD_COMMENTS = {
    "products": {
        "id": "商品唯一ID(主键)",
        "name": "商品名称",
        "category": "商品类目(数码/家电/家居/服饰/食品/美妆/运动/图书)",
        "price": "商品单价(元)",
    },
    "regions": {
        "id": "地区唯一ID(主键)",
        "name": "地区名称(华东/华北/华南/西南/东北/西北/华中)",
    },
    "orders": {
        "id": "订单唯一ID(主键)",
        "order_no": "订单号(唯一,如 DD000001)",
        "product_id": "外键,关联 products.id,表示买了哪个商品",
        "region_id": "外键,关联 regions.id,表示哪个地区下的单",
        "quantity": "购买数量(1~5)",
        "amount": "订单金额(元)= 商品单价 × 数量",
        "order_date": "下单日期(ISO 格式 YYYY-MM-DD)",
    },
}


def extract_ddl() -> list[str]:
    """从 SQLite 提取每张表的 DDL,并附上字段中文注释。

    返回: ["CREATE TABLE products ...;", "CREATE TABLE orders ...;", ...]
    """
    ddl_list = []
    conn = sqlite3.connect(DB_PATH)
    try:
        # 从系统表 sqlite_master 拿每张表的原始建表语句
        cur = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        for table_name, create_sql in cur.fetchall():
            # 在原始 DDL 后面追加"字段中文注释"小节
            comments = FIELD_COMMENTS.get(table_name, {})
            if comments:
                comment_lines = "\n".join(
                    f"  -- {col}: {desc}" for col, desc in comments.items()
                )
                create_sql = f"{create_sql}\n字段注释:\n{comment_lines}"
            ddl_list.append(create_sql)
    finally:
        conn.close()
    return ddl_list


# 业务文档:人工维护的"业务术语 → SQL 规则"映射
# 结构:{问题关键词/术语: 解释}
BUSINESS_DOCS = {
    "销售额": "销售额 = SUM(orders.amount),amount 是订单金额(元),已含单价×数量,不要用 SUM(price)",
    "上月": "上月 = 上一自然月。判断上月时,以【当前日期】为准计算:上月的第一天到上月的最后一天。查询上月数据必须在 WHERE 里加 order_date 的日期范围过滤",
    "上季度": "上季度 = 上一自然季度。判断上季度时,以【当前日期】为准计算:上季度的第一天到上季度的最后一天。查询上季度数据必须在 WHERE 里加 order_date 的日期范围过滤",
    "同比": "同比 = 与去年同月/同季度对比",
    "环比": "环比 = 与上一月/上一季度对比",
    "销售额Top": "销售额TopN = 按 SUM(amount) 降序取前 N 条,用 ORDER BY ... DESC LIMIT N",
    "订单量": "订单量 = COUNT(*) 或 COUNT(orders.id)",
    "客单价": "客单价 = SUM(amount) / COUNT(*),即总金额除以订单数",
    "平均价格": "商品平均价格 = AVG(products.price),直接对商品表的价格列求平均,不要用订单表的 amount 计算",
    "商品数量": "商品数量 = COUNT(*) FROM products,统计商品表行数",
    "价格区间": "价格区间查询用 products.price 的 BETWEEN 或 > / < 比较",
}

# 问题-SQL 对:人工精选的 few-shot 示例(每类问题 1 条)
# 结构:[(问题, 正确SQL), ...]
QUESTION_SQL_PAIRS = [
    (
        "华东区上月销售额Top3商品",
        """SELECT p.name, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           JOIN regions r ON o.region_id = r.id
           WHERE r.name = '华东'
             AND o.order_date >= '2026-08-01'
             AND o.order_date <= '2026-08-31'
           GROUP BY p.name
           ORDER BY sales DESC
           LIMIT 3""",
    ),
    (
        "全国各区域订单量",
        """SELECT r.name, COUNT(*) AS order_count
           FROM orders o
           JOIN regions r ON o.region_id = r.id
           GROUP BY r.name
           ORDER BY order_count DESC""",
    ),
    (
        "上季度各品类销售额占比",
        """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-04-01'
             AND o.order_date <= '2026-06-30'
           GROUP BY p.category
           ORDER BY sales DESC""",
    ),
    (
        "上季度各品类销售额",
        """SELECT p.category, SUM(o.amount) AS sales
           FROM orders o
           JOIN products p ON o.product_id = p.id
           WHERE o.order_date >= '2026-04-01'
             AND o.order_date <= '2026-06-30'
           GROUP BY p.category
           ORDER BY sales DESC""",
    ),
]


def get_collection(name: str):
    """获取(或创建)一个 ChromaDB 集合。

    返回: collection 对象,可对它 add / query。
    """
    # PersistentClient:向量数据持久化到磁盘,重启不丢
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    # 指定嵌入模型:ChromaDB 会自动用它对文本做向量化
    ef = embedding_functions.DefaultEmbeddingFunction()
    return client.get_or_create_collection(name=name, embedding_function=ef)


def index_ddl():
    """把 3 条 DDL 文本向量化入库。"""
    ddl_list = extract_ddl()
    collection = get_collection("ddl")
    collection.upsert(
        ids=[f"ddl_{i}" for i in range(len(ddl_list))],
        documents=ddl_list,
        metadatas=[{"type": "ddl", "idx": i} for i in range(len(ddl_list))],
    )
    print(f"✅ 已索引 {len(ddl_list)} 条 DDL")


def index_business_docs():
    """把业务文档向量化入库。"""
    docs = list(BUSINESS_DOCS.items())
    collection = get_collection("documents")
    collection.upsert(
        ids=[f"doc_{i}" for i in range(len(docs))],
        documents=[f"{k}: {v}" for k, v in docs],  # 拼成"术语: 解释"
        metadatas=[{"type": "doc", "term": k} for k, _ in docs],
    )
    print(f"✅ 已索引 {len(docs)} 条业务文档")


def index_question_sql():
    """把 问题-SQL 对 向量化入库。"""
    collection = get_collection("question_sql")
    collection.upsert(
        ids=[f"qs_{i}" for i in range(len(QUESTION_SQL_PAIRS))],
        documents=[q for q, _ in QUESTION_SQL_PAIRS],  # 只对"问题"做向量
        metadatas=[
            {"type": "question_sql", "sql": s, "source": "manual"} for _, s in QUESTION_SQL_PAIRS
        ],  # SQL 存到元数据,检索到时再取出来
    )
    print(f"✅ 已索引 {len(QUESTION_SQL_PAIRS)} 条 问题-SQL 对")


def _query_collection(name: str, question: str, top_k: int) -> list[dict]:
    """从一个集合里检索与问题最相关的 top_k 条。

    返回: [{"id":..., "document":..., "metadata":..., "distance":...}, ...]
    """
    collection = get_collection(name)
    result = collection.query(
        query_texts=[question],   # 查询文本(自动转向量)
        n_results=top_k,          # 返回最相似的 top_k 条
    )
    # ChromaDB 返回结构:ids / documents / metadatas / distances
    # 每个都是"外层列表套内层列表"(因为支持批量查询,我们只查 1 条)
    items = []
    for i, doc_id in enumerate(result["ids"][0]):
        items.append({
            "id": doc_id,
            "document": result["documents"][0][i],
            "metadata": result["metadatas"][0][i],
            "distance": result["distances"][0][i],
        })
    return items


# ---------- auto_train:自动学习 ----------

# 自动入库的上限:防止知识库无限膨胀(人工示例 + 自动示例)
MAX_AUTO_PAIRS = 100


def add_question_sql_pair(question: str, sql: str) -> bool:
    """auto_train:把执行成功的 (问题, SQL) 对自动写入 few-shot 集合。

    设计要点:
    - 去重:id 用问题文本的 MD5 哈希,相同问题重复入库会覆盖而不是新增
    - 上限:超过 MAX_AUTO_PAIRS 条自动示例后不再入库(防膨胀)
    - 标记:metadata 里 source="auto",与人工示例(source="manual")区分

    返回: True=已入库, False=跳过(超上限/参数无效)
    """
    question = (question or "").strip()
    sql = (sql or "").strip()
    if not question or not sql:
        return False

    collection = get_collection("question_sql")

    # 1. 上限检查:统计已有自动示例数量
    existing = collection.get(where={"source": "auto"})
    if len(existing["ids"]) >= MAX_AUTO_PAIRS:
        print(f"⏭️  auto_train 跳过:自动示例已达上限 {MAX_AUTO_PAIRS} 条")
        return False

    # 2. 去重入库:相同问题 → 相同 id → upsert 覆盖
    pair_id = f"auto_{hashlib.md5(question.encode('utf-8')).hexdigest()[:8]}"
    collection.upsert(
        ids=[pair_id],
        documents=[question],
        metadatas=[{"type": "question_sql", "sql": sql, "source": "auto"}],
    )
    print(f"🧠 auto_train 已学习: {question}")
    return True


def retrieve(question: str, top_k: int = 2) -> dict:
    """Schema RAG 检索:从三类知识各取最相关的 top_k 条,拼成上下文。

    返回: {
        "ddl_text":     "...",   # 相关表结构
        "doc_text":     "...",   # 相关业务文档
        "few_shot_text":"...",   # 相关问题-SQL 示例
    }
    """
    # 1. 检索 DDL(表结构)——取最相关的 2 条
    ddl_hits = _query_collection("ddl", question, top_k)
    ddl_text = "\n\n".join(h["document"] for h in ddl_hits)

    # 2. 检索业务文档(术语规则)
    doc_hits = _query_collection("documents", question, top_k)
    doc_text = "\n".join(h["document"] for h in doc_hits)

    # 3. 检索问题-SQL 对(few-shot 示例)
    qs_hits = _query_collection("question_sql", question, top_k)
    # SQL 存在 metadata["sql"] 里,取出来拼成"问题 + SQL"的示例
    few_shot_text = "\n\n".join(
        f"示例问题:{h['document']}\n示例SQL:\n{h['metadata']['sql']}"
        for h in qs_hits
    )

    return {
        "ddl_text": ddl_text,
        "doc_text": doc_text,
        "few_shot_text": few_shot_text,
    }


if __name__ == "__main__":
    # 一键索引三类知识
    index_ddl()
    index_business_docs()
    index_question_sql()
    print("🎉 向量库构建完成")

    # 演示:用真实问题测试检索效果
    print("\n" + "=" * 60)
    print("🔍 检索演示:华东区上月销售额Top3商品")
    print("=" * 60)
    ctx = retrieve("华东区上月销售额Top3商品")
    print("\n--- 命中的 DDL ---")
    print(ctx["ddl_text"][:300])
    print("\n--- 命中的业务文档 ---")
    print(ctx["doc_text"])
    print("\n--- 命中的 few-shot 示例 ---")
    print(ctx["few_shot_text"][:300])
