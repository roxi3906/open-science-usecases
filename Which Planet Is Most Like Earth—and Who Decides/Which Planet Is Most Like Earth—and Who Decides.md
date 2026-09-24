# Which Planet Is Most Like Earth—and Who Decides?

> 这份摘要由 `.science` 会话包解包后生成。研究结论来自包内 `session.json` 的最终实质性回答；本文没有重新运行原研究或独立核验外部主张。

## 包信息

- **包文件**：`Which Planet Is Most Like Earth—and Who Decides.science`
- **格式**：`open-science-session`，schema version `1`
- **创建时间戳**：`1790138596237`（包内 Unix 毫秒时间戳）
- **项目**：Which Planet Is Most Like Earth—and Who Decides?
- **会话标题**：Which Planet Is Most Like Earth——and Who Decides
- **项目 ID**：`cmuczkl6s0001rxl1gb17unbv`
- **会话 ID**：`01a0cc81-502b-7fa3-b05e-b034405f6dfc`
- **归档大小**：12,658,199 bytes；**解包后条目总大小**：26,680,061 bytes
- **条目数**：90（固定元数据文件 4，对象文件 86）
- **归档 SHA-256**：`784d666fe41c780bb691eb7e01ff995028e051ce4f955dd056ce7572dc58b1b7`

## 研究问题

- Which Planet Is Most Like Earth——and Who Decides
- Download the latest NASA Exoplanet Archive data and compare rankings based on size, temperature and distance from Earth. Does changing what we value change the winner? Use https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html and the archive’s parameter definitions.
- Please package the full report, data, figures and runnable analysis code so someone else can reproduce the rankings. Keep the original NASA download separate from the table with calculated columns, record when it was downloaded, and check any tied winners.

## 会话结论

Packaged and integrity-checked. The ZIP contains:

- Unmodified NASA TAP response in `data/raw/`
- UTC retrieval timestamp, exact query/URL, HTTP metadata, and SHA-256 checksum
- Separate calculated full table in `data/derived/`
- Top rankings and explicit winner-tie audit
- Two generated figures
- Full Markdown report
- Standalone `code/analyze_rankings.py`
- Dependency list, README, run summary, and checksum manifest

Download details:

- Retrieved: `2026-09-23T04:36:25.426652+00:00`
- NASA rows: 6,366
- Raw response SHA-256: `b5914b91571df05fb220cf8addebf18a8114b349c86471c4b0a9f5b216b3982a`
- ZIP validation: passed with no errors

Tie audit:

- Size-only: eight planets tied at exactly 1.0 Earth radius.
- Temperature-only: HD 40307 g is the unique 255 K winner.
- Distance-only: Proxima Centauri b and d are tied because they belong to the same system.
- All five composite rankings have unique winners.

Reproduction from the extracted package:

```sh
python -m pip install -r requirements.txt
python code/analyze_rankings.py
```

This regenerates the calculated table, rankings, tie audit, report, and figures without modifying the original NASA download.

- earth_like_exoplanet_reproducibility_2026-09-23.zip（包内对象：`earth_like_exoplanet_reproducibility_2026-09-23.zip`，未单独提交） — complete reproducibility package
- earth_like_exoplanet_full_report.md（包内对象：`earth_like_exoplanet_full_report.md`，未单独提交） — standalone report for convenient review

## 包内容

| 路径/类别 | 数量或大小 | 说明 |
| --- | ---: | --- |
| `README.md` | 250 bytes | 固定协议条目 |
| `manifest.json` | 34,211 bytes | 固定协议条目 |
| `session.json` | 389,285 bytes | 固定协议条目 |
| `records.json` | 930,980 bytes | 固定协议条目 |
| `objects/<sha256>` | 86 个，共 25,325,335 bytes | 内容对象，名称为 64 位十六进制 SHA-256 键 |

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
