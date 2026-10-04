"""Text analysis service.

The route calls analyze_text(); later, replace the body with real AI
entity/relationship extraction without touching the route.
"""
from app.models.requests import AnalyzeResponse


def analyze_text(text: str) -> AnalyzeResponse:
    # PLACEHOLDER: no AI is called yet, so the graph is intentionally empty.
    return AnalyzeResponse(
        nodes=[],
        edges=[],
        status="placeholder",
        message="AI analysis is not implemented yet. No nodes or edges were extracted.",
        input_length=len(text),
    )
