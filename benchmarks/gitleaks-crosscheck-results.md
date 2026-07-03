## Secret-detector cross-check vs gitleaks

Our secret detectors against the community-standard gitleaks ruleset. `agree` is over a small fragment-assembled probe set (both fire / both silent).

| detector | gitleaks rule | agree | our fires | gitleaks fires |
| --- | --- | --- | --- | --- |
| aws_access_key | `aws-access-token` | 2/3 | 1/3 | 2/3 |
| github_pat | `github-pat` | 2/2 | 1/2 | 1/2 |
| private_key | `private-key` | 1/2 | 1/2 | 0/2 |
| slack_token | `slack-bot-token` | 1/2 | 1/2 | 0/2 |

*No direct gitleaks counterpart:* email, us_ssn, credit_card, high_entropy_hex (gitleaks detects generic secrets by entropy, a different approach).

### Regexes (ours vs gitleaks)

- **aws_access_key**
  - ours: `\bAKIA[0-9A-Z]{16}\b`
  - gitleaks: `\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\b`
- **github_pat**
  - ours: `\bghp_[A-Za-z0-9]{36}\b`
  - gitleaks: `ghp_[0-9a-zA-Z]{36}`
- **private_key**
  - ours: `-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----`
  - gitleaks: `(?i)-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----[\s\S-]{64,}?KEY(?: BLOCK)?-----`
- **slack_token**
  - ours: `\bxox[baprs]-[A-Za-z0-9-]{10,}\b`
  - gitleaks: `xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*`
