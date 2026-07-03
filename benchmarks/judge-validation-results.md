## Judge validation results

Judge `heuristic-1` replayed over `data/routerbench_0shot.pkl (mistral-7b vs gpt-4)` (absolute gold threshold 0.5; κ floor 0.6 for reference).

**Overall:** n=36497, decided=258, abstained=36239 (99.3%).

| bucket | n | abstain % | κ (absolute) | acc (absolute) | κ (relative) | acc (relative) |
| --- | --- | --- | --- | --- | --- | --- |
| overall | 36497 | 99.3% | 0.048 | 0.562 | 0.038 | 0.760 |
| Chinese_character_riddles | 100 | 98.0% | 0.000 | 0.500 | 0.000 | 0.500 |
| abstract2title | 254 | 96.9% | 1.000 | 1.000 | 1.000 | 1.000 |
| accounting_audit | 30 | 3.3% | 0.000 | 0.207 | 0.000 | 0.345 |
| arc-challenge | 1470 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| bias_detection | 285 | 98.9% | 0.000 | 0.667 | 0.000 | 0.667 |
| chinese-lantern-riddles | 20 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese-remainder-theorem | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_ancient_masterpieces_dynasty | 16 | 75.0% | 0.000 | 0.000 | 0.000 | 0.750 |
| chinese_ancient_poetry | 25 | 16.0% | 0.000 | 0.000 | 0.000 | 0.905 |
| chinese_chu_ci | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_famous_novel | 20 | 75.0% | 0.000 | 0.600 | -0.923 | 0.000 |
| chinese_hard_translations | 21 | 90.5% | 0.000 | 0.000 | 1.000 | 1.000 |
| chinese_homonym | 21 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_idioms | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_modern_poem_identification | 21 | 28.6% | 0.000 | 0.000 | 0.000 | 0.467 |
| chinese_poem | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_shi_jing | 34 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_song_ci | 16 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_tang_poetries | 37 | 89.2% | 0.000 | 0.000 | 0.000 | 0.750 |
| chinese_zodiac | 394 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| consensus_summary | 362 | 60.5% | 0.048 | 0.783 | 0.132 | 0.916 |
| grade-school-math | 7450 | 99.9% | -0.200 | 0.625 | 0.385 | 0.750 |
| hellaswag | 10042 | 100.0% | 0.000 | 0.250 | 0.000 | 0.000 |
| mbpp | 427 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-abstract-algebra | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-anatomy | 135 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-astronomy | 152 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-business-ethics | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-clinical-knowledge | 265 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-college-biology | 144 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-college-chemistry | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-college-computer-science | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-college-mathematics | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-college-medicine | 173 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-college-physics | 102 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-computer-security | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-conceptual-physics | 235 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-econometrics | 114 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-electrical-engineering | 145 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-elementary-mathematics | 378 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-formal-logic | 126 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-global-facts | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-biology | 310 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-chemistry | 203 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-computer-science | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-european-history | 165 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-geography | 198 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-government-and-politics | 193 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-macroeconomics | 390 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-mathematics | 270 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-microeconomics | 238 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-physics | 151 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-psychology | 545 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-statistics | 216 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-us-history | 204 | 99.5% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-world-history | 237 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-human-aging | 223 | 99.6% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-human-sexuality | 131 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-international-law | 121 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-jurisprudence | 108 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-logical-fallacies | 163 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-machine-learning | 112 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-management | 103 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-marketing | 234 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-medical-genetics | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-miscellaneous | 783 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-moral-disputes | 346 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-moral-scenarios | 895 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-nutrition | 306 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-philosophy | 311 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-prehistory | 324 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-professional-accounting | 282 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-professional-law | 1534 | 99.9% | 1.000 | 1.000 | 0.000 | 0.000 |
| mmlu-professional-medicine | 272 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-professional-psychology | 612 | 99.8% | 1.000 | 1.000 | 0.000 | 0.000 |
| mmlu-public-relations | 110 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-security-studies | 245 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-sociology | 201 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-us-foreign-policy | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-virology | 166 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-world-religions | 171 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mtbench | 41 | 92.7% | 0.000 | 0.667 | 0.400 | 0.667 |
| mtbench-math | 20 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mtbench-reference | 19 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| test-match | 3 | 0.0% | 1.000 | 1.000 | 0.000 | 0.667 |
| winogrande | 1267 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |

### Overall confusion (absolute gold, decided rows only)

| judge \ gold | insufficient | sufficient |
| --- | --- | --- |
| insufficient | 11 | 7 |
| sufficient | 106 | 134 |

### By comparator (decided rows, accuracy vs absolute gold)

| comparator | fired | decided | accuracy |
| --- | --- | --- | --- |
| agreement | 4 | 4 | 1.000 |
| divergence | 8870 | 0 | — |
| refusal | 27604 | 235 | 0.536 |
| similarity | 19 | 19 | 0.789 |
