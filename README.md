# AIPOCH Open-Science 案例

本仓库收集完成的 Open-Science 会话导出、封面和介绍，根 `manifest.json` 是案例索引。

现有案例放在 `usecases/`，`catalog` 和 `catalog_name` 均为 `""`，表示无分类，原目录和路径保持不变。每个条目包含 `title`、唯一 kebab-case `name`、字符串 `catalog`、字符串 `catalog_name`，以及 `cover`、`case`、`introduction` 三种资源；每种资源都提供 `file_name`、`path`、`bytes`、`sha256`、`release_url`。URL 为空时采用本地文件，非空时采用远程资源；全远程条目可不创建本地目录，但仍保留规范 path。

非空 `catalog` 同时用于展示及根目录下的分类目录，三个资源的 `path` 均以 `<catalog>/` 开头，其下可自由分层、分目录。`catalog_name` 根据分类名称转换为 ASCII kebab-case，例如 `Data Science` → `data-science`；非 ASCII 名称、空转换结果、不同分类转换后撞名都报错，由用户调整。同一分类可以被多个案例复用。两个字段必须同时为空或同时填写。

```text
Data Science/示例目录/
  cover.png
  session.science
  introduction.md
```

目录、标题与 name 可独立命名。S3 始终使用 `<name>/<file_name>`，包解压内容位于 `<name>/extracted/`，目录迁移不改变 S3 资源键。

原始 `.science` 会话内容保持不变。案例中标明的模拟演示和未解决结论仍以源会话为准，收录不代表模型的一般性能保证。

- [新增和修改案例](AGENTS.md)
- [校验规则与命令](.github/CHECKING.md)
- [发布、去重与恢复](.github/PUBLISHING.md)

本地测试：`python3 -B -m unittest discover -s .github/tests -v`。测试使用临时数据及 fake S3，不执行真实发布。
