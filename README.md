# SVG 轮廓规范化 API (FastAPI + SQLite + Docker)

接收 SVG path 数据（`d` 属性）与填充规则，按照轮廓之间的**包含层级**将子路径
分类为**外环（outer）**与**孔洞（hole）**，并重写每个轮廓的绕向，使整组路径
在 `nonzero` 与 `evenodd` 两种填充规则下产生完全一致的填充结果。

- 外环统一为 **CCW**（屏幕视觉上的逆时针，可配置为 CW）
- 孔洞统一为 **CW**（与外环相反）
- 层级深度（depth）偶数 → 外环，奇数 → 孔洞
- 输出保留**原始子路径索引**、**父子层级**、**自定义元数据**
- 检出并返回边界警告：未闭合、退化轮廓、自交、重复点等
- SQLite 持久化，容器启动时**自动初始化建表**，开箱即调

## 目录结构

```
.
├── app/
│   ├── main.py        # FastAPI 路由与业务编排
│   ├── models.py      # Pydantic 请求/响应模型
│   ├── db.py          # SQLite 连接与自动建表
│   ├── svgpath.py     # SVG path 解析与曲线打平（M/L/H/V/C/S/Q/T/A/Z）
│   └── normalize.py   # 面积、包含关系、自交检测、绕向重写
├── tests/test_api.py  # 解析/几何/端到端测试（36 项断言）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

服务监听 `http://localhost:8000`，启动即完成：

- SQLite 数据库自动创建在卷 `svgpath-data`（容器内 `/app/data/svgpaths.db`）
- 表结构通过 `CREATE TABLE IF NOT EXISTS` 幂等初始化，重复启动安全
- 交互式文档：http://localhost:8000/docs （Swagger UI）
- 健康检查：`GET http://localhost:8000/health`

## 本地运行（不使用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 数据库默认位于 ./data/svgpaths.db，可用环境变量 DATABASE_PATH 覆盖
```

运行测试：

```bash
pip install fastapi "uvicorn[standard]" pydantic httpx
python -m tests.test_api
```

## API

### 1. 写入路径 `POST /paths`

请求：

```json
{
  "d": "M0 0 H100 V100 H0 Z M20 20 H80 V80 H20 Z M40 40 H60 V60 H40 Z",
  "fill_rule": "evenodd",
  "metadata": {"author": "alice", "project": "logo", "tags": ["v2"]}
}
```

- `d` *(必填)*：SVG path 数据，支持 `M m L l H h V v C c S s Q q T t A a Z z`
- `fill_rule`：`nonzero`（默认）或 `evenodd`
- `metadata`：任意 JSON 对象，原样存储并在所有查询中回显

响应 `201`：

```json
{
  "id": "c3eefb2c811748a899efc69689bb1372",
  "status": "received",
  "fill_rule": "evenodd",
  "metadata": {"author": "alice", "project": "logo", "tags": ["v2"]},
  "contour_count": 3,
  "created_at": "2026-09-22T12:00:00+00:00"
}
```

`d` 语法不合法返回 `422`，并指出出错的命令。

### 2. 规范化 `POST /paths/{id}/normalize`

请求体可省略（全部参数可选）：

```json
{
  "outer_orientation": "ccw",
  "flatten_tolerance": 0.1,
  "precision": 3
}
```

- `outer_orientation`：外环目标绕向，`ccw`（默认）/ `cw`；孔洞自动取反向
- `flatten_tolerance`：曲线打平与真实曲线的最大偏差（像素，默认 0.1）
- `precision`：输出坐标保留的小数位数（0–10，默认 3）

响应：

```json
{
  "d": "M0 0 L0 100 L100 100 L100 0 Z M20 20 L80 20 L80 80 L20 80 Z ...",
  "outer_orientation": "ccw",
  "contours": [
    {"index": 0, "parent_index": null, "depth": 0, "role": "outer",
     "orientation": "ccw", "signed_area": -10000.0, "point_count": 4,
     "d": "M0 0 L0 100 L100 100 L100 0 Z", "excluded": false},
    {"index": 1, "parent_index": 0, "depth": 1, "role": "hole",
     "orientation": "cw", "signed_area": 3600.0, "point_count": 4,
     "d": "M20 20 L80 20 L80 80 L20 80 Z", "excluded": false},
    {"index": 2, "parent_index": 1, "depth": 2, "role": "outer",
     "orientation": "ccw", "signed_area": -400.0, "point_count": 4,
     "d": "M40 40 L40 60 L60 60 L60 40 Z", "excluded": false}
  ],
  "warnings": [],
  "bounds": [0.0, 0.0, 100.0, 100.0]
}
```

- `index` 始终是该子路径在原始 `d` 中出现的顺序（从 0 开始）
- `depth`：包含层级深度（0 为最外层）；`parent_index`：直接父轮廓
- 曲线会被打平为折线后输出（`M…L…Z`），以保证面积与包含判断的精确性
- 重复调用会**替换**旧的规范化结果（轮廓/警告行先删后写）

### 3. 查询 `GET /paths/{id}`

一次返回原始路径、规范化结果（若有）、边界警告与元数据：

```json
{
  "id": "c3ee…",
  "status": "normalized",
  "fill_rule": "evenodd",
  "metadata": {"author": "alice", "project": "logo", "tags": ["v2"]},
  "original": {"d": "M0 0 H100 …", "contour_count": 3},
  "normalized": { "...": "同 /normalize 的完整响应" },
  "created_at": "…",
  "updated_at": "…"
}
```

未规范化时 `normalized` 为 `null`，`status` 为 `received`。ID 不存在返回 `404`。

### 其他

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/paths` | 列出全部路径（id、状态、填充规则、轮廓数） |
| GET | `/health` | 存活探针 |
| GET | `/docs` | Swagger UI |

