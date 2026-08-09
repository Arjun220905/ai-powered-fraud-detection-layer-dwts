# Dataset

This project uses Vagif Aliyev's public Kaggle
[Ethereum Fraud Detection Dataset](https://www.kaggle.com/datasets/vagifa/ethereum-frauddetection-dataset).
Kaggle lists its license as **Database: Open Database, Contents: Database Contents**;
users should review the source page and Kaggle terms before redistributing it. The supplied
`archive.zip` was extracted here as `transaction_dataset.csv` (9,841 wallet activity
rows, 51 source columns).

If the CSV is missing, download it from Kaggle's Ethereum Fraud Detection Dataset page
and place it at `data/transaction_dataset.csv`. The target column is `FLAG`; `Address`
identifies the wallet. The leading unnamed export column and `Index` are ignored.

Important: a row is an aggregated historical wallet snapshot, not a raw Ethereum
transaction. The simulator replays these snapshots to demonstrate streaming behavior.
Some wallets have repeated snapshots. Training, tuning, calibration, cross-validation,
and testing keep every wallet address in only one partition to prevent leakage.
