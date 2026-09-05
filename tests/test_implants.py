from app.implants import resolve_attribute_implants


def test_standard_plus3_implant_name_resolves_correctly():
    result = resolve_attribute_implants(("Ocular Filter - Basic",))
    assert result.attribute_bonus.perception == 3
    assert result.unknown_implants == ()


def test_standard_plus4_implant_name_resolves_correctly():
    result = resolve_attribute_implants(("Memory Augmentation - Standard",))
    assert result.attribute_bonus.memory == 4
    assert result.unknown_implants == ()


def test_standard_plus5_implant_name_resolves_correctly():
    result = resolve_attribute_implants(("Cybernetic Subprocessor - Improved",))
    assert result.attribute_bonus.intelligence == 5
    assert result.unknown_implants == ()


def test_mixed_implant_set_resolves_correctly():
    result = resolve_attribute_implants(
        (
            "Ocular Filter - Standard",
            "Memory Augmentation - Improved",
            "Neural Boost - Advanced",
            "Cybernetic Subprocessor - Basic",
            "Social Adaptation Chip - Limited",
        )
    )
    assert result.attribute_bonus.perception == 4
    assert result.attribute_bonus.memory == 5
    assert result.attribute_bonus.willpower == 5
    assert result.attribute_bonus.intelligence == 3
    assert result.attribute_bonus.charisma == 1


def test_unknown_implant_does_not_crash_and_is_reported():
    result = resolve_attribute_implants(("Mystery Implant",))
    assert result.attribute_bonus.total == 0
    assert result.unknown_implants == ("Mystery Implant",)
