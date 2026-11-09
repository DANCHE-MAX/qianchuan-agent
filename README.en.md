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

Reviewed on November 9, 2026. Capability reference: committed version `309a819`. Demo images use fictional data and do not establish production acceptance.

## New: local, read-only business dashboard

The latest dashboard brings together daily reports, comparison estimates, seven-day product trends, and questions about recorded results. All real data and configuration stay on the local machine.

| Business need | What the dashboard shows |
| --- | --- |
| Review results for a date | Recorded amounts and verification status; missing dates are not replaced with older reports |
| Compare profit assumptions | “Current-policy estimate” and “comparison estimate,” separate from the original report |
| Review products and campaigns | Recognizable product names, campaign totals, and links to historical report attachments |
| Explore additional ad spend | The GMV needed to cover an assumed increase in ad spend; not a sales forecast or executable budget |
| Review a missing historical date | Explicitly enabled, read-only product-campaign backfills, labeled as estimates, excluding livestream, and not sent |

The model uses confirmed tiered refund assumptions, a procurement multiplier of 3.2, and a 13.10% fee rate. Shipping and fixed costs are not included. These are modeling assumptions, not the day’s actual refund rate or final settlement profit. Historical reports retain their original values; comparisons across a policy change remain subject to verification.

Configure the approved local `data/model_policy/authority.json` file in the runtime directory; never commit it. Start the dashboard with:

```sh
.venv/bin/python tools/dashboard.py --root . --shadow-root /path/to/local/shadow
```

Open `http://127.0.0.1:8768` on the same machine. See the [detailed rules — Chinese](docs/current-profit-rules.md) for policy, verification, and operating boundaries.

![Local read-only dashboard — entirely fictional demo data, Chinese interface](docs/assets/readonly-dashboard-demo.jpg)

### Product names and attribution

Confirmed local identity evidence can supply product names without rewriting reports or delivery records. Historical names are marked as historical; company style codes are never guessed from titles, campaign names, or prices. New reports recheck campaign bindings rather than assuming old mappings still apply.

Single-product attribution can be confirmed from the matching account and date’s campaign list, including zero-spend campaigns. Multi-product and livestream campaigns remain unassigned when the necessary evidence is absent; they are not classified as store-managed by default. Real mappings and evidence remain local.

The current dashboard uses shorter display names derived from the original names. Full names remain available on hover and in search. Duplicate short names receive distinguishing labels; separate products are not merged. The dashboard no longer displays the “style code pending verification” wording, but underlying missing-value records remain. Product-attribution and amount gaps are still visible. Historical downloaded attachments keep their original labels.

### Product details and confirmed refund differences

Daily profit-chart supplementary pages and Excel can include returned product-level GMV and spend within a campaign. Campaign totals are counted once; product details do not count again toward profit, rankings, or pie-chart totals. Missing product-day and livestream bindings are not filled with zero.

**The dashboard no longer displays the within-campaign product-detail section.** Campaign GMV, spend, estimated profit, and totals remain. Backend details, missing-data reasons, daily supplementary pages, and Excel are retained.

Confirmed refund-related differences are handled per metric and date. Independently verified spend remains visible, while profit contribution uses the corresponding original daily report. A GMV difference is not deducted again as a profit loss. Unconfirmed contradictions still block delivery; other missing values remain explicitly unverified.

### Historical backfills and clearer presentation

Historical backfills must be explicitly enabled and cover only complete past days for product campaigns, excluding livestream. Ordinary daily reporting still uses yesterday. Backfills retain pagination, date, independent-total, and zero-spend GMV recheck requirements; failure or missing data is not a real zero.

Validated backfills are a fallback for missing dashboard dates; completed original reports take priority. Trends and answers label them as backfill estimates, excluding livestream and not sent. They use the currently confirmed policy and do not impersonate reports previously delivered. File-hash or scope mismatches leave data unverified. Backfill evidence never changes historical reports or delivery status.

The business dashboard provides expandable “How to read this” help while keeping amounts and missing-data labels visible. This applies to the dashboard interface; homepage demo images remain directly displayed. Clearly typed livestream records are hidden from the specified product and comparison views without changing source data, account totals, or live advertising. The former “Anomalies and recovery” dashboard panel has been removed; background monitoring remains active.

Answers use separate lines for rankings and notes, with the data date at the bottom. Missing-value, business-rule estimate, and budget-boundary notices remain. Text is rendered as plain text, not executable HTML.

### More reliable alert delivery and collection

ROI in anomaly cards is an observed ratio for the snapshot interval, not full-day ROI, an exact order period, or accounting profit. Persistent event identities help avoid reporting the same metric revision twice while keeping genuinely new campaign or interval events.

Within one runtime directory, API readers share conservative request spacing and temporary-error cooldowns. This is a local safeguard, not a published platform quota. Local diagnostics classify failures without exposing credentials, identities, request parameters, or raw exception text. Persistent failures still trigger the existing notices; failures are never replaced with zero values. Router, Wi-Fi, and proxy configuration is not changed.

### Acceptance checks remain separate from implementation

Product naming, attribution, supplementary details, and refund-display checks are included in the existing next daily-report acceptance run: November 10, 2026, after 10:05 Beijing time, for the November 9 report and November 8–9 comparison. This is a scheduled check, not evidence that it has passed.

Checks use that run’s actual evidence and delivered attachments: consistent names and amounts, no double counting, all profit pages before Excel, one native mention per daily package, and group-history checks for duplicate delivery. Historical coverage counts are not reused as current coverage. When no real refund difference occurs, only isolated synthetic cases are tested; no production event or extra group message is created.

These updates do not resume feedback, send additional group messages, or automatically change budgets, ROI targets, or campaign status. Real data, backups, configuration, and group or staff identities remain local.
