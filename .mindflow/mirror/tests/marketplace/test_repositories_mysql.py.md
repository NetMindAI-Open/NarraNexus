---
code_file: tests/marketplace/test_repositories_mysql.py
last_verified: 2026-07-22
stub: false
---

# test_repositories_mysql.py

MySQL env-gate(NARRANEXUS_MYSQL_TEST_URL)双方言测试:catalog/scan/team 三仓的 7 处手写 SQL 在真 MySQL 8.0 上跑(search LIKE/JSON 子串/dedup、list_defaults、increment、latest_for、team list/increment)。铁律级门禁要求。
