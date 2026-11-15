# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 4: Spatial Change Detection & ML Intelligence Architecture

**Document Reference:** `docs/engineering/M4_CHANGE_DETECTION_ARCHITECTURE.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial`), PyTorch/ONNX Inference Pipeline, Celery Workers, GOD'S EYE Review Interface

---

## 1. Executive Summary & Core Principle

In high-growth metropolitan centers like Mombasa, unauthorized developments, rapid construction, and encroachment on road reserves and coastal riparian zones outpace manual municipal inspection. Automated spatial change detection transforms satellite and drone imagery into actionable physical intelligence.

### The Immutable Separation Invariant:
> **AI inferences and machine learning predictions are empirical hypotheses. They are NEVER canonical legal facts.**
> Under no circumstances may an automated ML pipeline directly insert, mutate, or retire a canonical `SpatialEntity`, `Building`, or `LandParcel`. All model predictions are persisted as quarantine-isolated `ChangeCandidate` records requiring deterministic verification or human-in-the-loop (HITL) validation before canonical promotion.

```
Change Detection Architecture:
┌────────────────────────┐      ┌────────────────────────┐
│ Pre-Event Raster (T0)  │      │ Post-Event Raster (T1) │
└───────────┬────────────┘      └───────────┬────────────┘
            │                               │
            ▼                               ▼
┌────────────────────────────────────────────────────────┐
│ Bi-Temporal Computer Vision Pipeline                   │
│ - Geometric Coregistration & Orthorectification Check  │
│ - Siamese CNN / ViT Change Segmentation (e.g. ChangeFormer)
│ - Building Instance Segmentation (Mask R-CNN / YOLOv11) │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Vector Extraction & Regularization                     │
│ - Polygonization (Marching Squares / Potrace)          │
│ - Douglas-Peucker Simplification & Orthogonalization   │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Hypothesis Storage: `ChangeCandidate` Table            │
│ - confidence_score, change_type, predicted_polygon     │
│ - candidate_status: PENDING_REVIEW / REJECTED / PROMOTED│
└─────────────┬────────────────────────────┬─────────────┘
              │                            │
  Confidence >= 0.95 & Tier 0 Target       │ Low Confidence / Tier 3 Target
              ▼                            ▼
┌───────────────────────────┐  ┌─────────────────────────┐
│ Automated Staging Queue   │  │ GOD'S EYE HITL Review   │
│ (Flagged for Field Check) │  │ (Surveyor Inspector UI) │
└───────────────────────────┘  └───────────┬─────────────┘
                                           │ Confirmed & Signed
                                           ▼
                               ┌─────────────────────────┐
                               │ Canonical Promotion     │
                               │ `SpatialEntity` Insert/ │
                               │ Update via Ingestion FSM│
                               └─────────────────────────┘
```

---

## 2. Change Detection Taxonomy & Detection Classes

GLITTERS models physical change across 5 distinct operational classes:

### 2.1 Change Class Hierarchy
1. **NEW_CONSTRUCTION (Appearance):**
   - Emergence of a permanent structural footprint on previously vacant land.
   - Identified by spectral transition from vegetated/bare soil to built material, coupled with DSM height elevation jump ($> 2.5\text{ m}$).
2. **DEMOLITION (Disappearance / Clearance):**
   - Complete removal or structural collapse of a previously recorded building footprint.
   - Identified by loss of elevation in DSM and replacement of roof spectral signature with rubble, bare earth, or asphalt.
3. **HORIZONTAL_EXPANSION (Footprint Modification):**
   - Lateral addition to an existing building footprint (e.g. new wing, veranda enclosure, annex).
   - Computed via morphological difference:
     $$\Delta G = G_{\text{detected}} \setminus G_{\text{canonical}}$$
     where $\text{Area}(\Delta G) > 10\text{ m}^2$ and $\text{IoU}(G_{\text{detected}}, G_{\text{canonical}}) \in [0.40, 0.95]$.
4. **VERTICAL_MODIFICATION (Storey Addition):**
   - Expansion in building height without horizontal footprint change.
   - Detected exclusively through DSM-to-DSM elevation subtraction:
     $$\Delta H = H_{\text{DSM}, T_1} - H_{\text{DSM}, T_0} \ge 2.8\text{ m}$$
5. **ENCROACHMENT_VIOLATION (Spatial Rule Breach):**
   - Physical structure intersecting protected spatial buffers:
     - *Riparian Reserve:* Structures within 30 meters of high-water mark or river centerlines.
     - *Road Reserve:* Structures violating statutory setbacks (e.g., 6m from class C road, 15m from highway).
     - *Cadastral Parcel Encroachment:* Structures crossing a surveyed legal boundary into a neighboring parcel.

---

## 3. Data Model Specification (`ChangeCandidate`)

All pipeline detections are recorded in the dedicated `ChangeCandidate` model:

