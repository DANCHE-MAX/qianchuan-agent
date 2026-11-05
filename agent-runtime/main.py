"""投流助手 · 只读骨架

五条命令：
    python main.py init                         建库
    python main.py daily  --date 2026-10-21     出日报并推送
    python main.py balance                      余额预警（含消警与升级）
    python main.py suggest --date 2026-10-21    晚高峰预算方案
    python main.py feedback                     回填人工反馈

默认 dry-run：没配 webhook 就把消息打印到控制台，整条链路照样能验收。
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timedelta

from agent import balance as balance_lib
from agent import engine, notify, report, settings, sources, model, cards
from agent.store import Store

BASE = os.path.dirname(os.path.abspath(__file__))
BALANCE_CSV = os.path.join(BASE, "data", "balances.csv")


class InputFileError(ValueError):
    """用户提供的报表路径无效；只在本地提示，不向群发送系统异常。"""


class DeliveryConfigError(ValueError):
    """发送目标或渠道未配置；不可能群告警，只在本地提示。"""


class CardDeliveryError(RuntimeError):
    """卡片发送通道本身失败；不要再用同一通道发送异常提醒。"""


# ---------------------------------------------------------------- 基础设施
def _setup():
    cfg = settings.load_config()
    env = settings.load_env()
    store = Store()
    return cfg, env, store


def _now(value):
    return datetime.fromisoformat(value) if value else datetime.now()


def _source(cfg, env, override=None, input_path=None, only_date=None):
    """数据源优先级：命令行参数 > .env > mock。"""
    env = dict(env)
    if override:
        env["DATA_SOURCE"] = override
    if input_path:
        env["DATA_FILE"] = input_path
        if not override:
            env["DATA_SOURCE"] = "csv"
    return sources.build_source(cfg, env, only_date=only_date)


def _history():
    return engine.load_history(os.path.join(BASE, "data", "action_history.json"))


def _tag(decisions, kind, date_str):
    """给决策编号加前缀，区分「日报产生的」和「方案产生的」，避免主键撞车。"""
    stamp = date_str.replace("-", "")
    for i, d in enumerate(decisions, 1):
        d["decision_id"] = "%s-%s-%03d" % (kind, stamp, i)
    return decisions


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]


def _archive(kind, date_str, text):
    """落一份 md 存档，方便回看和对比（消息会滚动，文件不会）。"""
    outdir = os.path.join(BASE, "out")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "%s_%s.md" % (kind, date_str.replace("-", "")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _send(nf, text, at_roles=None):
    """统一出口：有 at_roles 走 text（企微只有 text 能 @），否则走 markdown。"""
    if at_roles:
        nf.send_text(text, at_roles=at_roles)
    else:
        nf.send_markdown(text)


def _notifier(cfg, env, dry=False):
    """--dry 时只打印，不往群里发。

    但即使 dry，也要先构造一次真实渠道 —— 否则配置写错了你从输出里看不出来。
    构造本身不发网络（飞书应用的 token 是懒加载的），所以安全。
    """
    real = notify.build_notifier(cfg, env)
    if dry:
        print("[dry] 正式渠道是 %s，本次只打印不发送" % real.name)
        return notify.ConsoleNotifier()
    return real


def _report_failure(cmd, exc):
    """任何命令崩了都要在群里喊一声。

    静默失败是这类系统最危险的失效方式：任务在后台跑、消息没发出，
    但没有任何人知道 —— 等发现时可能已经漏了三天日报。
    """
    try:
        cfg, env, _ = _setup()
        roles = (cfg.get("notify", {}) or {}).get("roles", {})
        if roles.get("运维"):
            targets = ["运维"]
        elif roles.get("投手"):
            targets = ["投手"]
        else:
            targets = []

        nf = notify.build_notifier(cfg, env)
        if isinstance(nf, notify.ConsoleNotifier):
            print("[仅本地记录] 飞书发送渠道未配置，异常信息未发送到群")
            return
        text = (
            "[系统异常] 投流助手执行失败\n"
            "命令：%s\n"
            "时间：%s\n"
            "错误：%s\n"
            "影响：该时点的报告或提醒没有送达\n"
            "处理：检查数据源与网络，然后手动重跑  python main.py %s"
        ) % (cmd, datetime.now().strftime("%Y-%m-%d %H:%M"),
             str(exc)[:300] or exc.__class__.__name__, cmd)
        nf.send_text(text, at_roles=targets or None)
        print("[已上报] 异常信息已发送到群")
    except Exception as inner:  # 上报本身失败时只打印，不要再抛
        print("[警告] 异常上报也失败了：%s" % inner)


# ---------------------------------------------------------------- 命令
def cmd_init(args):
    cfg, env, store = _setup()
    store.init_schema()
    print("数据库已初始化：%s" % store.path)
    print("四张表：plan_snapshot / decision / account_balance / balance_alert")
    print("下一步：把 config/.env.example 复制成 config/.env，填 WECOM_WEBHOOK")
    return 0


def cmd_daily(args):
    if settings.load_config().get('model', {}).get('enabled'):
        return cmd_model(args)
    cfg, env, store = _setup()
    now = _now(args.now)
    # 8:30 发的日报讲的是「昨天」，所以要取昨天 23:00 的快照。
    # 默认值设成这样，定时任务就不用传任何参数。
    date_str = args.date or (now - timedelta(days=1)).strftime("%Y-%m-%d")
    hour = args.hour if args.hour is not None else 23
    prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    src = _source(cfg, env, args.source, args.input, only_date=date_str)
    plans = src.fetch()
    if not plans:
        print("没有读到计划数据，检查数据源配置")
        return 1
    for p in plans:
        # 采集时间以本次运行为准，不要沿用 CSV 里写死的小时，否则报表时间和入库时间会对不上
        p["snapshot_hour"] = hour

    store.save_snapshots(plans, date_str, hour)
    decisions = _tag(engine.run(plans, cfg, _history(), now=now), "daily", date_str)
    store.save_decisions(decisions, date_str)

    text = report.daily_markdown(store, cfg, plans, decisions, date_str, prev_date, now)
    nf = _notifier(cfg, env, args.dry)
    nf.send_markdown(text)
    path = _archive("daily", date_str, text)

    todo = [d for d in decisions if d["action"] in ("decrease", "pause") and d.get("executable")]
    print("\n[%s] 数据源=%s 计划=%d 快照=%s %02d:00 待办=%d 通道=%s"
          % ("daily", src.name, len(plans), date_str, hour, len(todo), nf.name))
    print("存档：%s" % os.path.relpath(path, BASE))
    return 0


def cmd_balance(args):
    cfg, env, store = _setup()
    now = _now(args.now)
    today = now.strftime("%Y-%m-%d")
    rows = _read_csv(args.file or BALANCE_CSV)
    if not rows:
        print("没有读到余额表：%s" % (args.file or BALANCE_CSV))
        return 1

    nf = _notifier(cfg, env, args.dry)
    escalate_after = int(cfg["balance"].get("escalate_after_minutes", 60))
    sent = 0

    for row in rows:
        account = (row.get("account") or "").strip()
        if not account:
            continue
        try:
            bal = float((row.get("balance") or "0").replace(",", ""))
        except ValueError:
            print("[跳过] %s 余额无法解析：%r（当成 0 会触发假告警，所以跳过）"
                  % (account, row.get("balance")))
            continue

        updated = (row.get("updated_at") or "").strip()
        store.save_balance(account, bal, updated)

        hourly = store.spend_by_hour(account, today, window_hours=3, upto_hour=now.hour)
        rate, rate_src = balance_lib.compute_rate(hourly, fallback_hour=max(now.hour, 1))
        item = balance_lib.evaluate(account, bal, updated, rate, cfg, now)

        prev = store.get_alert(account, today)
        elapsed = 0
        if prev and prev.get("sent_at"):
            try:
                elapsed = (now - datetime.fromisoformat(prev["sent_at"])).total_seconds() / 60.0
            except ValueError:
                elapsed = 0

        action = balance_lib.decide_action(item["level"], prev, elapsed, escalate_after)
        print("[%s] %s 余额=%.0f 速率=%.0f(%s) ETA=%s 安全线=%.0f → %s / %s"
              % (now.strftime("%H:%M"), account, item["balance"], item["rate"], rate_src,
                 "—" if item["eta_hours"] is None else "%.1fh" % item["eta_hours"],
                 item["floor"], item["level"], action))

        if action == "silent":
            continue

        text, at_roles = report.balance_message(item, action, cfg, now)
        _send(nf, text, at_roles or None)
        store.set_alert(account, today, item["level"], item["eta_hours"],
                        now.isoformat(timespec="seconds"),
                        1 if action == "escalate" else 0)
        sent += 1

    print("\n[balance] 通道=%s 检查=%d 条 推送=%d 条" % (nf.name, len(rows), sent))
    return 0


def cmd_suggest(args):
    if settings.load_config().get('model', {}).get('enabled'):
        return cmd_model(args)
    cfg, env, store = _setup()
    now = _now(args.now)
    date_str = args.date or now.strftime("%Y-%m-%d")
    hour = args.hour if args.hour is not None else now.hour

    src = _source(cfg, env, args.source, args.input, only_date=date_str)
    plans = src.fetch()
    if not plans:
        print("没有读到计划数据")
        return 1
    for p in plans:
        p["snapshot_hour"] = hour
    store.save_snapshots(plans, date_str, hour)

    decisions = _tag(engine.run(plans, cfg, _history(), now=now), "suggest", date_str)
    store.save_decisions(decisions, date_str)

    text = report.suggest_markdown(decisions, cfg, date_str, now)
    nf = _notifier(cfg, env, args.dry)
    nf.send_markdown(text)
    path = _archive("suggest", date_str, text)

    actionable = [d for d in decisions if d["action"] in ("decrease", "increase", "pause")]
    print("\n[suggest] 数据源=%s 计划=%d 可调整=%d 通道=%s"
          % (src.name, len(plans), len(actionable), nf.name))
    print("存档：%s" % os.path.relpath(path, BASE))
    return 0


def cmd_model(args):
    """完整自然日模型；文件快照或只读API，--send 显式发送。"""
    from agent import qianchuan
    cfg, env = settings.load_config(), settings.load_env()
    mc = dict(cfg['model'])
    source = getattr(args, 'source', None)
    if source not in (None, 'xlsx', 'csv', 'api'):
        raise ValueError('模型仅接受原始计划xlsx/csv或只读api，不使用演示数据')
    today = model.china_today()
    if getattr(args, 'now', None):
        today = min(today, datetime.fromisoformat(args.now).date())
    latest = (today - timedelta(days=1)).isoformat()
    known_complete = None
    if source == 'api':
        if getattr(args, 'input', None):
            raise ValueError('--source api 与 --input 不能同时使用')
        mc['window_days'] = getattr(args, 'days', None) or mc['window_days']
        end = getattr(args, 'date', None) or latest
        rows, metadata = qianchuan.fetch_daily(env, end, mc['window_days'], today=today)
        path = str(qianchuan.save_snapshot(rows, metadata, os.path.join(BASE, 'data', 'qianchuan')))
    else:
        path = getattr(args, 'input', None) or env.get('DATA_FILE') or mc.get('input_file')
        if not path:
            raise ValueError('请用 --input 指定原始计划报表')
        if not os.path.isabs(path):
            path = os.path.join(BASE, path)
        if not os.path.isfile(path):
            raise InputFileError('找不到计划报表：%s。请把 --input 换成真实存在的文件路径。' % path)
        mc['window_days'] = (getattr(args, 'days', None) or
            mc.get('window_days_by_file', {}).get(os.path.basename(path), mc['window_days']))
        known_complete = mc.get('complete_through_by_file', {}).get(os.path.basename(path))
        if known_complete:
            latest = min(latest, known_complete)
        end = getattr(args, 'date', None) or latest
        if known_complete and end > known_complete:
            raise ValueError('该导出仅确认完整至%s，后续日期为部分日数据，请换完整导出' % known_complete)
        rows = model.read_daily(path)
    provenance = qianchuan.provenance(path)
    configured_account = env.get('QC_ADVERTISER_IDS', '').strip()
    if configured_account and provenance and str(provenance.get('advertiser_id')) != configured_account:
        raise ValueError('数据文件账户与当前配置账户不同，请使用当前账户的API或数据文件。')
    season = getattr(args, 'season', None) or mc['season']
    result = model.evaluate(rows, mc, season, end, today=today)
    result['source_file'] = os.path.abspath(path)
    provenance = qianchuan.provenance(path)
    if provenance:
        result['source_api'] = provenance
        result['audit']['completeness_note'] = 'API按计划/自然日读取，分页条数及日期校验通过；仅覆盖所选账户和乘方商品计划报表，不代表全店所有投放类型'
        result['notes'].append('千川API账户%s；读取时间%s；历史成交可能随平台归因/退款回补更新' %
                               (provenance['advertiser_id'], provenance['fetched_at']))
    elif known_complete:
        result['notes'].append('此原始导出仅确认完整至%s；窗口外数据不参与计算' % known_complete)
    from agent import budgets
    account = (result.get('source_api') or {}).get('advertiser_id')
    budget_dir = os.path.join(BASE, 'data', 'qianchuan')
    if source == 'api' or getattr(args, 'refresh_budgets', False):
        snapshot = budgets.fetch(env, account, result['plans'])
        budgets.save(snapshot, budget_dir)
    else:
        snapshot = budgets.latest(budget_dir, account)
    budgets.attach(result, snapshot)
    destination = getattr(args, 'output_dir', None) or os.path.join(BASE, 'out')
    saved = model.save(result, destination)
    card_list = cards.build(result)
    card_path = cards.save(card_list, saved)
    text = model.render(result)
    send = getattr(args, 'send', False) and not getattr(args, 'dry', False)
    if send:
        nf = notify.build_notifier(cfg, env)
        if isinstance(nf, notify.ConsoleNotifier):
            count = len(notify.feishu_allowed_chat_ids(env))
            if count > 1:
                raise DeliveryConfigError('已授权 %d 个群，但日报发送目标未指定。请在 config/.env 设置 FEISHU_CHAT_ID 为其中一个群的 chat_id。报告已保存，未发送。' % count)
            raise DeliveryConfigError('日报发送群未配置：请在 config/.env 设置 FEISHU_CHAT_ID；若只有一个 FEISHU_ALLOWED_CHAT_IDS，也可自动使用该群。报告已保存，未发送。')
        if not hasattr(nf, 'send_card'):
            raise DeliveryConfigError('当前通道不支持飞书卡片；卡片 JSON 已保存，未发送')
        for index, card in enumerate(card_list, 1):
            try:
                nf.send_card(card)
            except Exception as exc:
                raise CardDeliveryError('第 %d/%d 张卡片发送失败，之前可能已送达 %d 张；重跑可能造成重复消息。飞书错误：%s' %
                                        (index, len(card_list), index - 1, exc)) from exc
        print('已通过 %s 发送 %d 张日报卡片' % (nf.name, len(card_list)))
    else:
        print(text)
        print('\n[本地预览] 未发送飞书消息；显式 --send 才发送。')
    print('报告：' + str(saved))
    print('卡片：' + str(card_path))
    return 0


def cmd_status(args):
    """自检：库里有东西吗、数据是哪天的、反馈填了没。"""
    cfg, env, store = _setup()
    store.init_schema()
    c = store.conn
    snap = c.execute("SELECT COUNT(*) AS n, MAX(snapshot_date) AS d FROM plan_snapshot").fetchone()
    dec = c.execute("SELECT COUNT(*) AS n FROM decision").fetchone()
    adopted = c.execute(
        "SELECT COALESCE(adopted,'pending') AS s, COUNT(*) AS n FROM decision "
        "WHERE action IN ('decrease','increase','pause') GROUP BY s").fetchall()
    alerts = c.execute(
        "SELECT account, level, escalated FROM balance_alert ORDER BY account").fetchall()

    print("数据库：%s" % store.path)
    print("快照：%d 条%s" % (snap["n"], "（最新 %s）" % snap["d"] if snap["d"] else ""))
    print("决策：%d 条" % dec["n"])
    if adopted:
        print("采纳情况：" + " ｜ ".join("%s %d" % (r["s"], r["n"]) for r in adopted))
    else:
        print("采纳情况：无（还没跑过 suggest，或还没回填反馈）")
    if alerts:
        print("余额提醒：" + " ｜ ".join(
            "%s=%s%s" % (r["account"], r["level"], "（已升级）" if r["escalated"] else "")
            for r in alerts))
    else:
        print("余额提醒：无")
    # 直接问 notifier 自己叫什么，别在这里猜（支持企微/飞书webhook/飞书应用三种渠道）
    print("通道：%s" % notify.build_notifier(cfg, env).name)
    print("数据源：%s" % ((env.get("DATA_SOURCE") or "mock")))
    return 0


def cmd_chats(args):
    """列出「自建应用所在的群」，拿到 chat_id 填进 .env。"""
    cfg, env, store = _setup()
    app_id = (env.get("FEISHU_APP_ID") or "").strip()
    app_secret = (env.get("FEISHU_APP_SECRET") or "").strip()
    if not (app_id and app_secret):
        print("请先在 config/.env 里填 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        return 1

    nf = notify.FeishuAppNotifier(app_id, app_secret, "")
    payload = nf._request(
        "GET", nf.BASE + "/im/v1/chats?page_size=50",
        None, {"Authorization": "Bearer " + nf._tenant_token()})
    items = (payload.get("data") or {}).get("items") or []
    if not items:
        print("应用还没被加进任何群。")
        print("先把机器人加进群：群设置 → 群机器人 → 添加 → 选你的应用")
        return 0

    print("应用所在的群（把 chat_id 填进 .env 的 FEISHU_CHAT_ID）：")
    for it in items:
        print("  %s  %s" % (it.get("chat_id"), it.get("name") or "(未命名)"))
    return 0


def cmd_feedback(args):
    cfg, env, store = _setup()
    rows = _read_csv(args.file or os.path.join(BASE, "data", "feedback.csv"))
    if not rows:
        print("反馈表为空（%s）" % (args.file or "data/feedback.csv"))
        print("表头：decision_id,adopted,adopt_note,final_budget,result_2h,result_24h")
        return 1
    count = store.apply_feedback(rows)
    print("已回填 %d 条反馈到 decision 表" % count)
    print("adopted 取值：adopted / modified / rejected")
    return 0


def cmd_listen(args):
    """持续接收飞书群 @；报告查询只读，不触发模型重算或推送。"""
    from agent import listener
    listener.run()
    return 0


# ---------------------------------------------------------------- 入口
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="投流助手（只读骨架）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("init", help="建库")
    sub.add_parser("status", help="自检：库里的数据状态")
    sub.add_parser("chats", help="列出飞书应用所在的群，拿 chat_id")
    sub.add_parser("listen", help="启动飞书群 @ 消息长连接监听")

    p_d = sub.add_parser("daily", help="出日报并推送")
    p_d.add_argument("--date", help="报告日期 YYYY-MM-DD，默认昨天")
    p_d.add_argument("--hour", type=int, help="快照小时，默认 23")
    p_d.add_argument("--now", help="固定当前时间 ISO，便于复现")
    p_d.add_argument("--source", choices=["mock", "csv", "xlsx", "api"], help="覆盖 .env 的数据源")
    p_d.add_argument("--input", help="数据文件路径（.csv 或 .xlsx）")
    p_d.add_argument("--dry", action="store_true", help="只打印不发群，改文案时用")
    p_d.add_argument('--send', action='store_true', help='显式推送模型报告到飞书')
    p_d.add_argument('--season', choices=['autumn_winter', 'summer'])
    p_d.add_argument('--output-dir')
    p_d.add_argument('--refresh-budgets', action='store_true', help='读取报告内计划的当前后台预算；不修改历史数据或平台预算')
    p_d.add_argument('--days', type=int, choices=range(1, 91), metavar='1..90', help='完整自然日数量；单日使用 --days 1')

    p_b = sub.add_parser("balance", help="余额预警")
    p_b.add_argument("--now", help="固定当前时间 ISO")
    p_b.add_argument("--file", help="余额表路径，默认 data/balances.csv")
    p_b.add_argument("--dry", action="store_true", help="只打印不发群")

    p_s = sub.add_parser("suggest", help="晚高峰预算方案")
    p_s.add_argument("--date", help="方案日期 YYYY-MM-DD")
    p_s.add_argument("--hour", type=int)
    p_s.add_argument("--now", help="固定当前时间 ISO")
    p_s.add_argument("--source", choices=["mock", "csv", "xlsx", "api"])
    p_s.add_argument("--input", help="CSV 路径")
    p_s.add_argument("--dry", action="store_true", help="只打印不发群")
    p_s.add_argument('--send', action='store_true', help='显式推送模型报告到飞书')
    p_s.add_argument('--season', choices=['autumn_winter', 'summer'])
    p_s.add_argument('--output-dir')
    p_s.add_argument('--refresh-budgets', action='store_true', help='读取报告内计划的当前后台预算；不修改历史数据或平台预算')
    p_s.add_argument('--days', type=int, choices=range(1, 91), metavar='1..90', help='完整自然日数量；单日使用 --days 1')

    p_m = sub.add_parser('model', help='完整自然日的综合ROI、利润及15%%消耗参考模型')
    p_m.add_argument('--source', choices=['csv', 'xlsx', 'api'])
    p_m.add_argument('--input', help='原始计划维度xlsx或csv')
    p_m.add_argument('--date', help='最近完整日 YYYY-MM-DD；默认昨天')
    p_m.add_argument('--season', choices=['autumn_winter', 'summer'])
    p_m.add_argument('--output-dir')
    p_m.add_argument('--refresh-budgets', action='store_true', help='读取报告内计划的当前后台预算；不修改历史数据或平台预算')
    p_m.add_argument('--days', type=int, choices=range(1, 91), metavar='1..90', help='完整自然日数量；单日使用 --days 1')
    p_m.add_argument('--dry', action='store_true')
    p_m.add_argument('--send', action='store_true', help='发送至已配置飞书群')

    p_f = sub.add_parser("feedback", help="回填人工反馈")
    p_f.add_argument("--file", help="反馈表路径，默认 data/feedback.csv")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1

    handlers = {
        "init": cmd_init, "status": cmd_status, "chats": cmd_chats,
        "daily": cmd_daily, "balance": cmd_balance,
        "suggest": cmd_suggest, "feedback": cmd_feedback, 'model': cmd_model,
        "listen": cmd_listen,
    }
    try:
        return handlers[args.cmd](args)
    except Exception as exc:
        import traceback
        if isinstance(exc, (InputFileError, DeliveryConfigError, CardDeliveryError, notify.NotifierConfigError)):
            label = ('输入文件错误' if isinstance(exc, InputFileError) else
                     '卡片发送失败' if isinstance(exc, CardDeliveryError) else '发送配置错误')
            print('[%s] %s' % (label, exc), file=sys.stderr)
            return 1
        traceback.print_exc()
        # 本地预览失败也不得触发原有错误群通知。
        model_route = args.cmd in ('model', 'listen') or (args.cmd in ('daily', 'suggest') and
                      settings.load_config().get('model', {}).get('enabled'))
        if not getattr(args, 'dry', False) and (not model_route or getattr(args, 'send', False)):
            _report_failure(args.cmd, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
