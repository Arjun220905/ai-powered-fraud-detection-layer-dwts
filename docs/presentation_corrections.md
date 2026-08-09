# Required corrections for the final presentation

Use these statements in the final deck; the first-review PDF must not be presented
unchanged.

## Slide 3 - Objective and measurable outcomes

- Replace "identifies fraudulent activities before confirmation" with:
  "provides provider-visible pending-transaction risk signals and pre-broadcast
  screening, then applies persistent DWTS updates after confirmation."
- Accuracy: 98.42% on the wallet-group-separated held-out test set.
- False-positive rate: 0.85% on the held-out test set.
- Local API p99 latency: 38.575 ms across 100 sequential requests.
- DWTS convergence definition: within five score points of the repeated-prediction
  steady state; 7 safe or 10 consistently fraudulent observations from score 70.

## Slide 6 - Methodology

- Replace "every wallet in the network" with "every wallet observed in the configured
  simulated, provider-visible pending, or confirmed-block feed."
- State that pending decisions are advisory and never change DWTS before confirmation.
- State that a transaction can only be held or rejected before broadcast when the
  submitting application calls `POST /api/screen-transaction`.
- Replace "feature selection" with "numeric behavior feature filtering" unless a
  separate selection stage is added. The current pipeline uses 45 behavior features.

## Slides 7 and 8 - Architecture and flow

- Use the implemented 0-100 thresholds: Approve 75-100, Delay 55-74.99,
  Verify 30-54.99, and Block below 30.
- Add the cold-start `Observe` state for wallets with fewer than 10 confirmed events.
- Show pending assessments as advisory; only confirmed observations update DWTS.

## Slide 9 - Corrective measures

- The dataset contains 9,841 labelled wallet snapshots, not one million records.
- The three measured models are Logistic Regression, Random Forest, and XGBoost.
- SVM and LSTM were not used; LSTM is not justified by the static wallet-snapshot schema.

## Slide 10 - Results

- XGBoost: accuracy 98.42%, precision 96.98%, recall 95.87%, F1 96.42%,
  ROC-AUC 99.88%, PR-AUC 99.61%.
- State that the test set contains 1,968 rows, uses wallet-group-aware stratification,
  and has zero wallet overlap with training.
- DWTS is stateful and persistent for confirmed observations.
- Pending results track pending, confirmed, replaced, and dropped states.
- Model adaptation is reviewed-label retraining with explicit version promotion;
  the deployed model does not silently learn online.

## Slide 14 - References

Remove any reference that cannot be verified by DOI, publisher page, or complete paper.
At minimum retain:

1. T. Chen and C. Guestrin, "XGBoost: A Scalable Tree Boosting System,"
   KDD 2016, DOI: 10.1145/2939672.2939785.
2. "Near Real-Time Ethereum Fraud Detection Using Explainable AI in Blockchain
   Networks," Applied Sciences 15(19), 2025, DOI: 10.3390/app151910841.
3. Official FastAPI, scikit-learn, XGBoost, Web3.py, Ethereum, and Alchemy
   pending-subscription documentation used by the implementation.
