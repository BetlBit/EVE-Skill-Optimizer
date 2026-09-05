from pathlib import Path

import pytest

from app.evemon import EvemonImportError, import_evemon_character, list_evemon_characters, snapshot_from_dict, snapshot_to_dict


SAMPLE = '''<?xml version="1.0" encoding="utf-8"?>
<Settings clientID="DO_NOT_READ" clientSecret="SUPER_SECRET" revision="1">
  <esiKeys><esikey><refreshToken>SECRET_TOKEN</refreshToken></esikey></esiKeys>
  <characters>
    <ccp>
      <characterID>123456789</characterID>
      <name>Test Pilot</name>
      <freeSkillPoints>500000</freeSkillPoints>
      <freeRespecs>2</freeRespecs>
      <lastRespecDate>2026-08-01T12:00:00Z</lastRespecDate>
      <lastTimedRespec>2026-08-01T12:00:00Z</lastTimedRespec>
      <attributes>
        <intelligence>27</intelligence><memory>21</memory><perception>17</perception><willpower>17</willpower><charisma>17</charisma><booster>0</booster>
      </attributes>
      <skills>
        <skill typeID="3405" name="Biology" level="5" activelevel="5" skillpoints="1280000" ownsBook="true" isKnown="true" />
        <skill typeID="3411" name="Cybernetics" level="4" activelevel="4" skillpoints="181020" ownsBook="true" isKnown="true" />
      </skills>
      <queue>
        <skill typeID="3411" level="5" startSP="181020" endSP="1024000" startTime="2026-09-04T10:00:00Z" endTime="2026-09-10T10:00:00Z" />
      </queue>
      <implants>
        <activeCloneSet name="Current">
          <intelligence>Ocular Filter - Improved</intelligence>
          <memory>Memory Augmentation - Improved</memory>
          <perception>None</perception><willpower>None</willpower><charisma>None</charisma>
          <slot6>None</slot6><slot7>None</slot7><slot8>None</slot8><slot9>None</slot9><slot10>None</slot10>
        </activeCloneSet>
      </implants>
    </ccp>
  </characters>
</Settings>
'''


def test_evemon_import(tmp_path: Path):
    f = tmp_path / "settings.xml"
    f.write_text(SAMPLE, encoding="utf-8")

    chars = list_evemon_characters(f)
    assert chars == [{
        "character_id": 123456789,
        "name": "Test Pilot",
        "skills_count": 2,
        "allocated_sp": 1461020,
        "unallocated_sp": 500000,
    }]

    c = import_evemon_character(f)
    assert c.character_id == 123456789
    assert c.character_name == "Test Pilot"
    assert c.total_sp == 1461020
    assert c.unallocated_sp == 500000
    assert c.attributes.intelligence == 27
    assert c.skills[3405].trained_level == 5
    assert c.bonus_remaps == 2
    assert c.implant_attribute_bonus.perception == 5
    assert c.implant_attribute_bonus.memory == 5
    assert c.booster_attribute_bonus.total == 0
    assert len(c.skill_queue) == 1
    assert "Ocular Filter - Improved" in c.implant_names

    safe = snapshot_to_dict(c)
    dump = repr(safe)
    assert "SUPER_SECRET" not in dump
    assert "SECRET_TOKEN" not in dump
    assert "DO_NOT_READ" not in dump


def test_evemon_rejects_doctype(tmp_path: Path):
    f = tmp_path / "bad.xml"
    f.write_text('<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><Settings/>', encoding="utf-8")
    with pytest.raises(EvemonImportError):
        list_evemon_characters(f)


def test_old_snapshot_remains_readable():
    snapshot = snapshot_from_dict(
        {
            "character_id": 123456789,
            "character_name": "Old Pilot",
            "total_sp": 100,
            "unallocated_sp": 0,
            "attributes": {
                "intelligence": 20,
                "memory": 20,
                "perception": 20,
                "willpower": 20,
                "charisma": 19,
            },
            "skills": [],
            "implant_names": ["Ocular Filter - Improved"],
        }
    )

    assert snapshot.base_attributes.intelligence == 20
    assert snapshot.implant_attribute_bonus.perception == 5
    assert snapshot.booster_attribute_bonus.total == 0
