## Detector validation results

Reference detectors over `ai4privacy/pii-masking-200k (en, 43501 records)` (43501 labeled items).

**Micro** (pooled): precision 0.982, recall 0.535, F1 0.693. **Macro** (mean of detectors): precision 0.824, recall 0.427, F1 0.484.

| detector | tp | fp | fn | tn | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| credit_card | 65 | 72 | 2501 | 40863 | 0.474 | 0.025 | 0.048 |
| email | 4043 | 12 | 0 | 39446 | 0.997 | 1.000 | 0.999 |
| us_ssn | 518 | 0 | 1514 | 41469 | 1.000 | 0.255 | 0.406 |
