# Construction ERP Implementation Tracker

Baseline: Wave 0 commit `00f126eac9b6ab7718f904eb0e9f9346348b9981`

| Feature | Status | Files / models / services | Tests | Git commit | Known limitations |
|---|---|---|---|---|---|
| Project foundation and lifecycle | TESTED | `models.py`: `ConstructionProject`, `ProjectMember`, `ProjectPhase`; `services.py`; project transitions | Lifecycle, unauthorized transition | 04bd111 then follow-up commit | Planning state added; route remains integration-only |
| Civil inspections and defects | TESTED | `EngineeringInspection`, `Defect`, `InspectionChecklist`, `EngineeringIssue`; inspection and defect services | Inspection/defect workflow | follow-up commit | Evidence uses document/digest references, upload endpoint pending |
| Site diary | IMPLEMENTED / TESTED | `SiteDiary`; diary create and approval services | Diary approval workflow | follow-up commit | Labor summary is structured JSON; photo evidence still via technical document references |
| BoQ and revisions | TESTED | `BoQ`, `BoQSection`, `BoQItem`; draft, submit, review, approve, supersede, and revision services | Decimal rounding, negative-value rejection, approval permissions, revision integrity, CSV/XLSX API export; 10 focused workflow tests pass | 04bd111, follow-up, Stage C | PDF renderer is not installed; export API returns 406 for PDF |
| Budget and variations | TESTED | `ProjectBudget`, `BudgetCategory`, `CostRecord`, `ApprovedVariation`; budget revision and variation approval workflows | Approval permissions, immutable cost records, budget handoff, variation review/approval/rejection, organization isolation; 10 focused workflow tests pass | 04bd111, follow-up, Stage C | No duplicate accounting ledger; finance integration contract remains |
| Cost control and reporting | TESTED | Commitment, actual, and forecast records; category totals and project cost reports | Decimal totals, API workflow, category aggregation, CSV/XLSX output; 10 focused workflow tests pass | Stage C | Full suite: 499 passed, 3 failed in untouched `apps/spatial` change-detection tests; PDF export is unsupported; app mounting remains a separate integration change |
| Milestones and activities | TESTED | `ProjectMilestone`, `ProjectActivity`; dependency cycle validation | Milestone and activity dependency tests | 04bd111 then follow-up commit | Activity API collection is not mounted yet |
| Contractor, tender and bid | TESTED | `ContractorAssignment`, `Tender`, `TenderPackage`, `TenderBid`, `TenderDocument`, `WorkPackage`, `WorkOrder` | Award and bid isolation test | follow-up commit | Bid-document and evaluation APIs remain follow-up |
| RFI | TESTED | `RFI`, `RFIResponse`; submit/respond/close services | RFI lifecycle | 04bd111 | RFI attachment model not yet separate |
| Technical documents | IMPLEMENTED | `TechnicalDocument`; organization-scoped download | Cross-tenant test context | 04bd111 | No signed URL/download endpoint after app mounting review |
| Project reporting | TESTED | `project_report`, `project_report_csv` | Report functions exercised indirectly | follow-up commit | PDF/XLSX exports are not yet dependencies |

Statuses: NOT STARTED, IN PROGRESS, IMPLEMENTED, TESTED, INTEGRATED, BLOCKED, REVIEW REQUIRED.
