## Detector validation results

Reference detectors over `benchmarks/detector-corpus.jsonl` (49 labeled items).

**Micro** (pooled): precision 0.812, recall 0.867, F1 0.839. **Macro** (mean of detectors): precision 0.851, recall 0.914, F1 0.872.

| detector | tp | fp | fn | tn | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| aws_access_key | 2 | 0 | 0 | 47 | 1.000 | 1.000 | 1.000 |
| credit_card | 5 | 1 | 1 | 42 | 0.833 | 0.833 | 0.833 |
| email | 7 | 1 | 1 | 40 | 0.875 | 0.875 | 0.875 |
| github_pat | 2 | 0 | 0 | 47 | 1.000 | 1.000 | 1.000 |
| high_entropy_hex | 2 | 2 | 0 | 45 | 0.500 | 1.000 | 0.667 |
| private_key | 2 | 0 | 0 | 47 | 1.000 | 1.000 | 1.000 |
| slack_token | 3 | 0 | 0 | 46 | 1.000 | 1.000 | 1.000 |
| us_ssn | 3 | 2 | 2 | 42 | 0.600 | 0.600 | 0.600 |
