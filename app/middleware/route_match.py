"""Exact method/path matching for retired route templates."""

from __future__ import annotations

from collections.abc import Iterable


def matches_route_template(path: str, template: str) -> bool:
    """Match ``/a/{id}/b`` against ``/a/7/b`` without registering a route."""

    path_parts = path.strip("/").split("/")
    template_parts = template.strip("/").split("/")
    if len(path_parts) != len(template_parts):
        return False
    return all(
        template_part == path_part
        or (template_part.startswith("{") and template_part.endswith("}"))
        for path_part, template_part in zip(path_parts, template_parts, strict=True)
    )


def matches_retired_endpoint(
    method: str,
    path: str,
    endpoints: Iterable[tuple[str, str]],
) -> bool:
    """Return true when method+path is one of the retired endpoint contracts."""

    normalized = method.upper()
    return any(
        normalized == retired_method and matches_route_template(path, template)
        for retired_method, template in endpoints
    )


__all__ = ["matches_retired_endpoint", "matches_route_template"]
