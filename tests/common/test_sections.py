# tests/common/test_sections.py
from alr.common.sections import build_sections_map_full

class FakeVDB:
    Research_problem_DB_excel = "a"; Research_problem_DB_json = "b"; Research_problem_DB_bin = "c"
    Objective_DB_excel = "a"; Objective_DB_json = "b"; Objective_DB_bin = "c"
    Methodology_excel = "a"; Methodology_json = "b"; Methodology_bin = "c"
    Conclusion_DB_excel = "a"; Conclusion_DB_json = "b"; Conclusion_DB_bin = "c"
    Results_DB_excel = "a"; Results_DB_json = "b"; Results_DB_bin = "c"
    Research_Areas_DB_excel = "a"; Research_Areas_DB_json = "b"; Research_Areas_DB_bin = "c"
    Key_concepts_DB_excel = "a"; Key_concepts_DB_json = "b"; Key_concepts_DB_bin = "c"

def test_build_sections_map_full_has_all_seven_sections():
    result = build_sections_map_full(FakeVDB())
    assert len(result) == 7
    assert result["Methodology"] == ("a", "b", "c")  # would fail before the attribute-name fix