## curl 示例

```bash
# 1) 写入
ID=$(curl -s -X POST localhost:8000/paths \
  -H 'Content-Type: application/json' \
  -d '{"d":"M0 0h100v100h-100Z M20 20h60v60h-60Z","fill_rule":"evenodd",
       "metadata":{"name":"square-with-hole"}}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 2) 规范化
curl -s -X POST localhost:8000/paths/$ID/normalize | python -m json.tool

# 3) 查询
curl -s localhost:8000/paths/$ID | python -m json.tool
```

## 规范化语义

1. **解析**：按 SVG 规范解析全部命令并转为绝对坐标；`S/T` 解析为平滑
   三次/二次贝塞尔，`A` 椭圆弧按端点→圆心参数化（F.6.5）展开。
2. **打平**：贝塞尔曲线用 de Casteljau 递归细分直到弦高小于容差；圆弧按
   容差自适应步进。后续所有几何计算都在折线上进行。
3. **包含层级**：对每个轮廓做射线法点在多边形内测试（顶点落在其它轮廓边
   界上时自动换点），统计包围它的轮廓数得到 `depth`；`parent_index`
   取面积最小的包围轮廓（最近父级）。
4. **分类与重写**：depth 偶数为外环，奇数为孔洞；用鞋带公式计算有向面积
   （SVG 的 y 轴向下，面积为正即视觉顺时针）。方向不符则反转顶点序
   （反转时保持起始锚点不变）。
5. 交替绕向下，`nonzero` 与 `evenodd` 的填充区域一致，因此 `fill_rule`
   作为输入意图记录，不影响层级分类。

## 边界警告

| code | 触发条件 | 处理 |
| --- | --- | --- |
| `unclosed_contour` | 子路径缺少 `Z` | 自动按闭合处理并提示 |
| `degenerate_contour` | 去重后少于 3 点或面积为 0 | 排除出规范化 `d`，轮廓项 `excluded=true` |
| `duplicate_points` | 存在连续重复点 | 删除重复点并报告数量 |
| `self_intersection` | 非相邻边互相交叉 | 报告相交边对（上限 20 对），提示层级可能不可靠 |
| `self_intersection_check_truncated` | 边对过多，检测提前终止 | 仅提示，结果仍返回 |
| `empty_path` | 全部轮廓退化，无可输出几何 | 规范化 `d` 为空 |

## 数据持久化

SQLite 文件位置由环境变量 `DATABASE_PATH` 控制：

- Docker：`/app/data/svgpaths.db`，挂在命名卷 `svgpath-data` 上，容器重建不丢数据
- 本地：默认 `./data/svgpaths.db`

三张表：`paths`（路径、填充规则、元数据、规范化总输出）、`contours`
（每个轮廓的索引/层级/角色/绕向/面积/子路径 d）、`warnings`（警告明细）。
