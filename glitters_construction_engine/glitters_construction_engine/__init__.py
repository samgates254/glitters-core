"""
GLITTERS Construction Optimization Engine (`glitters_construction_engine` v2.0.0)
Phase 1: Engineering Authority, Evidence Integrity & Solver Correctness.
"""
from .project_model import (
    Activity,
    ConstructionProject,
    DEFAULT_CORE_AUTHORITY_SECRET,
    DependencyType,
    EngineeringSpecification,
    ExecutionMode,
    KenyanWorkCalendar,
    MeasurementStatus,
    MoneyKES,
    PrecedenceDependency,
    ProfessionalEngineeringApproval,
    ProjectArchetype,
    RateProvenanceStatus,
    ResourceCategory,
    ResourcePoolItem,
)
from .work_breakdown import WBSNode, WorkBreakdownStructure
from .quantity_takeoff import (
    QuantityTakeoffEngine,
    SignedMeasurementEvidence,
    TakeoffResult,
    UNIT_DIMENSIONS,
    convert_units_dimensionally,
)
from .cost import BOQEngine, BOQWorkItem, CostOptimizationEngine, ValueEngineeringAlternative
from .scheduling import (
    ActivityScheduleMetric,
    CriticalPathEngine,
    CyclicDependencyError,
    HAS_ORTOOLS,
    InfeasibleScheduleError,
    ScheduledActivity,
    SchedulingEngine,
    validate_schedule_feasibility,
)
from .resource_allocation import ResourceAllocationEngine
from .labour_optimization import LabourOptimizationEngine, LabourRoleSpec
from .equipment_optimization import DeliverySiteNode, EquipmentOptimizationEngine, EquipmentSpec
from .procurement import (
    ProcurementEngine,
    ProcurementRequisition,
    QuotationVerificationStatus,
    SupplierQuotation,
    deduplicate_requisitions,
)
from .inventory import InventoryOptimizationEngine, PriceBreakTier
from .risk import RiskAndUncertaintyEngine, RiskFactorProbabilities
from .earned_value import ActivityProgressRecord, EarnedValueAndAnomalyEngine, ProgressDataOrigin
from .simulation import ConstructionDigitalTwinEngine
from .evaluation import AlgorithmicEvaluationEngine
from .integration import CoreOrganizationContext, GlittersCoreIntegrationAdapter
from .datasets import (
    build_kenyan_benchmark_project,
    generate_large_scale_synthetic_project,
    get_synthetic_kenyan_supplier_quotations,
    get_verified_kenyan_supplier_quotations,
)

__version__ = "2.0.0"
