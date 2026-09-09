from pathlib import Path

from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from accessride.services.demo import DemoRecoveryService


def create_app(service: DemoRecoveryService | None = None) -> FastAPI:
    app = FastAPI(title="AccessRide Recovery", version="0.4.0")
    app.state.demo = service or DemoRecoveryService()
    templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "web" / "templates"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "fixture-only-single-process"}

    def render_demo(request: Request) -> HTMLResponse:
        current = app.state.demo
        current.refresh_options()
        confirmations = {}
        if current.incident.state.value == "OPTIONS_READY":
            for decision in current.options.values():
                if decision.status.value == "VERIFIED_COMPATIBLE":
                    confirmations[decision.decision_id] = current.issue_confirmation(decision.decision_id)
        return templates.TemplateResponse(request, "demo.html", {"incident": current.incident,
            "providers": current.roster.providers(), "options": current.options,
            "timeline": current.audit_timeline(), "confirmations": confirmations, "packet": current.packet})

    @app.get("/", response_class=HTMLResponse)
    @app.get("/demo", response_class=HTMLResponse)
    def demo(request: Request):
        return render_demo(request)

    @app.post("/demo/incidents/{incident_id}/approve", response_class=HTMLResponse)
    async def approve(request: Request, incident_id: str):
        current = app.state.demo
        if incident_id != current.incident.incident_id:
            raise HTTPException(404, "incident not found")
        # Origin is an additional browser safeguard. The local form capability
        # remains mandatory because command-line/local tooling may omit Origin.
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "cross-origin approval is refused")
        fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        decision_id = fields.get("decision_id", [""])[0]
        confirmation_token = fields.get("confirmation_token", [""])[0]
        try:
            current.approve_with_confirmation(decision_id, confirmation_token)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from error
        return render_demo(request)

    return app


app = create_app()
