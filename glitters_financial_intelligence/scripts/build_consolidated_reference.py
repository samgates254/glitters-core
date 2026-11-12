from __future__ import annotations
import os
import re

ORDERED_MODULES = [
    "glitters_financial_intelligence/currencies.py",
    "glitters_financial_intelligence/money.py",
    "glitters_financial_intelligence/accounting.py",
    "glitters_financial_intelligence/reconciliation.py",
    "glitters_financial_intelligence/payment_allocation.py",
    "glitters_financial_intelligence/anomaly_detection.py",
    "glitters_financial_intelligence/financial_graph.py",
    "glitters_financial_intelligence/forecasting.py",
    "glitters_financial_intelligence/risk.py",
    "glitters_financial_intelligence/evaluation.py",
    "tests/test_financial_intelligence.py",
]

def generate_consolidated(base_dir: str, out_path: str) -> None:
    parts = [
        '"""',
        'GLITTERS FINANCIAL INTELLIGENCE & DOUBLE-ENTRY RECONCILIATION ENGINE (v1.0.0)',
        'CONSOLIDATED STANDALONE REFERENCE IMPLEMENTATION',
        'Auto-generated from canonical modular package: glitters_financial_intelligence',
        '"""',
        'from __future__ import annotations',
        '',
    ]
    
    for rel in ORDERED_MODULES:
        full_path = os.path.join(base_dir, rel)
        with open(full_path) as fp:
            lines = fp.readlines()
        
        parts.append('# ==============================================================================')
        parts.append(f'# MODULE FILE: {rel}')
        parts.append('# ==============================================================================')
        
        skip_relative_import = False
        
        for line in lines:
            stripped = line.strip()
            # Skip from __future__ since it is placed at the very top
            if stripped.startswith('from __future__'):
                continue
            
            # Skip relative intra-package imports or imports from glitters_financial_intelligence
            if stripped.startswith('from .') or stripped.startswith('from glitters_financial_intelligence') or stripped.startswith('import glitters_financial_intelligence'):
                if '(' in stripped and ')' not in stripped:
                    skip_relative_import = True
                continue
            if skip_relative_import:
                if ')' in stripped:
                    skip_relative_import = False
                continue
                
            parts.append(line.rstrip('\r\n'))
        parts.append('')
    
    # Add main block at the end so it executes the tests if run directly
    parts.append('if __name__ == "__main__":')
    parts.append('    unittest.main()')
    parts.append('')
    
    with open(out_path, 'w') as out:
        out.write('\n'.join(parts))
    print(f'Successfully generated {out_path} ({len(parts)} lines)')

if __name__ == '__main__':
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    out_path = os.path.join(base_dir, 'glitters_financial_intelligence_reference.py')
    generate_consolidated(base_dir, out_path)
