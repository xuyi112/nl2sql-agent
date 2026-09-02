"""
电商销售演示数据生成脚本
========================
生成 3 张表(products / regions / orders)写入 SQLite 数据库:

  products  商品表   100 个商品,8 个类目
  regions   地区表   7 个地理分区
  orders    订单表   5 万笔订单,时间跨度最近 2 年

用法:
  python data/gen_data.py

产物:
  data/ecommerce.db  (SQLite 数据库文件)
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# ---------- 1. 常量配置 ----------

# 固定随机种子:保证每次生成的数据完全一样(可复现,演示稳定)
random.seed(42)

DB_PATH = Path(__file__).parent / "ecommerce.db"

PRODUCT_COUNT = 100     # 商品数量
REGION_COUNT = 7        # 地区数量
ORDER_COUNT = 50_000    # 订单数量

# 商品池:每个类目 8~15 个商品名(名字贴近真实,方便 LLM 理解)
CATEGORIES = {
    "数码": ["无线蓝牙耳机", "智能手表", "便携充电宝", "机械键盘", "无线鼠标",
            "USB-C 扩展坞", "运动相机", "智能音箱", "4K 显示器", "电竞耳机",
            "路由器", "平板电脑"],
    "家电": ["空气净化器", "扫地机器人", "电饭煲", "破壁机", "电热水壶",
            "吸尘器", "加湿器", "微波炉", "咖啡机", "电风扇"],
    "家居": ["记忆棉枕头", "四件套床品", "落地灯", "懒人沙发", "香薰机",
            "收纳箱", "窗帘", "地毯", "置物架", "垃圾桶"],
    "服饰": ["纯棉T恤", "牛仔裤", "运动外套", "羽绒服", "连衣裙",
            "卫衣", "休闲鞋", "棒球帽", "围巾", "袜子礼盒"],
    "食品": ["坚果礼盒", "咖啡豆", "燕麦片", "巧克力", "茶叶礼盒",
            "蜂蜜", "辣条大礼包", "即食鸡胸肉", "苏打饼干", "果干"],
    "美妆": ["保湿面霜", "防晒霜", "口红", "精华液", "面膜",
            "卸妆水", "洗发水", "身体乳", "香水", "粉底液"],
    "运动": ["瑜伽垫", "哑铃", "跳绳", "跑步机", "动感单车",
            "羽毛球拍", "篮球", "运动水壶", "护膝", "筋膜枪"],
    "图书": ["Python编程入门", "SQL必知必会", "深度学习", "经济学原理",
            "时间简史", "三体全集", "人类简史", "非暴力沟通", "原则", "活着"],
}

# 7 大地理分区
REGIONS = ["华东", "华北", "华南", "西南", "东北", "西北", "华中"]

# 日期范围:2024-08-01 ~ 2026-08-11(最近 2 年)
START_DATE = (2024, 8, 1)
END_DATE = (2026, 8, 11)


# ---------- 2. 建表 ----------

def create_tables(conn: sqlite3.Connection) -> None:
    """创建 3 张表。

    关键点:
    - 外键:orders.product_id -> products.id,orders.region_id -> regions.id
      这强制"订单必须关联真实存在的商品/地区",保证数据完整性
    - 索引:orders 表上的 3 个索引,让按地区/商品/日期过滤的查询变快
      5 万行不建索引也能跑,但这是真实工程的标配做法
    """
    conn.executescript("""
        -- 商品表
        CREATE TABLE IF NOT EXISTS products (
            id       INTEGER PRIMARY KEY,      -- 主键:自增整数
            name     TEXT    NOT NULL,         -- 商品名
            category TEXT    NOT NULL,         -- 类目
            price    REAL    NOT NULL          -- 单价(元)
        );

        -- 地区表
        CREATE TABLE IF NOT EXISTS regions (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE          -- UNIQUE:地区名不能重复
        );

        -- 订单表
        CREATE TABLE IF NOT EXISTS orders (
            id         INTEGER PRIMARY KEY,
            order_no   TEXT    NOT NULL UNIQUE,  -- 订单号,唯一
            product_id INTEGER NOT NULL REFERENCES products(id),  -- 外键
            region_id  INTEGER NOT NULL REFERENCES regions(id),   -- 外键
            quantity   INTEGER NOT NULL,         -- 购买数量
            amount     REAL    NOT NULL,         -- 订单金额(元)= price × quantity
            order_date TEXT    NOT NULL          -- 下单日期 ISO 格式:YYYY-MM-DD
        );

        -- 查询索引:按地区查 / 按商品查 / 按日期查
        CREATE INDEX IF NOT EXISTS idx_orders_region  ON orders(region_id);
        CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);
        CREATE INDEX IF NOT EXISTS idx_orders_date    ON orders(order_date);
    """)


# ---------- 3. 造数据 ----------

def generate_products() -> list[tuple]:
    """生成商品数据。

    返回: [(id, name, category, price), ...]
    - 遍历 CATEGORIES 字典,每个商品分配递增 id
    - 不同类目价格带不同:数码/家电贵,食品/图书便宜
    """
    products = []
    pid = 1                       # 商品 id 从 1 开始递增
    for category, names in CATEGORIES.items():
        for name in names:
            # 每个类目有自己的价格区间(元)
            price_range = {
                "数码": (50, 6000), "家电": (100, 5000),
                "家居": (20, 1500), "服饰": (30, 800),
                "食品": (15, 300),  "美妆": (20, 500),
                "运动": (15, 3000), "图书": (20, 150),
            }[category]
            price = round(random.uniform(*price_range), 2)
            products.append((pid, name, category, price))
            pid += 1
    return products


def generate_orders(products: list[tuple]) -> list[tuple]:
    """生成订单数据。

    返回: [(order_no, product_id, region_id, quantity, amount, order_date), ...]
    - 每个订单随机选 1 个商品、1 个地区、1 个日期
    - amount = price × quantity(保持数据一致,后面评测会用到)
    """
    orders = []
    # 预先把商品 id 和价格映射成字典,方便随机选取时查价格
    product_ids = [p[0] for p in products]
    prices = {p[0]: p[3] for p in products}  # 元组是 (id, name, category, price)

    start = date(*START_DATE)
    end = date(*END_DATE)
    total_days = (end - start).days  # 时间跨度:共多少天

    for i in range(1, ORDER_COUNT + 1):
        product_id = random.choice(product_ids)
        region_id = random.randint(1, REGION_COUNT)
        quantity = random.randint(1, 5)
        amount = round(prices[product_id] * quantity, 2)

        # 日期:起始日 + 随机偏移(0 ~ total_days 天)
        order_date = _date_to_iso(start + timedelta(days=random.randint(0, total_days)))

        orders.append((
            f"DD{i:06d}",          # 订单号 DD000001,DD000002 ...
            product_id,
            region_id,
            quantity,
            amount,
            order_date,
        ))
    return orders


# ---------- 4. 日期工具 ----------

def _date_to_iso(d: date) -> str:
    """把 datetime.date 格式化成 ISO 字符串 'YYYY-MM-DD'。"""
    return d.isoformat()


# ---------- 5. 主流程 ----------

def main() -> None:
    # 清掉旧库,保证重新生成时数据干净
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        create_tables(conn)

        # 批量插入(executemany 比逐条 execute 快得多)
        products = generate_products()
        conn.executemany(
            "INSERT INTO products (id, name, category, price) VALUES (?, ?, ?, ?)",
            products,
        )

        regions = [(i, name) for i, name in enumerate(REGIONS, start=1)]
        conn.executemany(
            "INSERT INTO regions (id, name) VALUES (?, ?)",
            regions,
        )

        orders = generate_orders(products)
        conn.executemany(
            """INSERT INTO orders
               (order_no, product_id, region_id, quantity, amount, order_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            orders,
        )

        conn.commit()  # 提交事务:不 commit 的话数据不会真正落盘
        print(f"✅ 数据生成完成: {DB_PATH}")
        print(f"   商品 {len(products)} 个 | 地区 {len(regions)} 个 | 订单 {len(orders)} 笔")

        # 顺手验证:抽查 5 条订单(已 JOIN 商品名和地区名)
        cur = conn.execute("""
            SELECT o.order_no, p.name, r.name, o.quantity, o.amount, o.order_date
            FROM orders o
            JOIN products p ON o.product_id = p.id
            JOIN regions  r ON o.region_id  = r.id
            ORDER BY o.id
            LIMIT 5
        """)
        print("\n抽查 5 笔订单(已 JOIN 商品名和地区名):")
        for row in cur.fetchall():
            print("   ", row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
