# Our Nearest Exoplanet: How Long Is the Trip?

> 这份摘要由 `.science` 会话包解包后生成。研究结论来自包内 `session.json` 的最终实质性回答；本文没有重新运行原研究或独立核验外部主张。

## 包信息

- **包文件**：`Our Nearest Exoplanet - How Long Is the Trip.science`
- **格式**：`open-science-session`，schema version `1`
- **创建时间戳**：`1790141639705`（包内 Unix 毫秒时间戳）
- **项目**：Our Nearest Exoplanet: How Long Is the Trip?
- **会话标题**：Our Nearest Exoplanet: How Long Is the Trip? How...
- **项目 ID**：`cmuczl7zl0002rxl1cpc47idb`
- **会话 ID**：`01a0ccae-5e87-7a91-a7f0-e281a1164aa2`
- **归档大小**：3,254,700 bytes；**解包后条目总大小**：12,995,561 bytes
- **条目数**：110（固定元数据文件 4，对象文件 106）
- **归档 SHA-256**：`05d7f12b19999e70fe04d3819788b15b428aebeed54f06d8474f80384ec47d5a`

## 研究问题

- Our Nearest Exoplanet: How Long Is the Trip?

How long would it take to reach the nearest confirmed exoplanet at 17 km/s, 1%, 10% and 90% of light speed? Download the latest distance data from the NASA Exoplanet Archive using https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html. Compare the time on Earth with the time experienced onboard, and explain what these hypothetical journeys leave out.
- Please package the study with a short report, a comparison chart, the original NASA data and runnable code that reproduces the results from that data. Include the download date and distance uncertainty, and check that the saved code reproduces the table.
- Please verify the saved reproduction script using the existing study artifacts in this session’s writable notebook folder. Keep all inputs and outputs inside that folder, then compare the regenerated table with the delivered results.

## 会话结论

Verification completed successfully inside the session’s writable notebook folder.

- The saved `reproduce.py` exited with code 0.
- It correctly identified Proxima Cen b and d at 1.30119000 pc.
- All four speeds and central calculated fields match the delivered table exactly.
- Uncertainty fields differ only through floating-point evaluation order:
  - Maximum absolute difference: \(7.78\times10^{-12}\) years, about 0.00025 milliseconds.
  - Maximum relative difference: \(1.48\times10^{-12}\).
- The files are numerically equivalent, although not byte-identical.
- The script also regenerated the chart successfully.

- verified_regenerated_journey_times.csv（包内对象：`verified_regenerated_journey_times.csv`，未单独提交） — Table regenerated directly by the saved script.
- verified_regenerated_travel_time_comparison.png（包内对象：`verified_regenerated_travel_time_comparison.png`，未单独提交） — Chart regenerated during verification.

## 包内容

| 路径/类别 | 数量或大小 | 说明 |
| --- | ---: | --- |
| `README.md` | 250 bytes | 固定协议条目 |
| `manifest.json` | 41,715 bytes | 固定协议条目 |
| `session.json` | 456,379 bytes | 固定协议条目 |
| `records.json` | 741,132 bytes | 固定协议条目 |
| `objects/<sha256>` | 106 个，共 11,756,085 bytes | 内容对象，名称为 64 位十六进制 SHA-256 键 |

### records.json 表计数

| 表 | 行数 |
| --- | ---: |
| `UploadFile` | 0 |
| `UploadVersion` | 0 |
| `ArtifactVersionInput` | 0 |
| `ArtifactMessageSnapshot` | 3 |
| `Review` | 0 |
| `Finding` | 0 |
| `ReviewFindingDisposition` | 0 |
| `ReviewScopeSnapshot` | 0 |
| `FileOriginSession` | 1 |
| `ArtifactLineage` | 9 |
| `ArtifactVersion` | 11 |

## 包声明的省略内容

- **excluded**：Account credentials, permission grants and provider continuation identities are excluded.

## 包内 README

# Open-Science Session package

Open-Science can inspect and import this archive as read-only research history. Import does not execute code or restore account credentials. Checksums verify bytes, not scientific claims or the identity of the sender.

## 解包协议说明

Open Science 的 session package 使用 gzip 压缩 tar；读取时先校验整个归档、条目类型、重复项、路径白名单、大小上限和兼容性，再写入新建目录。允许的固定文件包括 `manifest.json`、`session.json`、`records.json`、可选 `ro-crate-metadata.json`、`README.md` 和 `objects/<sha256>`。
