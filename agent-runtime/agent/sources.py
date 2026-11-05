"""数据源适配层：引擎不认识数据从哪来，只认识统一的计划快照 dict。

三条路，换的是这一层的实现，engine.py 一行都不用改：
  MockSource         内置演示数据（17 条，覆盖全部规则分支）
  CsvExportSource    后台导出的 CSV（现在）
  QianchuanApiSource 千川只读 API（第四步，占位）

列名映射放在 config/rules.json 的 columns 段，改表头不用改代码。
"""
import csv
import os

from .settings import strip_comments

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PlanSource:
    name = "base"

    def fetch(self):
        raise NotImplementedError


def _num(row, key, default=None):
    raw = (row.get(key) or "").strip().replace(",", "").replace("，", "")
    if raw == "" or raw == "-":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _to_num(value, default=None):
    """把一个单元格的值转成数字。处理千分位逗号、"-"（无数据）、空串。"""
    raw = str(value if value is not None else "").strip().replace(",", "").replace("，", "")
    if raw in ("", "-", "None", "nan"):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def parse_row(row, columns=None):
    """把一行原始数据变成标准快照。缺字段不猜，缺就是 None。"""
    cols = strip_comments(columns or {})

    def get(field):
        col = cols.get(field, field)
        return row.get(col, "")

    return {
        "plan_id": str(get("plan_id")).strip(),
        "platform": str(get("platform")).strip(),
        "account": str(get("account")).strip(),
        "plan_name": str(get("plan_name")).strip(),
        "campaign_type": str(get("campaign_type")).strip(),
        "status": (str(get("status")).strip() or "active").lower(),
        "daily_budget": _num(row, cols.get("daily_budget", "daily_budget"), 0.0),
        "spend_today": _num(row, cols.get("spend_today", "spend_today"), 0.0),
        "orders": int(_num(row, cols.get("orders", "orders"), 0) or 0),
        "gmv": _num(row, cols.get("gmv", "gmv"), 0.0),
        "refund": _num(row, cols.get("refund", "refund"), 0.0),
        "impressions": int(_num(row, cols.get("impressions", "impressions"), 0) or 0),
        "clicks": int(_num(row, cols.get("clicks", "clicks"), 0) or 0),
        "gross_margin": _num(row, cols.get("gross_margin", "gross_margin"), None),
        "snapshot_hour": int(_num(row, cols.get("snapshot_hour", "snapshot_hour"), 23) or 23),
    }


class CsvExportSource(PlanSource):
    """读后台导出的 CSV。

    这一层是最快接上真实数据的方式：不需要 API 资质、不需要开发者账号，
    每天从后台导一次，脚本读这个文件就行。
    """

    def __init__(self, path, columns=None):
        self.path = path
        self.columns = columns
        self.name = "csv:" + os.path.basename(path)

    def fetch(self):
        cols = strip_comments(self.columns or {})
        id_col = cols.get("plan_id", "plan_id")
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        plans = []
        for r in rows:
            if not (r.get(id_col) or "").strip():
                continue
            plans.append(parse_row(r, self.columns))
        return plans


class MockSource(PlanSource):
    """内置演示数据：不需要任何真实数据就能跑，且每条规则分支都有覆盖。"""

    name = "mock"

    def __init__(self, path=None):
        self.path = path or os.path.join(BASE, "data", "demo_plans.csv")

    def fetch(self):
        return CsvExportSource(self.path).fetch()


