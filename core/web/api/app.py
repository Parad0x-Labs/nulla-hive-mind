from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from .runtime import RuntimeServices, default_workspace_root
from .service import ApiResponse, dispatch_get, dispatch_post, json_response


def _starlette_response(response: ApiResponse) -> Response:
    if response.stream is not None:
        return StreamingResponse(response.stream, status_code=response.status, media_type=response.content_type, headers=response.headers)
    payload = response.body or b""
    return Response(payload, status_code=response.status, media_type=response.content_type, headers=response.headers)


async def _dispatch(request: Request) -> Response:
    runtime: RuntimeServices = request.app.state.runtime
    model_name: str = request.app.state.model_name
    get_dispatcher: Callable[..., ApiResponse] = getattr(request.app.state, "get_dispatcher", dispatch_get)
    post_dispatcher: Callable[..., ApiResponse] = getattr(request.app.state, "post_dispatcher", dispatch_post)
    workspace_root_provider: Callable[[], str] = getattr(
        request.app.state,
        "workspace_root_provider",
        default_workspace_root,
    )
    if request.method == "GET":
        response = get_dispatcher(
            path=request.url.path,
            query=parse_qs(request.url.query),
            runtime=runtime,
            model_name=model_name,
        )
        return _starlette_response(response)
    if request.method == "POST":
        raw_body = await request.body()
        if not raw_body:
            return _starlette_response(json_response(400, {"error": "empty body"}))
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return _starlette_response(json_response(400, {"error": "invalid JSON"}))
        response = post_dispatcher(
            path=request.url.path,
            body=body,
            headers=dict(request.headers.items()),
            runtime=runtime,
            model_name=model_name,
            workspace_root_provider=workspace_root_provider,
        )
        return _starlette_response(response)
    return _starlette_response(json_response(404, {"error": "not found"}))


def create_api_app(
    *,
    runtime: RuntimeServices,
    model_name: str,
    get_dispatcher: Callable[..., ApiResponse] = dispatch_get,
    post_dispatcher: Callable[..., ApiResponse] = dispatch_post,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> Starlette:
    app = Starlette(
        debug=False,
        routes=[
            Route("/", _dispatch, methods=["GET", "POST"]),
            Route("/{path:path}", _dispatch, methods=["GET", "POST"]),
        ],
    )
    app.state.runtime = runtime
    app.state.model_name = model_name
    app.state.get_dispatcher = get_dispatcher
    app.state.post_dispatcher = post_dispatcher
    app.state.workspace_root_provider = workspace_root_provider
    return app
