---
code_file: frontend/src/__tests__/locale-parity.test.ts
last_verified: 2026-09-03
stub: false
---

# locale-parity.test — 不许再产生新的 locale 键漂移

2026-09-03 新建。`chat.team.guide.relay` 的「4 次」8/20 提 cap 时漏改、本次又只改了 zh/en,
新键 `chat.team.manage.*` 同样只落 2/10,review 才抓到。i18next 的 fallbackLng 只兜**缺键**,
内容过期不兜。

**现状不是全键平价**:dev 基线上 ar/de/es/fr/ja/ko/pt/ru 各缺 en 约 480 个键、多 10-19 个
(复数形式 `_few/_many/_two/_zero` 与 `settings.quota.*`),zh 完全对齐。所以测试钉的是
`locale-parity.baseline.json` 这份快照:每个 locale 相对 en 不得出现**新的**缺键或多键;
把积压键翻掉(缺口变小)随时允许。重新生成快照:

```
python3 - <<'PY'
import json,glob
def flat(o,p=''):
    return [p] if not isinstance(o,dict) else [x for k,v in o.items() for x in flat(v,f'{p}.{k}' if p else k)]
en=set(flat(json.load(open('src/i18n/locales/en.json')))); base={}
for f in sorted(glob.glob('src/i18n/locales/*.json')):
    loc=f.split('/')[-1][:-5]
    if loc!='en':
        k=set(flat(json.load(open(f)))); base[loc]={'missing':sorted(en-k),'extra':sorted(k-en)}
json.dump(base,open('src/__tests__/locale-parity.baseline.json','w'),ensure_ascii=False,indent=1)
PY
```
