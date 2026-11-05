# 千川数据分析Agent

包含 `agent-runtime/` 当前运行程序与 `qianchuan-shadow/` 只读分析、利润测算和预算审批模块的代码及测试。

## 数据边界

本仓库不包含真实密钥、群及人员标识、订单、广告报表、成本配置、数据库、执行证据或历史经营报告。账号、商品标识和旧程序内嵌的精确退款率已替换为合成示例，不能用于真实经营决策。其余测试数字仅用于算法验证。

配置 `.env`、`rules.json`、利润 profile、审批身份和控制文件必须在本地单独提供。没有附带可执行的生产配置。不要直接用本副本覆盖正在运行的程序；原本机运行目录和数据保持独立。

## 本地验证

Python 3.12+。分别安装 `agent-runtime/requirements.txt` 和 `qianchuan-shadow/requirements.lock` 所列依赖。

```sh
cd agent-runtime
python -m unittest discover -s tests
cd ../qianchuan-shadow
PYTHONPATH=src python -m unittest discover -s tests
```


## 提交前检查

安装 Gitleaks 后运行 `gitleaks dir . --redact=100`；提交前核对 `git diff --cached`。`.gitignore` 不会清除已跟踪文件，也不能替代密钥扫描。

本地提交防护：`git config core.hooksPath .githooks`。推送前会检查已跟踪文件并扫描整个提交历史；若没有安装 Gitleaks，推送会停止。可通过 `GITLEAKS_BIN` 指定安装位置。


## 投手反馈闭环（第一期）

新增建议编号、群内身份核验、采纳/暂缓/拒绝/自报执行记录、只读预算核验和查询复盘。复用已有群与投手配置；独立数据库，不接入预算写入链路。默认登记预览，不主动发群。

使用方法、数据边界及CSV修复：[投手反馈闭环第一期](docs/feedback-phase1.md)。本期代码未自动部署到生产。
