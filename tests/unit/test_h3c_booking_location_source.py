from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_h3c_does_not_expose_plan_level_exam_location():
    registration_source = (
        ROOT / "app/services/h3c_registration.py"
    ).read_text(encoding="utf-8")
    journey_source = (
        ROOT / "app/services/certification_journey.py"
    ).read_text(encoding="utf-8")
    admin_source = (ROOT / "app/services/h3c_admin.py").read_text(encoding="utf-8")

    assert registration_source.count("exam_location=None") == 3
    assert 'exam_location = "以考试预约结果为准"' in registration_source
    assert "plan.exam_location" not in registration_source
    assert journey_source.count(
        '"exam_location": None if vendor == "h3c" else plan.exam_location'
    ) == 2
    assert "exam_location=data.exam_location" not in admin_source
    assert "exam_location=plan.exam_location" not in admin_source
