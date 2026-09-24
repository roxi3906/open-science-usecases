# How Many Homes Could AI Data Centers Power?

> 这份摘要由 `.science` 会话包解包后生成。研究结论来自包内 `session.json` 的最终实质性回答；本文没有重新运行原研究或独立核验外部主张。

## 包信息

- **包文件**：`How Many Homes Could AI Data Centers Power.science`
- **格式**：`open-science-session`，schema version `1`
- **创建时间戳**：`1790079563873`（包内 Unix 毫秒时间戳）
- **项目**：How Many Homes Could AI Data Centers Power?
- **会话标题**：How much electricity do AI data centers use in a year, an...
- **项目 ID**：`cmucm4snu0001rxry3l971zqp`
- **会话 ID**：`01a0c8f4-92ef-7712-ae9b-4474a369433c`
- **归档大小**：5,142,441 bytes；**解包后条目总大小**：10,974,332 bytes
- **条目数**：56（固定元数据文件 4，对象文件 52）
- **归档 SHA-256**：`3fd660652a0f25a417a37e32fa011b2ec2a250031c3133e08467d7f23eb69e91`

## 研究问题

- How much electricity do AI data centers use in a year, and how many homes is that equivalent to? Use the latest public figures you can find, matching the year and region for the comparison. I'd like to see how the answer changes depending on what counts as AI data center use and how household electricity use is measured.
- Can you also work out the AI-only comparison for the U.S. using the AI-server breakdown in the Berkeley Lab report, including cooling and other facility overhead? Compare it with U.S. households in the same year, and make a chart showing how the different definitions change the answer. If a number has to be estimated from a chart, please make that clear.
- Here's Figure 9 from the report. Please use it to finish the calculation and chart, with any chart-reading estimate clearly marked.
- Can we update this with the newest data available? I'd like 2025 to be the main comparison, or 2026 if there is a credible estimate. Please keep the year and region matched, distinguish estimates from measurements, and update the chart. If a newer AI-only comparison isn't possible, explain exactly which figure is missing rather than treating all data centers as AI.
- I found the source data for Figure 1 of this paper: https://www.nature.com/articles/s41893-025-01681-y. Could its 2025 and 2026 U.S. AI projections give us a same-year comparison? Please check whether cooling is already included, use the latest EIA household figures for each year, and show these as model projections rather than measured consumption.

## 会话结论

**Yes—the spreadsheet fills the earlier gap for a model-based comparison.** Its mid-case U.S. AI projections correspond to **6.18 million homes in 2025** and **11.24 million in 2026**.

| Year | AI electricity, including overhead | EIA annual electricity per residential customer | Equivalent homes |
|---|---:|---:|---:|
| 2025 | 64.77 TWh — model projection | 10,479 kWh — historical estimate | **6.18 million** |
| 2026 | 117.90 TWh — model projection | 10,487 kWh — forecast | **11.24 million** |

**Cooling is already included.** The paper calculates electricity using server consumption and regional PUE, which incorporates facility overhead. Applying another cooling multiplier would double-count it. These are the paper’s 2025-vintage projections for top-tier AI servers, not measured national consumption. [Paper and methods](https://www.nature.com/articles/s41893-025-01681-y).

The household denominators come from the latest available **September 2026 EIA outlook**. “Homes” means mean purchased-electricity equivalents per residential utility customer—not median household consumption or total household energy. Both year and region match. [EIA Table 7a](https://www.eia.gov/outlooks/steo/tables/pdf/7atab.pdf).

Across the five spreadsheet scenarios, the equivalents span **6.00–6.18 million in 2025** and **11.06–11.24 million in 2026**. These are scenario ranges, not confidence intervals. All values came directly from the spreadsheet; **no chart-reading estimates were needed**.

图像文件：`xiao_ai_us_household_projections_2025_2026.png`（包内对象，未单独提交）

- xiao_ai_us_household_projections_2025_2026.png（包内对象：`xiao_ai_us_household_projections_2025_2026.png`，未单独提交） — Updated AI-only projection chart.
- xiao_ai_us_2025_2026_calculations.csv（包内对象：`xiao_ai_us_2025_2026_calculations.csv`，未单独提交） — All five scenarios and conversions.
- xiao_ai_household_comparison_methods.md（包内对象：`xiao_ai_household_comparison_methods.md`，未单独提交） — Cooling verification, sources, scope, and reproducible calculations.

## 包内容

| 路径/类别 | 数量或大小 | 说明 |
| --- | ---: | --- |
| `README.md` | 250 bytes | 固定协议条目 |
| `manifest.json` | 20,535 bytes | 固定协议条目 |
| `session.json` | 739,934 bytes | 固定协议条目 |
| `records.json` | 990,560 bytes | 固定协议条目 |
| `objects/<sha256>` | 52 个，共 9,223,053 bytes | 内容对象，名称为 64 位十六进制 SHA-256 键 |

### records.json 表计数

| 表 | 行数 |
| --- | ---: |
| `UploadFile` | 2 |
| `UploadVersion` | 2 |
| `ArtifactVersionInput` | 1 |
| `ArtifactMessageSnapshot` | 3 |
| `Review` | 0 |
| `Finding` | 0 |
| `ReviewFindingDisposition` | 0 |
| `ReviewScopeSnapshot` | 0 |
| `FileOriginSession` | 1 |
| `ArtifactLineage` | 7 |
| `ArtifactVersion` | 7 |

## 包声明的省略内容

- **excluded**：Account credentials, permission grants and provider continuation identities are excluded.

## 包内 README

# Open-Science Session package

Open-Science can inspect and import this archive as read-only research history. Import does not execute code or restore account credentials. Checksums verify bytes, not scientific claims or the identity of the sender.

## 解包协议说明

Open Science 的 session package 使用 gzip 压缩 tar；读取时先校验整个归档、条目类型、重复项、路径白名单、大小上限和兼容性，再写入新建目录。允许的固定文件包括 `manifest.json`、`session.json`、`records.json`、可选 `ro-crate-metadata.json`、`README.md` 和 `objects/<sha256>`。
