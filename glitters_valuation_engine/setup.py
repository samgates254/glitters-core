from setuptools import setup, find_packages

setup(
    name="glitters-valuation-engine",
    version="1.1.0",
    description="GLITTERS Property Valuation Intelligence Engine — Phase 1: Evidence Integrity & Statistical Correctness",
    packages=find_packages(include=["glitters_valuation_engine", "glitters_valuation_engine.*"]),
    python_requires=">=3.11",
    install_requires=[
        "numpy>=1.24.0",
        "scipy>=1.10.0",
        "pandas>=2.0.0",
    ],
)
