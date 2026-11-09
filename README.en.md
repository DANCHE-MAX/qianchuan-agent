# Qianchuan Agent | Understand ad performance before acting

[简体中文](README.md) · **English**

**Spend less time compiling reports. See estimated profit, where ad spend goes, how long the balance may last, and what needs attention.**

The assistant analyzes and alerts; the media buyer makes decisions. It does not automatically change budgets or ROI targets, or pause campaigns. Estimated profit is not final accounting profit.

## Latest progress

From reading reports to identifying priorities and following up on missing data.

| Business challenge | What has improved |
| --- | --- |
| Unclear where to focus | Up to three entries each for profit, loss, and ad spend |
| Finding yesterday’s results takes time | Direct answers about top earners, largest losses, and where spend went |
| Too many separate alerts | Rule alerts of the same type and snapshot interval are grouped; balance and refund alerts remain separate |
| Missing data is hard to follow up | Local records track gaps, recheck the last seven complete calendar days, and record differences |
| Charts and details may disagree | Checks run before delivery; contradictions block sending, and missing values are not treated as zero |

These capabilities are based on committed code. Enablement and actual delivery must be verified using each environment’s runtime records and delivery receipts.

## What can it do now?

| Business question | What you can see |
| --- | --- |
| Are we making money? | Yesterday’s estimated profit, losses, and attention priorities |
| Where is the money going? | Two-day spend by product and time slot, compared with profit contribution |
| Is the balance sufficient? | Remaining balance, estimated time remaining, and changes in depletion rate |
| What should we check first? | A daily guide, alerts, and campaign-level Excel details |
| Did the missing data arrive later? | Local follow-up records and differences, without extra group messages |

## Business overview at a glance

Read the tree vertically; tap to enlarge on mobile. The diagram uses Chinese labels: business results → spending → balance risk → Feishu delivery → data follow-up → operating limits.

[![Vertical business overview: results, spending, balance, delivery, data follow-up and limits](docs/assets/business-tree.png)](docs/assets/business-tree.png)

## How to use it

| Situation | What happens |
| --- | --- |
| Daily report at 10:00 | Date notice → daily guide → profit charts → Excel → two-day spend chart → contribution charts |
| Balance checks every two hours | Balance and rate updates at even-numbered hours; rule-based warnings when the balance is RMB 500 or lower |
| A quick question about yesterday | Ask which product earned the most, lost the most, or accounted for the most ad spend |
| An anomaly or missing data | Review the alert; gaps remain marked for verification, with follow-up records stored locally |

All times are Beijing time (UTC+8). Local authorization and configuration are required. Each daily report package mentions the media buyer once. Balance alerts run separately; two-day charts are not sent again at midnight. Uncertain delivery is checked before retrying.

**Current question examples are in Chinese:** `昨天哪个商品赚得最多` (Which product earned the most yesterday?), `昨天哪个亏损最多` (Which lost the most yesterday?), and `昨天广告费花在哪里` (Where did yesterday’s ad spend go?). This translated page does not imply support for English commands.

## What still needs work?

| Next step | Current limitation |
| --- | --- |
| Establish final profit | Actual refunds, fees, and accounting records still need reconciliation |
| Resolve every data gap | Product campaign amounts can be rechecked under defined rules; livestream details and historical product mappings may still require manual review |
| Measure the impact of decisions | Data follow-up is implemented; adoption and advertising outcome tracking remain incomplete, and the feedback workflow is paused |
| Enable automatic actions | Separate authorization and acceptance checks are required; media buyers currently execute changes |
| Quantify time savings and business value | More usage and outcome evidence is needed; no revenue improvement is claimed |

**How to read the data:** totals include only calculable amounts. Eligible zero-spend product campaigns may use explicitly labeled business-rule estimates for missing values; these are not platform-confirmed sales. Unknown product attribution does not mean the amount is unknown. Store-managed campaigns and other unlinked campaigns are shown separately.

## Demo gallery

**All figures are fictional. These are not real screenshots, business results, or delivery records.** Images remain directly visible and can be enlarged on mobile. Chart labels are in Chinese, with English reading guides below. The report examples share the same fictional dataset and delivery order; the balance example uses separate illustrative values.

### ① What arrives in Feishu?

Date notice and one mention → daily guide → profit charts and Excel → two-day spend and contribution charts. Balance reminders follow their own schedule.

[![Feishu report delivery example](docs/assets/feishu-messages-template.svg)](docs/assets/feishu-messages-template.svg)

### ② Daily guide | What needs attention?

The first chart shows yesterday’s results, changes from the previous day, and three priority lists: profit, loss, and spend. Missing values are excluded from the relevant ranking.

[![Daily guide with results, changes and attention priorities](docs/assets/daily-guide-template.svg)](docs/assets/daily-guide-template.svg)

### ③ Profit | Which products need a closer look?

Bars above zero show profit; bars below show loss. Green means profit and red means loss. Unverified values are not filled with zero, and totals include only calculable amounts.

[![Estimated profit bar chart](docs/assets/profit-report-template.svg)](docs/assets/profit-report-template.svg)

### ④ Two-day spend | What changed by product and time slot?

In each pair, the left bar is the day before yesterday and the right bar is yesterday. Products keep consistent colors and both days use the same scale. Store-managed campaigns are magenta; unresolved product attribution is blue-gray. More spending does not necessarily mean better results.

[![Two-day stacked ad spend by product and time slot](docs/assets/two-day-spend-template.svg)](docs/assets/two-day-spend-template.svg)

### ⑤ Contribution | Does spending align with profit contribution?

The left charts show profitable products’ contribution; the right charts show ad spend shares. Losses, store-managed campaign profit, other unlinked campaign profit, and missing values are listed separately and excluded from the profit pie charts.

[![Two-day profit contribution and ad spend shares](docs/assets/two-day-contribution-template.svg)](docs/assets/two-day-contribution-template.svg)

### ⑥ Balance | Is the balance falling faster?

The three time windows share the same scale. Estimated time remaining assumes the current rate stays constant. Balance depletion is not the same as ad spend; missing samples or account adjustments require review.

[![Balance depletion rate comparison](docs/assets/balance-comparison-template.svg)](docs/assets/balance-comparison-template.svg)

[Demo data and color reference — Chinese](docs/demo-guide.md). Product colors stay consistent in the spend and contribution charts; balance-chart colors represent time windows only.

## Learn more

The detailed documents below are currently in Chinese.

- **Business rules:** [Reports and alerts](docs/automatic-profit-daily.md) · [Profit calculation](docs/current-profit-rules.md)
- **Verification:** [Completeness, business-rule estimates, and gap tracking](docs/report-and-verification.md)
- **Project history:** [Development journey](docs/development-journey.md)
- **Development:** [Developer guide](docs/developer-guide.md) · [Data protection](SECURITY.md)

Reviewed on November 9, 2026. Capability reference: committed version `af9cc6d`. Demo images use fictional data and do not establish production acceptance.
