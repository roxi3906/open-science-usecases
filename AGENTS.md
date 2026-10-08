# 案例新增规则

- 新增案例目录时，若目录内不存在 `.science` 包，必须在根目录 `manifest.json` 的对应案例条目中填写 `case.release_url`，指向该案例 `.science` 包的下载地址；不得缺失、为空字符串或仅包含空白字符。
- 若目录内已有 `.science` 包，`case.release_url` 可以为空；`case.path` 与 `case.release_url` 可以同时保留。
- 完成新增案例前，逐一检查新增目录及其 manifest 条目，确保满足上述要求。
