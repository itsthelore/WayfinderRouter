## Detector validation results

Reference detectors over `ai4privacy/pii-masking-200k (en, 43501 records)` (43501 labeled items).

**Micro** (pooled): precision 0.710, recall 0.566, F1 0.630. **Macro** (mean of detectors): precision 0.713, recall 0.461, F1 0.513.

| detector | tp | fp | fn | tn | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| credit_card | 330 | 1990 | 2236 | 38945 | 0.142 | 0.129 | 0.135 |
| email | 4043 | 12 | 0 | 39446 | 0.997 | 1.000 | 0.999 |
| us_ssn | 518 | 0 | 1514 | 41469 | 1.000 | 0.255 | 0.406 |
