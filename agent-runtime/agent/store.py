"""SQLite 数据层。

四张表见 sql/schema.sql。两条纪律：
1. 所有写入都带唯一键（INSERT OR REPLACE），脚本重跑不会产生重复数据；
2. 读出来的一律是 dict，业务层不碰 sqlite3.Row。
"""
import os
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE, "data", "app.db")
SCHEMA_PATH = os.path.join(BASE, "sql", "schema.sql")


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")

    # ---------- 建表 ----------
    def init_schema(self):
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.conn.commit()

    # ---------- 快照 ----------
    def save_snapshots(self, plans, snapshot_date, snapshot_hour):
        rows = [
            (
                p["plan_id"], p.get("platform"), p.get("account"), p.get("plan_name"),
                p.get("campaign_type"), p.get("status"),
                p.get("daily_budget"), p.get("spend_today"), p.get("orders"),
                p.get("gmv"), p.get("refund"), p.get("impressions"),
                p.get("clicks"), p.get("gross_margin"),
                snapshot_date, snapshot_hour,
            )
            for p in plans
        ]
        self.conn.executemany(
            """INSERT OR REPLACE INTO plan_snapshot
               (plan_id, platform, account, plan_name, campaign_type, status,
                daily_budget, spend_today, orders, gmv, refund, impressions,
                clicks, gross_margin, snapshot_date, snapshot_hour)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def spend_by_hour(self, account, snapshot_date, window_hours=3, upto_hour=23):
        """取某账户最近 N 个小时的累计消耗，用于算真实窗口速率。"""
        cur = self.conn.execute(
            """SELECT snapshot_hour AS h, SUM(spend_today) AS s
               FROM plan_snapshot
               WHERE account = ? AND snapshot_date = ? AND snapshot_hour <= ?
               GROUP BY snapshot_hour ORDER BY snapshot_hour DESC LIMIT ?""",
            (account, snapshot_date, upto_hour, window_hours),
        )
        return [dict(r) for r in cur.fetchall()]

    # ---------- 决策 ----------
    def save_decisions(self, decisions, snap_date):
        rows = []
        for d in decisions:
            rows.append((
                d["decision_id"], d.get("ts"), snap_date, d.get("plan_id"),
                d.get("platform"), d.get("account"), d.get("rule_id"),
                d.get("severity"), d.get("action"), d.get("current_budget"),
                d.get("target_budget"), d.get("finding"), d.get("judgment"),
                d.get("acceptance"), 1 if d.get("executable") else 0,
                "；".join(d.get("gate_blocks") or []),
            ))
        self.conn.executemany(
            """INSERT INTO decision
               (decision_id, ts, snap_date, plan_id, platform, account, rule_id,
                severity, action, current_budget, target_budget, finding,
                judgment, acceptance, executable, gate_blocks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(decision_id) DO UPDATE SET
                 ts=excluded.ts,
                 snap_date=excluded.snap_date,
                 plan_id=excluded.plan_id,
                 platform=excluded.platform,
                 account=excluded.account,
                 rule_id=excluded.rule_id,
                 severity=excluded.severity,
                 action=excluded.action,
                 current_budget=excluded.current_budget,
                 target_budget=excluded.target_budget,
                 finding=excluded.finding,
                 judgment=excluded.judgment,
                 acceptance=excluded.acceptance,
                 executable=excluded.executable,
                 gate_blocks=excluded.gate_blocks""",
            # 关键：这里故意不更新 adopted / adopt_note / final_budget /
            # result_2h / result_24h。否则同一天重跑一次，人工填的反馈就全没了。
            rows,
        )
        self.conn.commit()
        return len(rows)

    def decisions_on(self, snap_date):
        cur = self.conn.execute(
            """SELECT * FROM decision WHERE snap_date = ?
               ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                                      WHEN 'low' THEN 2 ELSE 3 END, plan_id""",
            (snap_date,),
        )
        return [dict(r) for r in cur.fetchall()]

    def adoption_stats(self, snap_date):
        cur = self.conn.execute(
            """SELECT COALESCE(adopted, 'pending') AS state, COUNT(*) AS n
               FROM decision WHERE snap_date = ? GROUP BY state""",
            (snap_date,),
        )
        return {r["state"]: r["n"] for r in cur.fetchall()}

    def apply_feedback(self, rows):
        """Partial CSV updates with per-row validation and append-only change audit."""
        import json, math
        from datetime import datetime, timezone
        fields = ('adopted', 'adopt_note', 'final_budget', 'result_2h', 'result_24h')
        result = dict(updated=0, unmatched=0, invalid=0, unchanged=0, details=[])
        with self.conn:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS feedback_import_audit (
                id INTEGER PRIMARY KEY, decision_id TEXT NOT NULL, at TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL)""")
            for index, row in enumerate(rows, 2):
                identity = str(row.get('decision_id') or '').strip()
                detail = {'row': index, 'decision_id': identity}
                changes = {}
                try:
                    if not identity: raise ValueError('缺少decision_id')
                    for field in fields:
                        value = row.get(field)
                        if value is None or str(value).strip() == '': continue
                        value = str(value).strip()
                        if field == 'adopted' and value not in ('adopted', 'modified', 'rejected'):
                            raise ValueError('采纳状态无效')
                        if field in ('final_budget', 'result_2h', 'result_24h'):
                            try: value = float(value)
                            except (ValueError, OverflowError): raise ValueError('金额或结果不是有效数字') from None
                            if not math.isfinite(value) or (field == 'final_budget' and value < 0):
                                raise ValueError('金额或结果超出有效范围')
                        changes[field] = value
                    found = self.conn.execute('SELECT * FROM decision WHERE decision_id=?', (identity,)).fetchone()
                    if found is None:
                        status = 'unmatched'
                    else:
                        before = {key: found[key] for key in fields}
                        changes = {k:v for k,v in changes.items() if before[k] != v}
                        status = 'updated' if changes else 'unchanged'
                        if changes:
                            self.conn.execute('UPDATE decision SET '+', '.join(k+'=?' for k in changes)+' WHERE decision_id=?',
                                              (*changes.values(), identity))
                            self.conn.execute('INSERT INTO feedback_import_audit(decision_id,at,before_json,after_json) VALUES (?,?,?,?)',
                                              (identity, datetime.now(timezone.utc).isoformat(), json.dumps(before,ensure_ascii=False),
                                               json.dumps(dict(before,**changes),ensure_ascii=False)))
                except ValueError as exc:
                    status = 'invalid'; detail['reason'] = str(exc)
                result[status] += 1; detail['status'] = status; result['details'].append(detail)
        return result

    # ---------- 余额 ----------
    def save_balance(self, account, balance, updated_at):
        self.conn.execute(
            "INSERT OR REPLACE INTO account_balance (account, balance, updated_at) VALUES (?,?,?)",
            (account, balance, updated_at),
        )
        self.conn.commit()

    def latest_balance(self, account):
        cur = self.conn.execute(
            """SELECT * FROM account_balance WHERE account = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (account,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    # ---------- 提醒状态（消警 / 防重复的核心） ----------
    def get_alert(self, account, alert_date):
        cur = self.conn.execute(
            "SELECT * FROM balance_alert WHERE account = ? AND alert_date = ?",
            (account, alert_date),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def set_alert(self, account, alert_date, level, eta_hours, sent_at, escalated=0):
        self.conn.execute(
            """INSERT OR REPLACE INTO balance_alert
               (account, alert_date, level, eta_hours, sent_at, escalated)
               VALUES (?,?,?,?,?,?)""",
            (account, alert_date, level, eta_hours, sent_at, escalated),
        )
        self.conn.commit()


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None
