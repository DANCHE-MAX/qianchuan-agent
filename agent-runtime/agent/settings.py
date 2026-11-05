"""配置与密钥加载。

约定：阈值在 config/rules.json（可以进 git），密钥在 config/.env（永不进 git）。
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path=None):
    p = path or os.path.join(BASE, "config", "rules.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_env(path=None):
    """极简 .env 解析，不引第三方库。文件不存在就返回空字典（= dry-run）。"""
    p = path or os.path.join(BASE, "config", ".env")
    env = {}
    if not os.path.exists(p):
        return env
    with open(p, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    env["_QC_ENV_PATH"] = os.path.abspath(p)
    return env


def strip_comments(d):
    """去掉配置里以 _ 开头的说明键，避免被当成真实数据。"""
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def collection_kinds(config, account):
    """Account-specific snapshot scope; balance collection is independent."""
    kinds=config.get('monitoring',{}).get('account_kinds',{}).get(str(account),['plan','material'])
    if not isinstance(kinds,list) or not kinds or len(set(kinds))!=len(kinds) or any(k not in ('plan','material') for k in kinds):
        raise ValueError('Invalid account collection kinds')
    return tuple(kinds)


def configured_collection_kinds(root,account):
    from pathlib import Path
    path=Path(root)/'config/rules.json'
    return collection_kinds(load_config(path) if path.exists() else {},account)
