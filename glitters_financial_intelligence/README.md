# GLITTERS Financial Intelligence Engine (`glitters_financial_intelligence` v1.0.0)

**Canonical Modular Python Distribution**

A mathematically rigorous, ORM-independent computational accounting and financial intelligence system designed for multi-tenant property operations, real estate portfolios, and commercial transactions.

## Eleven Non-Negotiable Invariants

1. **Exact Minor-Unit Arithmetic (INV-01)**: Zero floating-point representation. Rejects `float` in constructors, exchange rates, and multipliers with `MonetaryPrecisionError`.
2. **Zero-Sum General Ledger (INV-02)**: Strict $\sum \text{Debits} = \sum \text{Credits}$ balance across transaction and base functional currencies.
3. **Tamper-Evident Hash Chain (INV-03)**: Immutability via sequential SHA-256 hash chaining ($H_k = \text{SHA256}(H_{k-1} \parallel \text{canonical}(j_k))$).
4. **Controlled Reversals Only (INV-04)**: Historical entries are never mutated. Reversals post an inverse entry referencing `reverses_entry_id`.
5. **Payment Lifecycle Separation (INV-05)**: $\text{INITIATED} \ne \text{CONFIRMED} \ne \text{SETTLED}$. Unsettled payments allocate zero settlement against obligations.
6. **Reconciliation Human Authorization (INV-06)**: Matches with confidence below policy threshold or ambiguous candidates ($p_{(1)} - p_{(2)} < \delta_{\text{ambig}}$) never auto-finalize.
7. **Non-Accusatory Anomaly Telemetry (INV-07)**: Anomaly detection telemetry explicitly enforces `is_fraud_determination = False` with mandatory governance disclaimers.
8. **Strict Multi-Tenant Isolation (INV-08)**: Organization boundary $\text{org}(x) = \text{org}(y)$ enforced across Ledger, Reconciliation, Allocation, Anomaly Detection, and Transaction Graph.
9. **Zero Transaction Fabrication (INV-09)**: Clear epistemic separation between `HISTORICAL_FACT`, `MODEL_ASSUMPTION`, and `SIMULATED_SCENARIO`.
10. **Zero Invented Statutory Rates (INV-10)**: Kenyan tax and withholding rates require explicit parameterized configuration verified by licensed professionals.
11. **Decoupled from GLITTERS CORE (INV-11)**: Pure Python/NumPy/SciPy/Pandas domain core communicating strictly via immutable typed contract DTOs.

## Running the 15-Test Verification Suite

```bash
python3 -m unittest discover -s tests -t . -v
```
