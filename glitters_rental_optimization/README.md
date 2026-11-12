# GLITTERS Rental Pricing & Revenue Optimization Engine (`glitters_rental_optimization` v2.0.0)

**Canonical Modular Python Distribution**

A mathematically rigorous, causal econometric, and operations research rental pricing engine designed for **GLITTERS** multi-tenant residential and commercial property management.

## Core Capabilities & Non-Negotiable Invariants

1. **Seven Non-Interchangeable Monetary Quantities**:
   - Asking Rent ($P^{\text{ask}}$), Achievable Market Rent ($P^{\text{mkt}}$), Contractual Rent ($P^{\text{contract}}$), Recommended Rent ($P^{\text{rec}}$), Collected Rent ($R^{\text{coll}}$), Effective Rental Revenue ($\text{ERR}$), Net Operating Income ($\text{NOI}$).
2. **Contractual Lock on Active Leases**:
   - Active mid-term leases ($t_{\text{rem}} > 3$ months) are strictly locked (`PricingStatus.LOCKED_CONTRACTUAL`). Contractual rent is never rewritten.
   - Expiring leases ($1 \le t_{\text{rem}} \le 3$ months) route to the **Renewal Pricing Workflow** to generate forward-dated renewal proposals without mutating active contracts.
3. **Dimensionally Consistent Weibull Survival Hazard**:
   - Characteristic scale parameter $\eta_i(P) = (\lambda_i(P) q_i(P))^{-1}$ in **months**.
   - Cumulative hazard $H_i(P, \tau) = (\tau / \eta_i(P))^{\nu_s}$ (dimensionless) and instantaneous hazard $h_i(P, \tau) = (\nu_s / \eta_i(P)) (\tau / \eta_i(P))^{\nu_s - 1}$ ($\text{month}^{-1}$).
4. **Orthogonal IV-DML Causal Identification & Explicit Abstention**:
   - 5-fold cross-fitted Double Machine Learning Instrumental Variable moment equations.
   - First-stage weak instrument test ($F \ge 10.0$). If $F < 10.0$ or instruments are unobserved, engine explicitly abstains (`ABSTAINED_WEAK_OR_MISSING_IV`), falls back to Empirical Bayes segment prior, and inflates standard errors.
5. **Coupled Portfolio Nonlinear Programming (SLSQP)**:
   - Solves multi-unit portfolio revenue maximization subject to sibling unit parity bounds, landlord minimum occupancy targets, turnover maintenance capacity, and minimum return-on-equity yield targets.
   - Post-solve feasibility verification with constraint diagnostics.
6. **4-State Discrete-Time Markov Chain (DTMC) Occupancy Transitions**:
   - Full finite-horizon transition model over `VACANT`, `OCCUPIED`, `EXPIRING`, and `TURNOVER_MAINT` states.
7. **Anti-Discrimination & Fairness Auditing**:
   - Prohibits personal protected attributes at input layer.
   - Post-solve outcome-level fairness auditing (`audit_portfolio_pricing_fairness`) verifying markup disparity ($\le 0.05$) and renewal disparate impact ratio ($80\%$ rule).

## Running the 28-Test Verification Suite

```bash
python3 -m unittest discover -s tests -t . -v
```
