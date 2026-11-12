from __future__ import annotations
import os
import re

ORDERED_MODULES = [
    "src/glitters_portfolio_intelligence/domain/enums.py",
    "src/glitters_portfolio_intelligence/domain/money.py",
    "src/glitters_portfolio_intelligence/domain/entities.py",
    "src/glitters_portfolio_intelligence/cashflows/accounting.py",
    "src/glitters_portfolio_intelligence/returns/metrics.py",
    "src/glitters_portfolio_intelligence/scenarios/provenance.py",
    "src/glitters_portfolio_intelligence/scenarios/cvar_verifier.py",
    "src/glitters_portfolio_intelligence/scenarios/stress_engine.py",
    "src/glitters_portfolio_intelligence/optimization/constraints.py",
    "src/glitters_portfolio_intelligence/optimization/solver_backends.py",
    "src/glitters_portfolio_intelligence/optimization/rockafellar_cvar.py",
    "src/glitters_portfolio_intelligence/optimization/frontier.py",
    "src/glitters_portfolio_intelligence/optimization/multi_period.py",
    "src/glitters_portfolio_intelligence/governance/committee_gate.py",
    "src/glitters_portfolio_intelligence/integration/upstream_adapters.py",
    "src/glitters_portfolio_intelligence/integration/glitters_contracts_adapter.py",
]

def generate_consolidated(base_dir: str, out_path: str) -> None:
    parts = [
        '"""',
        'GLITTERS ENGINE 08: PORTFOLIO OPTIMIZATION & INVESTMENT INTELLIGENCE (v0.8.0)',
        'CONSOLIDATED STANDALONE REFERENCE IMPLEMENTATION',
        'Auto-generated from canonical modular package: glitters_portfolio_intelligence',
        'Proprietary - GLITTERS Algorithmic Platform',
        '"""',
        'from __future__ import annotations',
        '',
    ]

    for rel in ORDERED_MODULES:
        full_path = os.path.join(base_dir, rel)
        with open(full_path, "r", encoding="utf-8") as fp:
            lines = fp.readlines()

        parts.append('# ==============================================================================')
        parts.append(f'# MODULE FILE: {rel}')
        parts.append('# ==============================================================================')

        skip_multiline_relative = False

        for line in lines:
            stripped = line.strip()

            if skip_multiline_relative:
                if ')' in stripped:
                    skip_multiline_relative = False
                continue

            # Skip from __future__ since it is placed at the very top
            if stripped.startswith('from __future__'):
                continue

            # Skip relative intra-package imports
            if stripped.startswith('from .') or stripped.startswith('from ..'):
                if '(' in stripped and ')' not in stripped:
                    skip_multiline_relative = True
                continue

            parts.append(line.rstrip())

        parts.append('')

    content = '\n'.join(parts) + '\n'
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(content)
    print(f"Generated standalone reference at: {out_path} ({len(content)} bytes)")


if __name__ == "__main__":
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(base, "glitters_portfolio_intelligence_reference.py")
    generate_consolidated(base, target)
