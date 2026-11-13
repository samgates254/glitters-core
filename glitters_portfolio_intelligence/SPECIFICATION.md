# GLITTERS Engine 08: Formal Mathematical & Architectural Specification

**Version**: `0.8.0`
**Package**: `glitters_portfolio_intelligence`
**Standard**: ISO/IEC 15408 & Rockafellar-Uryasev (2000) Linear Convex Optimization

---

## 1. Problem Formulation & Scope

The **GLITTERS Portfolio Optimization & Investment Intelligence Engine** (`glitters_portfolio_intelligence`) solves the multi-period real-estate asset allocation problem under downside risk (Conditional Value-at-Risk), debt coverage constraints, liquidity requirements, and macroeconomic uncertainty in Kenya.

---

## 2. Mathematical Foundation

### 2.1 Exact Monetary Representation & Dual-State Algebra
- All balance sheet obligations, debt service, rent collections, CapEx, and transaction costs are strictly modeled as integer minor units (`int` cents where 1 KES = 100 cents).
- Fractional rounding during capital allocation is governed by the Hare-Niemeyer (Largest Remainder) conservation theorem:
  $$\sum_{i=1}^N \text{allocated\_cents}_i = \text{total\_capital\_cents}$$

### 2.2 Cash Flow Mathematics (Non-Double-Counting Model)
For each asset $i \in \{1, \dots, N\}$:
$$\begin{aligned}
GPR_i &= 12 \times \text{monthly\_gross\_potential\_rent\_cents}_i \\
\text{VacancyLoss}_i &= \left\lfloor GPR_i \times v_i \right\rceil \\
\text{BilledRent}_i &= GPR_i - \text{VacancyLoss}_i \\
\text{CreditLoss}_i &= \left\lfloor \text{BilledRent}_i \times c_i \right\rceil \\
EGI_i &= GPR_i - \text{VacancyLoss}_i - \text{CreditLoss}_i + OI_i \\
NOI_i &= EGI_i - OPEX_i - M_i \\
CFADS_i &= NOI_i - D_i - CapEx_i
\end{aligned}$$

### 2.3 Rockafellar-Uryasev Mean-CVaR Linear Formulation
Let $x = (x_1, \dots, x_N)^T$ be portfolio allocation weights ($\sum_{i=1}^N x_i = 1 - w_{\text{cash}}^{\min}, x_i \ge 0$).
Let $S$ discrete scenarios exist with probability vector $p = (p_1, \dots, p_S)^T$ ($\sum p_s = 1$).
Scenario portfolio return: $R_s(x) = \sum_{i=1}^N x_i r_{is}$.
Scenario portfolio loss: $L_s(x) = -R_s(x)$.

For confidence level $\alpha \in (0, 1)$ (typically $\alpha = 0.95$):
$$\min_{x, \gamma, u} \left( \lambda \left[ \gamma + \frac{1}{1 - \alpha} \sum_{s=1}^S p_s u_s \right] - (1 - \lambda) \sum_{i=1}^N x_i \bar{r}_i \right)$$
subject to:
$$\begin{aligned}
u_s + \gamma + \sum_{i=1}^N x_i r_{is} &\ge 0, \quad \forall s \in \{1, \dots, S\} \\
u_s &\ge 0, \quad \forall s \in \{1, \dots, S\} \\
\sum_{i=1}^N x_i &= 1.0 - w_{\text{cash}}^{\min} \\
x_i &\le w_i^{\max}, \quad \forall i \in \{1, \dots, N\} \\
\sum_{i=1}^N x_i LTV_i &\le LTV_{\max} \\
\sum_{i=1}^N x_i (DSCR_{\min} D_i - NOI_i) &\le 0 \\
\sum_{i \in \mathcal{G}_k} x_i &\le w_{\text{geo}, k}^{\max}, \quad \forall k \\
\sum_{i \in \mathcal{K}_c} x_i &\le w_{\text{cat}, c}^{\max}, \quad \forall c
\end{aligned}$$

### 2.4 Independent Empirical CVaR Verifier
To verify LP results independently of the solver:
$$CVaR_\alpha = \min_\eta \left[ \eta + \frac{1}{1 - \alpha} \sum_{s=1}^S p_s [L_s - \eta]^+ \right]$$
The verifier evaluates $\Phi(\eta)$ at all discrete loss breakpoints $L_{(k)}$ and performs 1-D bounded optimization, correctly handling repeated losses and fractional atom mass at VaR.

### 2.5 Multi-Period Trajectory Invariants
Across horizon $t = 1, \dots, T$:
1. **Action Mutual Exclusivity**: $a_{it} + d_{it} \le 1$ (no simultaneous buy and sell in same period).
2. **Holding Continuity**: $h_{it} = h_{i, t-1} + a_{it} - d_{it}$.
3. **Renovation Guardrail**: $v_{it} \le h_{it}$.
4. **Liquidity Invariant**: $Cash_t \ge Cash_{\min} \ge 0, \forall t$.
5. **Renovation Downtime**: Reduces rental revenue by $\text{downtime\_months} / 12$ during renovation.

---

## 3. Epistemic Provenance Standard

| Provenance Kind | Permitted Source | Transformation Method |
| :--- | :--- | :--- |
| `HISTORICAL_OBSERVATION` | Official statistical releases (KNBS, BORAQS, Hass) | Empirical replay / historical percentile |
| `STATISTICAL_FORECAST` | Econometric models, calibrated VAR | Conditional expectation / econometrics |
| `EXPERT_ASSUMPTION` | Documented Investment Committee guidelines | Parameterized deterministic stress shock |
| `SYNTHETIC_SCENARIO` | Generative stress-testing protocols | Non-linear synthetic tail simulation |

---

## 4. Governance & Human Oversight

Engine 08 is strictly advisory:
- Cannot execute transactions.
- Cannot post double-entry financial journal entries.
- Generates `PortfolioInvestmentBridge` with `HumanApprovalRequirement(requires_human_approval=True, approval_role="INVESTMENT_COMMITTEE")`.
