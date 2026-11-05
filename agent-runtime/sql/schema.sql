-- 投流助手数据层。四张表，第一天就要建好。
-- 第三张和第四张的“结果列”先空着，但它们决定了系统以后能不能变聪明。

PRAGMA journal_mode = WAL;

-- 1. 计划快照（每次采集写一批，唯一键防重复）
CREATE TABLE IF NOT EXISTS plan_snapshot (
  plan_id        TEXT    NOT NULL,
  platform       TEXT,
  account        TEXT,
  plan_name      TEXT,
  campaign_type  TEXT,
  status         TEXT,
  daily_budget   REAL,
  spend_today    REAL,
  orders         INTEGER,
  gmv            REAL,
  refund         REAL,
  impressions    INTEGER,
  clicks         INTEGER,
  gross_margin   REAL,
  snapshot_date  TEXT    NOT NULL,
  snapshot_hour  INTEGER NOT NULL,
  PRIMARY KEY (plan_id, snapshot_date, snapshot_hour)
);

-- 2. 决策日志（系统的记忆。没有它，一切建议都无法评价）
CREATE TABLE IF NOT EXISTS decision (
  decision_id    TEXT PRIMARY KEY,
  ts             TEXT,
  snap_date      TEXT,
  plan_id        TEXT,
  platform       TEXT,
  account        TEXT,
  rule_id        TEXT,
  severity       TEXT,
  action         TEXT,
  current_budget REAL,
  target_budget  REAL,
  finding        TEXT,
  judgment       TEXT,
  acceptance     TEXT,
  executable     INTEGER,
  gate_blocks    TEXT,
  -- 以下五列先空着，靠人回填 / 脚本回填
  adopted        TEXT,      -- adopted / modified / rejected / NULL
  adopt_note     TEXT,      -- 人改成了多少、为什么
  final_budget   REAL,      -- 人实际填进后台的数字
  result_2h      REAL,      -- 执行后 2 小时的净 ROI
  result_24h     REAL       -- 执行后 24 小时的净 ROI
);

-- 3. 账户余额（人工填或采集）
CREATE TABLE IF NOT EXISTS account_balance (
  account     TEXT NOT NULL,
  balance     REAL NOT NULL,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (account, updated_at)
);

-- 4. 余额提醒记录（用于去重和升级，避免每小时重复骚扰）
CREATE TABLE IF NOT EXISTS balance_alert (
  account     TEXT NOT NULL,
  alert_date  TEXT NOT NULL,
  level       TEXT,
  eta_hours   REAL,
  sent_at     TEXT,
  escalated   INTEGER DEFAULT 0,
  PRIMARY KEY (account, alert_date)
);
