from cmu_cli.official_materials import public_materials, readable_topic


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self):
        self.headers = {}

    def head(self, url, timeout, allow_redirects):
        return FakeResponse(
            headers={
                "Content-Length": "1234",
                "Content-Type": "application/pdf",
                "ETag": '"etag-1"',
                "Last-Modified": "Mon, 31 Aug 2026 17:42:44 GMT",
            }
        )


def test_readable_topic_preserves_acronyms_and_small_words():
    assert (
        readable_topic("Introduction and ML as function approximation")
        == "Introduction_and_ML_as_Function_Approximation"
    )
    assert readable_topic("MLE / MAP") == "MLE_and_MAP"


def test_public_materials_discovers_only_linked_slide_pdfs(monkeypatch):
    html = """
    <table>
      <tr><td>Mon, Aug. 31</td><td>Example topic</td>
          <td><a href="slides/03-example.pdf">Lecture 3</a></td></tr>
      <tr><td>Wed, Sep. 2</td><td>Example methods</td><td>Lecture 4</td></tr>
    </table>
    """
    monkeypatch.setattr(
        "cmu_cli.official_materials.public_document",
        lambda *args, **kwargs: html.encode(),
    )
    monkeypatch.setattr(
        "cmu_cli.official_materials.safe_request",
        lambda session, url, **kw: FakeSession().head(url, 30, False),
    )
    rows = public_materials("DEMO-101", "https://courses.example.invalid/")
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Lecture_03_Example_Topic.pdf"
    assert rows[0]["size"] == 1234
    assert rows[0]["id"].startswith(
        "course-site:https://courses.example.invalid/slides/"
    )
