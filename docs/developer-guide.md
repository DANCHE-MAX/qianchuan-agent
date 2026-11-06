# 开发者说明

[返回项目首页](../README.md)

## 数据边界

本仓库不包含真实密钥、群及人员标识、订单、广告报表、生产成本配置、数据库、执行证据或历史经营报告。示例账号、商品标识和示例退款率不能用于真实经营决策。

`.env`、`rules.json`、利润配置、审批身份和控制文件需在本地单独提供。不要直接用脱敏副本覆盖正在运行的程序；代码更新不代表生产部署。

## 本地验证

Python 3.12+。分别安装 `agent-runtime/requirements.txt` 和 `qianchuan-shadow/requirements.lock` 所列依赖。部分验证依赖本地测试配置或专门夹具，运行前请查看对应模块说明。

```sh
cd agent-runtime
python -m unittest discover -s tests
cd ../qianchuan-shadow
PYTHONPATH=src python -m unittest discover -s tests
```

## 提交检查

安装 Gitleaks 后运行 `gitleaks dir . --redact=100`，并核对 `git diff --cached`。`.gitignore` 不会清除已跟踪文件，也不能代替敏感信息扫描。

运行 `git config core.hooksPath .githooks` 启用本地推送检查。推送前会检查敏感路径并扫描提交历史；未安装 Gitleaks 时会停止推送，可通过 `GITLEAKS_BIN` 指定安装位置。

