## Judge validation results

Judge `heuristic-2` replayed over `data/routerbench_0shot.pkl (mistral-7b vs gpt-4)` (absolute gold threshold 0.5; κ floor 0.6 for reference).

**Overall:** n=36497, decided=2811, abstained=33686 (92.3%).

| bucket | n | abstain % | κ (absolute) | acc (absolute) | κ (relative) | acc (relative) |
| --- | --- | --- | --- | --- | --- | --- |
| overall | 36497 | 92.3% | -0.001 | 0.816 | 0.333 | 0.999 |
| Chinese_character_riddles | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| abstract2title | 254 | 96.9% | 1.000 | 1.000 | 1.000 | 1.000 |
| accounting_audit | 30 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| arc-challenge | 1470 | 79.3% | 0.000 | 0.951 | 1.000 | 1.000 |
| bias_detection | 285 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese-lantern-riddles | 20 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese-remainder-theorem | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_ancient_masterpieces_dynasty | 16 | 93.8% | 0.000 | 0.000 | 1.000 | 1.000 |
| chinese_ancient_poetry | 25 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_chu_ci | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_famous_novel | 20 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_hard_translations | 21 | 90.5% | 0.000 | 0.000 | 1.000 | 1.000 |
| chinese_homonym | 21 | 0.0% | 0.000 | 0.429 | 1.000 | 1.000 |
| chinese_idioms | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_modern_poem_identification | 21 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_poem | 15 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_shi_jing | 34 | 97.1% | 1.000 | 1.000 | 1.000 | 1.000 |
| chinese_song_ci | 16 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_tang_poetries | 37 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| chinese_zodiac | 394 | 93.1% | 0.000 | 0.704 | 1.000 | 1.000 |
| consensus_summary | 362 | 98.9% | 1.000 | 1.000 | 0.000 | 0.750 |
| grade-school-math | 7450 | 99.9% | 0.000 | 0.714 | 0.000 | 0.714 |
| hellaswag | 10042 | 80.5% | 0.000 | 0.788 | 1.000 | 1.000 |
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
| mmlu-college-medicine | 173 | 99.4% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-college-physics | 102 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-computer-security | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-conceptual-physics | 235 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-econometrics | 114 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-electrical-engineering | 145 | 99.3% | 0.000 | 0.000 | 1.000 | 1.000 |
| mmlu-elementary-mathematics | 378 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-formal-logic | 126 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-global-facts | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-biology | 310 | 99.7% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-high-school-chemistry | 203 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-computer-science | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-european-history | 165 | 99.4% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-high-school-geography | 198 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-government-and-politics | 193 | 99.0% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-high-school-macroeconomics | 390 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-mathematics | 270 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-microeconomics | 238 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-physics | 151 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-psychology | 545 | 99.6% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-high-school-statistics | 216 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-high-school-us-history | 204 | 96.1% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-high-school-world-history | 237 | 97.9% | 0.000 | 0.800 | 1.000 | 1.000 |
| mmlu-human-aging | 223 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-human-sexuality | 131 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-international-law | 121 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-jurisprudence | 108 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-logical-fallacies | 163 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-machine-learning | 112 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-management | 103 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-marketing | 234 | 99.6% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-medical-genetics | 100 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-miscellaneous | 783 | 99.5% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-moral-disputes | 346 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-moral-scenarios | 895 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-nutrition | 306 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-philosophy | 311 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-prehistory | 324 | 99.4% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-professional-accounting | 282 | 99.6% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-professional-law | 1534 | 99.7% | 0.000 | 0.800 | 1.000 | 1.000 |
| mmlu-professional-medicine | 272 | 99.3% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-professional-psychology | 612 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-public-relations | 110 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-security-studies | 245 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-sociology | 201 | 99.5% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-us-foreign-policy | 100 | 99.0% | 1.000 | 1.000 | 1.000 | 1.000 |
| mmlu-virology | 166 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mmlu-world-religions | 171 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mtbench | 41 | 92.7% | 0.000 | 0.667 | 0.400 | 0.667 |
| mtbench-math | 20 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| mtbench-reference | 19 | 100.0% | 0.000 | 0.000 | 0.000 | 0.000 |
| test-match | 3 | 66.7% | 1.000 | 1.000 | 1.000 | 1.000 |
| winogrande | 1267 | 65.8% | 0.000 | 0.868 | 1.000 | 1.000 |

### Overall confusion (absolute gold, decided rows only)

| judge \ gold | insufficient | sufficient |
| --- | --- | --- |
| insufficient | 0 | 1 |
| sufficient | 515 | 2295 |

### By comparator (decided rows, accuracy vs absolute gold)

| comparator | fired | decided | accuracy |
| --- | --- | --- | --- |
| agreement | 2790 | 2790 | 0.817 |
| divergence | 33686 | 0 | — |
| refusal | 2 | 2 | 0.500 |
| similarity | 19 | 19 | 0.789 |
