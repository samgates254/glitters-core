class AccessScope:
    PLATFORM = "platform"
    ORGANIZATION = "organization"
    CHOICES = (
        (PLATFORM, "Platform"),
        (ORGANIZATION, "Organization"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class StaffRole:
    PLATFORM_ADMIN = "platform_admin"
    EXECUTIVE_ADMIN = "executive_admin"
    FINANCE_ADMIN = "finance_admin"
    OPERATIONS_ADMIN = "operations_admin"
    REGIONAL_MANAGER = "regional_manager"
    BRANCH_MANAGER = "branch_manager"
    SALES_MANAGER = "sales_manager"
    PROPERTY_MANAGEMENT_MANAGER = "property_management_manager"
    CONSTRUCTION_MANAGER = "construction_manager"
    ENGINEERING_COORDINATOR = "engineering_coordinator"
    MARKETING_MANAGER = "marketing_manager"
    CUSTOMER_SUPPORT_MANAGER = "customer_support_manager"
    COMPLIANCE_OFFICER = "compliance_officer"
    AUDITOR = "auditor"

    CHOICES = (
        (PLATFORM_ADMIN, "Platform administrator"),
        (EXECUTIVE_ADMIN, "Executive administrator"),
        (FINANCE_ADMIN, "Finance administrator"),
        (OPERATIONS_ADMIN, "Operations administrator"),
        (REGIONAL_MANAGER, "Regional manager"),
        (BRANCH_MANAGER, "Branch manager"),
        (SALES_MANAGER, "Sales manager"),
        (PROPERTY_MANAGEMENT_MANAGER, "Property management manager"),
        (CONSTRUCTION_MANAGER, "Construction manager"),
        (ENGINEERING_COORDINATOR, "Engineering coordinator"),
        (MARKETING_MANAGER, "Marketing manager"),
        (CUSTOMER_SUPPORT_MANAGER, "Customer support manager"),
        (COMPLIANCE_OFFICER, "Compliance officer"),
        (AUDITOR, "Auditor"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class RoleEventAction:
    GRANTED = "granted"
    REVOKED = "revoked"
    CHOICES = (
        (GRANTED, "Granted"),
        (REVOKED, "Revoked"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


PROPERTY_WIDGET_ROLES = frozenset(
    {
        StaffRole.PLATFORM_ADMIN,
        StaffRole.EXECUTIVE_ADMIN,
        StaffRole.OPERATIONS_ADMIN,
        StaffRole.REGIONAL_MANAGER,
        StaffRole.BRANCH_MANAGER,
        StaffRole.SALES_MANAGER,
        StaffRole.PROPERTY_MANAGEMENT_MANAGER,
        StaffRole.COMPLIANCE_OFFICER,
        StaffRole.AUDITOR,
    }
)

MARKETPLACE_WIDGET_ROLES = PROPERTY_WIDGET_ROLES | frozenset(
    {
        StaffRole.MARKETING_MANAGER,
        StaffRole.CUSTOMER_SUPPORT_MANAGER,
    }
)

ENQUIRY_WIDGET_ROLES = frozenset(
    {
        StaffRole.PLATFORM_ADMIN,
        StaffRole.EXECUTIVE_ADMIN,
        StaffRole.OPERATIONS_ADMIN,
        StaffRole.REGIONAL_MANAGER,
        StaffRole.BRANCH_MANAGER,
        StaffRole.SALES_MANAGER,
        StaffRole.COMPLIANCE_OFFICER,
        StaffRole.AUDITOR,
    }
)

RENTAL_WIDGET_ROLES = frozenset(
    {
        StaffRole.PLATFORM_ADMIN,
        StaffRole.EXECUTIVE_ADMIN,
        StaffRole.OPERATIONS_ADMIN,
        StaffRole.REGIONAL_MANAGER,
        StaffRole.BRANCH_MANAGER,
        StaffRole.PROPERTY_MANAGEMENT_MANAGER,
        StaffRole.COMPLIANCE_OFFICER,
        StaffRole.AUDITOR,
    }
)

FINANCE_WIDGET_ROLES = frozenset(
    {
        StaffRole.PLATFORM_ADMIN,
        StaffRole.EXECUTIVE_ADMIN,
        StaffRole.FINANCE_ADMIN,
        StaffRole.COMPLIANCE_OFFICER,
        StaffRole.AUDITOR,
    }
)

CONSTRUCTION_WIDGET_ROLES = frozenset(
    {
        StaffRole.PLATFORM_ADMIN,
        StaffRole.EXECUTIVE_ADMIN,
        StaffRole.OPERATIONS_ADMIN,
        StaffRole.REGIONAL_MANAGER,
        StaffRole.BRANCH_MANAGER,
        StaffRole.CONSTRUCTION_MANAGER,
        StaffRole.ENGINEERING_COORDINATOR,
        StaffRole.COMPLIANCE_OFFICER,
        StaffRole.AUDITOR,
    }
)