```python
# apps/spatial/models.py

class ChangeCandidate(models.Model):
    """
    Stores unverified spatial change hypotheses generated by ML models
    or automated raster subtraction pipelines.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Inspection'),
        ('IN_REVIEW', 'Under Surveyor Investigation'),
        ('ACCEPTED', 'Verified and Promoted to Canonical'),
        ('REJECTED_FALSE_POSITIVE', 'Rejected: Shadow / Sensor Artifact'),
        ('REJECTED_TRANSIENT', 'Rejected: Transient Object (Container/Tent)'),
        ('SUPERSEDED', 'Superseded by Newer Observation'),
    ]

    CHANGE_TYPE_CHOICES = [
        ('NEW_CONSTRUCTION', 'New Building Appearance'),
        ('DEMOLITION', 'Building Demolition / Removal'),
        ('EXPANSION_HORIZONTAL', 'Horizontal Footprint Expansion'),
        ('EXPANSION_VERTICAL', 'Vertical Storey Addition'),
        ('ENCROACHMENT_PARCEL', 'Cross-Boundary Cadastral Encroachment'),
        ('ENCROACHMENT_RESERVE', 'Setback or Riparian Encroachment'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    change_type = models.CharField(max_length=32, choices=CHANGE_TYPE_CHOICES, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='PENDING', db_index=True)

    # Target reference (if modifying an existing entity)
    target_entity = models.ForeignKey(
        'spatial.SpatialEntity',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='detected_changes'
    )

    # Spatial footprint of the detected change
    geometry = models.GeometryField(srid=4326)
    area_sq_meters = models.FloatField()

    # Temporal interval of the change
    t0_observation = models.ForeignKey(
        'spatial.ImageryObservation',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='t0_changes'
    )
    t1_observation = models.ForeignKey(
        'spatial.ImageryObservation',
        on_delete=models.PROTECT,
        related_name='t1_changes'
    )

    # Model Lineage & Confidence
    model_name = models.CharField(max_length=64)       # e.g., 'ChangeFormerV2-Mombasa'
    model_version = models.CharField(max_length=32)    # e.g., '2026.02-r3'
    confidence_score = models.FloatField()             # 0.00 to 1.00
    evidence_chips = models.JSONField(
        default=dict,
        help_text="URLs to pre-aligned T0/T1 raster image chips and saliency maps"
    )

    # Audit Trail
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        'users.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_spatial_changes'
    )
    review_notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'change_type']),
            models.Index(fields=['confidence_score']),
        ]
```

---

## 4. Construction Progress Monitoring Lifecycle

GOD'S EYE tracks real estate and infrastructure development across 5 discrete construction phases:

```
Construction Stage Transition Engine:
┌───────────────┐     Site Clearing & Excavation
│  EARTHWORKS   ├─────────────────────────────────────────┐
└───────┬───────┘                                         │
        │ Trenching & Concrete Pour                       │
        ▼                                                 ▼
┌───────────────┐     Columns & Slab Casting       ┌─────────────┐
│  FOUNDATION   ├─────────────────────────────────►│ STALLED /   │
└───────┬───────┘                                  │ ABANDONED   │
        │ Superstructure Assembly                  └──────▲──────┘
        ▼                                                 │ No activity
┌───────────────┐     Roof Trusses & Sheeting             │ > 90 days
│ SUPERSTRUCTURE├─────────────────────────────────────────┤
└───────┬───────┘                                         │
        │ Finishes & Landscaping                          │
        ▼                                                 │
┌───────────────┐                                         │
│   COMPLETED   │                                         │
└───────────────┘                                         │
        │ Demolition / Clearance                          │
        └─────────────────────────────────────────────────┘
```

### 4.1 Automated Stage Classification
- **Phase 1: Earthworks:** Heavy soil disturbance, perimeter trenching, high vegetation loss, zero elevation increase.
- **Phase 2: Foundation:** Footings visible, reinforced concrete ground slab detected, low elevation offset ($0.3 - 0.8\text{ m}$).
- **Phase 3: Superstructure:** Vertical load-bearing columns and intermediate floor slabs detected; DSM height rises incrementally ($> 3.0\text{ m}$ per storey); shadows indicate open skeleton.
- **Phase 4: Roof Structure:** Roof trusses erected, continuous opaque roofing surface installed; height stabilizes.
- **Phase 5: Completed:** Paved aprons, painted finishes, active vehicular/pedestrian occupancy observed.
- **Anomaly Detection (Stalled Site):** When a site remains in Foundation or Superstructure stage across 3 consecutive observation epochs spanning $> 90\text{ days}$ with zero volumetric change ($\Delta V < 2\%$), the site is flagged `FLAG_CONSTRUCTION_STALLED` for developer risk assessment.

---

## 5. Human-in-the-Loop (HITL) Workflow in GOD'S EYE

To safeguard legal integrity, high-consequence decisions require human sign-off.

```
GOD'S EYE Verification Flow:
[ChangeCandidate Generated]
           │
           ├─► [Confidence < 0.70] ───────────────► Auto-Quarantine / Rejected
           │
           └─► [Confidence >= 0.70]
                     │
                     ▼
             [Review Queue in GOD'S EYE]
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
    [Side-by-Side           [Overlay Blink Mode]
     T0 vs T1 Chip View]     (Toggle 2024 vs 2026)
         │                       │
         └───────────┬───────────┘
                     ▼
         [Surveyor Action: APPROVE / EDIT / REJECT]
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
     [APPROVED]            [REJECTED]
          │                     │
          │ Updates             ▼
          │ `spatial_entity`   [Logged to ML False-Positive Bank]
          │ and Dataset        [Weights Retrained on Hard Negatives]
          ▼
     [Signed with Cryptographic Key]
```

### 5.1 Verification Audit Guarantee
Every promotion from `ChangeCandidate` to `SpatialEntity` captures:
1. Reviewer identity and professional registration number.
2. Review timestamp and cryptographic hash of the confirmation payload.
3. Original T0 and T1 image chip references preserved in immutable object storage.
