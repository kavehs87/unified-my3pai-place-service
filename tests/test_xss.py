from dmo.services.detail import _prosemirror_to_html, _safe_href, transform_description


class TestSafeHref:
    def test_normal_http(self):
        assert _safe_href("https://example.com") == "https://example.com"

    def test_normal_relative(self):
        assert _safe_href("/page") == "/page"

    def test_javascript_blocked(self):
        assert _safe_href("javascript:alert(1)") == "#"

    def test_javascript_case_insensitive(self):
        assert _safe_href("JavaScript:alert(1)") == "#"

    def test_data_blocked(self):
        assert _safe_href("data:text/html,<script>alert(1)</script>") == "#"

    def test_vbscript_blocked(self):
        assert _safe_href("vbscript:msgbox(1)") == "#"

    def test_empty_string(self):
        assert _safe_href("") == "#"

    def test_none(self):
        assert _safe_href(None) == "#"

    def test_whitespace_javascript(self):
        assert _safe_href("  javascript:alert(1)") == "#"


class TestProsemirrorToHtml:
    def test_script_tag_in_text_escaped(self):
        node = {"type": "text", "text": "<script>alert('xss')</script>hello"}
        result = _prosemirror_to_html(node)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_tag_breakout_escaped(self):
        node = {"type": "text", "text": "</a><script>alert(1)</script>"}
        result = _prosemirror_to_html(node)
        assert "&lt;/a&gt;" in result
        assert "<script>" not in result

    def test_link_javascript_href_blocked(self):
        node = {
            "type": "text",
            "text": "click",
            "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
        }
        result = _prosemirror_to_html(node)
        assert 'href="#"' in result
        assert "javascript:" not in result

    def test_link_data_href_blocked(self):
        node = {
            "type": "text",
            "text": "click",
            "marks": [{"type": "link", "attrs": {"href": "data:text/html,<script>XSS</script>"}}],
        }
        result = _prosemirror_to_html(node)
        assert 'href="#"' in result

    def test_link_normal_href_preserved(self):
        node = {
            "type": "text",
            "text": "link",
            "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
        }
        result = _prosemirror_to_html(node)
        assert 'href="https://example.com"' in result

    def test_code_block_injection_escaped(self):
        node = {"type": "codeBlock", "text": "</code></pre><script>alert(1)</script>"}
        result = _prosemirror_to_html(node)
        assert "<script>" not in result
        assert "&lt;/code&gt;" in result

    def test_bold_with_xss_text(self):
        node = {
            "type": "text",
            "text": "<img onerror=alert(1)>",
            "marks": [{"type": "bold"}],
        }
        result = _prosemirror_to_html(node)
        assert "<strong>" in result
        assert "<img" not in result  # raw <img tag must not appear
        assert "&lt;img" in result  # escaped version should appear

    def test_paragraph_with_safe_content(self):
        node = {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}
        result = _prosemirror_to_html(node)
        assert result == "<p>Hello world</p>"

    def test_heading_safe(self):
        node = {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Title"}],
        }
        result = _prosemirror_to_html(node)
        assert result == "<h2>Title</h2>"

    def test_list_safe(self):
        node = {
            "type": "bulletList",
            "content": [{"type": "listItem", "content": [{"type": "text", "text": "Item"}]}],
        }
        result = _prosemirror_to_html(node)
        assert result == "<ul><li>Item</li></ul>"


class TestTransformDescription:
    def test_html_passthrough_script_stripped(self):
        result = transform_description("<p>Hello</p><script>alert(1)</script>", "html")
        assert result is not None and "<script>" not in result
        assert result is not None and "<p>Hello</p>" in result

    def test_html_passthrough_event_handler_stripped(self):
        result = transform_description("<img src=x onerror=alert(1)>", "html")
        assert result is not None and "onerror" not in result

    def test_html_passthrough_safe_tags_preserved(self):
        result = transform_description("<p><strong>Bold</strong> and <em>italic</em></p>", "html")
        assert result is not None and "<strong>Bold</strong>" in result
        assert result is not None and "<em>italic</em>" in result

    def test_html_passthrough_link_href_preserved(self):
        result = transform_description('<a href="https://example.com">link</a>', "html")
        assert result is not None and 'href="https://example.com"' in result

    def test_html_passthrough_javascript_href_stripped(self):
        result = transform_description('<a href="javascript:alert(1)">click</a>', "html")
        assert result is not None and "javascript:" not in result

    def test_none_description(self):
        assert transform_description(None, "html") is None

    def test_empty_description(self):
        assert transform_description("", "html") == ""

    def test_prosemirror_valid(self):
        pm = '{"content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}]}'
        result = transform_description(pm, "prosemirror")
        assert result == "<p>Hello</p>"

    def test_prosemirror_invalid_json_returns_cleaned(self):
        result = transform_description("not json", "prosemirror")
        assert result == "not json"

    def test_unknown_format_returns_original(self):
        result = transform_description("raw text", "markdown")
        assert result == "raw text"

    def test_html_passthrough_data_uri_blocked(self):
        result = transform_description(
            '<a href="data:text/html,<script>XSS</script>">click</a>', "html"
        )
        assert result is not None and "data:" not in result
