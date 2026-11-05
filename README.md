# 千川 Agent（脱敏代码仓库）

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

## 版本历史

为避免旧历史中的经营数据和身份信息随代码上传，本仓库从脱敏快照开始新历史。原始历史保留在本地，没有上传。

## 提交前检查

安装 Gitleaks 后运行 `gitleaks dir . --redact=100`；提交前核对 `git diff --cached`。`.gitignore` 不会清除已跟踪文件，也不能替代密钥扫描。

本地提交防护：`git config core.hooksPath .githooks`。推送前会检查已跟踪文件并扫描整个提交历史；若没有安装 Gitleaks，推送会停止。可通过 `GITLEAKS_BIN` 指定安装位置。

旧 agent 测试部分读取本地 `config/rules.json`，需要先提供脱敏测试配置；真实导出报表回归不随仓库提供。上述测试命令不是生产启动命令。
