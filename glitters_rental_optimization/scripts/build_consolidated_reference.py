from __future__ import annotations
import os
import re

ORDERED_MODULES = [
    "glitters_rental_optimization/schemas.py",
    "glitters_rental_optimization/kenyan_market.py",
    "glitters_rental_optimization/demand/models.py",
    "glitters_rental_optimization/elasticity/estimator.py",
    "glitters_rental_optimization/vacancy/models.py",
    "glitters_rental_optimization/renewals/models.py",
    "glitters_rental_optimization/uncertainty/models.py",
    "glitters_rental_optimization/pricing/single_property.py",
    "glitters_rental_optimization/portfolio/optimizer.py",
    "glitters_rental_optimization/optimization/dynamic_pricing.py",
    "glitters_rental_optimization/evaluation/backtesting.py",
    "tests/test_glitters_engine.py",
]

def generate_consolidated(base_dir: str, out_path: str) -> None:
    parts = [
        '"""',
        'GLITTERS RENTAL PRICING & REVENUE OPTIMIZATION ENGINE (v2.0.0)',
        'CONSOLIDATED STANDALONE REFERENCE IMPLEMENTATION',
        'Auto-generated from canonical modular package: glitters_rental_optimization',
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
            
            # Skip relative intra-package imports
            if stripped.startswith('from .') or stripped.startswith('from glitters_rental_optimization'):
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
    out_path = os.path.join(base_dir, 'glitters_rental_optimization_reference.py')
    generate_consolidated(base_dir, out_path)
