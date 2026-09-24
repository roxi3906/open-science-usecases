# Hotter Years, Hotter Days?

> 这份摘要由 `.science` 会话包解包后生成。研究结论来自包内 `session.json` 的最终实质性回答；本文没有重新运行原研究或独立核验外部主张。

## 包信息

- **包文件**：`Hotter Years, Hotter Days.science`
- **格式**：`open-science-session`，schema version `1`
- **创建时间戳**：`1790135719153`（包内 Unix 毫秒时间戳）
- **项目**：Hotter Years, Hotter Days?
- **会话标题**：Can the Hottest Ten Days Reveal What Averages Hi...
- **项目 ID**：`cmuczjzej0000rxl1c0724aa2`
- **会话 ID**：`01a0cc55-33a0-7401-b69f-3b37a2531ff9`
- **归档大小**：52,025,701 bytes；**解包后条目总大小**：206,478,955 bytes
- **条目数**：123（固定元数据文件 4，对象文件 119）
- **归档 SHA-256**：`34936fe69b5100365a4dbee4bb902e82c166675e073b07439112bd252f337389`

## 研究问题

- Can the Hottest Ten Days Reveal What Averages Hide?

Do a year’s ten hottest days reveal warming more clearly than its average temperature? Investigate New York’s Central Park using the latest available NOAA GHCN-Daily data (station USW00094728), starting in 1991. Download the data from https://www.ncei.noaa.gov/pub/data/ghcn/daily/ and use its documentation. Compare complete years and explain the evidence and limitations in English.
- The yearly completeness and both trend slopes match an independent check of the raw data. Before finalizing, check whether the comparison holds when allowing for correlation between years and using a robust trend estimate. Keep the conclusion specific to Central Park and this period. Then save an English report and reproducible package with the raw data, runnable code, source dates, quality checks and figures.

## 会话结论

The conclusion holds for Central Park during 1991–2025 after accounting for correlation between years and using a robust trend estimator.

| Metric | Newey–West OLS slope | 95% HAC CI | Theil–Sen slope | 95% CI |
|---|---:|---:|---:|---:|
| Annual mean temperature | **+0.305 °C/decade** | +0.159 to +0.452 | **+0.323 °C/decade** | +0.059 to +0.550 |
| Mean of ten hottest maxima | −0.164 °C/decade | −0.451 to +0.123 | −0.241 °C/decade | −0.736 to +0.400 |

Newey–West inference used three annual lags. The annual-mean trend remains clearly positive (`p = 0.00005`), whereas the hot-ten trend remains statistically indistinguishable from zero (`p = 0.264`). Theil–Sen estimates give the same interpretation. The negative hot-ten estimates are not persuasive evidence of cooling because their intervals are wide and cross zero.

All 35 years from 1991 through 2025 passed the completeness and quality-flag checks. The package includes the exact NOAA `.dly` file and documentation, station metadata, source timestamps and ETags, SHA-256 provenance, runnable Python code, dependencies, annual results, quality audits, statistical outputs, and a clean-run verification. The packaged code successfully regenerated matching trend results from a fresh copy.

- central_park_robustness_report.md（包内对象：`central_park_robustness_report.md`，未单独提交） — English report with methods, evidence, and limitations
- central_park_trend_robustness.png（包内对象：`central_park_trend_robustness.png`，未单独提交） — OLS and Theil–Sen trend comparison
- central_park_reproducible_package.zip（包内对象：`central_park_reproducible_package.zip`，未单独提交） — Raw NOAA data, documentation, runnable code, checks, results, and figures

## 包内容

| 路径/类别 | 数量或大小 | 说明 |
| --- | ---: | --- |
| `README.md` | 250 bytes | 固定协议条目 |
| `manifest.json` | 46,714 bytes | 固定协议条目 |
| `session.json` | 389,213 bytes | 固定协议条目 |
| `records.json` | 784,114 bytes | 固定协议条目 |
| `objects/<sha256>` | 119 个，共 205,258,664 bytes | 内容对象，名称为 64 位十六进制 SHA-256 键 |

### records.json 表计数

| 表 | 行数 |
| --- | ---: |
| `UploadFile` | 0 |
| `UploadVersion` | 0 |
| `ArtifactVersionInput` | 0 |
| `ArtifactMessageSnapshot` | 2 |
| `Review` | 0 |
| `Finding` | 0 |
| `ReviewFindingDisposition` | 0 |
| `ReviewScopeSnapshot` | 0 |
| `FileOriginSession` | 1 |
| `ArtifactLineage` | 6 |
| `ArtifactVersion` | 6 |

## 包声明的省略内容

- **excluded**：Account credentials, permission grants and provider continuation identities are excluded.

## 包内 README

# Open-Science Session package

Open-Science can inspect and import this archive as read-only research history. Import does not execute code or restore account credentials. Checksums verify bytes, not scientific claims or the identity of the sender.

## 解包协议说明

Open Science 的 session package 使用 gzip 压缩 tar；读取时先校验整个归档、条目类型、重复项、路径白名单、大小上限和兼容性，再写入新建目录。允许的固定文件包括 `manifest.json`、`session.json`、`records.json`、可选 `ro-crate-metadata.json`、`README.md` 和 `objects/<sha256>`。