class XlsxPlanSource(PlanSource):
    """读千川「乘方推广-数据」xlsx 的计划维度报表。

    这个报表的三个坑（都已在代码里处理）：
      1. 里面混着「日期=全部」「小时区间=全部」的汇总行，不过滤会把金额算好几遍；
      2. 没有「日预算」列 —— 「消耗进度」判断（R004）做不了，自动跳过；
      3. 没有「曝光」「点击」列 —— CTR 算不出来，素材疲劳（R006）也跳过。

    好在有「净成交金额」（已扣退款），所以净 ROI 是准的，不用单独要退款列。
    """

    name = "xlsx"

    DEFAULTS = {
        "plan_id": "计划ID",
        "plan_name": "计划名称",
        "campaign_type": "投放类型",
        "date": "日期",
        "hour": "小时区间",
        "gmv": "整体成交金额",
        "net_gmv": "净成交金额",
        "spend": "整体消耗",
        "orders": "净成交订单数",
    }

    def __init__(self, path, columns=None, only_date=None, account="抖音主账户"):
        self.path = path
        self.columns = strip_comments(columns or {})
        self.only_date = only_date
        self.account = account
        self.skipped = {}
        self.name = "xlsx:" + os.path.basename(path)

    def _col(self, key):
        return self.columns.get(key, self.DEFAULTS[key])

    def fetch(self):
        from .xlsx import read_rows

        rows = read_rows(self.path)
        if not rows:
            return []
        header = [str(h).strip() for h in rows[0]]
        pos = {name: i for i, name in enumerate(header)}

        def cell(row, name):
            i = pos.get(name)
            if i is None or i >= len(row):
                return ""
            return str(row[i]).strip()

        plans = []
        skipped = {"summary": 0, "hourly": 0, "other_date": 0}

        for row in rows[1:]:
            date = cell(row, self._col("date"))
            hour = cell(row, self._col("hour"))

            # 汇总行必须剔掉，否则同一天的金额会被算 24 遍
            if date in ("全部", "", "-"):
                skipped["summary"] += 1
                continue
            if hour != "全部":
                skipped["hourly"] += 1
                continue
            if self.only_date and date != self.only_date:
                skipped["other_date"] += 1
                continue

            plan_id = cell(row, self._col("plan_id"))
            if not plan_id:
                continue

            gmv = _to_num(cell(row, self._col("gmv")), 0.0) or 0.0
            net = _to_num(cell(row, self._col("net_gmv")), None)
            refund = round(max(0.0, gmv - net), 2) if net is not None else 0.0

            plans.append({
                "plan_id": plan_id,
                "platform": "抖音",
                "account": self.account,
                "plan_name": cell(row, self._col("plan_name")),
                "campaign_type": cell(row, self._col("campaign_type")) or "乘方投放",
                "status": "active",
                # 报表里没有的三项留 None，规则会自动跳过相关判断
                "daily_budget": None,
                "impressions": None,
                "clicks": None,
                "spend_today": _to_num(cell(row, self._col("spend")), 0.0),
                "orders": int(_to_num(cell(row, self._col("orders")), 0) or 0),
                "gmv": gmv,
                "refund": refund,
                "gross_margin": None,   # 用 config 里的默认毛利率
                "snapshot_hour": 23,
                "_date": date,
            })

        self.skipped = skipped
        return plans


class QianchuanApiSource(PlanSource):
    """千川只读 API（占位，第四步实现）。

    要跑通必须同时具备三样，缺一样都别开工：
      1. 巨量引擎开放平台的应用资质（App ID / App Secret），需企业主体审核；
      2. 商家侧 OAuth 授权，拿到 access_token 与 refresh_token；
      3. 广告账户 ID，以及报表接口的读取权限。

    两个必须知道的坑：
      - 报表数据有 15 分钟到 1 小时的延迟，且不是实时流；
      - 写接口没有幂等参数，所以本系统只读，永不写。
    """

    name = "qianchuan-api"

    def __init__(self, app_id=None, access_token=None, advertiser_ids=None):
        self.app_id = app_id
        self.access_token = access_token
        self.advertiser_ids = advertiser_ids or []

    def fetch(self):
        raise NotImplementedError(
            "千川 API 尚未接入。先用 DATA_SOURCE=csv 跑通，再实现本类的 fetch()。\n"
            "需要的配置项见 config/.env.example 的 QC_* 段。"
        )


def build_source(cfg, env, only_date=None):
    """按 .env 里的 DATA_SOURCE 选数据源。文件后缀是 .xlsx 时自动走表格读取。"""
    kind = (env.get("DATA_SOURCE") or "mock").strip().lower()
    columns = cfg.get("columns")
    path = (env.get("DATA_FILE") or "").strip()

    if kind in ("csv", "xlsx") and not path:
        raise RuntimeError("DATA_SOURCE=%s 但 DATA_FILE 为空，请在 config/.env 里填文件路径" % kind)

    if kind == "csv" and path.lower().endswith((".xlsx", ".xlsm")):
        # xlsx 用自己的列名配置（columns 是给 CSV 的规范列名用的，见 xlsx_columns 注释）
        return XlsxPlanSource(path, cfg.get("xlsx_columns"), only_date=only_date)

    if kind == "csv":
        return CsvExportSource(path, columns)

    if kind in ("xlsx", "excel"):
        # xlsx 用自己的列名配置（columns 是给 CSV 的规范列名用的，见 xlsx_columns 注释）
        return XlsxPlanSource(path, cfg.get("xlsx_columns"), only_date=only_date)

    if kind == "api":
        return QianchuanApiSource(
            app_id=env.get("QC_APP_ID"),
            access_token=env.get("QC_ACCESS_TOKEN"),
            advertiser_ids=[x for x in (env.get("QC_ADVERTISER_IDS") or "").split(",") if x],
        )

    return MockSource()
