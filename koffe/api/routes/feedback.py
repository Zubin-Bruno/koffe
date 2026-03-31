from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from koffe.db.database import get_db
from koffe.db.models import Feedback

router = APIRouter()


@router.post("/feedback", response_class=HTMLResponse, tags=["feedback"])
def submit_feedback(
    roaster_suggestion: str = Form(""),
    general_feedback: str = Form(""),
    db: Session = Depends(get_db),
):
    """Save user feedback and return a thank-you HTML snippet for HTMX swap."""
    roaster_suggestion = roaster_suggestion.strip() or None
    general_feedback = general_feedback.strip() or None

    if roaster_suggestion or general_feedback:
        entry = Feedback(
            roaster_suggestion=roaster_suggestion,
            general_feedback=general_feedback,
        )
        db.add(entry)
        db.commit()

    return "<p class='feedback-success'>¡Gracias por tu feedback!</p>"
