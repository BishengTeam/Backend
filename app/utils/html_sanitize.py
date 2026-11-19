"""严格白名单 HTML 消毒：协议正文入库与渲染前使用。"""
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "div", "span", "strong", "b", "em", "i", "u", "s",
    "sub", "sup", "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "a", "img",
})

VOID_TAGS = frozenset({"br", "hr", "img"})

ALLOWED_STYLE_PROPERTIES = frozenset({
    "color", "background-color", "text-align", "text-indent",
    "font-size", "font-weight", "font-family", "font-style",
    "text-decoration", "line-height", "letter-spacing",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-collapse", "border-width", "border-style", "border-color",
    "vertical-align", "white-space", "width", "height", "max-width",
})

_STYLE_FORBIDDEN = ("url(", "expression", "javascript:", "@import", "</", "\\")
_URL_SCHEMES = ("http://", "https://", "mailto:", "tel:")

# 这些标签的文本内容同样必须丢弃（保留会变成可见的脚本源码或被再次解释）。
DANGEROUS_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "noscript",
    "template", "svg", "math", "form", "link", "meta", "base",
})


def _safe_url(value: str, *, image: bool = False) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if lowered.startswith(_URL_SCHEMES):
        return candidate
    if image and lowered.startswith("data:image/"):
        return candidate
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    if candidate.startswith("#"):
        return candidate
    return None


def _safe_style(value: str) -> str | None:
    declarations: list[str] = []
    for part in value.split(";"):
        if ":" not in part:
            continue
        prop, raw = part.split(":", 1)
        prop = prop.strip().lower()
        raw = raw.strip()
        if prop not in ALLOWED_STYLE_PROPERTIES or not raw:
            continue
        lowered = raw.lower()
        if any(token in lowered for token in _STYLE_FORBIDDEN):
            continue
        declarations.append(f"{prop}: {raw}")
    return "; ".join(declarations) or None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in DANGEROUS_TAGS:
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return
        if tag not in ALLOWED_TAGS:
            return

        safe_attrs: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.lower()
            value = "" if value is None else value
            if name.startswith("on"):
                continue
            if name == "style":
                style = _safe_style(value)
                if style:
                    safe_attrs.append(("style", style))
            elif name in {"colspan", "rowspan"} and value.isdigit():
                safe_attrs.append((name, value))
            elif name in {"width", "height"} and value.replace("%", "").isdigit():
                safe_attrs.append((name, value))
            elif tag == "a" and name == "title":
                safe_attrs.append((name, value))
            elif tag == "a" and name == "href":
                url = _safe_url(value)
                if url:
                    safe_attrs.append((name, url))
            elif tag == "img" and name == "src":
                url = _safe_url(value, image=True)
                if url:
                    safe_attrs.append((name, url))
            elif tag == "img" and name in {"alt", "title"}:
                safe_attrs.append((name, value))

        rendered = "".join(
            f' {key}="{escape(value, quote=True)}"' for key, value in safe_attrs
        )
        if tag == "a":
            self.parts.append(
                f'<a{rendered} rel="noopener noreferrer" target="_blank">'
            )
        else:
            self.parts.append(f"<{tag}{rendered}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_stack:
            if tag == self.skip_stack[-1]:
                self.skip_stack.pop()
            return
        if tag in VOID_TAGS or tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        self.parts.append(escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def result(self) -> str:
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts)


def sanitize_html(content: str | None) -> str:
    if not content:
        return ""
    parser = _Sanitizer()
    parser.feed(content)
    parser.close()
    return parser.result()
