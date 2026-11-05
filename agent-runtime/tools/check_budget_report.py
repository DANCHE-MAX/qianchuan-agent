"""Run the new budget diagnostic without sending Feishu or starting services."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import budget_diagnostics,settings


def main():
    parser=argparse.ArgumentParser(description='Open API预算诊断，只读平台并保存本地，不发飞书。')
    parser.add_argument('--project',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--account',required=True)
    parser.add_argument('--search',default='')
    args=parser.parse_args();root=Path(args.project).resolve()
    try:
        env=settings.load_env(root/'config/.env')
        if env.get('QC_ADVERTISER_IDS','').strip()!=args.account:
            raise ValueError('目标账户与配置不一致')
        cfg=settings.load_config(root/'config/rules.json').get('budget_diagnostics',{})
        snapshot=budget_diagnostics.fetch(env,cfg,search=args.search)
        path=budget_diagnostics.save(snapshot,root)
        source=snapshot['report_source']
        summary={'path':str(path),'advertiser_id':args.account,'evidence_origin':snapshot['evidence_origin'],
            'report_source':source,'matched_plans':len(snapshot['plans']),'total_plans_in_scope':snapshot['total_plans_in_scope'],
            'plans':[{k:p[k] for k in ('plan_id','plan_name','budget_snapshot','today_spend','today_orders',
                'report_ratios','performance_status','performance_cutoff','target_roi','roi_target_comparable','missing','judgment')}
                for p in snapshot['plans']], 'platform_roi_mapping':None,'execution_allowed':False,
            'note':'未发飞书、未调用投放写接口、未清除ROI映射缺口。'}
        print(json.dumps(summary,ensure_ascii=False))
        incomplete=source.get('state') not in ('available','empty','not_requested') or any(
            p['performance_status']!='available' or p['budget_snapshot']['status']!='ok'
            for p in snapshot['plans'])
        return 1 if incomplete else 0
    except Exception as error:
        print('预算报表诊断未完成：'+type(error).__name__+'；请核对本机配置及接口权限，未返回旧数据代替。',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
