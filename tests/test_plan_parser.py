import pytest
from app.plan_parser import parse_plan


def test_parse_roman_and_numeric():
    p = parse_plan("""
    # test
    Amarr Frigate V
    Gunnery 4
    - Mechanics III
    """, name="Combat")
    assert [(x.skill_name, x.level) for x in p.targets] == [
        ("Amarr Frigate", 5),
        ("Gunnery", 4),
        ("Mechanics", 3),
    ]


def test_bad_line_fails():
    with pytest.raises(ValueError):
        parse_plan("Amarr Frigate")


def test_parse_localized_eve_export_uses_hint_as_canonical_skill_name():
    p = parse_plan(
        """
        <localized hint="Spaceship Command">Допуски к управлению кораблями*</localized> 1
        <localized hint="Amarr Battleship">Амаррские линкоры*</localized> 5
        <localized hint="Large Pulse Laser Specialization">Спецкурс: большие импульсные лазеры*</localized> IV
        """,
        name="Paladin",
    )
    assert [(x.skill_name, x.level) for x in p.targets] == [
        ("Spaceship Command", 1),
        ("Amarr Battleship", 5),
        ("Large Pulse Laser Specialization", 4),
    ]


def test_parse_localized_eve_export_supports_single_quotes_and_entities():
    p = parse_plan("<localized hint='Science &amp; Industry'>Наука*</localized> 3")
    assert [(x.skill_name, x.level) for x in p.targets] == [("Science & Industry", 3)]
