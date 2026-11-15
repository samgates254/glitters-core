from django.db import migrations


def remediate_chg_00001_forward(apps, schema_editor):
    ChangeCandidate = apps.get_model("spatial", "ChangeCandidate")
    ChangeReview = apps.get_model("spatial", "ChangeReview")

    candidates = ChangeCandidate.objects.filter(candidate_id="CHG_00001", review_status="VERIFIED")
    for candidate in candidates:
        ChangeReview.objects.create(
            candidate=candidate,
            reviewer=None,
            decision="CONFIRM_OBSERVATION",
            previous_status="VERIFIED",
            new_status="HUMAN_REVIEWED",
            comments=(
                "Auditable governance remediation: Transitioned candidate from premature VERIFIED "
                "status to HUMAN_REVIEWED. Field verification is required before granting VERIFIED status. "
                "250,000 m² extent is an algorithmic tile-saturation artifact caused by low CVA threshold (0.15 vs 0.22) "
                "on the 50x50 tile (500m x 500m = 250,000 m²) without sub-tile segmentation."
            ),
        )
        candidate.review_status = "HUMAN_REVIEWED"
        candidate.save(update_fields=["review_status"])


def remediate_chg_00001_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("spatial", "0007_changedetectionproject_changedetectionrun_and_more"),
    ]

    operations = [
        migrations.RunPython(remediate_chg_00001_forward, remediate_chg_00001_reverse),
    ]
