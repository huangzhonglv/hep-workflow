from __future__ import annotations

import re
from pathlib import Path


AGENT_ROUTE_CASES = [
    ("reproduce arxiv 2401.01234", "repro-orchestrator"),
    ("reproduce Fig. 3 of paper", "repro-orchestrator"),
    ("start a new project", "hep-orchestrator"),
    ("run the full pipeline", "hep-orchestrator"),
    ("project progress", "hep-orchestrator"),
    ("project status", "hep-orchestrator"),
    ("reproduction progress", "repro-orchestrator"),
]


def frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None, f"missing YAML frontmatter in {path}"
    return match.group(1)


def description(frontmatter_text: str) -> str:
    lines = frontmatter_text.splitlines()
    output: list[str] = []
    in_description = False

    for line in lines:
        if line.startswith("description:"):
            in_description = True
            remainder = line.split(":", 1)[1].strip()
            if remainder and remainder != ">":
                output.append(remainder)
            continue
        if in_description:
            if line and not line.startswith(" "):
                break
            output.append(line.strip())

    return " ".join(output)


def quoted_trigger_phrases(path: Path) -> set[str]:
    desc = description(frontmatter(path))
    phrases: set[str] = set()

    for quoted in re.findall(r'"([^"]+)"', desc):
        for phrase in quoted.split("/"):
            normalized = " ".join(phrase.strip().lower().split())
            if normalized:
                phrases.add(normalized)

    return phrases


def matches(query: str, phrases: set[str]) -> bool:
    lowered = query.lower()
    return any(phrase in lowered for phrase in phrases)


def primary_route(query: str, phrase_sets: dict[str, set[str]]) -> str | None:
    matched = {
        name for name, phrases in phrase_sets.items() if matches(query, phrases)
    }
    for name in [
        "repro-orchestrator",
        "hep-orchestrator",
        "hep-paper-formalize",
        "hep-idea",
    ]:
        if name in matched:
            return name
    return None


def test_paper_reproduction_queries_trigger_paper_formalize_not_hep_idea(repo_root) -> None:
    hep_idea = repo_root / ".claude" / "skills" / "hep-idea" / "SKILL.md"
    hep_paper = repo_root / ".claude" / "skills" / "hep-paper-formalize" / "SKILL.md"
    hep_orchestrator = repo_root / ".claude" / "agents" / "hep-orchestrator.md"

    idea_phrases = quoted_trigger_phrases(hep_idea)
    paper_phrases = quoted_trigger_phrases(hep_paper)
    orchestrator_phrases = quoted_trigger_phrases(hep_orchestrator)

    assert "research idea" in idea_phrases
    assert "reproduce" in paper_phrases
    assert "run the full pipeline" in orchestrator_phrases

    paper_queries = [
        "reproduce arxiv 2401.01234",
        "reproduce paper fig 3a",
        "replicate fig 2 from the local PDF",
        "rebuild paper Table 2",
        "import paper from DOI 10.1234/example",
        "build workspace from arxiv 2601.01234",
    ]

    for query in paper_queries:
        assert matches(query, paper_phrases), query
        assert not matches(query, idea_phrases), query


def test_hep_idea_queries_do_not_trigger_paper_formalize(repo_root) -> None:
    hep_idea = repo_root / ".claude" / "skills" / "hep-idea" / "SKILL.md"
    hep_paper = repo_root / ".claude" / "skills" / "hep-paper-formalize" / "SKILL.md"

    idea_phrases = quoted_trigger_phrases(hep_idea)
    paper_phrases = quoted_trigger_phrases(hep_paper)

    idea_queries = [
        "research idea for a muon g-2 project",
        "new project about scalar triplets",
        "propose a topic for BSM phenomenology",
        "generate proposal for lepton flavor violation",
        "start a new study of ALP constraints",
        "research direction for a one-loop tau observable",
    ]

    for query in idea_queries:
        assert matches(query, idea_phrases), query
        assert not matches(query, paper_phrases), query


def test_agent_trigger_precedence_routes_to_one_agent(repo_root) -> None:
    phrase_sets = {
        "hep-orchestrator": quoted_trigger_phrases(
            repo_root / ".claude" / "agents" / "hep-orchestrator.md"
        ),
        "repro-orchestrator": quoted_trigger_phrases(
            repo_root / ".claude" / "agents" / "repro-orchestrator.md"
        ),
    }

    assert "run the full pipeline" in phrase_sets["hep-orchestrator"]
    assert "project status" in phrase_sets["hep-orchestrator"]
    assert "reproduce" in phrase_sets["repro-orchestrator"]
    assert "reproduce" in phrase_sets["repro-orchestrator"]

    for query, expected in AGENT_ROUTE_CASES:
        matched_agents = {
            name for name, phrases in phrase_sets.items() if matches(query, phrases)
        }
        assert matched_agents == {expected}, query


def test_agent_vs_skill_primary_route_precedence(repo_root) -> None:
    phrase_sets = {
        "hep-orchestrator": quoted_trigger_phrases(
            repo_root / ".claude" / "agents" / "hep-orchestrator.md"
        ),
        "repro-orchestrator": quoted_trigger_phrases(
            repo_root / ".claude" / "agents" / "repro-orchestrator.md"
        ),
        "hep-idea": quoted_trigger_phrases(
            repo_root / ".claude" / "skills" / "hep-idea" / "SKILL.md"
        ),
        "hep-paper-formalize": quoted_trigger_phrases(
            repo_root / ".claude" / "skills" / "hep-paper-formalize" / "SKILL.md"
        ),
    }

    assert matches("reproduce arxiv 2401.01234", phrase_sets["hep-paper-formalize"])
    assert (
        primary_route("reproduce arxiv 2401.01234", phrase_sets)
        == "repro-orchestrator"
    )

    assert matches("start a new project", phrase_sets["hep-idea"])
    assert (
        primary_route("start a new project", phrase_sets)
        == "hep-orchestrator"
    )
