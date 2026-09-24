# Can AI Beat Wordle?

> 这份摘要由 `.science` 会话包解包后生成。研究结论来自包内 `session.json` 的最终实质性回答；本文没有重新运行原研究或独立核验外部主张。

## 包信息

- **包文件**：`Can a Simple Algorithm Beat AI at Wordle.science`
- **格式**：`open-science-session`，schema version `1`
- **创建时间戳**：`1790072695123`（包内 Unix 毫秒时间戳）
- **项目**：Can AI Beat Wordle?
- **会话标题**：How many guesses does AI usually need to solve Wordle? Tr...
- **项目 ID**：`cmuchpzre0000rxicgzmt5my5`
- **会话 ID**：`01a0c89a-3411-7040-a549-6f01a2551e02`
- **归档大小**：671,109 bytes；**解包后条目总大小**：5,454,927 bytes
- **条目数**：30（固定元数据文件 4，对象文件 26）
- **归档 SHA-256**：`6e816f1f92cbe4feeed9e3217c6f1bd0d9c046fd4ba653017b9812391f421610`

## 研究问题

- How many guesses does AI usually need to solve Wordle? Try it against a simple word-filtering algorithm using the same word list and answers. Who wins more often, and who needs fewer guesses?
- Can you just play the games yourself here, with a program giving you the clues? I want to see how you do against the simple algorithm.

## 会话结论

**I averaged 3.87 guesses; the simple algorithm averaged 3.80. Both solved all 30 games.**

| Result | Me (GPT-6-astra) | Filtering algorithm |
|---|---:|---:|
| Solved within six guesses | 30/30 | 30/30 |
| Average guesses | 3.87 | **3.80** |
| Median guesses | 4 | 4 |
| Finished in fewer guesses | 6 games | **9 games** |

The other **15 games were ties**. The algorithm used just two fewer guesses across the entire experiment—a small edge, not convincing evidence of general superiority.

Both used the same [2,315-word dictionary](https://gist.github.com/cfreshman/a03ef2cba789d8cf00c08f767e0fad7b), randomly selected answers, and **SLATE** opener. I chose guesses from the clues without seeing hidden answers or program-filtered candidates. The algorithm eliminated incompatible words and favored common letters.

So, **about four guesses for me in this pilot**. These games shared one conversation; they don’t establish how AI generally performs.

- wordle-results.csv（包内对象：`wordle-results.csv`，未单独提交） — Results for all 30 games.
- wordle-audit.zip（包内对象：`wordle-audit.zip`，未单独提交） — Every guess and clue, dictionary, sampling seed, and verified replay code.

## 包内容

| 路径/类别 | 数量或大小 | 说明 |
| --- | ---: | --- |
| `README.md` | 250 bytes | 固定协议条目 |
| `manifest.json` | 10,124 bytes | 固定协议条目 |
| `session.json` | 296,421 bytes | 固定协议条目 |
| `records.json` | 867,282 bytes | 固定协议条目 |
| `objects/<sha256>` | 26 个，共 4,280,850 bytes | 内容对象，名称为 64 位十六进制 SHA-256 键 |

### records.json 表计数

| 表 | 行数 |
| --- | ---: |
| `UploadFile` | 0 |
| `UploadVersion` | 0 |
| `ArtifactVersionInput` | 0 |
| `ArtifactMessageSnapshot` | 1 |
| `Review` | 0 |
| `Finding` | 0 |
| `ReviewFindingDisposition` | 0 |
| `ReviewScopeSnapshot` | 0 |
| `FileOriginSession` | 1 |
| `ArtifactLineage` | 2 |
| `ArtifactVersion` | 2 |

## 包声明的省略内容

- **excluded**：Account credentials, permission grants and provider continuation identities are excluded.

## 包内 README

# Open-Science Session package

Open-Science can inspect and import this archive as read-only research history. Import does not execute code or restore account credentials. Checksums verify bytes, not scientific claims or the identity of the sender.

## 解包协议说明

Open Science 的 session package 使用 gzip 压缩 tar；读取时先校验整个归档、条目类型、重复项、路径白名单、大小上限和兼容性，再写入新建目录。允许的固定文件包括 `manifest.json`、`session.json`、`records.json`、可选 `ro-crate-metadata.json`、`README.md` 和 `objects/<sha256>`。
