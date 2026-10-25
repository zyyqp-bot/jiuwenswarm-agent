# 语料来源清单

`ai-governance/` 下的文件均为公开法律法规原文，抓取日期 2026-10-24（Asia/Shanghai）。正文来自下列官方页面，保留条号与章节标题；抓取脚本只做 HTML 去标签与段落合并，不改写条文。

| 文件 | 条数 | 发布/通过 | 施行 | 来源 |
|---|---:|---|---|---|
| 生成式人工智能服务管理暂行办法.md | 24 | 国家互联网信息办公室等七部门，第15号令，2023-07-13 | 2023-08-15 | <https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm> |
| 人工智能生成合成内容标识办法.md | 14 | 国家互联网信息办公室等四部门，国信办通字〔2025〕2号，2025-03-14 | 2025-09-01 | <https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm> |
| 互联网信息服务算法推荐管理规定.md | 35 | 国家互联网信息办公室等四部门，第9号令，2021-12-31 | 2022-03-01 | <https://www.cac.gov.cn/2022-01/04/c_1642894606364259.htm> |
| 互联网信息服务深度合成管理规定.md | 25 | 国家互联网信息办公室等三部门，第12号令，2022-11-25 | 2023-01-10 | <https://www.cac.gov.cn/2022-12/11/c_1672221949354811.htm> |
| 中华人民共和国个人信息保护法.md | 74 | 第十三届全国人大常委会第三十次会议，2021-08-20 | 2021-11-01 | <http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html> |
| 中华人民共和国数据安全法.md | 55 | 第十三届全国人大常委会第二十九次会议，2021-06-10 | 2021-09-01 | <http://www.npc.gov.cn/npc/c2/c30834/202106/t20210610_311888.html> |

`education-policy/` 是第二个最小工作区的语料：教育领域的公开法律与政策文件，抓取日期 2026-10-25。用途是验证“换语料 + 换提示词即可运行”和两个工作区互不泄漏检索结果，不追求完整的教育法体系。抓取脚本见 [scripts/fetch_education_corpus.py](../scripts/fetch_education_corpus.py)。

| 文件 | 来源 |
|---|---|
| 中华人民共和国教育法.md | <https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202205/t20220527_1070804.html> |
| 中华人民共和国学位法.md | <https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202405/t20240513_1122444.html> |
| 中华人民共和国学前教育法.md | <http://www.moe.gov.cn/jyb_sjzl/sjzl_zcfg/zcfg_jyfl/202411/t20241108_1161363.html> |
| 中华人民共和国义务教育法.md | <http://www.moe.gov.cn/jyb_sjzl/sjzl_zcfg/zcfg_jyfl/202110/t20211029_575949.html> |
| 湖南省职业教育条例.md | <https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202412/t20241209_33521114.html> |
| 未成年人网络保护条例.md | <https://www.gov.cn/zhengce/zhengceku/202310/content_6911289.htm> |
| 湖南省实施未成年人保护法办法.md | <http://www.hunan.gov.cn/hnszf/szf/hnzb_18/c102939/202411/02222gqgsdsddtcfvurptpusubvvggrfbfffbrhukgepdkfqnfkqserucnbsqdb/202406/t20240614_33327758.html> |
| 湖南省预防中小学生溺水若干规定.md | <http://www.hunan.gov.cn/hnszf/szf/hnzb_18/c102939/202411/02222gqgsdsddtcfvurptpusubvvggrfbfffbrhukgepdkfqnfkqserucnbsqdb/202406/t20240614_33327763.html> |
| 2026年湖南省普通高校招生工作通知.md | <https://jyt.hunan.gov.cn/jyt/sjyt/xxgk/zcfg/flfg/202606/t20260601_1179223.html> |

## 待补

- 《中华人民共和国网络安全法》：2025-10-28 修正、2026-01-01 施行；国家法律法规数据库只提供 OFD/PDF 合并文本，尚未取得可核对的纯文本，暂不纳入，避免引用过期条文。
- 《生成式人工智能服务安全基本要求》（TC260-003）：GB/T 45654—2025，标准文本需从全国标准信息公共服务平台下载。
- 欧盟 AI Act 等域外文本：当前工作区只面向中国境内合规，暂不纳入。

新增文档时：放进 `ai-governance/`、在上表补一行、重新导入并写一条检索断言或手工验收记录。
