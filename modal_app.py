"""Modal endpoint: live linting for the drafting demo.

Deploy (policyengine workspace; unset keychain overrides first):

    unset MODAL_TOKEN_ID MODAL_TOKEN_SECRET && modal deploy modal_app.py

Serves POST /lint {"text": "..."} -> {"findings": [...], "clean": bool}.
"""

import modal

app = modal.App("legislint")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("lxml>=5.0", "fastapi[standard]")
    .add_local_dir("src/legislint", "/root/legislint")
)


@app.function(image=image, min_containers=1, timeout=30)
@modal.asgi_app()
def api():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    from legislint import findings as findings_mod
    from legislint import textparse

    web = FastAPI()
    web.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )

    class LintRequest(BaseModel):
        text: str

    @web.get("/")
    def health():
        return {"ok": True, "checks": list(findings_mod.CHECKS)}

    @web.post("/lint")
    def lint(req: LintRequest):
        if len(req.text) > 200_000:
            return {"findings": [{"type": "input_too_large", "criterion": None,
                                  "detail": "text over 200k characters",
                                  "anchor": "", "location": "document"}],
                    "clean": False}
        found = textparse.lint_text(req.text)
        return {
            "findings": [findings_mod.finding_dict(f) for f in found],
            "clean": not found,
        }

    return web
